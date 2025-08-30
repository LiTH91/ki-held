import asyncio
from typing import Dict, Any


def _make_fake_ocr_result() -> Dict[str, Any]:
    # Three authors stacked vertically with increasing indentation
    # Max (level 0), Anna (level 1), Tom (level 2)
    words = [
        # Authors
        {"text": "Max", "confidence": 95, "bbox": {"left": 100, "top": 100, "width": 60, "height": 20}},
        {"text": "Mustermann", "confidence": 95, "bbox": {"left": 165, "top": 100, "width": 140, "height": 20}},
        {"text": "Anna", "confidence": 95, "bbox": {"left": 120, "top": 180, "width": 70, "height": 20}},
        {"text": "Schmidt", "confidence": 95, "bbox": {"left": 195, "top": 180, "width": 120, "height": 20}},
        {"text": "Tom", "confidence": 95, "bbox": {"left": 140, "top": 260, "width": 60, "height": 20}},
        {"text": "Weber", "confidence": 95, "bbox": {"left": 205, "top": 260, "width": 100, "height": 20}},

        # Content near each author (below or same y-range)
        {"text": "Das", "confidence": 90, "bbox": {"left": 110, "top": 130, "width": 40, "height": 18}},
        {"text": "ist", "confidence": 90, "bbox": {"left": 155, "top": 130, "width": 35, "height": 18}},
        {"text": "ein", "confidence": 90, "bbox": {"left": 195, "top": 130, "width": 35, "height": 18}},
        {"text": "Kommentar", "confidence": 90, "bbox": {"left": 235, "top": 130, "width": 120, "height": 18}},

        {"text": "Antwort", "confidence": 90, "bbox": {"left": 130, "top": 210, "width": 90, "height": 18}},
        {"text": "auf", "confidence": 90, "bbox": {"left": 225, "top": 210, "width": 35, "height": 18}},
        {"text": "Max", "confidence": 90, "bbox": {"left": 265, "top": 210, "width": 60, "height": 18}},

        {"text": "Noch", "confidence": 90, "bbox": {"left": 150, "top": 290, "width": 50, "height": 18}},
        {"text": "eine", "confidence": 90, "bbox": {"left": 205, "top": 290, "width": 50, "height": 18}},
        {"text": "Antwort", "confidence": 90, "bbox": {"left": 260, "top": 290, "width": 90, "height": 18}},
    ]

    # Simple lines aggregation for completeness; not used heavily by analyzer
    lines = [
        {
            "text": "Max Mustermann",
            "words": words[0:2],
            "avg_confidence": 95,
        },
        {
            "text": "Anna Schmidt",
            "words": words[2:4],
            "avg_confidence": 95,
        },
        {
            "text": "Tom Weber",
            "words": words[4:6],
            "avg_confidence": 95,
        },
    ]

    return {
        "text": "",
        "confidence": 92,
        "words": words,
        "lines": lines,
        "word_count": len(words),
        "line_count": len(lines),
    }


def test_thread_hierarchy_indentation(monkeypatch):
    # Import here to avoid side effects if other tests import earlier
    from backend import mcp_service

    # Stub OCR to deterministic fake output
    monkeypatch.setattr(
        mcp_service.ocr_service,
        "extract_text_from_base64",
        lambda img_b64, **kwargs: _make_fake_ocr_result(),
    )

    # Provide any non-empty base64 so the function doesn't try to screenshot
    screenshot_b64 = "ZHVtbXk="

    result = asyncio.run(mcp_service.analyze_comment_hierarchy_visual(screenshot_b64))

    assert isinstance(result, list)
    assert len(result) == 3  # Three authors detected

    # Sort by Y position to map them in visual order (top to bottom)
    result_sorted = sorted(result, key=lambda r: r["y_position"]) 

    # Expected levels: 0, 1, 2 (based on left positions 100, 120, 140)
    levels = [c["thread_level"] for c in result_sorted]
    assert levels == [0, 1, 2]

    # Parent relationships: level 1 and 2 should have a parent
    assert result_sorted[0]["parent_comment_id"] is None
    assert result_sorted[1]["parent_comment_id"] is not None
    assert result_sorted[2]["parent_comment_id"] is not None


