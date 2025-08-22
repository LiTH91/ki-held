"""
Template Matching Service for UI element detection using OpenCV.
This service provides template matching capabilities for detecting Facebook UI elements.
"""
import os
import base64
from io import BytesIO
from typing import List, Dict, Any, Optional, Tuple, NamedTuple
from pathlib import Path
import logging

import cv2
import numpy as np
from PIL import Image

from backend.config import settings

log = logging.getLogger(__name__)

class MatchResult(NamedTuple):
    """Result of a template matching operation."""
    template_name: str
    confidence: float
    location: Tuple[int, int]  # (x, y) top-left corner
    size: Tuple[int, int]     # (width, height)
    center: Tuple[int, int]   # (x, y) center point

class TemplateService:
    """Service for template matching using OpenCV."""
    
    def __init__(self, templates_dir: str = "fb-templates"):
        """
        Initialize template matching service.
        
        Args:
            templates_dir: Directory containing template images
        """
        self.templates_dir = Path(templates_dir)
        self.templates_cache: Dict[str, np.ndarray] = {}
        self.load_templates()
        
    def load_templates(self):
        """Load all template images from the templates directory."""
        if not self.templates_dir.exists():
            log.error(f"Templates directory not found: {self.templates_dir}")
            return
            
        template_files = list(self.templates_dir.glob("*.png")) + list(self.templates_dir.glob("*.jpg"))
        
        for template_file in template_files:
            try:
                # Load template image in grayscale for better matching
                template_img = cv2.imread(str(template_file), cv2.IMREAD_GRAYSCALE)
                if template_img is not None:
                    template_name = template_file.stem  # filename without extension
                    self.templates_cache[template_name] = template_img
                    log.info(f"✅ Loaded template: {template_name} ({template_img.shape})")
                else:
                    log.warning(f"⚠️ Failed to load template: {template_file}")
            except Exception as e:
                log.error(f"❌ Error loading template {template_file}: {e}")
        
        log.info(f"📚 Loaded {len(self.templates_cache)} templates from {self.templates_dir}")
    
    def match_template_in_base64(self, image_base64: str, template_name: str, 
                                threshold: float = 0.7, method: int = cv2.TM_CCOEFF_NORMED) -> List[MatchResult]:
        """
        Find template matches in a base64-encoded image.
        
        Args:
            image_base64: Base64-encoded image data
            template_name: Name of template to search for
            threshold: Matching confidence threshold (0.0 to 1.0)
            method: OpenCV template matching method
            
        Returns:
            List of MatchResult objects for found matches
        """
        try:
            # Decode base64 image
            image_data = base64.b64decode(image_base64)
            image_pil = Image.open(BytesIO(image_data))
            
            # Convert PIL to OpenCV format (grayscale)
            image_cv = cv2.cvtColor(np.array(image_pil), cv2.COLOR_RGB2GRAY)
            
            return self.match_template_in_image(image_cv, template_name, threshold, method)
            
        except Exception as e:
            log.error(f"❌ Failed to process base64 image for template matching: {e}")
            return []
    
    def match_template_in_image(self, image: np.ndarray, template_name: str,
                               threshold: float = 0.7, method: int = cv2.TM_CCOEFF_NORMED) -> List[MatchResult]:
        """
        Find template matches in an OpenCV image.
        
        Args:
            image: OpenCV image (grayscale)
            template_name: Name of template to search for
            threshold: Matching confidence threshold (0.0 to 1.0)
            method: OpenCV template matching method
            
        Returns:
            List of MatchResult objects for found matches
        """
        if template_name not in self.templates_cache:
            log.warning(f"⚠️ Template '{template_name}' not found in cache")
            return []
        
        template = self.templates_cache[template_name]
        
        try:
            # Perform template matching
            result = cv2.matchTemplate(image, template, method)
            
            # Find all matches above threshold
            locations = np.where(result >= threshold)
            matches = []
            
            template_h, template_w = template.shape
            
            # Group nearby matches to avoid duplicates
            for pt in zip(*locations[::-1]):  # Switch x,y coordinates
                x, y = pt
                confidence = result[y, x]
                
                # Check if this match is too close to existing matches
                is_duplicate = False
                for existing_match in matches:
                    dist = np.sqrt((x - existing_match.location[0])**2 + (y - existing_match.location[1])**2)
                    if dist < min(template_w, template_h) * 0.5:  # If closer than half template size
                        is_duplicate = True
                        break
                
                if not is_duplicate:
                    center_x = x + template_w // 2
                    center_y = y + template_h // 2
                    
                    match = MatchResult(
                        template_name=template_name,
                        confidence=float(confidence),
                        location=(x, y),
                        size=(template_w, template_h),
                        center=(center_x, center_y)
                    )
                    matches.append(match)
            
            # Sort matches by confidence (highest first)
            matches.sort(key=lambda m: m.confidence, reverse=True)
            
            log.info(f"🎯 Found {len(matches)} matches for template '{template_name}' (threshold: {threshold})")
            return matches
            
        except Exception as e:
            log.error(f"❌ Error in template matching for '{template_name}': {e}")
            return []
    
    def find_facebook_elements(self, image_base64: str, element_types: List[str] = None, 
                              threshold: float = 0.7) -> Dict[str, List[MatchResult]]:
        """
        Find multiple Facebook UI elements in an image.
        
        Args:
            image_base64: Base64-encoded screenshot
            element_types: List of template names to search for (None = all)
            threshold: Matching confidence threshold
            
        Returns:
            Dictionary mapping template names to lists of matches
        """
        if element_types is None:
            element_types = list(self.templates_cache.keys())
        
        results = {}
        
        for element_type in element_types:
            matches = self.match_template_in_base64(image_base64, element_type, threshold)
            if matches:
                results[element_type] = matches
                log.info(f"✅ Found {len(matches)} instances of '{element_type}'")
            else:
                log.debug(f"🔍 No matches found for '{element_type}'")
        
        return results
    
    def find_best_match(self, image_base64: str, template_names: List[str], 
                       threshold: float = 0.7) -> Optional[MatchResult]:
        """
        Find the best matching template from a list of candidates.
        
        Args:
            image_base64: Base64-encoded screenshot
            template_names: List of template names to try
            threshold: Minimum confidence threshold
            
        Returns:
            Best MatchResult or None if no match found
        """
        best_match = None
        best_confidence = threshold
        
        for template_name in template_names:
            matches = self.match_template_in_base64(image_base64, template_name, threshold)
            for match in matches:
                if match.confidence > best_confidence:
                    best_match = match
                    best_confidence = match.confidence
        
        if best_match:
            log.info(f"🏆 Best match: '{best_match.template_name}' with confidence {best_match.confidence:.3f}")
        else:
            log.warning(f"❌ No matches found above threshold {threshold} for templates: {template_names}")
        
        return best_match
    
    def get_template_info(self) -> Dict[str, Dict[str, Any]]:
        """
        Get information about all loaded templates.
        
        Returns:
            Dictionary with template information
        """
        info = {}
        for name, template in self.templates_cache.items():
            info[name] = {
                "shape": template.shape,
                "size": template.shape[1] * template.shape[0],  # width * height
                "path": str(self.templates_dir / f"{name}.png")
            }
        return info


# Create global instance
template_service = TemplateService()

# Facebook-specific helper functions
def find_relevanteste_button(image_base64: str) -> Optional[MatchResult]:
    """Find the 'Relevanteste' (Most Relevant) button."""
    return template_service.find_best_match(
        image_base64, 
        ["relevanteste"], 
        threshold=0.6
    )

def find_comment_buttons(image_base64: str) -> Dict[str, List[MatchResult]]:
    """Find various comment-related buttons."""
    comment_templates = [
        "alle-kommentare",
        "alle-xx-kommentare-ansehen", 
        "Antwort-ansehen"
    ]
    return template_service.find_facebook_elements(
        image_base64,
        comment_templates,
        threshold=0.6
    )

def find_reaction_elements(image_base64: str) -> Dict[str, List[MatchResult]]:
    """Find reaction buttons and combinations."""
    reaction_templates = [
        "reaction-like", "reaction-happy", "reaction-mad", "reaction-oh", "reaction-sad",
        "reactions-happylike", "reactions-happyoh", "reactions-likehappy", 
        "reactions-likehappymad", "reactions-likemadhappy", "reactions-likeoh",
        "reactions-madlikehappy", "reactions-ohlike"
    ]
    return template_service.find_facebook_elements(
        image_base64,
        reaction_templates,
        threshold=0.7
    )
