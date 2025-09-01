#!/usr/bin/env python3
"""
Standalone OCR preview tool (no MCP/UI automation).

- Loads PNGs from a directory (default: fb-screenshots-test)
- Runs OCR using backend.ocr_service (Tesseract)
- Parses Facebook-style comments using a local copy of the parsing logic
- Writes HTML and JSON previews to an output directory (default: ocr_previews)

Run examples (from project root):
  python tools/ocr_preview.py --input fb-screenshots-test --out ocr_previews --limit 5
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any


def _ensure_project_root_on_path() -> None:
    this_file = Path(__file__).resolve()
    project_root = this_file.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


_ensure_project_root_on_path()

from PIL import Image  # type: ignore

# Import OCR service (safe: no MCP/UI side effects)
from backend.ocr_service import ocr_service  # type: ignore


# ------------------------
# Minimal parsing utilities
# ------------------------
import re
import unicodedata


def _normalize_text(value: str) -> str:
    if not isinstance(value, str):
        return ''
    text = unicodedata.normalize('NFC', value)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_ui_element(line: str) -> bool:
    if not line:
        return False
    ui_elements = [
        'alle kommentare', 'all comments', 'mehr anzeigen', 'show more',
        'relevanteste', 'most relevant', 'neueste', 'newest',
        'weitere kommentare', 'more comments', 'antworten', 'replies',
        'teilen', 'share', 'melden', 'report', 'verbergen', 'hide'
    ]
    return any(element in line.lower() for element in ui_elements)


def is_timestamp_line(line: str) -> bool:
    if not line:
        return False
    patterns = [
        r'\bvor\s+\d+\s+min\.?',
        r'\bvor\s+\d+\s+std\.?',
        r'\bvor\s+\d+\s+stunde[n]?\b',
        r'\bvor\s+\d+\s+minute[n]?\b',
        r'\bvor\s+\d+\s+tag(e|en)?\b',
        r'\bvor\s+\d+\s+woche(n)?\b',
        r'\bvor\s+\d+\s+monat(e|en)?\b',
        r'\bvor\s+\d+\s+jahr(e|en)?\b',
        r'\b\d+\s*h\b',
        r'\b\d+\s*m\b',
        r'\b\d+\s*d\b',
        r'\b\d+\s*w\b',
        r'\b\d+\s+hours?\s+ago\b',
        r'\b\d+\s+minutes?\s+ago\b',
        r'\b\d+\s+days?\s+ago\b',
        r'\b\d{1,2}\.\d{1,2}\.\d{2,4}\b',
    ]
    low = line.lower()
    return any(re.search(p, low) for p in patterns)


def is_engagement_line(line: str) -> bool:
    if not line:
        return False
    patterns = [
        r'\bgefällt\s+mir\b',
        r'\d+\s+gefällt\s+mir\b',
        r'\blike\b',
        r'\d+\s+like',
        r'\bantworten\b',
        r'\breply\b',
        r'\d+\s+kommentar',
        r'\bcomment\b',
        r'👍|❤️|😊|😢|😡|🔥',
    ]
    low = line.lower()
    return any(re.search(p, low) for p in patterns)


def is_potential_author_name(line: str) -> bool:
    if not line or len(line) > 80:
        return False
    if is_ui_element(line) or is_timestamp_line(line) or is_engagement_line(line):
        return False
    if line.lower().startswith('antwort '):
        return False
    if any(char in line for char in ['http', 'www', '@', '#', '...']):
        return False
    if any(char.isdigit() for char in line):
        return False
    words = line.split()
    if len(words) > 6:
        return False
    return True


def parse_facebook_author_line(line: str) -> Dict[str, Any]:
    line = line.strip()
    m = re.match(r'^(.+?)\s+(?:Antwort|antwort)\s+(.+)$', line)
    if m:
        return {'author': m.group(1).strip(), 'is_reply': True, 'reply_target': m.group(2).strip()}
    if re.match(r'^(?:Antwort|antwort)\b', line):
        return {'author': '', 'is_reply': False, 'reply_target': None}
    words = line.split()
    if len(words) >= 4:
        mid = len(words) // 2
        return {'author': ' '.join(words[:mid]), 'is_reply': True, 'reply_target': ' '.join(words[mid:])}
    return {'author': line, 'is_reply': False, 'reply_target': None}


def extract_comment_block(lines: List[str], start_index: int) -> Dict[str, Any] | None:
    if start_index >= len(lines):
        return None
    author_line = lines[start_index]
    comment: Dict[str, Any] = {
        'author': '', 'content': '', 'reply_target': None,
        'timestamp': '', 'reactions': '', 'reaction_count': 0, 'comment_count': 0,
        'is_reply': False, 'parent_comment_id': None, 'source': 'facebook_ocr_structured'
    }
    current_index = start_index
    info = parse_facebook_author_line(author_line)
    comment['author'] = info['author']
    comment['is_reply'] = info['is_reply']
    comment['reply_target'] = info['reply_target']
    current_index += 1

    content_lines: List[str] = []
    while current_index < len(lines):
        line = lines[current_index]
        if is_timestamp_line(line):
            comment['timestamp'] = line
            current_index += 1
            break
        elif is_engagement_line(line):
            comment['reactions'] = line
            current_index += 1
            break
        elif is_potential_author_name(line):
            break
        elif is_ui_element(line):
            current_index += 1
            break
        else:
            content_lines.append(line)
            current_index += 1
            if len(' '.join(content_lines)) >= 1500:
                break

    comment['content'] = ' '.join(content_lines).strip()
    if not comment['content'] and comment['is_reply'] and comment['reply_target']:
        comment['content'] = f"Antwort an {comment['reply_target']}"

    if current_index < len(lines) and is_engagement_line(lines[current_index]):
        comment['reactions'] = lines[current_index]
        current_index += 1

    if comment['content'] or (comment['is_reply'] and comment['reply_target']):
        return {'comment': comment, 'next_index': current_index}
    return None


def parse_facebook_comment_structure(lines: List[str]) -> List[Dict[str, Any]]:
    comments: List[Dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if is_ui_element(line):
            i += 1
            continue
        if is_potential_author_name(line):
            block = extract_comment_block(lines, i)
            if block:
                comments.append(block['comment'])
                i = block['next_index']
            else:
                i += 1
        else:
            i += 1
    return comments


def parse_comments_from_ocr_text(ocr_text: str) -> List[Dict[str, Any]]:
    lines = [_normalize_text(line) for line in (ocr_text or '').split('\n')]
    lines = [l for l in lines if l]
    parsed = parse_facebook_comment_structure(lines)
    # Basic validation: author and at least 1 char content
    valid: List[Dict[str, Any]] = []
    for c in parsed:
        author = (c.get('author') or '').strip()
        content = (c.get('content') or '').strip()
        if author and content:
            valid.append(c)
    return valid


# ------------------------
# HTML rendering
# ------------------------
def _html_escape(s: str) -> str:
    return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def render_html_preview(image_path: Path, comments: List[Dict[str, Any]]) -> str:
    items = []
    for idx, c in enumerate(comments, 1):
        author = _html_escape(c.get('author', ''))
        content = _html_escape(c.get('content', ''))
        reply = c.get('is_reply')
        target = _html_escape(c.get('reply_target') or '')
        ts = _html_escape(c.get('timestamp') or '')
        reacts = _html_escape(c.get('reactions') or '')
        items.append(
            f"""
            <div class=\"comment\">
              <div class=\"meta\">
                <span class=\"author\">{author}</span>
                {(' <span class=\"reply\">antwortet auf ' + target + '</span>') if (reply and target) else ''}
              </div>
              <div class=\"content\">{content}</div>
              <div class=\"foot\">{ts} {(' · ' + reacts) if reacts else ''}</div>
            </div>
            """
        )

    style = """
    <style>
      body{font-family:ui-sans-serif,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;background:#f8fafc;color:#0f172a}
      h1{font-size:20px;margin:0 0 12px}
      .src{color:#334155;font-size:12px;margin-bottom:16px}
      .comment{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 14px;margin:12px 0}
      .meta{font-weight:600;margin-bottom:6px}
      .meta .reply{font-weight:400;color:#334155;margin-left:6px}
      .content{white-space:pre-wrap;line-height:1.35}
      .foot{color:#475569;font-size:12px;margin-top:6px}
    </style>
    """

    return f"""
    <html>
      <head><meta charset=\"utf-8\">{style}<title>OCR Preview - {image_path.name}</title></head>
      <body>
        <h1>OCR Preview</h1>
        <div class=\"src\">Source image: {image_path}</div>
        {''.join(items) if items else '<div>No comments parsed.</div>'}
      </body>
    </html>
    """


# ------------------------
# CLI
# ------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description='Run OCR and parse Facebook comments for preview.')
    parser.add_argument('--input', '-i', type=str, default='fb-screenshots-test', help='Input directory with PNGs')
    parser.add_argument('--out', '-o', type=str, default='ocr_previews', help='Output directory for HTML/JSON previews')
    parser.add_argument('--limit', type=int, default=0, help='Max number of images to process (0 = all)')
    args = parser.parse_args()

    in_dir = Path(args.input).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not in_dir.exists():
        print(f"[ERROR] Input directory not found: {in_dir}")
        sys.exit(1)

    images = sorted([p for p in in_dir.glob('*.png')])
    if args.limit and args.limit > 0:
        images = images[: args.limit]

    if not images:
        print(f"[INFO] No PNG images found in: {in_dir}")
        return

    processed = 0
    for img_path in images:
        try:
            image = Image.open(img_path)
            ocr_result = ocr_service.extract_text_from_image(image)
            ocr_text = ocr_result.get('text', '')
            comments = parse_comments_from_ocr_text(ocr_text)

            # Write JSON
            json_path = out_dir / f"{img_path.stem}.json"
            with json_path.open('w', encoding='utf-8') as jf:
                json.dump({'image': str(img_path), 'comments': comments}, jf, ensure_ascii=False, indent=2)

            # Write HTML
            html = render_html_preview(img_path, comments)
            html_path = out_dir / f"{img_path.stem}.html"
            with html_path.open('w', encoding='utf-8') as hf:
                hf.write(html)

            print(f"[OK] {img_path.name} -> {html_path.name}, {json_path.name} ({len(comments)} comments)")
            processed += 1
        except Exception as e:
            print(f"[ERROR] Failed on {img_path.name}: {e}")

    print(f"[DONE] Processed {processed} images. Output: {out_dir}")


if __name__ == '__main__':
    main()


