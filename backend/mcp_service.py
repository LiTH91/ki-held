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
# Remember the last meaningful on-page click to reliably refocus before scrolling
last_focus_point: list[int] | None = None
movement_suppressed_until: datetime | None = None

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
                                log.info(f"✅ Found coordinates for '{button_text}' at ({x}, {y})")
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
                await mcp_client.send_command("Move-Tool", {"to_loc": [x, y]})
                await asyncio.sleep(random.uniform(0.12, 0.25))
                log.info(f"🖱️ Clicking template '{template_name}' at adjusted ({x}, {y})")
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
                            log.info(f"✅ Found '{candidate}' at ({x}, {y}). Clicking it.")
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

async def execute_facebook_workflow():
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
        
        # Schritt 4: Alle "alle xx Kommentare-ansehen" Buttons klicken
        log.info("4️⃣ Clicking all 'alle xx Kommentare-ansehen' buttons...")
        load_more_count = await click_all_load_more_comments()
        
        # Pause zwischen verschiedenen Aktivitätstypen
        if load_more_count > 0:
            await human_wait("transition between activities")
            await simulate_brief_reading()
        
        # Schritt 5: Alle "Kommentar ansehen" Buttons klicken
        log.info("5️⃣ Clicking all 'Kommentar ansehen' buttons...")
        view_count = await click_all_view_comment_buttons()
        
        # Abschließende menschliche Aktivität
        await simulate_page_completion(load_more_count + view_count)
        
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
                        log.info(f"🎯 Found numbered comment button: '{line_lower}' at ({x}, {y})")
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

# ===== ANTI-DETECTION FUNCTIONS =====

async def add_human_mouse_movement():
    """Simuliert zufällige menschliche Mausbewegungen."""
    try:
        global movement_suppressed_until
        if movement_suppressed_until and datetime.now(timezone.utc) < movement_suppressed_until:
            log.debug("[MOVE_SUPPRESS] Skipping human mouse movement (suppressed)")
            return
        # Gelegentliche zufällige Bewegungen
        if random.random() < 0.3:  # 30% Chance
            # Kleine zufällige Bewegung
            await mcp_client.send_command("Move-Tool", {
                "to_loc": [
                    random.randint(100, 800), 
                    random.randint(200, 600)
                ]
            })
            await asyncio.sleep(random.uniform(0.2, 0.6))
            log.debug("🐭 Added random mouse movement")
    except Exception as e:
        log.debug(f"Mouse movement failed: {e}")

async def add_subtle_mouse_movement():
    """Subtile Mausbewegung vor Aktionen."""
    try:
        global movement_suppressed_until
        if movement_suppressed_until and datetime.now(timezone.utc) < movement_suppressed_until:
            log.debug("[MOVE_SUPPRESS] Skipping subtle mouse movement (suppressed)")
            return
        if random.random() < 0.4:  # 40% Chance für subtile Bewegung
            # Sehr kleine Bewegung
            current_x, current_y = 400, 300  # Fallback position
            new_x = current_x + random.randint(-50, 50)
            new_y = current_y + random.randint(-30, 30)
            
            await mcp_client.send_command("Move-Tool", {"to_loc": [new_x, new_y]})
            await asyncio.sleep(random.uniform(0.1, 0.3))
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
    return await execute_facebook_workflow()

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
            success = await execute_facebook_workflow()
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
    return dom_content

async def scroll_page(wheel_times: int = 2):
    """Scrolls the page down by a specified amount."""
    await _rate_limited("scroll")
    # Try to ensure the browser content has focus before scrolling
    try:
        # Avoid clicking to focus. Only adjust focus and move to safe center.
        await mcp_client.send_command("Deselect-Close-Tool", {})
        await asyncio.sleep(0.1)
        await mcp_client.send_command("Safe-Center-Move-Tool", {})
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
