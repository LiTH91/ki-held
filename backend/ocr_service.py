"""
OCR Service for processing screenshots and extracting text using Tesseract.
"""
import os
import base64
from io import BytesIO
from typing import List, Dict, Any, Optional, Tuple
from PIL import Image
import pytesseract
from pathlib import Path
import logging

from backend.config import settings

log = logging.getLogger(__name__)

class OCRService:
    """Service for Optical Character Recognition using Tesseract."""
    
    def __init__(self):
        """Initialize OCR service with Tesseract configuration."""
        self._configure_tesseract()
        
    def _configure_tesseract(self):
        """Configure Tesseract path and settings."""
        # Set Tesseract path - try multiple possible locations
        tesseract_paths = [
            # Local installation in project folder
            Path(__file__).parent.parent / "Tesseract-OCR" / "tesseract.exe",
            # Standard Windows installation paths
            Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
            Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
            # Default (assumes it's in PATH)
            "tesseract"
        ]
        
        for tesseract_path in tesseract_paths:
            try:
                pytesseract.pytesseract.tesseract_cmd = str(tesseract_path)
                # Test if this path works
                version = pytesseract.get_tesseract_version()
                log.info(f"✅ Tesseract configured successfully at {tesseract_path} (version: {version})")
                return
            except Exception as e:
                log.debug(f"❌ Tesseract path {tesseract_path} failed: {e}")
                continue
                
        raise RuntimeError("Could not configure Tesseract. Please ensure it's installed.")
    
    def extract_text_from_base64(self, image_base64: str, **kwargs) -> Dict[str, Any]:
        """
        Extract text from a base64-encoded image.
        
        Args:
            image_base64: Base64-encoded image data
            **kwargs: Additional arguments for pytesseract (lang, config, etc.)
            
        Returns:
            Dict containing extracted text and metadata
        """
        try:
            # Decode base64 image
            image_data = base64.b64decode(image_base64)
            image = Image.open(BytesIO(image_data))
            
            return self.extract_text_from_image(image, **kwargs)
            
        except Exception as e:
            log.error(f"❌ Failed to extract text from base64 image: {e}")
            return {
                "text": "",
                "confidence": 0,
                "error": str(e),
                "words": [],
                "lines": []
            }
    
    def extract_text_from_image(self, image: Image.Image, **kwargs) -> Dict[str, Any]:
        """
        Extract text from a PIL Image.
        
        Args:
            image: PIL Image object
            **kwargs: Additional arguments for pytesseract
            
        Returns:
            Dict containing extracted text and metadata
        """
        try:
            # Default configuration for better accuracy
            default_config = {
                'lang': 'deu+eng',  # German + English
                'config': '--oem 3 --psm 6'  # Best OCR Engine Mode, assume uniform block of text
            }
            
            # Merge with provided kwargs
            ocr_config = {**default_config, **kwargs}
            
            # Extract text
            text = pytesseract.image_to_string(image, **ocr_config)
            
            # Get detailed data with bounding boxes and confidence scores
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, **ocr_config)
            
            # Process the data to extract words and lines with confidence
            words = []
            lines = []
            current_line = []
            current_line_num = -1
            
            for i in range(len(data['text'])):
                if data['text'][i].strip():  # Only non-empty text
                    word_info = {
                        'text': data['text'][i],
                        'confidence': data['conf'][i],
                        'bbox': {
                            'left': data['left'][i],
                            'top': data['top'][i],
                            'width': data['width'][i],
                            'height': data['height'][i]
                        }
                    }
                    words.append(word_info)
                    
                    # Group words by line
                    line_num = data['line_num'][i]
                    if line_num != current_line_num:
                        if current_line:
                            lines.append({
                                'text': ' '.join([w['text'] for w in current_line]),
                                'words': current_line,
                                'avg_confidence': sum([w['confidence'] for w in current_line]) / len(current_line)
                            })
                        current_line = [word_info]
                        current_line_num = line_num
                    else:
                        current_line.append(word_info)
            
            # Add the last line
            if current_line:
                lines.append({
                    'text': ' '.join([w['text'] for w in current_line]),
                    'words': current_line,
                    'avg_confidence': sum([w['confidence'] for w in current_line]) / len(current_line)
                })
            
            # Calculate overall confidence
            overall_confidence = sum([w['confidence'] for w in words]) / len(words) if words else 0
            
            result = {
                "text": text.strip(),
                "confidence": overall_confidence,
                "words": words,
                "lines": lines,
                "word_count": len(words),
                "line_count": len(lines)
            }
            
            log.info(f"🔍 OCR extracted {len(words)} words in {len(lines)} lines (confidence: {overall_confidence:.1f}%)")
            log.debug(f"📄 OCR text preview: '{text[:100]}...'")
            
            return result
            
        except Exception as e:
            log.error(f"❌ Failed to extract text from image: {e}")
            return {
                "text": "",
                "confidence": 0,
                "error": str(e),
                "words": [],
                "lines": []
            }
    
    def find_text_in_ocr_result(self, ocr_result: Dict[str, Any], search_texts: List[str], fuzzy: bool = True) -> List[Dict[str, Any]]:
        """
        Find specific text patterns in OCR results.
        
        Args:
            ocr_result: Result from extract_text_* methods
            search_texts: List of text patterns to search for
            fuzzy: Whether to use fuzzy matching (case-insensitive, whitespace-normalized)
            
        Returns:
            List of matches with their positions and confidence
        """
        matches = []
        
        if not ocr_result.get('words'):
            return matches
            
        def normalize_text(text: str) -> str:
            """Normalize text for fuzzy matching."""
            if not fuzzy:
                return text
            return ' '.join(text.lower().split())
        
        # Search in individual words
        for word in ocr_result['words']:
            word_text = normalize_text(word['text'])
            
            for search_text in search_texts:
                search_normalized = normalize_text(search_text)
                
                if (not fuzzy and search_text in word['text']) or (fuzzy and search_normalized in word_text):
                    matches.append({
                        'text': word['text'],
                        'search_pattern': search_text,
                        'confidence': word['confidence'],
                        'bbox': word['bbox'],
                        'match_type': 'word',
                        'center': {
                            'x': word['bbox']['left'] + word['bbox']['width'] // 2,
                            'y': word['bbox']['top'] + word['bbox']['height'] // 2
                        }
                    })
        
        # Search in lines (for multi-word patterns)
        for line in ocr_result['lines']:
            line_text = normalize_text(line['text'])
            
            for search_text in search_texts:
                search_normalized = normalize_text(search_text)
                
                if (not fuzzy and search_text in line['text']) or (fuzzy and search_normalized in line_text):
                    # Calculate line bounding box from words
                    words = line['words']
                    if words:
                        left = min(w['bbox']['left'] for w in words)
                        top = min(w['bbox']['top'] for w in words)
                        right = max(w['bbox']['left'] + w['bbox']['width'] for w in words)
                        bottom = max(w['bbox']['top'] + w['bbox']['height'] for w in words)
                        
                        bbox = {
                            'left': left,
                            'top': top,
                            'width': right - left,
                            'height': bottom - top
                        }
                        
                        matches.append({
                            'text': line['text'],
                            'search_pattern': search_text,
                            'confidence': line['avg_confidence'],
                            'bbox': bbox,
                            'match_type': 'line',
                            'center': {
                                'x': left + (right - left) // 2,
                                'y': top + (bottom - top) // 2
                            }
                        })
        
        # Sort matches by confidence (highest first)
        matches.sort(key=lambda x: x['confidence'], reverse=True)
        
        log.info(f"🎯 Found {len(matches)} OCR matches for patterns: {search_texts}")
        
        return matches

# Global OCR service instance
ocr_service = OCRService()
