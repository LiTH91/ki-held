print("[DEBUG mcp_service] Starting import of mcp_service.py")
import asyncio
import os
from collections import deque
from datetime import datetime, timedelta, timezone
import random
import re
from urllib.parse import urlparse
from typing import Dict, Any
print("[DEBUG mcp_service] About to import human_wait from utils")
from .utils import human_wait
from .logger import setup_logging
log = setup_logging()
from .utils.human_mouse import move_mouse_to, click_mouse, scroll_mouse
from .ocr_service import ocr_service
from .template_service import template_service, find_relevanteste_button, find_comment_buttons
from pathlib import Path

# Instantiate MCP client for default navigation/content mode
from .mcp_client import MCPClient

mcp_client = MCPClient()

# PATCH: Chrome MCP client for scroll position tracking and browser APIs
from .chrome_mcp_client import get_chrome_mcp_client

# Fatal control-flow exception used to abort extraction when navigation leaves the post/modal
class AbortExtractionError(Exception):
    pass

# Global HARD STOP flag to prevent any further desktop interactions after fatal abort
hard_stop_active: bool = False

# PATCH: string normalization utility for FB text matching
def _normalize(s: str) -> str:
    """
    Normalize Facebook UI text:
    - remove zero-width chars
    - replace NBSP with normal space
    - lowercase, strip
    - collapse multiple spaces
    """
    if s is None:
        return ""
    s = s.replace("\xa0", " ")
    s = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", s)
    s = s.lower().strip()
    s = " ".join(s.split())
    return s

# Movement diagnostics
last_move_point: tuple[int, int] | None = None
last_move_at: datetime | None = None
# Remember the last meaningful on-page click to reliably refocus before scrolling
last_focus_point: list[int] | None = None
movement_suppressed_until: datetime | None = None
# Store extracted comments from screenshot analysis
extracted_comment_data: list[dict] = []

# Global safety tracking for profile navigation prevention
profile_navigation_failures = 0
MAX_PROFILE_NAVIGATION_FAILURES = 2  # Circuit breaker threshold
safety_mode_until: datetime | None = None

# TODO: Replace with actual Windows-MCP API integration

MAX_ACTIONS_PER_MIN = int(os.getenv('MCP_MAX_ACTIONS_PER_MIN', 20))
action_timestamps = deque(maxlen=MAX_ACTIONS_PER_MIN)

def _looks_like_fb_post_view(state_text: str) -> bool:
    if not state_text:
        return False
    text = state_text.lower()
    markers = [
        'gefällt mir', 'kommentieren', 'teilen',
        'relevanteste', 'most relevant', 'neueste', 'top-kommentare',
        'alle kommentare', 'all comments', 'kommentare'
    ]
    return any(m in text for m in markers)

def _urls_refer_to_same_fb_post(target_url: str, current_url: str) -> bool:
    if not target_url or not current_url:
        return False
    t = target_url.strip().lower()
    c = current_url.strip().lower()
    # Strip scheme and www
    def _norm(u: str) -> str:
        try:
            p = urlparse(u)
            host = (p.netloc or '').lstrip('www.')
            path = p.path or ''
            query = p.query or ''
            return host + path + (('?' + query) if query else '')
        except Exception:
            return u
    tn = _norm(t)
    cn = _norm(c)
    log.debug(f"[URL_COMPARE] raw target='{t}', raw current='{c}'")
    log.debug(f"[URL_COMPARE] norm target='{tn}', norm current='{cn}'")
    if 'facebook.com' not in cn:
        log.debug("[URL_COMPARE] current not a facebook URL; returning False")
        return False
    # Direct equality or prefix match
    if tn == cn or cn.startswith(tn) or tn.startswith(cn):
        log.debug("[URL_COMPARE] equal/prefix match -> True")
        return True
    # Match by /posts/<id> segment
    m_t = re.search(r"/posts/([^/?#]+)", tn)
    m_c = re.search(r"/posts/([^/?#]+)", cn)
    if m_t and m_c and m_t.group(1) == m_c.group(1):
        log.debug("[URL_COMPARE] matched /posts/<id> -> True")
        return True
    # Match by pfbid token
    pf_t = re.search(r"pfbid[\w]+", tn)
    pf_c = re.search(r"pfbid[\w]+", cn)
    if pf_t and pf_c and pf_t.group(0) == pf_c.group(0):
        log.debug("[URL_COMPARE] matched pfbid -> True")
        return True
    # Fallback: current contains target's pfbid or vice versa
    if pf_t and pf_t.group(0) in cn:
        log.debug("[URL_COMPARE] current contains target pfbid -> True")
        return True
    if pf_c and pf_c.group(0) in tn:
        log.debug("[URL_COMPARE] target contains current pfbid -> True")
        return True
    log.debug("[URL_COMPARE] no heuristic matched -> False")
    return False

async def _rate_limited(action_name: str):
    # Determine rate limit dynamically from environment
    limit = int(os.getenv('MCP_MAX_ACTIONS_PER_MIN', 20))
    global action_timestamps
    # Recreate timestamps deque if the limit has changed
    if action_timestamps.maxlen != limit:
        action_timestamps = deque(action_timestamps, maxlen=limit)
        log.info(f"[RATE_LIMIT] Updated deque maxlen to {limit}")
    now = datetime.now()
    # HARD STOP: prevent any further desktop interactions after fatal abort
    if action_name in ("click", "scroll", "screenshot_fullscreen", "screenshot", "move", "type", "shortcut"):
        try:
            from backend.mcp_service import hard_stop_active  # local import to avoid cycles
        except Exception:
            hard_stop_active_local = False
        else:
            hard_stop_active_local = hard_stop_active
        if hard_stop_active_local:
            log.error(f"🛑 HARD-STOP ACTIVE: Blocking action '{action_name}'")
            raise AbortExtractionError("Hard stop active - blocking interaction")
    # Diagnostic logs to validate rate limit configuration
    log.info(f"[RATE_LIMIT DIAG] Module-level MAX_ACTIONS_PER_MIN={MAX_ACTIONS_PER_MIN}")
    current_env_max = int(os.getenv('MCP_MAX_ACTIONS_PER_MIN', MAX_ACTIONS_PER_MIN))
    log.info(f"[RATE_LIMIT DIAG] Dynamic env MCP_MAX_ACTIONS_PER_MIN={current_env_max}")
    log.info(f"[RATE_LIMIT DIAG] action_timestamps length={len(action_timestamps)}, deque maxlen={action_timestamps.maxlen}")
    log.info(f"[RATE_LIMIT] Current time: {now}")
    log.info(f"[RATE_LIMIT] Action timestamps ({len(action_timestamps)}/{limit}): {list(action_timestamps)}")
    if len(action_timestamps) >= limit:
        oldest_action = action_timestamps[0]
        time_since_oldest = (now - oldest_action).total_seconds()
        log.info(f"[RATE_LIMIT] Time since oldest action: {time_since_oldest:.2f}s (limit={limit})")
        if time_since_oldest < 60:
            sleep_time = 60 - time_since_oldest
            log.info(f"[RATE_LIMIT] Sleeping {sleep_time:.2f}s to respect action cap.")
            await asyncio.sleep(sleep_time)
    # Record this action timestamp
    action_timestamps.append(now)
    log.info(f"[RATE_LIMIT] Recorded action. New timestamps ({len(action_timestamps)}/{limit}): {list(action_timestamps)}")
    await human_wait(action_name)

def normalize_facebook_text(text: str) -> str:
    """Normalisiert Facebook-Text für robuste Suche.
    # PATCH: Implement robust normalization (zero-width removal, NBSP handling, lowercase, collapse spaces)
    """
    if not text:
        return ""
    try:
        s = text.replace("\xa0", " ")
        # PATCH: remove zero-width characters via regex
        s = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", s)
        s = s.lower().strip()
        s = " ".join(s.split())
        return s
    except Exception:
        return (text or "").lower().strip()

async def find_button_with_ocr(button_texts: list[str], region: list[int] = None) -> bool:
    """
    Verwendet Screenshot+OCR um Buttons zu finden.
    
    Args:
        button_texts: Liste der zu suchenden Button-Texte
        region: Optional [x, y, width, height] für spezifische Bildschirmregion
        
    Returns:
        True wenn Button gefunden und geklickt wurde, False sonst
    """
    try:
        log.info(f"🔍 Searching for buttons with OCR: {button_texts}")
        
        # Screenshot machen
        screenshot_result = await mcp_client.send_command("Screenshot-Tool", {"region": region} if region else {})
        
        if not screenshot_result or not hasattr(screenshot_result, 'data'):
            log.error("❌ Failed to capture screenshot")
            return False
            
        # Base64 Screenshot-Daten extrahieren - Fix: Use content instead of data
        if hasattr(screenshot_result, 'content') and screenshot_result.content and len(screenshot_result.content) > 0:
            screenshot_data = screenshot_result.content[0].text
        else:
            screenshot_data = screenshot_result.data if hasattr(screenshot_result, 'data') else None
            
        if not screenshot_data:
            log.error("❌ Screenshot data is None/empty for OCR")
            return False
            
        if "Base64 data: " in screenshot_data:
            base64_data = screenshot_data.split("Base64 data: ")[1]
        else:
            log.error("❌ Could not extract base64 data from screenshot")
            return False
        
        # OCR durchführen
        ocr_result = ocr_service.extract_text_from_base64(base64_data)
        detected_text = ocr_result.get("text", "") if ocr_result else ""
        log.info(f"📄 OCR detected text: '{detected_text[:200]}...'")
        log.debug(f"[OCR] words_count={len(ocr_result.get('words', [])) if ocr_result else 'NA'} lines_count={len(ocr_result.get('lines', [])) if ocr_result else 'NA'} confid={ocr_result.get('confidence') if ocr_result else 'NA'}")
        
        if not detected_text:
            log.warning("⚠️ No text detected by OCR")
            return False
        
        # Text normalisieren und nach Buttons suchen
        normalized_text = normalize_facebook_text(detected_text)
        
        for button_text in button_texts:
            normalized_button = normalize_facebook_text(button_text)
            if normalized_button and normalized_button in normalized_text:
                log.info(f"🎯 Found button text '{button_text}' in OCR results!")
                
                # Hier könnten wir mit OCR auch die Position bestimmen
                # Für jetzt verwenden wir einen Fallback zur State-Tool Suche
                try:
                    await asyncio.sleep(random.uniform(0.6, 1.2))
                    state_result = await mcp_client.send_command("State-Tool", {"use_vision": False})
                    state_data = state_result.data if hasattr(state_result, 'data') and state_result.data else ''
                    
                    # Versuche Koordinaten im State-Tool zu finden
                    for line in state_data.split('\n'):
                        normalized_line = normalize_facebook_text(line.strip())
                        if normalized_button in normalized_line:
                            match = re.search(r'\((\d+),\s*(\d+)\)', line)
                            if match:
                                x, y = int(match.group(1)), int(match.group(2))
                                # Do not clamp the 'Alle Kommentare' filter selection
                                if normalize_facebook_text(button_text) not in (normalize_facebook_text("Alle Kommentare"), normalize_facebook_text("All comments")):
                                    x, y = clamp_to_modal(x, y)
                                    log.info(f"✅ Found coordinates for '{button_text}' at ({x}, {y}) [modal-clamped]")
                                else:
                                    log.info(f"✅ Found coordinates for '{button_text}' at ({x}, {y}) [no clamp]")
                                await mcp_client.send_command("Click-Tool", {"loc": [x, y]})
                                
                                # Record last focus point
                                global last_focus_point
                                last_focus_point = [x, y]
                                return True
                except Exception as e:
                    log.error(f"❌ Error in fallback coordinate search: {e}")
                
                log.warning(f"⚠️ Found text '{button_text}' in OCR but could not determine coordinates")
                return False
        
        log.warning(f"❌ None of the button texts found in OCR: {button_texts}")
        return False
        
    except Exception as e:
        log.error(f"❌ Error in OCR button search: {e}")
        return False

async def find_button_with_template_matching(button_types: list[str]) -> bool:
    """
    Verwendet Template Matching um Buttons zu finden.
    
    Args:
        button_types: Liste der zu suchenden Button-Template-Namen
        
    Returns:
        True wenn Button gefunden und geklickt wurde, False sonst
    """
    try:
        log.info(f"🔍 Searching for buttons with Template Matching: {button_types}")
        
        # Screenshot machen
        screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
        
        if not screenshot_result or not hasattr(screenshot_result, 'data'):
            log.error("❌ Failed to capture screenshot for template matching")
            return False
            
        # Base64 Screenshot-Daten extrahieren - Fix: Use content instead of data
        if hasattr(screenshot_result, 'content') and screenshot_result.content and len(screenshot_result.content) > 0:
            screenshot_data = screenshot_result.content[0].text
        else:
            screenshot_data = screenshot_result.data if hasattr(screenshot_result, 'data') else None
            
        if not screenshot_data:
            log.error("❌ Screenshot data is None/empty for template matching")
            return False
            
        if "Base64 data: " in screenshot_data:
            base64_data = screenshot_data.split("Base64 data: ")[1]
        else:
            log.error("❌ Could not extract base64 data from screenshot for template matching")
            return False
        
        # Template Matching für jeden Button-Typ
        log.debug(f"[TM] evaluating templates for: {button_types}")
        for button_type in button_types:
            template_name = _map_button_to_template(button_type)
            if not template_name:
                continue
                
            # PATCH: Conservative threshold to avoid false-positives that click on profiles
            detection_threshold = 0.65 if template_name == "Antwort-ansehen" else 0.6  # Higher to reduce false-positives
            matches = template_service.match_template_in_base64(
                base64_data, 
                template_name, 
                threshold=detection_threshold
            )
            
            if matches:
                # Verwende das beste Match
                best_match = matches[0]  # Already sorted by confidence
                # Convert numpy coordinates to regular Python integers
                x, y = int(best_match.center[0]), int(best_match.center[1])
                w, h = int(best_match.size[0]), int(best_match.size[1])
                # Validation logs for raw center before any offset
                raw_x, raw_y = x, y
                log.info(f"[TM] raw_center=({raw_x}, {raw_y}) size=({w}x{h}) template='{template_name}' conf={best_match.confidence:.3f}")
                # Adjust click towards label area (left of icon) for comment filters
                if template_name in ("alle-kommentare", "alle-xx-kommentare-ansehen"):
                    left_offset = int(min(max(w * 0.35, 24), 96))
                    log.info(f"[TM] applying_left_offset={left_offset}px for '{template_name}' raw=({raw_x}, {raw_y}) -> target=({raw_x - left_offset}, {y})")
                    x = x - left_offset
                    log.debug(f"[TM] applied_left_offset={left_offset} template_w={w} template_h={h}")
                
                log.info(f"🎯 Found template match for '{button_type}' -> '{template_name}' at ({x}, {y}) with confidence {best_match.confidence:.3f}")
                log.debug(f"[TM] match_count={len(matches)} first_conf={best_match.confidence:.3f}")
                
                # Move then click with small hesitation to improve accuracy
                log.info(f"[TM] Move-Tool target=({x}, {y}); Click-Tool follows")
                # Do NOT clamp for 'alle-kommentare' (dropdown selection). Clamp only for expansion clicks later
                if template_name not in ("alle-kommentare",):
                    # Derive image size from current screenshot base64 for percentage clamp
                    try:
                        import base64, cv2
                        import numpy as np
                        img_bytes = base64.b64decode(base64_data)
                        img_array = np.frombuffer(img_bytes, dtype=np.uint8)
                        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                        ih, iw = (img.shape[0], img.shape[1]) if img is not None else (None, None)
                    except Exception:
                        iw, ih = None, None
                    x, y = clamp_to_modal(x, y, image_width=iw, image_height=ih)
                await mcp_client.send_command("Move-Tool", {"to_loc": [x, y]})
                await asyncio.sleep(random.uniform(0.12, 0.25))
                if template_name not in ("alle-kommentare",):
                    log.info(f"🖱️ Clicking template '{template_name}' at adjusted ({x}, {y}) [modal-clamped]")
                else:
                    log.info(f"🖱️ Clicking template '{template_name}' at ({x}, {y}) [no clamp]")
                await mcp_client.send_command("Click-Tool", {"loc": [x, y]})
                # Suppress random movement briefly after critical click
                if template_name in ("alle-kommentare", "alle-xx-kommentare-ansehen"):
                    global movement_suppressed_until
                    movement_suppressed_until = datetime.now(timezone.utc) + timedelta(seconds=3.0)
                    log.debug(f"[MOVE_SUPPRESS] Enabled until {movement_suppressed_until}")
                
                # Record last focus point
                global last_focus_point
                last_focus_point = [x, y]
                return True
        
        log.warning(f"❌ No template matches found for buttons: {button_types}")
        return False
        
    except Exception as e:
        log.error(f"❌ Error in template matching button search: {e}")
        return False

def _map_button_to_template(button_text: str) -> str:
    """
    Mappt Button-Text auf entsprechenden Template-Namen.
    
    Args:
        button_text: Text des zu suchenden Buttons
        
    Returns:
        Template-Name oder None wenn kein Mapping existiert
    """
    # Mapping von Button-Texten zu Template-Namen
    mapping = {
        # Sort button (main page)
        "Relevanteste": "relevanteste",
        "Most relevant": "relevanteste", 
        "Most Relevant": "relevanteste",
        "Top-Kommentare": "relevanteste",
        "Top comments": "relevanteste",
        
        # Dropdown menu option (after clicking Relevanteste)
        "Alle Antworten": "alle-kommentare",
        "All replies": "alle-kommentare",
        "All comments": "alle-kommentare",
        "Alle Kommentare": "alle-kommentare",
        
        # Individual comment reply buttons
        "Antworten": "Antwort-ansehen",
        "Antwort anzeigen": "Antwort-ansehen", 
        "Kommentar ansehen": "Antwort-ansehen",
        "replies": "Antwort-ansehen",
        "show replies": "Antwort-ansehen",
        
        # Load more comments buttons (with numbers)
        "weitere Kommentare": "alle-xx-kommentare-ansehen",
        "Weitere Antworten": "alle-xx-kommentare-ansehen",
        "more comments": "alle-xx-kommentare-ansehen",
        "view more comments": "alle-xx-kommentare-ansehen"
    }
    
    return mapping.get(button_text)

async def find_and_click_button_robust(button_texts: list[str], max_retries: int = 2) -> bool:
    """
    Optimierte robuste Suche mit schnellem Fallback zu erweiterten Methoden.
    
    Strategie:
    - Attempt 1: State-Tool (schnell)
    - Attempt 2: State-Tool + OCR + Template (umfassend)
    """
    # PATCH: State-Tool Fallback - will_try_ocr_now wird immer True bei leerem State-Tool
    will_try_ocr_now = False
    
    for attempt in range(max_retries):
        try:
            log.info(f"🔍 Attempt {attempt + 1}/{max_retries}: Searching for buttons: {button_texts}")
            log.debug(f"[ROBUST] attempt_index={attempt} max_retries={max_retries}")
            
            # === METHOD 1: State-Tool Text Search ===
            # Small reliability wait to allow dynamic content to settle
            await asyncio.sleep(random.uniform(0.6, 1.2))
            
            # PATCH: State-Tool Fallback - Exception handling für State-Tool
            try:
                state_result = await mcp_client.send_command("State-Tool", {"use_vision": False})
                state_data = state_result.data if hasattr(state_result, 'data') and state_result.data else ''
            except Exception as e:
                log.error(f"🛑 State-Tool exception: {e}")
                state_data = ''
            
            log.info(f"📄 State data length: {len(state_data)}")
            
            # PATCH: State-Tool Fallback - Bei leerem State-Tool sofort OCR+Template
            if len(state_data) == 0:
                log.error("🛑 CRITICAL: State-Tool returned empty data!")
                log.error("[OCR_FALLBACK] Activating OCR+Template fallback immediately")
                will_try_ocr_now = True
                
                log.info("🔄 Empty data detected - trying OCR immediately...")
                # PATCH: Fallback auf ursprüngliche Funktionen wenn Enhanced Funktionen Probleme haben
                try:
                    ocr_success = await find_button_with_ocr_enhanced(button_texts)
                    if ocr_success:
                        return True
                except Exception as ocr_e:
                    log.warning(f"[OCR_FALLBACK] Enhanced OCR failed: {ocr_e}, trying original OCR...")
                    ocr_success = await find_button_with_ocr(button_texts)
                    if ocr_success:
                        return True
                
                log.info("🔄 OCR failed - trying Template Matching...")
                try:
                    template_success = await find_button_with_template_matching_enhanced(button_texts)
                    if template_success:
                        return True
                except Exception as template_e:
                    log.warning(f"[OCR_FALLBACK] Enhanced Template failed: {template_e}, trying original Template...")
                    template_success = await find_button_with_template_matching(button_texts)
                    if template_success:
                        return True
                    
                log.warning(f"❌ Attempt {attempt + 1}: State-Tool empty + advanced methods failed")
                continue
            
            log.debug(f"📄 State data preview: '{state_data[:200]}...'")
            
            # Normalisiere den gesamten State-Text
            normalized_state = normalize_facebook_text(state_data)
            
            # PATCH: Bounding-Box Filter - Screenshot-Dimensionen für Modal-Bounds abrufen
            iw = ih = None
            try:
                screenshot_result2 = await mcp_client.send_command("Screenshot-Tool", {})
                if screenshot_result2 and hasattr(screenshot_result2, 'content') and screenshot_result2.content:
                    sdata = screenshot_result2.content[0].text
                    if "Base64 data: " in sdata:
                        import base64, cv2
                        import numpy as np
                        b64 = sdata.split("Base64 data: ")[1]
                        img_bytes = base64.b64decode(b64)
                        img_arr = np.frombuffer(img_bytes, dtype=np.uint8)
                        img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                        ih, iw = (img.shape[0], img.shape[1]) if img is not None else (None, None)
            except Exception:
                pass
            
            import re
            for line in state_data.split('\n'):
                normalized_line = line.strip()
                line_normalized = normalize_facebook_text(normalized_line)
                
                for candidate in button_texts:
                    candidate_normalized = normalize_facebook_text(candidate)
                    
                    # Fuzzy match: Kandidat ist im normalisierten Text enthalten
                    if candidate_normalized and candidate_normalized in line_normalized:
                        log.info(f"🎯 Found fuzzy match for '{candidate}' in line: {normalized_line[:100]}...")
                        
                        # Extrahiere Koordinaten
                        match = re.search(r'\((\d+),\s*(\d+)\)', normalized_line)
                        if match:
                            x, y = int(match.group(1)), int(match.group(2))
                            
                            # PATCH: Bounding-Box Filter - Prüfe Modal-Bounds
                            if not is_within_modal_bounds(x, y, iw, ih):
                                log.warning(f"[FILTER_LOG] Coordinates ({x}, {y}) outside modal bounds - skipping")
                                continue
                            
                            # Do not clamp for 'Alle Kommentare' dropdown selection
                            if normalize_facebook_text(candidate) not in (normalize_facebook_text("Alle Kommentare"), normalize_facebook_text("All comments")):
                                x, y = clamp_to_modal(x, y, image_width=iw, image_height=ih)
                                log.info(f"✅ Found '{candidate}' at ({x}, {y}) [modal-clamped]")
                            else:
                                log.info(f"✅ Found '{candidate}' at ({x}, {y}) [no clamp]")
                            
                            # PATCH: Re-enable Pre-Click Validation - prevents false-positive clicks on profiles
                            if not await pre_click_validate(x, y, candidate):
                                log.warning(f"[SKIP_LOG] Pre-click validation failed for '{candidate}' at ({x}, {y}) - preventing profile click")
                                continue
                            
                            log.info(f"✅ Clicking '{candidate}' at ({x}, {y})")
                            await mcp_client.send_command("Click-Tool", {"loc": [x, y]})
                            
                            # Record last focus point
                            global last_focus_point
                            last_focus_point = [x, y]
                            return True
                        else:
                            log.warning(f"⚠️ Found text '{candidate}' but no coordinates in line: {normalized_line}")
            
            log.warning(f"❌ Attempt {attempt + 1}: State-Tool could not find buttons: {button_texts}")
            
            # === EARLY FALLBACK: Try advanced methods after first failure ===
            if attempt >= 1:  # Nach dem 2. Versuch (attempt 0, 1)
                log.info("[OCR_FALLBACK] Quick fallback to OCR-based button search...")
                try:
                    ocr_success = await find_button_with_ocr_enhanced(button_texts)
                    if ocr_success:
                        return True
                except Exception as ocr_e:
                    log.warning(f"[OCR_FALLBACK] Enhanced OCR failed: {ocr_e}, trying original OCR...")
                    ocr_success = await find_button_with_ocr(button_texts)
                    if ocr_success:
                        return True
                
                log.info("[OCR_FALLBACK] Quick fallback to Template Matching...")
                try:
                    template_success = await find_button_with_template_matching_enhanced(button_texts)
                    if template_success:
                        return True
                except Exception as template_e:
                    log.warning(f"[OCR_FALLBACK] Enhanced Template failed: {template_e}, trying original Template...")
                    template_success = await find_button_with_template_matching(button_texts)
                    if template_success:
                        return True
            
            # Reduced wait time for faster execution
            if attempt < max_retries - 1:
                wait_time = 1.5 if attempt == 0 else 2.0  # Shorter waits
                log.info(f"⏳ Waiting {wait_time}s before next attempt...")
                await asyncio.sleep(wait_time)
        
        except Exception as e:
            log.error(f"❌ Error in attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(1.0)  # Reduced error wait time

    return False

# PATCH: Erweiterte OCR-Funktion mit Bounding-Box Filter und Pre-Click Validation
async def find_button_with_ocr_enhanced(button_texts: list[str], region: list[int] = None) -> bool:
    """
    Erweiterte OCR-basierte Button-Suche mit Modal-Bounds und Pre-Click Validation.
    """
    try:
        log.info(f"🔍 [OCR_ENHANCED] Searching for buttons: {button_texts}")
        
        # Screenshot machen
        screenshot_result = await mcp_client.send_command("Screenshot-Tool", {"region": region} if region else {})
        
        if not screenshot_result or not hasattr(screenshot_result, 'content') or not screenshot_result.content:
            log.error("❌ [OCR_ENHANCED] Failed to capture screenshot")
            return False
            
        screenshot_data = screenshot_result.content[0].text
        if "Base64 data: " not in screenshot_data:
            log.error("❌ [OCR_ENHANCED] Could not extract base64 data from screenshot")
            return False
            
        base64_data = screenshot_data.split("Base64 data: ")[1]
        
        # PATCH: Screenshot-Dimensionen für Bounding-Box Filter
        iw = ih = None
        try:
            import base64, cv2, numpy as np
            img_bytes = base64.b64decode(base64_data)
            img_arr = np.frombuffer(img_bytes, dtype=np.uint8)
            img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
            ih, iw = (img.shape[0], img.shape[1]) if img is not None else (None, None)
        except Exception:
            pass
        
        # OCR durchführen
        ocr_result = ocr_service.extract_text_from_base64(base64_data)
        if not ocr_result or not ocr_result.get("words"):
            log.warning("⚠️ [OCR_ENHANCED] No text detected by OCR")
            return False
        
        # PATCH: Erweiterte Wort-basierte Suche mit Bounding-Box Filter
        words = ocr_result.get("words", [])
        if not words:
            log.warning("⚠️ [OCR_ENHANCED] No words found in OCR result")
            return False
            
        for i, word_info in enumerate(words):
            try:
                word_text = word_info.get("text", "").strip()
                if not word_text:
                    continue
                    
                word_normalized = normalize_facebook_text(word_text)
                
                for button_text in button_texts:
                    button_normalized = normalize_facebook_text(button_text)
                    
                    if button_normalized and button_normalized in word_normalized:
                        bbox = word_info.get("bbox", [])
                        if len(bbox) >= 4:
                            x = int((bbox[0] + bbox[2]) / 2)  # Mitte der Bounding Box
                            y = int((bbox[1] + bbox[3]) / 2)
                        else:
                            log.warning(f"[OCR_ENHANCED] Invalid bbox for word '{word_text}': {bbox}")
                            continue
                        
                        # PATCH: Bounding-Box Filter
                        if not is_within_modal_bounds(x, y, iw, ih):
                            log.warning(f"[FILTER_LOG] OCR match '{button_text}' at ({x}, {y}) outside modal bounds - skipping")
                            continue
                        
                        # PATCH: Re-enable Pre-Click Validation - prevents false-positive clicks
                        if not await pre_click_validate(x, y, button_text):
                            log.warning(f"[SKIP_LOG] OCR pre-click validation failed for '{button_text}' at ({x}, {y}) - preventing false click")
                            continue
                        
                        log.info(f"✅ [OCR_ENHANCED] Found '{button_text}' at ({x}, {y}). Clicking it.")
                        await mcp_client.send_command("Click-Tool", {"loc": [x, y]})
                        
                        global last_focus_point
                        last_focus_point = [x, y]
                        return True
                        
            except Exception as word_e:
                log.warning(f"[OCR_ENHANCED] Error processing word {i}: {word_e}")
                continue
        
        return False
        
    except Exception as e:
        log.error(f"❌ [OCR_ENHANCED] Exception: {e}")
        return False

# PATCH: Erweiterte Template-Matching-Funktion mit Bounding-Box Filter und Pre-Click Validation
async def find_button_with_template_matching_enhanced(button_types: list[str]) -> bool:
    """
    Erweiterte Template-Matching-basierte Button-Suche mit Modal-Bounds und Pre-Click Validation.
    """
    try:
        log.info(f"🔍 [TEMPLATE_ENHANCED] Searching for buttons: {button_types}")
        
        # Screenshot machen
        screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
        
        if not screenshot_result or not hasattr(screenshot_result, 'content') or not screenshot_result.content:
            log.error("❌ [TEMPLATE_ENHANCED] Failed to capture screenshot")
            return False
            
        screenshot_data = screenshot_result.content[0].text
        if "Base64 data: " not in screenshot_data:
            log.error("❌ [TEMPLATE_ENHANCED] Could not extract base64 data from screenshot")
            return False
            
        base64_data = screenshot_data.split("Base64 data: ")[1]
        
        # PATCH: Screenshot-Dimensionen für Bounding-Box Filter
        iw = ih = None
        try:
            import base64, cv2, numpy as np
            img_bytes = base64.b64decode(base64_data)
            img_arr = np.frombuffer(img_bytes, dtype=np.uint8)
            img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
            ih, iw = (img.shape[0], img.shape[1]) if img is not None else (None, None)
        except Exception:
            pass
        
        # Template Matching für jeden Button-Typ
        for button_type in button_types:
            template_name = _map_button_to_template(button_type)
            if not template_name:
                continue
                
            # PATCH: Conservative threshold to avoid false-positives that click on profiles
            detection_threshold = 0.65 if template_name == "Antwort-ansehen" else 0.6  # Higher to reduce false-positives
            matches = template_service.match_template_in_base64(
                base64_data, 
                template_name, 
                threshold=detection_threshold
            )
            
            if matches:
                # Verwende das beste Match
                best_match = max(matches, key=lambda m: m.confidence)
                # PATCH: Convert numpy types to native Python int to avoid serialization errors
                x, y = int(best_match.center[0]), int(best_match.center[1])
                confidence = best_match.confidence
                
                # PATCH: Bounding-Box Filter
                if not is_within_modal_bounds(x, y, iw, ih):
                    log.warning(f"[FILTER_LOG] Template match '{button_type}' at ({x}, {y}) outside modal bounds - skipping")
                    continue
                
                # PATCH: Re-enable Pre-Click Validation - prevents false-positive clicks on profiles
                if not await pre_click_validate(x, y, button_type):
                    log.warning(f"[SKIP_LOG] Template pre-click validation failed for '{button_type}' at ({x}, {y}) - preventing profile click")
                    continue
                
                log.info(f"✅ [TEMPLATE_ENHANCED] Found '{button_type}' at ({x}, {y}) [confidence: {confidence:.2f}]. Clicking it.")
                await mcp_client.send_command("Click-Tool", {"loc": [x, y]})
                
                global last_focus_point
                last_focus_point = [x, y]
                return True
        
        return False
        
    except Exception as e:
        log.error(f"❌ [TEMPLATE_ENHANCED] Exception: {e}")
        return False

async def execute_facebook_workflow(original_url: str = ""):
    """
    Vollständiger Facebook-Workflow mit Anti-Detection-Maßnahmen:
    1. Chrome öffnen und URL eingeben
    2. "Relevanteste" finden (mit Scroll-Fallback)
    3. "Alle Antworten" im Dropdown wählen
    4. Alle "alle xx Kommentare-ansehen" Buttons klicken
    5. Alle "Kommentar ansehen" Buttons klicken
    """
    log.info("🚀 Starting comprehensive Facebook workflow with anti-detection...")
    # Reset HARD STOP at the start of each workflow
    try:
        global hard_stop_active
        hard_stop_active = False
        log.debug("[HARD_STOP] Reset to False at workflow start")
    except Exception:
        pass
    
    try:
        # Schritt 1: Chrome focus sicherstellen mit menschlicher Verzögerung
        log.info("1️⃣ Ensuring Chrome focus...")
        try:
            await mcp_client.send_command("Switch-Tool", {"name": "chrome"})
            await human_wait("Chrome focus")
            await add_human_mouse_movement()
            await mcp_client.send_command("Safe-Center-Move-Tool", {})
            await human_wait("cursor positioning")
        except Exception as e:
            log.warning(f"⚠️ Chrome focus failed: {e}")
        
        # Menschliches Lese-Verhalten simulieren
        await simulate_page_reading()
        
        # Schritt 2: "Relevanteste" finden (mit Scroll-Fallback)
        log.info("2️⃣ Searching for 'Relevanteste' button...")
        relevanteste_found = await find_relevanteste_with_scroll()
        
        if not relevanteste_found:
            log.error("❌ Could not find 'Relevanteste' button after scrolling")
            log.error("🛑 CRITICAL: Facebook workflow failed at step 2. This indicates:")
            log.error("   • Page not fully loaded")
            log.error("   • Not a Facebook post page")  
            log.error("   • Facebook UI has changed")
            log.error("   • State-Tool returning empty data (check Windows-MCP connection)")
            return False
        
        log.info("✅ 'Relevanteste' clicked successfully!")
        await human_wait("dropdown opening")  # Warten bis Dropdown öffnet
        
        # Schritt 3: "Alle Antworten" im Dropdown wählen
        log.info("3️⃣ Searching for 'Alle Antworten' in dropdown...")
        alle_antworten_texts = [
            "Alle Antworten", "All replies", "All comments", 
            "Alle Kommentare", "All Comments"
        ]
        
        alle_antworten_found = await find_and_click_button_robust(alle_antworten_texts, max_retries=2)
        
        if not alle_antworten_found:
            log.error("❌ Could not find 'Alle Antworten' in dropdown")
            return False
            
        log.info("✅ 'Alle Antworten' clicked successfully!")
        await human_wait("page loading")  # Warten bis Seite lädt
        # Give the UI a moment to settle before confirming selection
        await asyncio.sleep(random.uniform(2.0, 4.0))

        # Confirmation step: ensure the filter shows 'Alle Kommentare' and not 'Relevanteste'
        confirmed = await confirm_alle_kommentare_selected()
        if not confirmed:
            log.error("🛑 Confirmation failed: 'Alle Kommentare' not active after click. Stopping workflow.")
            return False
        
        # Kurzes Lese-Verhalten nach Laden
        await simulate_brief_reading()
        
        # Schritt 4: Neue Strategie - Screenshot + OCR basiertes Kommentar-Scraping mit progressivem Scrollen
        # === Schritt 4: SET ALLE KOMMENTARE ANCHOR ===
        log.info("4️⃣ Setting 'Alle Kommentare' anchor point...")
        anchor_screenshot = await mcp_client.send_command("Screenshot-Tool", {})
        anchor_data = None
        if anchor_screenshot and hasattr(anchor_screenshot, 'content') and anchor_screenshot.content:
            anchor_data = anchor_screenshot.content[0].text
            log.info("📍 'Alle Kommentare' anchor screenshot captured")
        
        # === Schritt 5: DISTANCE-MEASURED PRELOAD ===
        log.info("5️⃣ Starting distance-measured pre-scroll to load all Facebook comments...")
        scroll_distance_data = await measure_scroll_distance_and_preload()
        
        # === Schritt 6: DISTANCE-GUIDED ANCHOR RETURN ===
        log.info("6️⃣ Returning to 'Alle Kommentare' anchor using measured distance...")
        anchor_found = await return_to_anchor_with_distance(scroll_distance_data)
        
        if not anchor_found:
            log.warning("⚠️ Distance-guided return failed, trying template-only fallback...")
            await return_to_alle_kommentare_anchor()
        
        # === Schritt 7: COMMENT EXTRACTION ===
        log.info("7️⃣ Starting progressive screenshot-based comment extraction...")
        
        # Store the original URL for validation purposes
        find_and_click_expansion_buttons._original_url = original_url
        
        extracted_comments = await extract_comments_adaptive()
        log.info(f"📊 Extracted {len(extracted_comments)} comments via adaptive analysis")
        
        # Speichere die extrahierten Kommentare für das finale DOM
        global extracted_comment_data
        extracted_comment_data = extracted_comments
        
        # Abschließende menschliche Aktivität
        await simulate_page_completion(len(extracted_comments))
        
        log.info("🎉 Facebook workflow completed successfully!")
        return True
        
    except Exception as e:
        log.error(f"❌ Error in Facebook workflow: {e}")
        return False

async def find_relevanteste_with_scroll() -> bool:
    """
    Sucht "Relevanteste" Button mit Scroll-Fallback.
    Scroll maximal 2x wenn nicht sofort gefunden.
    """
    relevanteste_texts = [
        "Relevanteste", "Most relevant", "Most Relevant", 
        "Top-Kommentare", "Top comments", "Topkommentare"
    ]
    
    # Versuch 1: Sofort suchen
    log.info("🔍 Attempt 1: Searching for 'Relevanteste' without scrolling...")
    found = await find_and_click_button_robust(relevanteste_texts, max_retries=2)
    if found:
        return True
    
    # Versuch 2: Nach 1x scrollen
    log.info("📜 Scrolling once and searching again...")
    await scroll_page_down(1)
    await human_wait("page content review")
    
    found = await find_and_click_button_robust(relevanteste_texts, max_retries=2)
    if found:
        return True
    
    # Versuch 3: Nach 2x scrollen
    log.info("📜 Scrolling once more and searching again...")
    await scroll_page_down(1)
    await human_wait("final page review")
    
    found = await find_and_click_button_robust(relevanteste_texts, max_retries=2)
    return found

async def click_all_load_more_comments() -> int:
    """
    Klickt alle "alle xx Kommentare-ansehen" Buttons bis keine mehr da sind.
    Berücksichtigt Zahlen in den Button-Texten.
    
    Returns:
        Anzahl der geklickten Buttons
    """
    clicked_count = 0
    max_iterations = 10  # Sicherheitsgrenze
    
    for iteration in range(max_iterations):
        log.info(f"🔄 Load more comments iteration {iteration + 1}/{max_iterations}")
        
        # Suche nach "alle xx Kommentare" patterns mit Zahlen
        found_button = await find_numbered_comment_buttons()
        
        if not found_button:
            log.info(f"✅ No more 'alle xx Kommentare-ansehen' buttons found. Clicked {clicked_count} total.")
            break
            
        clicked_count += 1
        log.info(f"✅ Clicked load more comments button #{clicked_count}")
        
        # Menschliches Verhalten nach dem Klick
        await human_wait("comment loading")  # Warten bis neue Kommentare laden
        
        # Gelegentlich Hesitation zwischen Klicks
        if clicked_count > 1:
            await add_click_hesitation()
        
        # Scroll nach unten um neue Buttons zu finden
        await scroll_page_down(1)
    
    return clicked_count

async def click_all_view_comment_buttons() -> int:
    """
    Klickt alle "Kommentar ansehen" Buttons bis keine mehr da sind.
    
    Returns:
        Anzahl der geklickten Buttons
    """
    clicked_count = 0
    max_iterations = 20  # Mehr Iterationen da es mehr einzelne Kommentare geben kann
    
    view_comment_texts = [
        "Kommentar ansehen", "Antwort ansehen", "Antworten",
        "replies", "show replies", "view comment"
    ]
    
    for iteration in range(max_iterations):
        log.info(f"🔄 View comment iteration {iteration + 1}/{max_iterations}")
        
        found_button = await find_and_click_button_robust(view_comment_texts, max_retries=1)
        
        if not found_button:
            log.info(f"✅ No more 'Kommentar ansehen' buttons found. Clicked {clicked_count} total.")
            break
            
        clicked_count += 1
        log.info(f"✅ Clicked view comment button #{clicked_count}")
        
        # Menschliches Verhalten zwischen Klicks
        brief_pause = random.uniform(0.8, 2.0)
        await asyncio.sleep(brief_pause)
        
        # Hesitation alle paar Klicks
        if clicked_count % 3 == 0:
            await add_click_hesitation()
        
        # Gelegentlich scrollen um neue Buttons zu finden
        if clicked_count % 5 == 0:
            await scroll_page_down(1)
            # Kurze Lesepause nach dem Scrollen
            await simulate_brief_reading()
    
    return clicked_count

async def find_numbered_comment_buttons() -> bool:
    """
    Sucht nach "alle xx Kommentare-ansehen" Buttons mit Zahlen.
    Kombiniert Text-Suche, OCR und Template Matching.
    """
    # Pattern für OCR-Suche mit Zahlen
    number_patterns = [
        r"alle \d+ kommentare",
        r"alle \d+ antworten", 
        r"\d+ weitere kommentare",
        r"\d+ more comments",
        r"view \d+ more",
        r"show \d+ more"
    ]
    
    try:
        # Methode 1: Text-basierte Suche mit Regex
        await asyncio.sleep(random.uniform(0.5, 1.1))
        state_result = await mcp_client.send_command("State-Tool", {"use_vision": False})
        state_data = state_result.data if hasattr(state_result, 'data') and state_result.data else ''
        log.debug(f"[NUM_BTN] state_len={len(state_data)}")
        
        import re
        for line in state_data.split('\n'):
            line_lower = line.lower()
            for pattern in number_patterns:
                if re.search(pattern, line_lower):
                    # Versuche Koordinaten zu extrahieren
                    match = re.search(r'\((\d+),\s*(\d+)\)', line)
                    if match:
                        x, y = int(match.group(1)), int(match.group(2))
                        # Numbered buttons are expansion clicks -> clamp
                        # Percentage clamp for expansion numbered buttons
                        iw = ih = None
                        try:
                            screenshot_result3 = await mcp_client.send_command("Screenshot-Tool", {})
                            if screenshot_result3 and hasattr(screenshot_result3, 'content') and screenshot_result3.content:
                                sdata3 = screenshot_result3.content[0].text
                                if "Base64 data: " in sdata3:
                                    import base64, cv2
                                    import numpy as np
                                    b643 = sdata3.split("Base64 data: ")[1]
                                    img_bytes3 = base64.b64decode(b643)
                                    img_arr3 = np.frombuffer(img_bytes3, dtype=np.uint8)
                                    img3 = cv2.imdecode(img_arr3, cv2.IMREAD_COLOR)
                                    ih, iw = (img3.shape[0], img3.shape[1]) if img3 is not None else (None, None)
                        except Exception:
                            pass
                        x, y = clamp_to_modal(x, y, image_width=iw, image_height=ih)
                        log.info(f"🎯 Found numbered comment button: '{line_lower}' at ({x}, {y}) [modal-clamped]")
                        await mcp_client.send_command("Click-Tool", {"loc": [x, y]})
                        
                        global last_focus_point
                        last_focus_point = [x, y]
                        return True
        
        # Methode 2: Template Matching Fallback
        screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
        # Extract from content first, fallback to data
        screenshot_data = None
        if screenshot_result and hasattr(screenshot_result, 'content') and screenshot_result.content:
            screenshot_data = screenshot_result.content[0].text
        elif screenshot_result and hasattr(screenshot_result, 'data'):
            screenshot_data = screenshot_result.data
        if screenshot_data and "Base64 data: " in screenshot_data:
            base64_data = screenshot_data.split("Base64 data: ")[1]
            matches = template_service.match_template_in_base64(
                base64_data, 
                "alle-xx-kommentare-ansehen", 
                threshold=0.7  # PATCH: Increased from 0.6 to 0.7 to reduce false positives
            )
            if matches:
                best_match = matches[0]
                x, y = int(best_match.center[0]), int(best_match.center[1])
                w = int(best_match.size[0])
                # Validation logs
                raw_x, raw_y = x, y
                log.info(f"[NUM_BTN] raw_center=({raw_x}, {raw_y}) w={w} conf={best_match.confidence:.3f}")
                left_offset = int(min(max(w * 0.35, 24), 96))
                target_x = x - left_offset
                log.info(f"[NUM_BTN] applying_left_offset={left_offset}px raw=({raw_x}, {raw_y}) -> target=({target_x}, {y})")
                x = target_x
                log.info(f"🎯 Found numbered comment button via template at adjusted ({x}, {y}) (offset {left_offset})")
                log.info(f"[NUM_BTN] Move-Tool target=({x}, {y}); Click-Tool follows")
                x, y = clamp_to_modal(x, y)
                await mcp_client.send_command("Move-Tool", {"to_loc": [x, y]})
                await asyncio.sleep(random.uniform(0.12, 0.25))
                await mcp_client.send_command("Click-Tool", {"loc": [x, y]})
                # Suppress random movement briefly after critical click
                global movement_suppressed_until
                movement_suppressed_until = datetime.now(timezone.utc) + timedelta(seconds=3.0)
                log.debug(f"[MOVE_SUPPRESS] Enabled until {movement_suppressed_until}")
                
                last_focus_point = [x, y]
                return True
        
        return False
        
    except Exception as e:
        log.error(f"❌ Error searching for numbered comment buttons: {e}")
        return False

async def confirm_alle_kommentare_selected() -> bool:
    """Take a screenshot and confirm the filter shows 'Alle Kommentare' not 'Relevanteste'.
    Returns True if confirmation passes; False otherwise.
    """
    try:
        # Screenshot
        screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
        screenshot_data = None
        if screenshot_result and hasattr(screenshot_result, 'content') and screenshot_result.content:
            screenshot_data = screenshot_result.content[0].text
        elif screenshot_result and hasattr(screenshot_result, 'data'):
            screenshot_data = screenshot_result.data
        if not screenshot_data or "Base64 data: " not in screenshot_data:
            log.error("❌ Confirmation screenshot missing or invalid")
            return False
        base64_data = screenshot_data.split("Base64 data: ")[1]

        # Look for confirmed template first
        confirmed = template_service.match_template_in_base64(
            base64_data,
            "alle-kommentare-confirmed",
            threshold=0.6
        )
        if confirmed:
            log.info("✅ Confirmation: 'Alle Kommentare' is active.")
            return True

        # If 'Relevanteste' still visible at the control, treat as failure
        still_relevant = template_service.match_template_in_base64(
            base64_data,
            "relevanteste",
            threshold=0.6
        )
        if still_relevant:
            log.error("❌ Confirmation: 'Relevanteste' still active after click.")
            return False

        # PATCH: OCR fallback - search screen text for 'alle kommentare' or 'all comments'
        try:
            from .ocr_service import ocr_service
            ocr_res = ocr_service.extract_text_from_base64(base64_data)
            otext = normalize_facebook_text(ocr_res.get('text', '') if ocr_res else '')
            if any(k in otext for k in [normalize_facebook_text('alle kommentare'), normalize_facebook_text('all comments')]):
                log.info("✅ Confirmation via OCR: 'Alle Kommentare' text present.")
                return True
        except Exception as _e:
            log.debug(f"[CONFIRM_OCR] fallback failed: {_e}")

        # If neither found, be conservative
        log.warning("⚠️ Confirmation inconclusive: neither template nor OCR confirmed.")
        return False
    except Exception as e:
        log.error(f"❌ Error during confirmation step: {e}")
        return False

async def scroll_page_down(wheel_times: int = 2):
    """Hilfsfunktion zum Scrollen mit menschlichem Verhalten."""
    try:
        # Check movement suppression before any mouse movement
        global movement_suppressed_until
        if not (movement_suppressed_until and datetime.now(timezone.utc) < movement_suppressed_until):
            # Only add subtle movement if not suppressed
            await add_subtle_mouse_movement()
        else:
            log.debug("[MOVE_SUPPRESS] Skipping pre-scroll mouse movement (suppressed)")
        
        await mcp_client.send_command("Scroll-Tool", {"direction": "down", "wheel_times": wheel_times})
        log.debug(f"📜 Scrolled down by {wheel_times} wheel times")
        
        # Kurze Pause nach dem Scrollen (Menschen lesen)
        reading_time = random.uniform(0.8, 2.5)
        await asyncio.sleep(reading_time)
        
    except Exception as e:
        log.error(f"❌ Error scrolling: {e}")

async def move_cursor_to_safe_modal_area(current_x: int, current_y: int):
    """
    Move cursor to a safe area within the Facebook modal/content area only.
    Avoids sidebars, taskbars, and other problematic screen areas.
    """
    log.info("🔧 NEW CODE: Using move_cursor_to_safe_modal_area function!")
    try:
        # PATCH: Use percentage-based modal bounds derived from current screenshot size for adaptability
        iw = ih = None
        try:
            screenshot_result_sz = await mcp_client.send_command("Screenshot-Tool", {})
            if screenshot_result_sz and hasattr(screenshot_result_sz, 'content') and screenshot_result_sz.content:
                content_text_sz = screenshot_result_sz.content[0].text
                if "Base64 data: " in content_text_sz:
                    import base64, cv2, numpy as np
                    b64 = content_text_sz.split("Base64 data: ")[-1]
                    img_bytes = base64.b64decode(b64)
                    img_arr = np.frombuffer(img_bytes, dtype=np.uint8)
                    img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                    ih, iw = (img.shape[0], img.shape[1]) if img is not None else (None, None)
        except Exception:
            pass

        # Compute modal-safe rectangle
        if iw and ih and iw > 0 and ih > 0:
            left = int(iw * 0.20)
            right = int(iw * 0.80)
            top = int(ih * 0.15)
            bottom = int(ih * 0.88)
        else:
            left, right, top, bottom = 300, 1200, 200, 700

        # Calculate safe position relative to current click, bias to center-right
        target_x = max(left + 100, min(current_x + 200, right - 100))
        target_y = max(top + 50, min(current_y + 100, bottom - 50))

        # Human-like jitter
        target_x += random.randint(-30, 30)
        target_y += random.randint(-20, 20)

        # Final clamp inside modal bounds
        final_x = max(left, min(target_x, right))
        final_y = max(top, min(target_y, bottom))

        log.info(f"🎯 Moving cursor from ({current_x}, {current_y}) to safe modal area ({final_x}, {final_y})")

        await mcp_client.send_command("Move-Tool", {"to_loc": [final_x, final_y]})
        
        # Brief pause to let any hover effects settle
        await asyncio.sleep(0.2)
        
    except Exception as e:
        log.error(f"❌ Error in modal-safe cursor movement: {e}")
        # Fallback to Safe-Center-Move-Tool if our calculation fails
        await mcp_client.send_command("Safe-Center-Move-Tool", {})

def clamp_to_modal(x: int, y: int, *, image_width: int | None = None, image_height: int | None = None) -> tuple[int, int]:
    """
    Clamp coordinates to modal/content area using percentage-of-screenshot when available,
    with absolute fallback for safety.
    """
    # Percentage bounds (relative to screenshot)
    if image_width and image_height and image_width > 0 and image_height > 0:
        left = int(image_width * 0.20)   # 20% from left
        right = int(image_width * 0.80)  # 80% from left
        top = int(image_height * 0.15)   # 15% from top
        bottom = int(image_height * 0.88) # 88% from top (allow deeper content)
    else:
        # Absolute fallback (legacy)
        left, right, top, bottom = 300, 1200, 200, 700
    clamped_x = max(left, min(x, right))
    clamped_y = max(top, min(y, bottom))
    # PATCH: add small human-like jitter then re-clamp to stay inside safe area
    try:
        jx = random.randint(-3, 3)
        jy = random.randint(-3, 3)
        clamped_x = max(left, min(clamped_x + jx, right))
        clamped_y = max(top, min(clamped_y + jy, bottom))
    except Exception:
        pass
    if clamped_x != x or clamped_y != y:
        log.info(f"[MODAL_CLAMP] Adjusted position from ({x}, {y}) to ({clamped_x}, {clamped_y}) within bounds L{left}-R{right} T{top}-B{bottom}")
    return clamped_x, clamped_y

# PATCH: Bounding-Box Filter Hilfsfunktion
def is_within_modal_bounds(x: int, y: int, width: int | None = None, height: int | None = None) -> bool:
    """
    Prüft ob Koordinaten innerhalb des erlaubten Facebook-Post-Modal-Fensters liegen.
    
    Args:
        x, y: Koordinaten zum Prüfen
        width, height: Screenshot-Dimensionen (optional)
        
    Returns:
        True wenn Koordinaten innerhalb der Bounding-Box liegen
    """
    # PATCH: Definiere Bounding-Box-Fenster für erlaubte Klickbereiche
    if width and height and width > 0 and height > 0:
        # Prozentuale Bounds basierend auf Screenshot-Größe
        min_x = int(width * 0.2)   # 20% von links
        max_x = int(width * 0.8)   # 80% von links  
        min_y = int(height * 0.15) # 15% von oben
        max_y = int(height * 0.88) # 88% von oben
    else:
        # Absolute Fallback-Werte
        min_x, max_x, min_y, max_y = 300, 1200, 200, 700
    
    is_within = min_x <= x <= max_x and min_y <= y <= max_y
    
    if not is_within:
        log.debug(f"[FILTER_LOG] Coordinates ({x}, {y}) outside bounds: x∈[{min_x},{max_x}], y∈[{min_y},{max_y}]")
    
    return is_within

# PATCH: Pre-Click Validation Hilfsfunktion  
async def pre_click_validate(x: int, y: int, expected_text: str) -> bool:
    """
    Validiert vor dem Klick, ob an der Zielkoordinate der erwartete Button-Text vorhanden ist.
    
    Args:
        x, y: Zielkoordinaten für den Klick
        expected_text: Erwarteter Button-Text ("Antwort", "Kommentare", etc.)
        
    Returns:
        True wenn Validierung erfolgreich, False sonst
    """
    try:
        # PATCH: Erstelle Crop-Screenshot von ca. 80x30 Pixeln um die Zielkoordinate
        crop_width, crop_height = 80, 30
        # PATCH: Convert numpy types to native Python int to avoid serialization errors
        crop_x = max(0, int(x) - crop_width // 2)
        crop_y = max(0, int(y) - crop_height // 2)
        
        # Screenshot mit Region erstellen
        screenshot_result = await mcp_client.send_command("Screenshot-Tool", {
            "region": [crop_x, crop_y, crop_width, crop_height]
        })
        
        if not screenshot_result or not hasattr(screenshot_result, 'content') or not screenshot_result.content:
            log.warning(f"[SKIP_LOG] Pre-click validation: Could not capture crop screenshot at ({x}, {y})")
            return False
            
        screenshot_data = screenshot_result.content[0].text
        if "Base64 data: " not in screenshot_data:
            log.warning(f"[SKIP_LOG] Pre-click validation: Invalid screenshot data format")
            return False
            
        base64_data = screenshot_data.split("Base64 data: ")[1]
        
        # PATCH: Template-Matching Validation
        normalized_expected = normalize_facebook_text(expected_text)
        
        # PATCH: Stricter template matching for validation to prevent false-positives
        template_name = _map_button_to_template(expected_text)
        if template_name:
            matches = template_service.match_template_in_base64(
                base64_data, 
                template_name, 
                threshold=0.7  # Higher threshold for validation - must be very confident
            )
            if matches:
                log.debug(f"[VALIDATION] Strong template match found for '{expected_text}' at crop region (threshold 0.7)")
                return True
        
        # PATCH: OCR Substring-Check als Fallback
        ocr_result = ocr_service.extract_text_from_base64(base64_data)
        if ocr_result and ocr_result.get("text"):
            detected_text = normalize_facebook_text(ocr_result["text"])
            
            # Überprüfe ob erwarteter Text im OCR-Text enthalten ist
            if normalized_expected and normalized_expected in detected_text:
                log.debug(f"[VALIDATION] OCR substring match for '{expected_text}' in '{detected_text}'")
                return True
            
            # Zusätzliche Fuzzy-Checks für häufige Varianten
            fuzzy_checks = {
                "antwort": ["antwort", "reply", "answer"],
                "kommentar": ["kommentar", "comment", "view"],
                "alle": ["alle", "all", "view"],
                "ansehen": ["ansehen", "view", "see"]
            }
            
            for key, variants in fuzzy_checks.items():
                if key in normalized_expected.lower():
                    for variant in variants:
                        if variant in detected_text.lower():
                            log.debug(f"[VALIDATION] Fuzzy match '{variant}' for '{expected_text}'")
                            return True
        
        log.warning(f"[SKIP_LOG] Pre-click validation failed: Expected '{expected_text}', found '{detected_text if 'detected_text' in locals() else 'N/A'}'")
        return False
        
    except Exception as e:
        log.error(f"[SKIP_LOG] Pre-click validation exception: {e}")
        return False

async def dodge_cursor_then_center(from_x: int, from_y: int):
    """Quick dodge out of hover popups: move to opposite modal corner, then center-safe."""
    # PATCH: Use percentage-based bounds when available
    iw = ih = None
    try:
        screenshot_result_sz = await mcp_client.send_command("Screenshot-Tool", {})
        if screenshot_result_sz and hasattr(screenshot_result_sz, 'content') and screenshot_result_sz.content:
            content_text_sz = screenshot_result_sz.content[0].text
            if "Base64 data: " in content_text_sz:
                import base64, cv2, numpy as np
                b64 = content_text_sz.split("Base64 data: ")[-1]
                img_bytes = base64.b64decode(b64)
                img_arr = np.frombuffer(img_bytes, dtype=np.uint8)
                img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                ih, iw = (img.shape[0], img.shape[1]) if img is not None else (None, None)
    except Exception:
        pass
    if iw and ih and iw > 0 and ih > 0:
        left = int(iw * 0.20); right = int(iw * 0.80); top = int(ih * 0.15); bottom = int(ih * 0.88)
    else:
        left, right, top, bottom = 300, 1200, 200, 700
    mid_x = (left + right) // 2
    mid_y = (top + bottom) // 2
    # Pick opposite corner from current point
    target_x = right - 60 if from_x < mid_x else left + 60
    target_y = bottom - 60 if from_y < mid_y else top + 60
    target_x, target_y = clamp_to_modal(target_x, target_y, image_width=iw, image_height=ih)
    log.info(f"[DODGE] Moving away from ({from_x}, {from_y}) to ({target_x}, {target_y}) before centering")
    try:
        await mcp_client.send_command("Move-Tool", {"to_loc": [target_x, target_y]})
        await asyncio.sleep(0.25)
    except Exception as e:
        log.warning(f"[DODGE] Move failed: {e}")
    # Then go to safe modal area
    try:
        await move_cursor_to_safe_modal_area(target_x, target_y)
    except Exception as e:
        log.warning(f"[DODGE] Centering failed: {e}")

# ===== ANTI-DETECTION FUNCTIONS =====

async def add_human_mouse_movement():
    """Simuliert zufällige menschliche Mausbewegungen innerhalb der Modal-Grenzen."""
    try:
        global movement_suppressed_until
        if movement_suppressed_until and datetime.now(timezone.utc) < movement_suppressed_until:
            log.debug("[MOVE_SUPPRESS] Skipping human mouse movement (suppressed)")
            return
        # Gelegentliche zufällige Bewegungen
        if random.random() < 0.3:  # 30% Chance
            # MODAL-SAFE zufällige Bewegung - bleibe in Facebook Modal
            modal_x = random.randint(350, 1150)  # Within modal boundaries
            modal_y = random.randint(250, 650)   # Within modal boundaries
            await mcp_client.send_command("Move-Tool", {"to_loc": [modal_x, modal_y]})
            await asyncio.sleep(random.uniform(0.2, 0.6))
            log.debug(f"🐭 Added modal-safe random movement to ({modal_x}, {modal_y})")
    except Exception as e:
        log.debug(f"Mouse movement failed: {e}")

async def add_subtle_mouse_movement():
    """Subtile Mausbewegung vor Aktionen innerhalb der Modal-Grenzen."""
    try:
        global movement_suppressed_until
        if movement_suppressed_until and datetime.now(timezone.utc) < movement_suppressed_until:
            log.debug("[MOVE_SUPPRESS] Skipping subtle mouse movement (suppressed)")
            return
        if random.random() < 0.4:  # 40% Chance für subtile Bewegung
            # MODAL-SAFE kleine Bewegung - bleibe im Modal-Bereich
            base_x, base_y = 600, 400  # Safe modal center as fallback
            new_x = max(350, min(base_x + random.randint(-80, 80), 1150))  # Constrain to modal
            new_y = max(250, min(base_y + random.randint(-50, 50), 650))   # Constrain to modal
            
            await mcp_client.send_command("Move-Tool", {"to_loc": [new_x, new_y]})
            await asyncio.sleep(random.uniform(0.1, 0.3))
            log.debug(f"🐭 Added modal-safe subtle movement to ({new_x}, {new_y})")
    except Exception as e:
        log.debug(f"Subtle mouse movement failed: {e}")

async def simulate_page_reading():
    """Simuliert menschliches Leseverhalten auf einer Seite."""
    log.info("📖 Simulating human reading behavior...")
    
    # Mehrere kurze Scroll-Bewegungen mit Pausen
    scroll_count = random.randint(2, 4)
    
    for i in range(scroll_count):
        # Kurz scrollen
        await mcp_client.send_command("Scroll-Tool", {"direction": "down", "wheel_times": 1})
        
        # Lesepause
        reading_time = random.uniform(1.2, 3.5)
        await asyncio.sleep(reading_time)
        
        # Gelegentlich zurück scrollen (wie Menschen)
        if random.random() < 0.2:  # 20% Chance
            await asyncio.sleep(random.uniform(0.3, 0.8))
            await mcp_client.send_command("Scroll-Tool", {"direction": "up", "wheel_times": 1})
            await asyncio.sleep(random.uniform(0.5, 1.2))

async def simulate_brief_reading():
    """Kurzes Leseverhalten zwischen Aktionen."""
    log.debug("📄 Brief reading simulation...")
    
    # Kurze Pause für "Lesen"
    reading_time = random.uniform(1.5, 3.0)
    await asyncio.sleep(reading_time)
    
    # Gelegentlich kleine Scroll-Bewegung
    if random.random() < 0.3:
        await mcp_client.send_command("Scroll-Tool", {"direction": "down", "wheel_times": 1})
        await asyncio.sleep(random.uniform(0.8, 1.5))

async def simulate_page_completion(total_clicks: int):
    """Simuliert Verhalten am Ende der Seiten-Interaktion."""
    log.info(f"🏁 Simulating completion behavior after {total_clicks} interactions...")
    
    # Länger Pause am Ende (Menschen überdenken das Gelesene)
    completion_time = random.uniform(2.0, 5.0)
    await asyncio.sleep(completion_time)
    
    # Gelegentlich nochmal nach oben scrollen
    if random.random() < 0.4:  # 40% Chance
        scroll_up_count = random.randint(1, 3)
        for _ in range(scroll_up_count):
            await mcp_client.send_command("Scroll-Tool", {"direction": "up", "wheel_times": 2})
            await asyncio.sleep(random.uniform(1.0, 2.0))

async def add_click_hesitation():
    """Fügt menschliche Unschlüssigkeit vor Klicks hinzu."""
    if random.random() < 0.15:  # 15% Chance für Zögern
        log.debug("🤔 Adding click hesitation...")
        
        # Kurze Bewegung weg und zurück
        await add_subtle_mouse_movement()
        await asyncio.sleep(random.uniform(0.3, 0.8))
        
        # Manchmal nochmal bewegen
        if random.random() < 0.5:
            await add_subtle_mouse_movement()
            await asyncio.sleep(random.uniform(0.2, 0.5))

async def add_typing_rhythm():
    """Simuliert menschlichen Typing-Rhythmus (für zukünftige Nutzung)."""
    # Zufällige Pausen zwischen Tastenanschlägen
    typing_delays = [
        random.uniform(0.08, 0.15),  # Normale Geschwindigkeit
        random.uniform(0.15, 0.25),  # Etwas langsamer
        random.uniform(0.05, 0.12)   # Schneller
    ]
    
    return random.choice(typing_delays)

# ===== TIMING DIAGNOSTICS =====

async def test_detection_timing():
    """
    Diagnostik-Funktion um die Timing-Verbesserungen zu zeigen.
    Zeigt die neuen vs. alten Timing-Strategien.
    """
    log.info("🕐 === DETECTION TIMING ANALYSIS ===")
    log.info("📊 OLD STRATEGY (before optimization):")
    log.info("   • Max retries: 3")
    log.info("   • Wait between attempts: 3s")
    log.info("   • OCR/Template: Only on attempt 3")
    log.info("   • Total time before advanced methods: ~9+ seconds")
    log.info("")
    log.info("📊 NEW STRATEGY (optimized):")
    log.info("   • Max retries: 2") 
    log.info("   • Wait between attempts: 1.5s → 2.0s")
    log.info("   • OCR/Template: On attempt 2 (after 1.5s)")
    log.info("   • Total time before advanced methods: ~1.5 seconds")
    log.info("")
    log.info("🚀 SPEED IMPROVEMENT: ~6x faster fallback to advanced detection!")
    log.info("⏱️  From 9+ seconds → 1.5 seconds before trying OCR/Template matching")
    log.info("🎯 Advanced methods now tried within 30 seconds, not several minutes")

# Backwards compatibility - replace old function
async def click_fb_relevanteste_and_all_replies():
    """Legacy function - redirects to new workflow."""
    log.info("🔄 Redirecting to new comprehensive Facebook workflow...")
    return await execute_facebook_workflow("")

async def navigate(url: str) -> dict:
    """
    Vereinfachte Navigation ohne Retyping-Probleme.
    Inspiriert vom bereitgestellten Code-Beispiel.
    
    Returns:
        dict: {"facebook_workflow_used": bool, "success": bool}
    """
    await _rate_limited("navigate")
    log.info(f"[SIMPLE_NAV] Navigating to {url}")
    
    try:
        # Chrome starten
        await mcp_client.send_command("Launch-Tool", {"name": "chrome"})
        log.info("[SIMPLE_NAV] Launched Chrome. Waiting for it to open...")
        await asyncio.sleep(5)
        
        # Pre-scan Check: Look for login/verification screens before proceeding
        log.info("[SIMPLE_NAV] Performing pre-scan check for login/verification screens...")
        await human_wait("pre-scan check")
        verification_keywords = ["Verify your identity", "Bestätigen Sie Ihre Identität", "Login", "Anmelden"]
        state_result = await mcp_client.send_command("State-Tool", {"use_vision": False})
        state_data = state_result.data if hasattr(state_result, 'data') and state_result.data else ''
        
        for keyword in verification_keywords:
            if keyword.lower() in state_data.lower():
                log.warning(f"Login or verification screen detected with keyword: '{keyword}'.")
                raise RuntimeError("Login or identity verification required. Please log in manually and start a new scan.")

        # URL einmal eingeben mit menschlichem Typing-Verhalten
        log.info(f"[SIMPLE_NAV] Typing URL into address bar...")
        
        # Kleine Hesitation vor URL-Eingabe
        await add_click_hesitation()
        
        await mcp_client.send_command("Shortcut-Tool", {"shortcut": ["ctrl", "l"]})
        
        # Kurze Pause vor dem Tippen (menschlich)
        await asyncio.sleep(random.uniform(0.3, 0.8))
        type_result = await mcp_client.send_command("Type-Tool", {"text": url, "press_enter": True})
        log.info(f"[SIMPLE_NAV] Type-Tool result: {type_result}")
        
        # Warten bis Seite geladen ist mit menschlicher Variabilität
        loading_time = random.uniform(4.0, 7.0)
        log.info(f"[SIMPLE_NAV] Waiting {loading_time:.1f}s for page to load...")
        await asyncio.sleep(loading_time)
        
        # Facebook-spezifische Aktionen
        if "facebook.com" in url.lower():
            log.info("[SIMPLE_NAV] Detected Facebook URL, starting comprehensive Facebook workflow...")
            
            # Der neue Workflow übernimmt die Chrome-Fokussierung
            success = await execute_facebook_workflow(url)
            if not success:
                log.error("[SIMPLE_NAV] Facebook workflow failed - stopping navigation")
                raise RuntimeError("Facebook workflow failed at critical step. Cannot proceed with scan.")
            
            log.info(f"[SIMPLE_NAV] Facebook workflow completed successfully - no additional scanning cycles needed.")
            return {"facebook_workflow_used": True, "success": True}

    except Exception as e:
        log.error(f"[SIMPLE_NAV] Failed to navigate: {e}")
        raise RuntimeError("Could not navigate in Chrome for the scan.") from e

    log.info(f"[SIMPLE_NAV] Navigation to {url} complete.")
    return {"facebook_workflow_used": False, "success": True}

async def find_and_click_button(button_texts: list[str]) -> bool:
    """Uses State-Tool to find a button by its text and clicks it."""
    try:
        state_result = await mcp_client.send_command("State-Tool", {"use_vision": False})
        # Ensure we are accessing the data attribute correctly from the result object
        state_data = state_result.data if hasattr(state_result, 'data') and state_result.data else ''
        
        # Relaxed parser: accept menu items/options and any clickable line containing coordinates
        import re
        for line in state_data.split('\n'):
            normalized_line = line.strip()
            for candidate in button_texts:
                if candidate.lower() in normalized_line.lower():
                    match = re.search(r'\((\d+),\s*(\d+)\)', normalized_line)
                    if match:
                        x, y = int(match.group(1)), int(match.group(2))
                        log.info(f"Found '{candidate}' at ({x}, {y}). Clicking it.")
                        await mcp_client.send_command("Click-Tool", {"loc": [x, y]})
                        # Record last focus point to help future scrolls target the page
                        global last_focus_point
                        last_focus_point = [x, y]
                        return True
        log.warning(f"Could not find any of the buttons: {button_texts}")
        return False
    except Exception as e:
        log.error(f"Error while trying to find and click button: {e}")
        return False

async def get_scroll_position() -> dict:
    """Get current scroll position to track if we've reached bottom of page."""
    try:
        # Take a small screenshot to analyze scroll position using OCR
        screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
        if screenshot_result and hasattr(screenshot_result, 'content') and screenshot_result.content:
            content_text = screenshot_result.content[0].text if screenshot_result.content else ""
            if "Base64 data: " in content_text:
                screenshot_data = content_text.split("Base64 data: ")[-1]
                from .ocr_service import ocr_service
                ocr_result = ocr_service.extract_text_from_base64(screenshot_data)
                ocr_text = ocr_result.get('text', '') if ocr_result else ''
                
                # More comprehensive bottom detection
                bottom_indicators = [
                    "keine weiteren kommentare", "end of comments", "bottom", "keine kommentare mehr",
                    "privatsphäre", "impressum", "nutzungsbedingungen", "werbung", "entwickler",
                    "ende der kommentare", "keine antworten mehr", "footer", "copyright"
                ]
                
                for indicator in bottom_indicators:
                    if indicator.lower() in ocr_text.lower():
                        log.info(f"🔽 Page bottom detected with indicator: '{indicator}'")
                        return {"at_bottom": True, "indicator": indicator}
                        
        return {"at_bottom": False, "indicator": None}
    except Exception as e:
        log.debug(f"Could not determine scroll position: {e}")
        return {"at_bottom": False, "indicator": None}

async def intelligent_scroll_and_search() -> bool:
    """
    Intelligent scrolling that continues until no new expansion buttons are found.
    Uses multiple strategies:
    1. Progressive scroll distance (start small, increase gradually)
    2. Track scroll position to detect page bottom
    3. Look for buttons after each scroll
    4. Stop when no new buttons found for multiple attempts
    """
    log.info("🔍 Starting intelligent scroll and search for expansion buttons...")
    
    try:
        buttons_found = False
        scroll_attempts = 0
        max_scroll_attempts = 20   # Even more persistent scrolling
        consecutive_failures = 0
        max_consecutive_failures = 6  # Only give up after many failures
        last_content_hash = None  # Track content to detect if page stopped changing
        stuck_counter = 0  # Counter for when content stops changing
        
        # CONSERVATIVE scroll distances - smaller steps to avoid missing content
        scroll_distances = [1, 2, 2, 3, 3, 4, 4, 5, 6, 7]  # More conservative, repeated small steps
        
        while scroll_attempts < max_scroll_attempts and consecutive_failures < max_consecutive_failures:
            scroll_attempts += 1
            
            # Check if we've reached the bottom before scrolling more
            scroll_pos = await get_scroll_position()
            if scroll_pos["at_bottom"]:
                log.info(f"🏁 Reached bottom of page (indicator: {scroll_pos['indicator']})")
                break
            
            # Use progressive scroll distance
            scroll_distance = scroll_distances[min(scroll_attempts - 1, len(scroll_distances) - 1)]
            
            log.info(f"📜 Intelligent scroll attempt {scroll_attempts}/{max_scroll_attempts} (distance: {scroll_distance})")
            
            # Scroll down with progressive distance
            await mcp_client.send_command("Scroll-Tool", {"direction": "down", "wheel_times": scroll_distance})
            await asyncio.sleep(random.uniform(1.2, 2.2))  # Wait for content to load
            
            # Check if content has changed (to detect if we're stuck)
            try:
                screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
                if screenshot_result and hasattr(screenshot_result, 'content') and screenshot_result.content:
                    current_content = screenshot_result.content[0].text[:500]  # First 500 chars as hash
                    current_hash = hash(current_content)
                    
                    if last_content_hash and current_hash == last_content_hash:
                        stuck_counter += 1
                        log.debug(f"📄 Content unchanged after scroll (stuck_counter: {stuck_counter})")
                        if stuck_counter >= 3:
                            log.info("🚫 Content stopped changing - likely reached end of scrollable area")
                            break
                    else:
                        stuck_counter = 0  # Reset if content changed
                        last_content_hash = current_hash
            except Exception as e:
                log.debug(f"Could not check content change: {e}")
            
            # Look for expansion buttons after scrolling
            expansion_found = await find_and_click_expansion_buttons()
            
            if expansion_found:
                log.info(f"✅ Found expansion buttons after scroll attempt {scroll_attempts}")
                buttons_found = True
                consecutive_failures = 0  # Reset failure counter
                
                # Wait for expanded content to load before continuing
                await asyncio.sleep(random.uniform(2.0, 3.0))
                
                # Continue scrolling to look for more buttons after expansion
                continue
            else:
                consecutive_failures += 1
                log.debug(f"❌ No buttons found in attempt {scroll_attempts} (consecutive failures: {consecutive_failures})")
                
                # If we've failed multiple times, try a MODERATE scroll (not too big)
                if consecutive_failures >= 2 and scroll_attempts < max_scroll_attempts:
                    log.info("📜 Multiple failures - trying moderate scroll distance")
                    await mcp_client.send_command("Scroll-Tool", {"direction": "down", "wheel_times": 5})  # Reduced from 8 to 5
                    await asyncio.sleep(random.uniform(1.5, 2.5))
                    
                    # One more attempt with larger scroll
                    final_attempt = await find_and_click_expansion_buttons()
                    if final_attempt:
                        log.info("✅ Found buttons with larger scroll distance")
                        buttons_found = True
                        consecutive_failures = 0
                        await asyncio.sleep(random.uniform(2.0, 3.0))
                        continue
        
        if buttons_found:
            log.info(f"🎯 Intelligent scrolling successful - found expansion buttons after {scroll_attempts} attempts")
        else:
            log.info(f"🏁 Intelligent scrolling complete - no new buttons found after {scroll_attempts} attempts")
        
        return buttons_found
        
    except Exception as e:
        log.error(f"❌ Error during intelligent scroll and search: {e}")
        return False

async def final_cleanup_pass() -> bool:
    """
    Final thorough pass to catch any missed comment expansion buttons.
    Scrolls through the ENTIRE page with minimal steps to ensure nothing is missed.
    """
    log.info("🧹 Starting final cleanup pass to catch any missed expansion buttons...")
    
    try:
        # INITIAL URL VALIDATION before starting cleanup
        log.info("🔍 Initial URL validation before final cleanup")
        try:
            original_url = getattr(find_and_click_expansion_buttons, '_original_url', "facebook.com/posts/")
            url_validation_result = await validate_scan_url(original_url)
            if not url_validation_result:
                log.error("🚨 FINAL CLEANUP: Profile navigation detected before cleanup! Aborting final pass.")
                return False
        except Exception as e:
            log.warning(f"⚠️ Initial cleanup URL validation failed: {e}")
        
        # Continue with cleanup if validation passed
        # First, scroll to the very top to start fresh
        log.info("📍 Scrolling to top for final cleanup pass")
        await mcp_client.send_command("Scroll-Tool", {"direction": "up", "wheel_times": 20})
        await asyncio.sleep(2.0)
        
        # Wait for page to settle
        await asyncio.sleep(1.0)
        
        total_buttons_found = 0
        scroll_step = 0
        max_scroll_steps = 30  # More thorough than normal pass
        no_button_streak = 0
        max_no_button_streak = 5  # Allow longer without buttons
        
        while scroll_step < max_scroll_steps and no_button_streak < max_no_button_streak:
            scroll_step += 1
            
            log.info(f"🔍 Final cleanup step {scroll_step}/{max_scroll_steps}")
            
            # INTERMEDIATE VALIDATION: Check every 10 steps
            if scroll_step % 10 == 0:
                log.info(f"🔍 Intermediate URL validation (cleanup step {scroll_step})")
                try:
                    original_url = getattr(find_and_click_expansion_buttons, '_original_url', "")
                    url_validation_result = await validate_scan_url(original_url)
                    if not url_validation_result:
                        log.error("🚨 INTERMEDIATE CLEANUP VALIDATION: Profile navigation detected! Stopping cleanup.")
                        return False
                except Exception as e:
                    log.warning(f"⚠️ Intermediate cleanup URL validation failed: {e}")
            
            # Look for buttons in current view
            buttons_found_this_step = await find_and_click_expansion_buttons()
            
            if buttons_found_this_step:
                total_buttons_found += 1
                no_button_streak = 0  # Reset streak
                log.info(f"✅ Found buttons in cleanup step {scroll_step} (total found: {total_buttons_found})")
                
                # Wait longer after finding buttons to let content load
                await asyncio.sleep(random.uniform(3.0, 4.0))
            else:
                no_button_streak += 1
                log.debug(f"📭 No buttons in step {scroll_step} (streak: {no_button_streak})")
            
            # Small, careful scroll down
            await mcp_client.send_command("Scroll-Tool", {"direction": "down", "wheel_times": 1})
            await asyncio.sleep(random.uniform(0.8, 1.2))  # Shorter pause for efficiency
        
        if total_buttons_found > 0:
            log.info(f"🎯 Final cleanup pass completed: Found {total_buttons_found} additional expansion buttons")
            return True
        else:
            log.info("🎯 Final cleanup pass completed: No additional buttons found - extraction truly complete")
            return False
            
    except Exception as e:
        log.error(f"❌ Error during final cleanup pass: {e}")
        return False

async def measure_scroll_distance_and_preload() -> Dict[str, Any]:
    """
    PATCH: Measure scroll distance during pre-loading for precise anchor return.
    Uses Chrome-MCP to track actual pixel distances traveled.
    
    Returns:
        Dict containing:
        - scroll_distance_pixels: Total pixels scrolled down
        - scroll_attempts: Number of scroll operations
        - start_position: Starting scroll position
        - end_position: Final scroll position
    """
    log.info("📏 [DISTANCE] Starting distance-measured pre-loading...")
    
    try:
        # Get Chrome MCP client for scroll position tracking
        chrome_client = await get_chrome_mcp_client()
        
        if not chrome_client.is_connected:
            log.warning("⚠️ [DISTANCE] Chrome-MCP not connected, falling back to screenshot-only detection")
            await preload_all_facebook_comments()
            return {"scroll_distance_pixels": 0, "scroll_attempts": 0, "start_position": 0, "end_position": 0}
        
        # Record starting position
        start_scroll = await chrome_client.get_scroll_position()
        start_position = start_scroll.get("scrollTop", 0)
        log.info(f"📏 [DISTANCE] Starting position: {start_position}px")
        
        # Start from the top
        await mcp_client.send_command("Scroll-Tool", {"direction": "up", "wheel_times": 10})
        await asyncio.sleep(2.0)
        
        # Update start position after going to top
        start_scroll = await chrome_client.get_scroll_position()
        start_position = start_scroll.get("scrollTop", 0)
        log.info(f"📏 [DISTANCE] Adjusted start position: {start_position}px")
        
        # PATCH: Screenshot-based end detection with distance tracking
        scroll_attempts = 0
        consecutive_unchanged_screens = 0
        max_unchanged_screens = 5  # Same as original
        last_screenshot_hash = None
        safety_limit = 500
        
        while consecutive_unchanged_screens < max_unchanged_screens and scroll_attempts < safety_limit:
            scroll_attempts += 1
            
            # Take a screenshot to check current content
            screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
            if screenshot_result and hasattr(screenshot_result, 'content') and screenshot_result.content:
                screenshot_data = screenshot_result.content[0].text
                if "Base64 data: " in screenshot_data:
                    base64_data = screenshot_data.split("Base64 data: ")[1]
                    current_screenshot_hash = hash(base64_data[:1000])
                    
                    if last_screenshot_hash and current_screenshot_hash == last_screenshot_hash:
                        consecutive_unchanged_screens += 1
                        log.debug(f"[DISTANCE] Screen unchanged (consecutive: {consecutive_unchanged_screens}/{max_unchanged_screens})")
                        if consecutive_unchanged_screens >= max_unchanged_screens:
                            log.info(f"📏 [DISTANCE] Reached end after {scroll_attempts} scroll attempts (5 unchanged screens)")
                            break
                    else:
                        if last_screenshot_hash:
                            consecutive_unchanged_screens = 0
                            log.debug(f"[DISTANCE] Screen changed, reset unchanged counter")
                        last_screenshot_hash = current_screenshot_hash
            
            # Consistent scroll distance
            scroll_distance = 5
            await mcp_client.send_command("Scroll-Tool", {"direction": "down", "wheel_times": scroll_distance})
            
            # Shorter wait time for faster loading
            await asyncio.sleep(1.5)
            
            # Progress logging
            if scroll_attempts % 10 == 0:
                current_scroll = await chrome_client.get_scroll_position()
                current_position = current_scroll.get("scrollTop", 0)
                distance_so_far = current_position - start_position
                log.info(f"📏 [DISTANCE] Progress: {scroll_attempts} attempts, {distance_so_far}px traveled (unchanged screens: {consecutive_unchanged_screens}/5)")
        
        # Record final position
        end_scroll = await chrome_client.get_scroll_position()
        end_position = end_scroll.get("scrollTop", 0)
        total_distance = end_position - start_position
        
        # Completion messages
        if consecutive_unchanged_screens >= max_unchanged_screens:
            log.info(f"📏 [DISTANCE] Completed distance-measured pre-scroll after {scroll_attempts} attempts")
            log.info(f"📏 [DISTANCE] Total distance traveled: {total_distance}px ({start_position}px → {end_position}px)")
        else:
            log.warning(f"📏 [DISTANCE] Pre-scroll stopped at safety limit ({safety_limit} attempts)")
        
        log.info("📏 [DISTANCE] Distance-measured pre-scroll complete - will return to anchor with precision")
        
        return {
            "scroll_distance_pixels": total_distance,
            "scroll_attempts": scroll_attempts,
            "start_position": start_position,
            "end_position": end_position
        }
        
    except Exception as e:
        log.error(f"❌ [DISTANCE] Error during distance-measured pre-scroll: {e}")
        # Fallback to original method
        log.info("📏 [DISTANCE] Falling back to screenshot-only pre-loading")
        await preload_all_facebook_comments()
        return {"scroll_distance_pixels": 0, "scroll_attempts": 0, "start_position": 0, "end_position": 0}

async def preload_all_facebook_comments():
    """
    PATCH: Aggressive pre-scrolling strategy to load ALL Facebook comments.
    Facebook lazy-loads comments as the user scrolls, so we need to scroll
    completely to the end to ensure all comments are loaded before extraction.
    """
    log.info("📜 Starting aggressive pre-scroll to load all Facebook comments...")
    
    try:
        # Start from the top
        await mcp_client.send_command("Scroll-Tool", {"direction": "up", "wheel_times": 10})
        await asyncio.sleep(2.0)
        
        # PATCH: Only screenshot-based end detection - no artificial maximum
        scroll_attempts = 0
        consecutive_unchanged_screens = 0
        max_unchanged_screens = 5  # PATCH: Reduced from 10 to 5 for faster detection and resource savings
        last_screenshot_hash = None
        
        # PATCH: Screenshot-based end detection with safety limit for extreme cases
        safety_limit = 500  # Safety limit to prevent truly infinite loops (extremely rare)
        while consecutive_unchanged_screens < max_unchanged_screens and scroll_attempts < safety_limit:
            scroll_attempts += 1
            
            # Take a screenshot to check current content
            screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
            if screenshot_result and hasattr(screenshot_result, 'content') and screenshot_result.content:
                screenshot_data = screenshot_result.content[0].text
                if "Base64 data: " in screenshot_data:
                    # PATCH: Screenshot-based end detection - hash the full screenshot
                    import hashlib
                    current_screenshot_hash = hashlib.md5(screenshot_data.encode()).hexdigest()
                    
                    if current_screenshot_hash == last_screenshot_hash:
                        consecutive_unchanged_screens += 1
                        log.debug(f"[PRELOAD] Unchanged screen detected {consecutive_unchanged_screens}/{max_unchanged_screens} times")
                        
                        # Stop after max_unchanged_screens consecutive identical screenshots
                        if consecutive_unchanged_screens >= max_unchanged_screens:
                            log.info(f"🏁 [PRELOAD] Reached end after {scroll_attempts} scroll attempts ({max_unchanged_screens} unchanged screens)")
                            break
                    else:
                        consecutive_unchanged_screens = 0  # Reset counter when screen changes
                        last_screenshot_hash = current_screenshot_hash
                        log.debug(f"[PRELOAD] Screen changed, reset unchanged counter")
            
            # PATCH: Text-based end detection completely removed - using screenshot comparison only
            
            # PATCH: Consistent scroll distance - no need to reduce near "end" since we don't know the end
            scroll_distance = 5  # Consistent aggressive scrolling
            await mcp_client.send_command("Scroll-Tool", {"direction": "down", "wheel_times": scroll_distance})
            
            # Shorter wait time for faster loading, but still allow content to load
            await asyncio.sleep(1.5)
            
            # PATCH: Progress logging without artificial maximum
            if scroll_attempts % 10 == 0:
                log.info(f"📜 [PRELOAD] Scroll progress: {scroll_attempts} attempts (unchanged screens: {consecutive_unchanged_screens}/5)")
        
        # PATCH: Different completion messages based on how the loop ended
        if consecutive_unchanged_screens >= max_unchanged_screens:
            log.info(f"📜 [PRELOAD] Completed pre-scroll after {scroll_attempts} attempts (reached end via screenshot detection)")
        else:
            log.warning(f"📜 [PRELOAD] Pre-scroll stopped at safety limit ({safety_limit} attempts) - may not have reached true end")
        log.info("📜 [PRELOAD] Pre-scroll complete - will return to anchor via template matching")
        
    except Exception as e:
        log.error(f"❌ [PRELOAD] Error during pre-scroll: {e}")
        # Continue anyway - extraction might still work with partial content

async def return_to_anchor_with_distance(scroll_distance_data: Dict[str, Any]) -> bool:
    """
    PATCH: Return to anchor using measured distance for precision guidance.
    Combines distance-based estimation with template matching for robust navigation.
    
    Args:
        scroll_distance_data: Distance data from measure_scroll_distance_and_preload()
        
    Returns:
        bool: True if anchor found, False otherwise
    """
    log.info("📍 [DISTANCE] Returning to anchor using measured distance...")
    
    try:
        chrome_client = await get_chrome_mcp_client()
        
        if not chrome_client.is_connected or scroll_distance_data.get("scroll_distance_pixels", 0) == 0:
            log.warning("⚠️ [DISTANCE] No distance data or Chrome-MCP unavailable, falling back to template-only search")
            return await return_to_alle_kommentare_anchor()
        
        total_distance = scroll_distance_data["scroll_distance_pixels"]
        start_position = scroll_distance_data["start_position"]
        
        # Calculate target position (with 10% buffer to avoid overshooting)
        target_position = start_position + (total_distance * 0.1)
        log.info(f"📍 [DISTANCE] Target position: {target_position}px (10% from start, total distance was {total_distance}px)")
        
        # Phase 1: Fast scroll to estimated target area
        current_scroll = await chrome_client.get_scroll_position()
        current_position = current_scroll.get("scrollTop", 0)
        
        log.info(f"📍 [DISTANCE] Current position: {current_position}px, target: {target_position}px")
        
        # If we're far from target, do large scrolls first
        while current_position > target_position + 500:  # 500px buffer
            await mcp_client.send_command("Scroll-Tool", {"direction": "up", "wheel_times": 10})
            await asyncio.sleep(1.0)
            current_scroll = await chrome_client.get_scroll_position()
            current_position = current_scroll.get("scrollTop", 0)
            log.debug(f"[DISTANCE] Fast scroll: {current_position}px")
        
        log.info(f"📍 [DISTANCE] Near target area ({current_position}px), switching to template search...")
        
        # Phase 2: Template-based search in target area
        scroll_attempts = 0
        consecutive_unchanged_screens = 0
        max_unchanged_screens = 5
        last_screenshot_hash = None
        safety_limit = 50  # Smaller limit since we're in the right area
        
        while consecutive_unchanged_screens < max_unchanged_screens and scroll_attempts < safety_limit:
            scroll_attempts += 1
            
            # Take screenshot and look for anchor
            screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
            if not screenshot_result or not hasattr(screenshot_result, 'content') or not screenshot_result.content:
                log.warning(f"[DISTANCE] Failed to capture screenshot on attempt {scroll_attempts}")
                await asyncio.sleep(1.0)
                continue
                
            screenshot_data = screenshot_result.content[0].text
            if "Base64 data: " not in screenshot_data:
                continue
                
            base64_data = screenshot_data.split("Base64 data: ")[1]
            
            # Screenshot-based top detection
            current_screenshot_hash = hash(base64_data[:1000])
            if last_screenshot_hash and current_screenshot_hash == last_screenshot_hash:
                consecutive_unchanged_screens += 1
                log.debug(f"[DISTANCE] Screen unchanged (consecutive: {consecutive_unchanged_screens}/{max_unchanged_screens})")
                if consecutive_unchanged_screens >= max_unchanged_screens:
                    log.info(f"📍 [DISTANCE] Reached page top after {scroll_attempts} attempts (screenshot-based detection)")
                    break
            else:
                if last_screenshot_hash:
                    consecutive_unchanged_screens = 0
                    log.debug(f"[DISTANCE] Screen changed, reset unchanged counter")
                last_screenshot_hash = current_screenshot_hash
            
            # Look for anchor template
            matches = template_service.match_template_in_base64(
                base64_data,
                "alle-kommentare-confirmed",
                threshold=0.75
            )
            
            if matches:
                best_match = matches[0]
                confidence = best_match.confidence
                log.info(f"🎯 [DISTANCE] Potential anchor found with confidence {confidence:.3f} after {scroll_attempts} attempts")
                
                if confidence >= 0.8:
                    log.info(f"✅ [DISTANCE] High confidence match - accepting as anchor")
                    
                    # Small adjustment scroll to center the anchor nicely
                    await mcp_client.send_command("Scroll-Tool", {"direction": "down", "wheel_times": 2})
                    await asyncio.sleep(1.0)
                    
                    # Log final position
                    final_scroll = await chrome_client.get_scroll_position()
                    final_position = final_scroll.get("scrollTop", 0)
                    log.info(f"📍 [DISTANCE] Successfully returned to anchor at {final_position}px")
                    return True
                else:
                    log.warning(f"⚠️ [DISTANCE] Low confidence {confidence:.3f} - continuing search")
            
            # Moderate scroll distance in target area
            await mcp_client.send_command("Scroll-Tool", {"direction": "up", "wheel_times": 5})
            await asyncio.sleep(1.0)
            
            # Progress logging
            if scroll_attempts % 10 == 0:
                current_scroll = await chrome_client.get_scroll_position()
                current_position = current_scroll.get("scrollTop", 0)
                log.info(f"📍 [DISTANCE] Search progress: {scroll_attempts} attempts at {current_position}px (unchanged screens: {consecutive_unchanged_screens}/{max_unchanged_screens})")
        
        # End messages
        if consecutive_unchanged_screens >= max_unchanged_screens:
            log.warning(f"⚠️ [DISTANCE] Reached page top after {scroll_attempts} attempts - anchor not found in visible area")
            log.info("📍 [DISTANCE] Starting extraction from current position (likely near page top)")
        else:
            log.error(f"❌ [DISTANCE] Anchor search stopped at safety limit ({safety_limit} attempts)")
            log.error("❌ [DISTANCE] Continuing anyway - extraction may start from wrong position")
        return False
        
    except Exception as e:
        log.error(f"❌ [DISTANCE] Error during distance-guided anchor return: {e}")
        log.info("📍 [DISTANCE] Falling back to template-only anchor search")
        return await return_to_alle_kommentare_anchor()

async def return_to_alle_kommentare_anchor():
    """
    Returns to the 'Alle Kommentare' anchor point using template matching.
    Uses the 'alle-kommentare-confirmed.png' template to find the anchor.
    """
    log.info("📍 Searching for 'Alle Kommentare' anchor...")
    
    # PATCH: Screenshot-based top detection - same logic as end detection but upward
    scroll_attempts = 0
    consecutive_unchanged_screens = 0
    max_unchanged_screens = 5  # Stop after 5 consecutive unchanged screenshots (page top reached)
    last_screenshot_hash = None
    safety_limit = 200  # Ultimate safety for extreme cases
    
    try:
        # PATCH: Only stop when anchor is found OR page top reached (screenshot-based)
        while consecutive_unchanged_screens < max_unchanged_screens and scroll_attempts < safety_limit:
            scroll_attempts += 1
            
            # Take screenshot and look for anchor
            screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
            if not screenshot_result or not hasattr(screenshot_result, 'content') or not screenshot_result.content:
                log.warning(f"[ANCHOR] Failed to capture screenshot on attempt {scroll_attempts}")
                await asyncio.sleep(1.0)
                continue
                
            screenshot_data = screenshot_result.content[0].text
            if "Base64 data: " not in screenshot_data:
                continue
                
            base64_data = screenshot_data.split("Base64 data: ")[1]
            
            # PATCH: Screenshot-based top detection - check if content stopped changing
            current_screenshot_hash = hash(base64_data[:1000])  # Hash first 1000 chars for speed
            if last_screenshot_hash and current_screenshot_hash == last_screenshot_hash:
                consecutive_unchanged_screens += 1
                log.debug(f"[ANCHOR] Screen unchanged (consecutive: {consecutive_unchanged_screens}/{max_unchanged_screens})")
                if consecutive_unchanged_screens >= max_unchanged_screens:
                    log.info(f"📍 [ANCHOR] Reached page top after {scroll_attempts} attempts (screenshot-based detection)")
                    break
            else:
                if last_screenshot_hash:  # Only reset if we had a previous hash
                    consecutive_unchanged_screens = 0
                    log.debug(f"[ANCHOR] Screen changed, reset unchanged counter")
                last_screenshot_hash = current_screenshot_hash
            
            # PATCH: Stricter threshold for anchor detection to prevent false positives
            matches = template_service.match_template_in_base64(
                base64_data,
                "alle-kommentare-confirmed",
                threshold=0.75  # Higher threshold for more reliable anchor detection
            )
            
            if matches:
                # PATCH: Double validation to prevent false positives
                best_match = matches[0]
                confidence = best_match.confidence
                log.info(f"🎯 [ANCHOR] Potential anchor found with confidence {confidence:.3f} after {scroll_attempts} attempts")
                
                # Require high confidence for anchor detection
                if confidence >= 0.8:
                    log.info(f"✅ [ANCHOR] High confidence match - accepting as anchor")
                    
                    # Small adjustment scroll to center the anchor nicely
                    await mcp_client.send_command("Scroll-Tool", {"direction": "down", "wheel_times": 2})
                    await asyncio.sleep(1.0)
                    
                    log.info("📍 [ANCHOR] Successfully returned to 'Alle Kommentare' position")
                    return True
                else:
                    log.warning(f"⚠️ [ANCHOR] Low confidence {confidence:.3f} - continuing search for better match")
            
            # PATCH: Larger scroll distance to cover ground faster - was 3, now 8
            await mcp_client.send_command("Scroll-Tool", {"direction": "up", "wheel_times": 8})
            await asyncio.sleep(1.0)
            
            # PATCH: Progress logging with screenshot-based approach
            if scroll_attempts % 10 == 0:
                log.info(f"📍 [ANCHOR] Search progress: {scroll_attempts} attempts (unchanged screens: {consecutive_unchanged_screens}/{max_unchanged_screens})")
        
        # PATCH: Different end messages based on why the loop ended
        if consecutive_unchanged_screens >= max_unchanged_screens:
            log.warning(f"⚠️ [ANCHOR] Reached page top after {scroll_attempts} attempts - anchor not found in visible area")
            log.info("📍 [ANCHOR] Starting extraction from current position (likely near page top)")
        else:
            log.error(f"❌ [ANCHOR] Anchor search stopped at safety limit ({safety_limit} attempts)")
            log.error("❌ [ANCHOR] Continuing anyway - extraction may start from wrong position")
        return False
        
    except Exception as e:
        log.error(f"❌ [ANCHOR] Error during anchor search: {e}")
        return False

async def extract_comments_adaptive() -> list[dict]:
    """
    PATCH: Adaptive extraction strategy - Screenshot->Check->Click->Scroll dynamically.
    No more fixed cycles - adapts to post length and button availability.
    """
    extracted_comments = []
    no_buttons_cycles = 0
    max_no_button_cycles = 5  # Stop after 5 consecutive cycles without buttons
    total_buttons_clicked = 0
    position_counter = 0
    seen_content = set()  # Avoid duplicate content
    
    log.info("📸 [ADAPTIVE] Starting dynamic extraction strategy...")
    
    try:
        await asyncio.sleep(2.0)  # Initial pause
        
        while no_buttons_cycles < max_no_button_cycles:
            position_counter += 1
            log.info(f"🔄 [ADAPTIVE] Position {position_counter} (no-button cycles: {no_buttons_cycles}/{max_no_button_cycles})")
            
            # STEP 1: Extract comments from current position
            current_comments = await extract_visible_comments_ocr(seen_content)
            log.info(f"📝 [ADAPTIVE] Extracted {len(current_comments)} comments from position {position_counter}")
            
            # Add comments to main list (duplicates already filtered by extract_visible_comments_ocr)
            extracted_comments.extend(current_comments)
            
            # STEP 2: Look for expansion buttons in current view
            buttons_found = await find_and_click_expansion_buttons()
            
            if buttons_found:
                log.info(f"✅ [ADAPTIVE] Found and clicked expansion buttons at position {position_counter}")
                total_buttons_clicked += 1
                no_buttons_cycles = 0  # Reset counter - we found buttons!
                
                # Wait for content to load after clicking
                await asyncio.sleep(3.0)
                
                # Extract comments from expanded view
                expanded_comments = await extract_visible_comments_ocr(seen_content)
                log.info(f"📝 [ADAPTIVE] Extracted {len(expanded_comments)} comments from expanded view")
                
                # Add expanded comments to main list (duplicates already filtered)
                extracted_comments.extend(expanded_comments)
                
                # Continue from this position - don't scroll yet, check for more buttons first
                continue
                
            else:
                log.info(f"❌ [ADAPTIVE] No expansion buttons found at position {position_counter}")
                no_buttons_cycles += 1
                
                # STEP 3: Scroll down one step and continue searching
                log.info(f"📜 [ADAPTIVE] Scrolling down 1 step to search for more buttons...")
                await mcp_client.send_command("Scroll-Tool", {"direction": "down", "wheel_times": 1})
                await asyncio.sleep(1.5)  # Shorter wait for single scroll
            
            # Safety: URL validation every 10 positions
            if position_counter % 10 == 0:
                try:
                    original_url = getattr(find_and_click_expansion_buttons, '_original_url', "")
                    url_validation_result = await validate_scan_url(original_url)
                    if not url_validation_result:
                        log.error("🚨 [ADAPTIVE] URL validation failed - stopping extraction")
                        break
                except Exception as e:
                    log.warning(f"⚠️ [ADAPTIVE] URL validation error: {e}")
        
        log.info(f"🏁 [ADAPTIVE] Extraction completed: {total_buttons_clicked} buttons clicked, {len(extracted_comments)} comments extracted")
        return extracted_comments
        
    except Exception as e:
        log.error(f"❌ [ADAPTIVE] Extraction failed: {e}")
        return extracted_comments

async def extract_comments_via_screenshots() -> list[dict]:
    """
    PATCH: Adaptive approach - Screenshot->Check->Click->Scroll dynamically.
    No more fixed cycles - adapts to post length and button availability.
    """
    extracted_comments = []
    no_buttons_cycles = 0
    max_no_button_cycles = 5  # Stop after 5 consecutive cycles without buttons
    total_buttons_clicked = 0
    seen_content = set()  # Avoid duplicate content
    
    log.info("📸 Starting adaptive screenshot + template matching comment extraction...")
    
    try:
        # Initial pause to let page content fully load
        await asyncio.sleep(2.0)  # Extra time for dynamic content to load
        
        # PATCH: Adaptive extraction - no more from top, start from anchor position
        position_counter = 0
        
        while no_buttons_cycles < max_no_button_cycles:
            position_counter += 1
            log.info(f"🔄 [ADAPTIVE] Extraction position {position_counter} (no-button cycles: {no_buttons_cycles}/{max_no_button_cycles})")
            
            # PERIODIC URL VALIDATION: Check every 10 positions
            if position_counter > 0 and position_counter % 10 == 0:
                log.info(f"🔍 Periodic URL validation (position {position_counter})")
                try:
                    original_url = getattr(find_and_click_expansion_buttons, '_original_url', "")
                    url_validation_result = await validate_scan_url(original_url)
                    if not url_validation_result:
                        log.error("🚨 PERIODIC VALIDATION: Profile navigation detected! Stopping extraction.")
                        break
                except Exception as e:
                    log.warning(f"⚠️ Periodic URL validation failed: {e}")
            
            # SAFETY: Dodge then center occasionally to dismiss any persistent popups
            if position_counter % 5 == 0:  # Every 5 positions
                try:
                    await dodge_cursor_then_center(600, 400)
                    log.debug("🛡️ SAFETY: Dodge+Center at position start")
                except Exception as e:
                    log.warning(f"⚠️ Could not perform dodge at position start: {e}")
            
            # Phase 1: Extract visible comments via screenshots + OCR
            cycle_comments = await extract_visible_comments_ocr(seen_content)
            extracted_comments.extend(cycle_comments)
            
            # Phase 2: Look for and click comment expansion buttons
            try:
                expansion_clicked = await find_and_click_expansion_buttons()
            except AbortExtractionError as fatal:
                log.error(f"🛑 ABORTING EXTRACTION: {fatal}")
                break
            
            # FIX 3: Handle the new return values including fallback scroll trigger
            if expansion_clicked == "NEED_SCROLL":
                # Found buttons but all were unsafe (avatar conflicts) - trigger fallback scroll
                log.info("🔄 FALLBACK SCROLL: All expansion buttons were unsafe, attempting scroll to find better positions")
                try:
                    expansion_clicked_after_scroll = await intelligent_scroll_and_search()
                except AbortExtractionError as fatal:
                    log.error(f"🛑 ABORTING EXTRACTION during fallback scroll: {fatal}")
                    break
                
                if expansion_clicked_after_scroll:
                    log.info("✅ Fallback scroll found safer expansion buttons")
                else:
                    no_progress_cycles += 1
                    log.info(f"⚠️ Fallback scroll completed but no buttons clicked (no_progress: {no_progress_cycles})")
            elif not expansion_clicked:
                # No expansion buttons found - use intelligent scrolling to find more
                try:
                    expansion_clicked_after_scroll = await intelligent_scroll_and_search()
                except AbortExtractionError as fatal:
                    log.error(f"🛑 ABORTING AFTER SCROLL: {fatal}")
                    break
                
                if not expansion_clicked_after_scroll:
                    consecutive_no_buttons += 1
                    if consecutive_no_buttons >= 2:
                        log.info("🎯 No expansion buttons found after intelligent scrolling - extraction complete")
                        break
                    else:
                        log.info(f"🔄 No buttons found (attempt {consecutive_no_buttons}/2), continuing...")
                else:
                    consecutive_no_buttons = 0
            else:
                consecutive_no_buttons = 0
            
            # Wait for new content to load after expansion
            if expansion_clicked or cycle_count == 0:
                await asyncio.sleep(random.uniform(2.0, 3.5))
                
                # Move cursor to safe neutral position after content loads
                await mcp_client.send_command("Safe-Center-Move-Tool", {})
                log.debug("🛡️ Reset cursor to safe center position")
            
            cycle_count += 1
        
        log.info(f"📊 Progressive extraction completed: {len(extracted_comments)} total comments across {cycle_count} cycles")
        
        # FINAL CLEANUP PASS - thorough scan for any missed buttons
        log.info("🧹 Starting final cleanup pass to catch any missed expansion buttons...")
        additional_buttons_found = await final_cleanup_pass()
        
        if additional_buttons_found:
            log.info("🔄 Final cleanup found additional buttons - doing one more extraction cycle")
            # One final extraction cycle to capture any newly expanded content
            final_cycle_comments = await extract_visible_comments_ocr(seen_content)
            extracted_comments.extend(final_cycle_comments)
            log.info(f"📊 After final cleanup: {len(extracted_comments)} total comments")
        
        return extracted_comments
        
    except Exception as e:
        log.error(f"❌ Progressive extraction failed: {e}")
        return extracted_comments

async def extract_visible_comments_ocr(seen_content: set) -> list[dict]:
    """Extract comments visible on current screen using OCR."""
    cycle_comments = []
    max_screenshots = 3  # Multiple screenshots per cycle
    
    for screenshot_num in range(max_screenshots):
        log.info(f"📸 Screenshot {screenshot_num + 1}/{max_screenshots} for current view")
        
        # Take screenshot
        screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
        
        # Extract base64 data from Screenshot-Tool response
        screenshot_data = None
        if screenshot_result and hasattr(screenshot_result, 'content') and screenshot_result.content:
            content_text = screenshot_result.content[0].text if screenshot_result.content else ""
            if "Base64 data: " in content_text:
                screenshot_data = content_text.split("Base64 data: ")[-1]
        
        if not screenshot_data:
            log.warning("⚠️ Screenshot failed - could not extract base64 data")
            continue
        
        # Extract text using OCR
        from .ocr_service import ocr_service
        ocr_result = ocr_service.extract_text_from_base64(screenshot_data)
        ocr_text = ocr_result.get('text', '') if ocr_result else ''
        
        if not ocr_text or len(ocr_text.strip()) < 50:
            log.warning("⚠️ OCR returned minimal text, skipping")
            continue
        
        # Check for duplicate content
        content_hash = hash(ocr_text[:200])
        if content_hash in seen_content:
            log.debug(f"🔄 Duplicate content detected in screenshot {screenshot_num + 1}")
            continue
        seen_content.add(content_hash)
        
        # Parse comments from OCR text
        parsed_comments = parse_comments_from_ocr(ocr_text)
        if parsed_comments:
            cycle_comments.extend(parsed_comments)
            log.info(f"📝 Extracted {len(parsed_comments)} comments from screenshot {screenshot_num + 1}")
        
        # Small scroll between screenshots to capture different content
        if screenshot_num < max_screenshots - 1:
            await mcp_client.send_command("Scroll-Tool", {"direction": "down", "wheel_times": 2})
            await asyncio.sleep(0.8)
    
    return cycle_comments

async def validate_scan_url(original_url: str) -> bool:
    """
    Validates that we're still on the original scan URL and not accidentally navigated to a profile.
    Returns True if we're on the correct URL, False if we've navigated away.
    """
    try:
        # Take a screenshot and check for URL indicators in the address bar
        screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
        
        if not screenshot_result or not screenshot_result.content:
            log.warning("⚠️ URL validation failed - could not take screenshot")
            return True  # Assume OK if we can't check
        
        # Extract base64 image data
        screenshot_data = None
        for content in screenshot_result.content:
            if hasattr(content, 'text') and 'base64,' in content.text:
                screenshot_data = content.text.split('base64,')[1]
                break
        
        if not screenshot_data:
            log.warning("⚠️ URL validation failed - could not extract screenshot data")
            # Try using Get-URL-Tool to check current URL directly
            try:
                url_result = await mcp_client.send_command("Get-Active-URL-Tool", {})
                if url_result and hasattr(url_result, 'content') and url_result.content:
                    current_url = ""
                    for content in url_result.content:
                        if hasattr(content, 'text'):
                            current_url = content.text.strip()
                            break
                    
                    # If URL doesn't match expected post URL, we've navigated away
                    if current_url and original_url:
                        if original_url not in current_url and "posts/pfbid" not in current_url:
                            log.error(f"🚨 URL MISMATCH DETECTED! Expected: {original_url}, Current: {current_url}")
                            return False
                            
                log.info("✅ URL validation passed (direct URL check)")
                return True
            except Exception as url_error:
                log.warning(f"⚠️ Could not validate URL: {url_error}")
                return False  # Conservative: fail if we can't validate
        
        # Use OCR to read the screen content
        from .ocr_service import ocr_service
        screenshot_text = ocr_service.extract_text_from_base64(screenshot_data)
        
        # Check for profile page indicators (more comprehensive list)
        profile_indicators = [
            "freund/in hinzufügen", "freund hinzufügen", "nachricht senden",
            "add friend", "send message", "message", "anfrage senden",
            "follow", "folgen", "unfollow", "entfolgen",
            "timeline", "chronik", "about", "über", "friends", "freunde",
            "photos", "fotos", "bilder", "more", "mehr",
            "arbeitet bei", "works at", "lives in", "wohnt in",
            "studied at", "hat studiert", "went to", "ging auf",
            "relationship", "beziehung", "single", "in einer beziehung",
            "lagerarbeiter", "kommissionierer", "hochregalstaplerfahrer",  # Specific to logs
            "ist hier aufgewachsen", "ist hier zur schule gegangen",  # Profile-specific phrases
            "hat als", "hat studiert", "arbeitet als"  # Work/education indicators
        ]
        
        # Also check if we're NOT on a post URL anymore
        post_indicators = [
            "/posts/", "/post/", "pfbid", "facebook.com/",
            "kommentare", "comments", "gefällt mir", "like",
            "teilen", "share", "antworten", "replies"
        ]
        
        screenshot_lower = screenshot_text.lower()
        
        # Check for profile indicators
        profile_detected = any(indicator in screenshot_lower for indicator in profile_indicators)
        
        # Check for post indicators (should be present on correct page)
        post_detected = any(indicator in screenshot_lower for indicator in post_indicators)
        
        # Get current URL to check if we've navigated to a profile
        try:
            url_result = await mcp_client.send_command("Get-URL-Tool", {})
            current_url = ""
            if url_result and hasattr(url_result, 'content') and url_result.content:
                for content in url_result.content:
                    if hasattr(content, 'text'):
                        current_url = content.text.strip()
                        break
            
            # Check if current URL looks like a profile URL
            if current_url and ("facebook.com/profile.php" in current_url or 
                               ("/people/" in current_url) or
                               (current_url.count("/") == 3 and "posts" not in current_url and "pfbid" not in current_url)):
                log.error(f"🚨 PROFILE URL DETECTED! Current URL: {current_url}")
                log.error("🚨 We have navigated to a profile page instead of staying on the post")
                return False
                
        except Exception as url_error:
            log.warning(f"⚠️ Could not get current URL for validation: {url_error}")
        
        # More aggressive detection: if we see strong profile indicators, it's a profile page
        strong_profile_indicators = [
            "freund/in hinzufügen", "freund hinzufügen", "nachricht senden",
            "add friend", "send message", "anfrage senden",
            "ist hier aufgewachsen:", "hat als", "arbeitet als",  # Profile info patterns
            "wohnt in", "studiert", "hat studiert", "zur schule gegangen"  # More profile patterns
        ]
        
        strong_profile_detected = any(indicator in screenshot_lower for indicator in strong_profile_indicators)
        
        if strong_profile_detected:
            global profile_navigation_failures, safety_mode_until
            profile_navigation_failures += 1
            found_indicators = [ind for ind in strong_profile_indicators if ind in screenshot_lower]
            log.error(f"🚨 STRONG PROFILE NAVIGATION DETECTED! Found indicators: {found_indicators}")
            log.error(f"🚨 This means we accidentally clicked on a username/profile link (failure #{profile_navigation_failures})")
            
            # Activate safety mode if too many failures
            if profile_navigation_failures >= MAX_PROFILE_NAVIGATION_FAILURES:
                safety_mode_until = datetime.now() + timedelta(minutes=5)
                log.error(f"🚨 CIRCUIT BREAKER ACTIVATED! Too many profile navigation failures. No expansion clicks for 5 minutes.")
                log.error(f"🚨 Safety mode until: {safety_mode_until}")
            
            return False
        
        # Weaker check: if many profile indicators but no post indicators  
        if profile_detected and not post_detected:
            found_indicators = [ind for ind in profile_indicators if ind in screenshot_lower]
            log.error(f"🚨 PROFILE NAVIGATION DETECTED! Found indicators: {found_indicators}")
            log.error("🚨 This means we accidentally clicked on a username/profile link")
            return False
        
        # Additional check: look for the original post ID in the current screen
        if "pfbid" in original_url:
            post_id = original_url.split("pfbid")[1].split("/")[0][:20]  # First 20 chars of post ID
            if post_id not in screenshot_text:
                log.warning(f"⚠️ Original post ID '{post_id}' not found in current screen - might have navigated away")
                # Don't return False here as post ID might not always be visible
        
        log.debug("✅ URL validation passed - still on correct page")
        return True
        
    except Exception as e:
        log.error(f"❌ URL validation error: {e}")
        # Be strict during extraction; let callers decide to abort
        return False

async def find_and_click_expansion_buttons() -> bool:
    """Find and click comment expansion buttons using template matching."""
    log.info("🔍 Looking for comment expansion buttons...")
    
    # Check if we're in safety mode due to profile navigation failures
    global safety_mode_until
    if safety_mode_until and datetime.now() < safety_mode_until:
        log.warning(f"🛡️ SAFETY MODE ACTIVE: Skipping expansion clicks until {safety_mode_until}")
        log.warning("🛡️ This is to prevent further accidental profile navigation")
        return False
    
    try:
        # Look for all types of expansion buttons
        expansion_templates = ["alle-xx-kommentare-ansehen", "Antwort-ansehen"]
        buttons_clicked = 0
        total_matches_found = 0  # Track total template matches for fallback logic
        
        # PATCH: Deduping seen boxes (avoid re-clicking same button)
        seen_boxes: set[tuple[int, int]] = set()

        for template_name in expansion_templates:
            # Take screenshot for template matching
            screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
            
            # Extract base64 data from Screenshot-Tool response
            screenshot_data = None
            if screenshot_result and hasattr(screenshot_result, 'content') and screenshot_result.content:
                content_text = screenshot_result.content[0].text if screenshot_result.content else ""
                if "Base64 data: " in content_text:
                    screenshot_data = content_text.split("Base64 data: ")[-1]
            
            if not screenshot_data:
                log.warning("⚠️ Screenshot failed for template matching")
                continue
            
            # Find template matches
            from .template_service import template_service
            # PATCH: Higher thresholds to prevent false-positives on profiles (especially after martin.eder case)
            detection_threshold = 0.75 if template_name == "Antwort-ansehen" else 0.7  # PATCH: Increased thresholds significantly
            matches = template_service.match_template_in_base64(screenshot_data, template_name, threshold=detection_threshold)
            
            if matches:
                total_matches_found += len(matches)  # Track total matches for fallback logic
                log.info(f"🎯 Found {len(matches)} '{template_name}' buttons")
                
                # CRITICAL: Sort matches by Y coordinate (top to bottom) to prevent hover popups blocking lower buttons
                matches_sorted = sorted(matches, key=lambda m: m.center[1])
                log.info(f"📍 Sorted {len(matches_sorted)} buttons from top to bottom")
                
                # DIAGNOSTIC: Analyze the area around each match before clicking
                for i, match in enumerate(matches_sorted[:3]):  # Limit to first 3 to avoid spam
                    x, y = int(match.center[0]), int(match.center[1])
                    w, h = int(match.size[0]), int(match.size[1])
                    confidence = match.confidence if hasattr(match, 'confidence') else 'unknown'

                    # PATCH: dedupe key (bucketize by 8px)
                    box_key = (x // 8, y // 8)
                    if box_key in seen_boxes:
                        log.debug(f"[DEDUP] Skipping already seen button box at ~({x},{y})")
                        continue
                    seen_boxes.add(box_key)
                    
                    log.info(f"🔍 DIAGNOSTIC: Template match {i+1} for '{template_name}':")
                    log.info(f"   📍 Raw center: ({x}, {y})")
                    log.info(f"   📐 Size: {w}x{h}")
                    log.info(f"   🎯 Confidence: {confidence}")
                    
                    # PATCH: Pre-validate that template match is within modal bounds before processing
                    # Get current modal bounds
                    iw = ih = None
                    try:
                        screenshot_result_sz = await mcp_client.send_command("Screenshot-Tool", {})
                        if screenshot_result_sz and hasattr(screenshot_result_sz, 'content') and screenshot_result_sz.content:
                            content_text_sz = screenshot_result_sz.content[0].text
                            if "Base64 data: " in content_text_sz:
                                import base64, cv2, numpy as np
                                b64 = content_text_sz.split("Base64 data: ")[-1]
                                img_bytes = base64.b64decode(b64)
                                img_arr = np.frombuffer(img_bytes, dtype=np.uint8)
                                img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                                ih, iw = (img.shape[0], img.shape[1]) if img is not None else (None, None)
                    except Exception:
                        pass
                    
                    if iw and ih and iw > 0 and ih > 0:
                        modal_left = int(iw * 0.20)
                        modal_right = int(iw * 0.80)
                        modal_top = int(ih * 0.15)
                        modal_bottom = int(ih * 0.88)
                    else:
                        modal_left, modal_right, modal_top, modal_bottom = 300, 1200, 200, 700
                    
                    # Check if template center is outside modal bounds
                    if not (modal_left <= x <= modal_right and modal_top <= y <= modal_bottom):
                        log.warning(f"⚠️ SKIPPING: Template match at ({x}, {y}) is outside modal bounds ({modal_left}-{modal_right}, {modal_top}-{modal_bottom})")
                        continue  # Skip this button entirely
                    
                    # DIAGNOSTIC: Extract and analyze text around the click area
                    try:
                        screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
                        if screenshot_result and hasattr(screenshot_result, 'content') and screenshot_result.content:
                            content_text = screenshot_result.content[0].text if screenshot_result.content else ""
                            if "Base64 data: " in content_text:
                                screenshot_data = content_text.split("Base64 data: ")[-1]
                                from .ocr_service import ocr_service
                                ocr_result = ocr_service.extract_text_from_base64(screenshot_data)
                                ocr_text = ocr_result.get('text', '') if ocr_result else ''
                                
                                # Extract text in a 200x200 pixel area around the click point
                                log.info(f"🔍 DIAGNOSTIC: OCR text around click area ({x-100}, {y-100}) to ({x+100}, {y+100}):")
                                log.info(f"   📝 Full OCR text sample: {ocr_text[:200]}...")
                                
                                # Check for profile indicators near the click area
                                profile_words = ['freund', 'nachricht', 'lagerarbeiter', 'kommissionierer', 'hinzufügen', 'senden']
                                detected_profile_words = [word for word in profile_words if word in ocr_text.lower()]
                                if detected_profile_words:
                                    log.warning(f"⚠️ DIAGNOSTIC: Profile-related words detected near click area: {detected_profile_words}")
                    except Exception as diag_error:
                        log.warning(f"⚠️ DIAGNOSTIC: Failed to analyze click area: {diag_error}")
                    
                    # Apply ULTRA-CONSERVATIVE click positioning to avoid usernames
                    original_x, original_y = x, y
                    if template_name in ("Antwort-ansehen",):
                        # PATCH: Increase offset to avoid usernames for reply links - was too conservative
                        left_offset = int(min(max(w * 0.4, 20), 80))  # Increased from 0.15 to 0.4
                        x = x - left_offset
                        log.info(f"🔧 FIXED: Applied larger left_offset={left_offset}px (Antwort-ansehen), moved from ({original_x}, {original_y}) to ({x}, {y})")
                        
                        # Re-validate after offset - if now outside bounds, skip this button
                        if not (modal_left <= x <= modal_right and modal_top <= y <= modal_bottom):
                            log.warning(f"⚠️ SKIPPING: After offset, click position ({x}, {y}) is outside modal bounds")
                            continue
                    else:
                        # FIX 1: Horizontal bias for 'alle-xx-kommentare-ansehen' to avoid avatars
                        # Click 60% from left edge (40% from right) to avoid usernames/avatars on the left
                        # PATCH: Fix MatchResult attribute access - use .location instead of .left
                        x = int(match.location[0] + w * 0.6)  # Biased towards right side of button
                        y = int(match.center[1])
                        log.info(f"🔧 FIXED: Using right-biased click for '{template_name}' at ({x}, {y}) [60% from left edge]")
                        
                        # Enhanced left edge safety with higher threshold for modal-aware positioning
                        modal_safety_margin = modal_left + 200 if 'modal_left' in locals() else 500
                        if x < modal_safety_margin:
                            # PATCH: Fix MatchResult attribute access - use .location instead of .left
                            x = int(match.location[0] + w * 0.8)  # Move even further right
                            log.warning(f"⚠️ ENHANCED SAFETY: Moved to 80% from left edge: ({x}, {y})")
                            
                        # Final fallback: if still too close, use right edge of button
                        if x < modal_safety_margin:
                            # PATCH: Fix MatchResult attribute access - calculate right edge from location + size
                            x = int(match.location[0] + w - 20)  # 20px from right edge of button
                            log.warning(f"⚠️ FINAL FALLBACK: Moved to right edge of button: ({x}, {y})")
                    
                    log.info(f"🖱️ FINAL CLICK POSITION: button {i+1} at ({x}, {y}) [moved {x-original_x}px from original]")
                    
                    # FIX 2: Enhanced OCR safety check - scan 120x40px area around click target
                    try:
                        screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
                        if screenshot_result and hasattr(screenshot_result, 'content') and screenshot_result.content:
                            content_text = screenshot_result.content[0].text if screenshot_result.content else ""
                            if "Base64 data: " in content_text:
                                screenshot_data = content_text.split("Base64 data: ")[-1]
                                
                                # Extract text in 120x40 area around click point for profile detection
                                from .ocr_service import ocr_service
                                import base64, cv2, numpy as np
                                
                                try:
                                    img_bytes = base64.b64decode(screenshot_data)
                                    img_arr = np.frombuffer(img_bytes, dtype=np.uint8)
                                    img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                                    
                                    # Crop 120x40 area around click point
                                    crop_x1 = max(0, x - 60)
                                    crop_y1 = max(0, y - 20)
                                    crop_x2 = min(img.shape[1], x + 60)
                                    crop_y2 = min(img.shape[0], y + 20)
                                    cropped_img = img[crop_y1:crop_y2, crop_x1:crop_x2]
                                    
                                    # Convert cropped area back to base64 for OCR
                                    _, buffer = cv2.imencode('.png', cropped_img)
                                    cropped_b64 = base64.b64encode(buffer).decode('utf-8')
                                    
                                    # OCR the cropped area only
                                    crop_ocr_result = ocr_service.extract_text_from_base64(cropped_b64)
                                    crop_text = _normalize(crop_ocr_result.get('text', '') if crop_ocr_result else '')
                                    
                                    # PATCH: Enhanced profile detection keywords including names and time indicators
                                    profile_indicators = [
                                        # Profile actions
                                        'freund', 'nachricht', 'senden', 'hinzufügen', 'friend', 'message',
                                        'follow', 'folgen', 'abonnieren', 'subscribe',
                                        # Job titles/profile descriptions that appear near profile links
                                        'lagerarbeiter', 'kommissionierer', 'arbeiter', 'angestellte', 'manager',
                                        'bei', 'arbeitet bei', 'works at', 'studiert', 'studies',
                                        # Common German names (from the error case: martin.eder.906)
                                        'martin', 'eder', 'thomas', 'michael', 'andreas', 'stefan', 'christian',
                                        'peter', 'wolfgang', 'alexander', 'daniel', 'matthias', 'florian',
                                        'markus', 'simon', 'johannes', 'manuel', 'david', 'sebastian',
                                        'maria', 'anna', 'julia', 'lisa', 'sarah', 'nicole', 'sandra',
                                        'claudia', 'andrea', 'katharina', 'petra', 'sabine', 'christina',
                                        # Time indicators near profile links (only longer words to avoid false positives)
                                        'std', 'min', 'tag', 'woche', 'monat', 'stunden', 'minuten', 'tage',
                                        'vor', 'ago', 'vor', 'gestern', 'yesterday', '·', '•',
                                        # Profile indicators
                                        '@', '€', 'eur', 'profile', 'profil', 'user', 'person'
                                    ]
                                    
                                    # PATCH: Positive whitelist for known button texts
                                    crop_text_lower = crop_text.lower()
                                    valid_button_phrases = [
                                        'antwort', 'antworten', 'ansehen', 'kommentar', 'kommentare', 
                                        'alle', 'weitere', 'mehr', 'zeigen', 'show', 'view',
                                        'reply', 'replies', 'comment', 'comments', 'see', 'see more'
                                    ]
                                    
                                    # If crop contains clear button text, skip profile detection entirely
                                    is_valid_button = any(phrase in crop_text_lower for phrase in valid_button_phrases)
                                    if is_valid_button:
                                        log.info(f"✅ OCR SAFETY: Valid button text detected: '{crop_text[:50]}' - skipping profile check")
                                    else:
                                        # Only check for profile indicators if it's not a clear button
                                        detected_indicators = []
                                        for word in profile_indicators:
                                            if len(word) <= 2:  # Skip single letters/short words
                                                continue
                                            if word in crop_text_lower:
                                                detected_indicators.append(word)
                                    
                                        if detected_indicators:
                                            log.warning(f"🚨 OCR SAFETY: Profile indicators detected in click area: {detected_indicators}")
                                            log.warning(f"🚨 Crop text: '{crop_text[:100]}...'")
                                            log.warning(f"🚨 SKIPPING button {i+1} to avoid profile click!")
                                            continue  # Skip this button entirely
                                        else:
                                            log.info(f"✅ OCR SAFETY: Click area clean, crop text: '{crop_text[:50]}...'")
                                        
                                except Exception as crop_error:
                                    log.debug(f"Crop OCR failed, using full screenshot: {crop_error}")
                                    # Fallback to full screenshot OCR with original logic
                                    ocr_result = ocr_service.extract_text_from_base64(screenshot_data)
                                    ocr_text = _normalize(ocr_result.get('text', '') if ocr_result else '')
                                    profile_words = ['freund/in hinzufügen', 'nachricht senden', 'freund/in hinzu', 'nachricht send']
                                    detected_profile_words = [word for word in profile_words if word in ocr_text]
                                    if detected_profile_words:
                                        log.warning(f"🚨 FALLBACK SAFETY: Profile phrases detected: {detected_profile_words}")
                                        continue
                                    
                    except Exception as safety_error:
                        log.warning(f"⚠️ Enhanced safety check failed, proceeding with caution: {safety_error}")
                    
                    # IMMEDIATE safety - move to safe modal position before clicking
                    try:
                        await move_cursor_to_safe_modal_area(x, y)
                        log.debug("🛡️ Pre-click: Moved to safe modal position")
                    except Exception as pre_click_error:
                        log.warning(f"⚠️ Pre-click modal positioning failed: {pre_click_error}")
                        # Fallback to Safe-Center-Move-Tool if modal positioning fails
                        await mcp_client.send_command("Safe-Center-Move-Tool", {})
                    await asyncio.sleep(0.2)  # Brief pause
                    
                    # Move to click position and click quickly
                    # Percentage-based clamp within modal, keep within template box vertically if clamp would push out
                    # Derive current screenshot size for percent clamp
                    iw = ih = None
                    try:
                        screenshot_result_sz = await mcp_client.send_command("Screenshot-Tool", {})
                        if screenshot_result_sz and hasattr(screenshot_result_sz, 'content') and screenshot_result_sz.content:
                            content_text_sz = screenshot_result_sz.content[0].text
                            if "Base64 data: " in content_text_sz:
                                import base64, cv2
                                import numpy as np
                                b64 = content_text_sz.split("Base64 data: ")[-1]
                                img_bytes = base64.b64decode(b64)
                                img_arr = np.frombuffer(img_bytes, dtype=np.uint8)
                                img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                                ih, iw = (img.shape[0], img.shape[1]) if img is not None else (None, None)
                    except Exception:
                        pass
                    cx, cy = clamp_to_modal(x, y, image_width=iw, image_height=ih)
                    log.debug(f"[CLAMP_LOG] pre=({x},{y}) size=({iw}x{ih}) clamped=({cx},{cy})")
                    # Keep Y within template box if clamped outside vertically
                    top_t = original_y - int(h / 2)
                    bot_t = original_y + int(h / 2)
                    if cy < top_t or cy > bot_t:
                        cy = original_y
                        log.info(f"[TEMPLATE_BOX] Adjusted Y from {y} to template center {cy}")
                    x, y = cx, cy
                    await mcp_client.send_command("Move-Tool", {"to_loc": [x, y]})
                    try:
                        globals()["last_move_point"] = (x, y)
                        globals()["last_move_at"] = datetime.now()
                        log.debug(f"[MOVE_LOG] Move-Tool to ({x},{y}) at {last_move_at}")
                    except Exception:
                        pass
                    await asyncio.sleep(random.uniform(0.05, 0.1))  # Minimal pause before click
                    await mcp_client.send_command("Click-Tool", {"loc": [x, y]})
                    log.debug(f"[MOVE_LOG] Click-Tool at ({x},{y}) immediately after move")
        
                    # IMMEDIATE dodge after click to exit any hover popup before further checks
                    try:
                        await dodge_cursor_then_center(x, y)
                        log.info(f"🛡️ SAFETY: Immediate Dodge+Center applied after click for button {i+1}")
                    except Exception as e:
                        log.warning(f"[DODGE] immediate dodge failed: {e}")
                    
                    # Allow UI to react a bit
                    await asyncio.sleep(0.2)
                    # Detect reply-input misclick around click area; if detected, skip this button
                    try:
                        screenshot_result_reply = await mcp_client.send_command("Screenshot-Tool", {})
                        if screenshot_result_reply and hasattr(screenshot_result_reply, 'content') and screenshot_result_reply.content:
                            sdata = screenshot_result_reply.content[0].text
                            if "Base64 data: " in sdata:
                                from .ocr_service import ocr_service
                                ocr_res = ocr_service.extract_text_from_base64(sdata.split("Base64 data: ")[-1])
                                otext = (ocr_res.get('text', '') or '').lower()
                                if any(key in otext for key in ["antworten", "antwort", "reply", "repl"]):
                                    log.warning("[MISCLICK] Reply input detected after click; skipping this button and continuing")
                                    continue
                    except Exception:
                        pass
                    await asyncio.sleep(0.1)

                    buttons_clicked += 1
                    log.info(f"🖱️ Successfully clicked expansion button {i+1}")
                    
                    # IMMEDIATE VALIDATION: Check if we accidentally clicked a profile link or modal closed
                    await asyncio.sleep(1.0)  # Longer wait for page to respond properly
                    try:
                        original_url = getattr(find_and_click_expansion_buttons, '_original_url', "")
                        
                        # CRITICAL: Check URL first to catch modal closures and profile navigation
                        url_result = await mcp_client.send_command("Get-Active-URL-Tool", {})
                        current_url = ""
                        if url_result and hasattr(url_result, 'content') and url_result.content:
                            for content in url_result.content:
                                if hasattr(content, 'text'):
                                    current_url = content.text.strip()
                                    break
                        
                        # If URL changed significantly, stop immediately
                        if current_url and original_url:
                            if (original_url not in current_url and 
                                "posts/pfbid" not in current_url and 
                                "facebook.com" in current_url):
                                log.error(f"🚨 URL CHANGED! Original: {original_url}")
                                log.error(f"🚨 Current: {current_url}")
                                log.error("🚨 Modal closed or navigated away - STOPPING SCAN!")
                                try:
                                    globals()["hard_stop_active"] = True
                                    log.error("🛑 HARD-STOP ACTIVATED (URL change)")
                                except Exception:
                                    pass
                                raise AbortExtractionError("URL changed - abort extraction")
                        
                        immediate_validation = await validate_scan_url(original_url)
                        if not immediate_validation:
                            log.error(f"🚨 IMMEDIATE VALIDATION FAILED after button {i+1} click! Profile navigation detected.")
                            log.error("🚨 ABORTING ALL FURTHER EXPANSION CLICKS TO PREVENT MORE PROFILE NAVIGATION!")
                            try:
                                globals()["hard_stop_active"] = True
                                log.error("🛑 HARD-STOP ACTIVATED (immediate validation failed)")
                            except Exception:
                                pass
                            raise AbortExtractionError("Profile navigation detected - abort extraction")
                    except AbortExtractionError:
                        # Propagate fatal abort
                        raise
                    except Exception as e:
                        log.warning(f"⚠️ Immediate validation failed: {e}")
                    
                    # ENHANCED VALIDATION: Comprehensive URL/page validation
                    try:
                        # Get the original URL from the global context - if not available, skip URL-specific checks
                        original_url = getattr(find_and_click_expansion_buttons, '_original_url', "")
                        url_validation_result = await validate_scan_url(original_url)
                        if not url_validation_result:
                            log.error("🚨 COMPREHENSIVE PROFILE NAVIGATION DETECTED! Stopping extraction immediately.")
                            raise AbortExtractionError("Comprehensive validation failed - abort extraction")
                    except AbortExtractionError:
                        # Propagate fatal abort
                        raise
                    except Exception as e:
                        log.debug(f"Could not validate page content: {e}")
                    
                    # Extended pause between clicks for maximum safety
                    await asyncio.sleep(random.uniform(2.5, 4.0))
        
        # FIX 3: Return different values to distinguish between success, no buttons, and need for fallback scroll
        if buttons_clicked > 0:
            log.info(f"✅ Successfully clicked {buttons_clicked} expansion buttons")
            return True
        else:
            # Check if we found templates but skipped them all due to safety concerns
            if total_matches_found > 0:
                log.warning(f"⚠️ Found {total_matches_found} expansion button templates but skipped all due to safety concerns (likely avatar/profile conflicts)")
                log.info("🔄 FALLBACK NEEDED: Recommending scroll to find safer button positions")
                return "NEED_SCROLL"  # Special return value to trigger fallback scroll
            else:
                log.info("ℹ️ No expansion buttons found in current view")
                return False
            
    except AbortExtractionError as fatal:
        log.error(f"🛑 FATAL: {fatal}")
        raise
    except Exception as e:
        log.error(f"❌ Error finding expansion buttons: {e}")
        return False

def parse_comments_from_ocr(ocr_text: str) -> list[dict]:
    """Parse comment data from OCR extracted text."""
    comments = []
    
    try:
        lines = ocr_text.split('\n')
        current_comment = {}
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Look for author names (usually short lines before content)
            if len(line) < 50 and not any(char.isdigit() for char in line) and line.count(' ') <= 3:
                # Possible author name
                if current_comment:
                    if current_comment.get('content'):
                        comments.append(current_comment)
                
                current_comment = {
                    'author': line,
                    'content': '',
                    'timestamp': '',
                    'reactions': '',
                    'source': 'screenshot_ocr'
                }
            
            # Look for timestamps
            elif any(pattern in line.lower() for pattern in ['min', 'std', 'tag', 'woche', 'monat', 'jahr', 'h ', 'm ', 'd ']):
                if current_comment:
                    current_comment['timestamp'] = line
            
            # Look for reaction indicators
            elif any(reaction in line.lower() for reaction in ['gefällt', 'like', 'love', 'antworten', 'reply']):
                if current_comment:
                    current_comment['reactions'] = line
            
            # Everything else is likely comment content
            else:
                if current_comment:
                    if current_comment['content']:
                        current_comment['content'] += ' ' + line
                    else:
                        current_comment['content'] = line
        
        # Don't forget the last comment
        if current_comment and current_comment.get('content'):
            comments.append(current_comment)
        
        # Filter out very short or invalid comments
        valid_comments = []
        for comment in comments:
            if (comment.get('content', '').strip() and 
                len(comment['content'].strip()) > 10 and
                comment.get('author', '').strip()):
                valid_comments.append(comment)
        
        log.info(f"📝 Parsed {len(valid_comments)} valid comments from OCR text")
        return valid_comments
        
    except Exception as e:
        log.error(f"❌ Comment parsing failed: {e}")
        return []

async def get_dom() -> str:
    """Gets the full page DOM from the browser."""
    await _rate_limited("get_dom")
    log.info("[MCP] Getting DOM...")
    call_result = await mcp_client.send_command("Get-Browser-DOM-Tool")
    
    # The actual DOM content is in the 'data' attribute of the CallToolResult object.
    dom_content = call_result.data if hasattr(call_result, 'data') else ''
    
    if not dom_content:
        # Retry once after toggling focus to the page to avoid empty captures
        try:
            await mcp_client.send_command("Safe-Center-Move-Tool", {})
            await asyncio.sleep(0.2)
        except Exception:
            pass
        call_result = await mcp_client.send_command("Get-Browser-DOM-Tool")
        dom_content = call_result.data if hasattr(call_result, 'data') else ''
    log.info(f"[MCP] Received DOM (length: {len(dom_content or '')}).")
    
    # If we have extracted comments from screenshots, include them in the DOM
    global extracted_comment_data
    if extracted_comment_data:
        log.info(f"[DOM] Appending {len(extracted_comment_data)} screenshot-extracted comments to DOM")
        
        # Create a structured comments section
        comments_html = "\n<!-- SCREENSHOT-EXTRACTED COMMENTS -->\n"
        comments_html += "<div class='screenshot-extracted-comments'>\n"
        
        for i, comment in enumerate(extracted_comment_data):
            comments_html += f"  <div class='comment' data-source='screenshot-ocr' data-index='{i}'>\n"
            comments_html += f"    <div class='author'>{comment.get('author', 'Unknown')}</div>\n"
            comments_html += f"    <div class='content'>{comment.get('content', '')}</div>\n"
            comments_html += f"    <div class='timestamp'>{comment.get('timestamp', '')}</div>\n"
            comments_html += f"    <div class='reactions'>{comment.get('reactions', '')}</div>\n"
            comments_html += f"  </div>\n"
        
        comments_html += "</div>\n"
        comments_html += "<!-- END SCREENSHOT-EXTRACTED COMMENTS -->\n"
        
        # Append to DOM content
        if dom_content:
            dom_content += comments_html
        else:
            # If DOM is empty (Facebook blocked), return only our extracted comments
            dom_content = f"<html><body>{comments_html}</body></html>"
        
        log.info(f"[DOM] Enhanced DOM with screenshot comments (new length: {len(dom_content)})")
    
    return dom_content

async def scroll_page(wheel_times: int = 2):
    """Scrolls the page down by a specified amount."""
    await _rate_limited("scroll")
    # Try to ensure the browser content has focus before scrolling
    try:
        # Avoid clicking to focus. Only adjust focus and move within modal area.
        await mcp_client.send_command("Deselect-Close-Tool", {})
        await asyncio.sleep(0.1)
        await move_cursor_to_safe_modal_area(600, 400)
        await asyncio.sleep(0.3)
    except Exception:
        pass
    wt = max(1, min(2, wheel_times))
    log.info(f"Scrolling page down by {wt} wheel times.")
    await mcp_client.send_command("Scroll-Tool", {"direction": "down", "wheel_times": wt})
    log.info("Scroll complete.")

async def click(coords: list[int]):
    await _rate_limited("click")
    log.info(f"Clicking at coordinates: {coords}")
    # Use MCP safe click which refuses backdrop clicks and retries slightly inward
    x, y = coords[0], coords[1]
    result = await mcp_client.send_command('Click-Tool', {'loc': [x, y], 'button': 'left', 'clicks': 1})
    if isinstance(result, str) and 'Refused to click outside' in result:
        result = await mcp_client.send_command('Click-Tool', {'loc': [x-5, y-5], 'button': 'left', 'clicks': 1})
    log.info(f"Click result: {result}")



async def screenshot_fullscreen(save_path: str) -> str:
    await _rate_limited("screenshot_fullscreen")
    log.info(f"Taking fullscreen screenshot and saving to {save_path}")
    # result = await mcp_client.send_command("screenshot", {"path": save_path})
    # return result['path']
    log.info(f"[SIMULATE] Taking screenshot to {save_path}")
    await asyncio.sleep(1)
    return save_path

async def close():
    log.info("Closing MCP client connection.")
    await mcp_client.client.close()
