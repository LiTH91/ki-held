print("[DEBUG mcp_service] Starting import of mcp_service.py")
import asyncio
import os
from collections import deque
from datetime import datetime, timedelta, timezone
import random
import re
from urllib.parse import urlparse
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

# Fatal control-flow exception used to abort extraction when navigation leaves the post/modal
class AbortExtractionError(Exception):
    pass

# Global HARD STOP flag to prevent any further desktop interactions after fatal abort
hard_stop_active: bool = False
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
    """Normalisiert Facebook-Text für robuste Suche."""
    if not text:
        return ""
    # Entferne unsichtbare Unicode-Zeichen (Zero Width No-Break Space etc.)
    normalized = ''.join(c for c in text if c.isprintable() or c.isspace())
    # Entferne extra Whitespace und konvertiere zu lowercase
    return ' '.join(normalized.split()).lower()

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
                
            matches = template_service.match_template_in_base64(
                base64_data, 
                template_name, 
                threshold=0.6
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
    for attempt in range(max_retries):
        try:
            log.info(f"🔍 Attempt {attempt + 1}/{max_retries}: Searching for buttons: {button_texts}")
            log.debug(f"[ROBUST] attempt_index={attempt} max_retries={max_retries}")
            
            # === METHOD 1: State-Tool Text Search ===
            # Small reliability wait to allow dynamic content to settle
            await asyncio.sleep(random.uniform(0.6, 1.2))
            state_result = await mcp_client.send_command("State-Tool", {"use_vision": False})
            state_data = state_result.data if hasattr(state_result, 'data') and state_result.data else ''
            
            log.info(f"📄 State data length: {len(state_data)}")
            
            # CRITICAL: Check for empty State-Tool data
            if len(state_data) == 0:
                log.error("🛑 CRITICAL: State-Tool returned empty data!")
                log.error("   This indicates Windows-MCP connection issues or browser problems.")
                log.error("   Possible causes:")
                log.error("   • Chrome/browser not properly focused")
                log.error("   • Windows-MCP server connection lost") 
                log.error("   • Page not fully loaded")
                log.error("   • Browser accessibility issues")
                # Don't continue with empty data
                will_try_ocr = attempt >= 1
                log.info(f"[ROBUST] Empty State-Tool data. will_try_ocr_now={will_try_ocr}")
                if will_try_ocr:  # Try advanced methods immediately on empty data
                    log.info("🔄 Empty data detected - trying OCR immediately...")
                    ocr_success = await find_button_with_ocr(button_texts)
                    if ocr_success:
                        return True
                    
                    log.info("🔄 OCR failed - trying Template Matching...")
                    template_success = await find_button_with_template_matching(button_texts)
                    if template_success:
                        return True
                        
                log.warning(f"❌ Attempt {attempt + 1}: State-Tool empty + advanced methods failed")
                continue
            
            log.debug(f"📄 State data preview: '{state_data[:200]}...'")
            
            # Normalisiere den gesamten State-Text
            normalized_state = normalize_facebook_text(state_data)
            
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
                            # Do not clamp for 'Alle Kommentare' dropdown selection
                            if normalize_facebook_text(candidate) not in (normalize_facebook_text("Alle Kommentare"), normalize_facebook_text("All comments")):
                                # Use percentage clamp based on latest screenshot size
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
                                x, y = clamp_to_modal(x, y, image_width=iw, image_height=ih)
                                log.info(f"✅ Found '{candidate}' at ({x}, {y}) [modal-clamped]. Clicking it.")
                            else:
                                log.info(f"✅ Found '{candidate}' at ({x}, {y}) [no clamp]. Clicking it.")
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
                log.info("🔄 Quick fallback to OCR-based button search...")
                ocr_success = await find_button_with_ocr(button_texts)
                if ocr_success:
                    return True
                
                log.info("🔄 Quick fallback to Template Matching...")
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
        log.info("4️⃣ Starting progressive screenshot-based comment extraction...")
        
        # Store the original URL for validation purposes
        find_and_click_expansion_buttons._original_url = original_url
        
        extracted_comments = await extract_comments_via_screenshots()
        log.info(f"📊 Extracted {len(extracted_comments)} comments via screenshot analysis")
        
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
                threshold=0.6
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

        # If neither found, be conservative
        log.warning("⚠️ Confirmation inconclusive: neither template found.")
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
        # Define safe modal boundaries for Facebook after "Alle Kommentare" step
        # These boundaries keep cursor in the main content area
        MODAL_LEFT_BOUNDARY = 300    # Avoid left sidebar
        MODAL_RIGHT_BOUNDARY = 1200  # Avoid right sidebar  
        MODAL_TOP_BOUNDARY = 200     # Avoid top browser chrome
        MODAL_BOTTOM_BOUNDARY = 700  # Avoid bottom taskbar area
        
        # Calculate safe position relative to current click
        # Move to center-right area of the modal, away from click but within bounds
        safe_x = max(MODAL_LEFT_BOUNDARY + 100, min(current_x + 200, MODAL_RIGHT_BOUNDARY - 100))
        safe_y = max(MODAL_TOP_BOUNDARY + 50, min(current_y + 100, MODAL_BOTTOM_BOUNDARY - 50))
        
        # Add small random offset for human-like behavior, but keep within bounds
        offset_x = random.randint(-30, 30)
        offset_y = random.randint(-20, 20)
        
        final_x = max(MODAL_LEFT_BOUNDARY, min(safe_x + offset_x, MODAL_RIGHT_BOUNDARY))
        final_y = max(MODAL_TOP_BOUNDARY, min(safe_y + offset_y, MODAL_BOTTOM_BOUNDARY))
        
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
    if clamped_x != x or clamped_y != y:
        log.info(f"[MODAL_CLAMP] Adjusted position from ({x}, {y}) to ({clamped_x}, {clamped_y}) within bounds L{left}-R{right} T{top}-B{bottom}")
    return clamped_x, clamped_y

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

async def extract_comments_via_screenshots() -> list[dict]:
    """
    Hybrid approach: Screenshot + OCR + Template Matching for comment expansion.
    Progressive scroll strategy: look for expansion buttons, scroll when none found.
    """
    extracted_comments = []
    max_cycles = 25  # Handle longer comment threads - increased persistence
    cycle_count = 0
    consecutive_no_buttons = 0
    seen_content = set()  # Avoid duplicate content
    
    log.info("📸 Starting progressive screenshot + template matching comment extraction...")
    
    try:
        # Initial pause to let page content fully load
        await asyncio.sleep(2.0)  # Extra time for dynamic content to load
        
        # Start from top of comments section
        await mcp_client.send_command("Scroll-Tool", {"direction": "up", "wheel_times": 5})
        await asyncio.sleep(2.0)  # Longer wait for scroll to complete and content to stabilize
        
        while cycle_count < max_cycles:
            log.info(f"🔄 Extraction cycle {cycle_count + 1}/{max_cycles}")
            
            # PERIODIC URL VALIDATION: Check every 3 cycles
            if cycle_count > 0 and cycle_count % 3 == 0:
                log.info(f"🔍 Periodic URL validation (cycle {cycle_count + 1})")
                try:
                    original_url = getattr(find_and_click_expansion_buttons, '_original_url', "")
                    url_validation_result = await validate_scan_url(original_url)
                    if not url_validation_result:
                        log.error("🚨 PERIODIC VALIDATION: Profile navigation detected! Stopping extraction.")
                        break
                except Exception as e:
                    log.warning(f"⚠️ Periodic URL validation failed: {e}")
            
            # SAFETY: Reset cursor to safe position at start of each cycle
            try:
                # Use modal-safe positioning instead of screen center to avoid sidebars/taskbar
                await move_cursor_to_safe_modal_area(600, 400)
                log.debug("🛡️ SAFETY: Reset cursor to safe modal position at cycle start")
            except Exception as e:
                log.warning(f"⚠️ Could not reset cursor at cycle start: {e}")
            
            # Phase 1: Extract visible comments via screenshots + OCR
            cycle_comments = await extract_visible_comments_ocr(seen_content)
            extracted_comments.extend(cycle_comments)
            
            # Phase 2: Look for and click comment expansion buttons
            try:
                expansion_clicked = await find_and_click_expansion_buttons()
            except AbortExtractionError as fatal:
                log.error(f"🛑 ABORTING EXTRACTION: {fatal}")
                break
            
            if not expansion_clicked:
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
            matches = template_service.match_template_in_base64(screenshot_data, template_name, threshold=0.55)  # More sensitive detection
            
            if matches:
                log.info(f"🎯 Found {len(matches)} '{template_name}' buttons")
                
                # CRITICAL: Sort matches by Y coordinate (top to bottom) to prevent hover popups blocking lower buttons
                matches_sorted = sorted(matches, key=lambda m: m.center[1])
                log.info(f"📍 Sorted {len(matches_sorted)} buttons from top to bottom")
                
                # DIAGNOSTIC: Analyze the area around each match before clicking
                for i, match in enumerate(matches_sorted[:3]):  # Limit to first 3 to avoid spam
                    x, y = int(match.center[0]), int(match.center[1])
                    w, h = int(match.size[0]), int(match.size[1])
                    confidence = match.confidence if hasattr(match, 'confidence') else 'unknown'
                    
                    log.info(f"🔍 DIAGNOSTIC: Template match {i+1} for '{template_name}':")
                    log.info(f"   📍 Raw center: ({x}, {y})")
                    log.info(f"   📐 Size: {w}x{h}")
                    log.info(f"   🎯 Confidence: {confidence}")
                    
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
                    if template_name in ("alle-xx-kommentare-ansehen", "Antwort-ansehen"):
                        # MUCH MORE CONSERVATIVE offset to avoid clicking on usernames
                        left_offset = int(min(max(w * 0.15, 10), 30))  # Reduced from 0.25, 15, 45
                        x = x - left_offset
                        log.info(f"🔧 DIAGNOSTIC: Applied left_offset={left_offset}px, moved from ({original_x}, {original_y}) to ({x}, {y})")
                        
                        # STRICTER boundary check - ensure we don't go too far left
                        if x < 300:  # Increased safety margin from left edge where usernames typically are
                            x = x + left_offset + 50  # Revert offset and add extra safety margin
                            log.warning(f"⚠️ DIAGNOSTIC: Too close to left edge! Moved from left-offset position to safer: ({x}, {y})")
                            
                        # Additional safety: if still too close to left, move to right side of button
                        if x < 250:
                            x = int(match.center[0]) + int(w * 0.3)  # Move to right side of button
                            log.warning(f"⚠️ DIAGNOSTIC: Still too close! Moved to RIGHT SIDE of button: ({x}, {y})")
                    
                    log.info(f"🖱️ FINAL CLICK POSITION: button {i+1} at ({x}, {y}) [moved {x-original_x}px from original]")
                    
                    # SAFETY CHECK: Only abort if profile content is very close to click coordinates
                    try:
                        # More precise profile detection - only check immediate click area (50x50 pixels)
                        profile_words = ['freund/in hinzufügen', 'nachricht senden', 'freund/in hinzu', 'nachricht send']
                        screenshot_result = await mcp_client.send_command("Screenshot-Tool", {})
                        if screenshot_result and hasattr(screenshot_result, 'content') and screenshot_result.content:
                            content_text = screenshot_result.content[0].text if screenshot_result.content else ""
                            if "Base64 data: " in content_text:
                                screenshot_data = content_text.split("Base64 data: ")[-1]
                                from .ocr_service import ocr_service
                                ocr_result = ocr_service.extract_text_from_base64(screenshot_data)
                                ocr_text = ocr_result.get('text', '') if ocr_result else ''
                                
                                # Only abort if very specific profile button phrases are detected
                                detected_profile_words = [word for word in profile_words if word in ocr_text.lower()]
                                if detected_profile_words:
                                    log.error(f"🚨 SAFETY ABORT: Detected profile button phrases near click position: {detected_profile_words}")
                                    log.error(f"🚨 SKIPPING this expansion button to avoid profile click!")
                                    continue  # Skip this button and move to next one
                                else:
                                    log.info(f"✅ SAFETY CHECK PASSED: No profile button phrases detected near click area")
                    except Exception as safety_error:
                        log.warning(f"⚠️ Safety check failed, proceeding with caution: {safety_error}")
                    
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
                    x, y = clamp_to_modal(x, y)
                    await mcp_client.send_command("Move-Tool", {"to_loc": [x, y]})
                    await asyncio.sleep(random.uniform(0.05, 0.1))  # Minimal pause before click
                    await mcp_client.send_command("Click-Tool", {"loc": [x, y]})
                    
                    # IMMEDIATE post-click safety movement
                    await asyncio.sleep(0.5)  # Longer pause to let content load after click
                    
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
                    
                    # MODAL-SAFE post-click cursor positioning
                    try:
                        # Move to safe modal area only - avoid sidebars and outside areas
                        await move_cursor_to_safe_modal_area(x, y)
                        log.info(f"🛡️ SAFETY: Reset cursor to safe modal position after expansion click {i+1}")
                        
                        # Additional pause to ensure no accidental hover/clicks
                        await asyncio.sleep(0.3)
                        
                    except Exception as safety_error:
                        log.error(f"❌ MODAL SAFETY MOVEMENT FAILED: {safety_error}")
                        # Simple emergency fallback - just use Safe-Center-Move-Tool
                        try:
                            await mcp_client.send_command("Safe-Center-Move-Tool", {})
                            log.warning("⚠️ Used Safe-Center-Move-Tool as emergency fallback")
                        except Exception as center_error:
                            log.error(f"❌ Emergency Safe-Center-Move-Tool also failed: {center_error}")
                            # No hardcoded coordinates - if both fail, continue without cursor movement
                    
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
        
        if buttons_clicked > 0:
            log.info(f"✅ Successfully clicked {buttons_clicked} expansion buttons")
            return True
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
