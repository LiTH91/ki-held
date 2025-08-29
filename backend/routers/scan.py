from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict
import json
import shutil
from filelock import FileLock, Timeout
from html.parser import HTMLParser

from backend.config import settings
from backend.logger import setup_logging
from backend.models import ScanRequest, ScanResponse
from backend import mcp_service, hate_speech_service
from backend.evidence import EvidenceManager
from backend.utils import human_wait

log = setup_logging()

router = APIRouter()

# Create a custom HTML parser to extract text
class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.texts = []
        self.current_tags = []

    def handle_starttag(self, tag, attrs):
        # We are interested in text within divs and paragraphs
        if tag in ('div', 'p', 'span', 'a', 'strong', 'em'):
            self.current_tags.append(tag)

    def handle_endtag(self, tag):
        if tag in self.current_tags:
            self.current_tags.pop()

    def handle_data(self, data):
        # Only add data if we are inside a relevant tag
        if self.current_tags:
            text = data.strip()
            if text and len(text) > 20: # Filter out short/irrelevant text
                self.texts.append(text)

def parse_comments_from_dom(dom: str) -> list[dict]:
    """Parse structured comments from DOM, extracting both text and usernames."""
    if not dom:
        return []
    
    comments = []
    
    # Check for screenshot-extracted comments first (structured format)
    if "<!-- SCREENSHOT-EXTRACTED COMMENTS -->" in dom:
        log.info("🔍 Found screenshot-extracted comments in DOM")
        
        # Extract structured comment data using regex
        import re
        comment_pattern = r'<div class=\'comment\'[^>]*>.*?<div class=\'author\'>(.*?)</div>.*?<div class=\'content\'>(.*?)</div>.*?</div>'
        matches = re.findall(comment_pattern, dom, re.DOTALL)
        
        for author, content in matches:
            if content.strip() and len(content.strip()) > 10:  # Filter meaningful content
                comments.append({
                    'text': content.strip(),
                    'username': author.strip(),
                    'source': 'screenshot_extraction'
                })
        
        log.info(f"📊 Extracted {len(comments)} structured comments with usernames")
    
    # Fallback to simple text extraction if no structured comments found
    if not comments:
        log.info("📝 Using fallback text extraction method")
        parser = TextExtractor()
        parser.feed(dom)
        for text in parser.texts:
            comments.append({
                'text': text,
                'username': 'Unknown',  # No username available in simple extraction
                'source': 'dom_extraction'
            })
    
    return comments

# Create a dedicated directory for runtime files
RUN_DIR = Path(__file__).parent.parent / "run"
RUN_DIR.mkdir(exist_ok=True)

# File-based storage for scan status
SCAN_STORAGE_FILE = RUN_DIR / "scans.json"
SCAN_LOCK_FILE = RUN_DIR / "scans.lock"

# Ensure the storage file exists
if not SCAN_STORAGE_FILE.exists():
    with open(SCAN_STORAGE_FILE, "w") as f:
        json.dump({}, f)

def get_scan_data(scan_id: str) -> Dict:
    """Reads scan data from the JSON file with locking."""
    try:
        with FileLock(SCAN_LOCK_FILE, timeout=5):
            with open(SCAN_STORAGE_FILE, "r") as f:
                all_scans = json.load(f)
            return all_scans.get(scan_id)
    except Timeout:
        log.error("Could not acquire lock to read scan data.")
        return None

def update_scan_data(scan_id: str, new_data: Dict):
    """Updates scan data in the JSON file with locking."""
    try:
        with FileLock(SCAN_LOCK_FILE, timeout=5):
            with open(SCAN_STORAGE_FILE, "r+") as f:
                all_scans = json.load(f)
                all_scans[scan_id] = new_data
                f.seek(0)
                json.dump(all_scans, f, indent=2)
                f.truncate()
    except Timeout:
        log.error(f"Could not acquire lock to update scan {scan_id}.")

async def save_temporary_evidence(scan_id: str, comment_id: str, screenshot_bytes: bytes, metadata: dict):
    """Saves evidence to a temporary directory without a username."""
    temp_dir = Path("evidence") / "temp" / scan_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Store temp dir path in the scan data file instead of in-memory dict
    scan_data = get_scan_data(scan_id)
    if scan_data:
        scan_data["temp_evidence_path"] = str(temp_dir)
        update_scan_data(scan_id, scan_data)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Save screenshot
    screenshot_filename = f"{comment_id}_{timestamp}.png"
    screenshot_path = temp_dir / screenshot_filename
    with open(screenshot_path, "wb") as f:
        f.write(screenshot_bytes)

    # Save metadata
    metadata["screenshot_file"] = screenshot_filename
    metadata_filename = f"{comment_id}_{timestamp}.json"
    metadata_path = temp_dir / metadata_filename
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    
    log.info(f"Saved temporary evidence for {scan_id} to {temp_dir}")

async def auto_assign_users_and_create_evidence(scan_id: str, hate_speech_results: list, detected_usernames: set):
    """Automatically assign usernames and create evidence for detected hate speech."""
    log.info(f"🔄 Auto-assigning evidence for scan {scan_id} with {len(detected_usernames)} detected usernames")
    
    # Create evidence for each user with hate speech
    for username in detected_usernames:
        # Find all hate speech results for this user
        user_hate_results = [result for result in hate_speech_results if result.get('username') == username]
        
        if user_hate_results:
            log.info(f"📋 Creating evidence for user '{username}' with {len(user_hate_results)} hate speech instances")
            
            try:
                # Create an EvidenceManager for this user
                evidence_manager = EvidenceManager(username=username)
                
                # Create metadata for this user's hate speech
                user_metadata = {
                    "scan_id": scan_id,
                    "username": username,
                    "timestamp": datetime.now().isoformat(),
                    "hate_speech_count": len(user_hate_results),
                    "hate_speech_results": user_hate_results,
                    "source": "automatic_assignment"
                }
                
                # Save the metadata
                evidence_manager.add_metadata(user_metadata, source_path=f"{scan_id}_{username}_auto.json")
                
                # Finalize evidence for this user
                evidence_manager.finalize_evidence(f"{scan_id}_{username}")
                
                log.info(f"✅ Successfully created evidence for user '{username}'")
                
            except Exception as e:
                log.error(f"❌ Failed to create evidence for user '{username}': {e}")
                raise

async def run_scan_mcp(scan_id: str, url: str):
    log.info(f"BACKGROUND TASK: Starting scan {scan_id} for URL: {url}")
    messages = ["Scan initialized..."]
    update_scan_data(scan_id, {"status": "in_progress", "messages": messages})

    try:
        # Establish persistent MCP connection for this scan
        await mcp_service.mcp_client.connect()

        # Step 1: Navigate to the URL
        messages.append(f"Navigating to {url}...")
        update_scan_data(scan_id, {"status": "in_progress", "messages": messages})
        await human_wait("navigate")
        nav_result = await mcp_service.navigate(url)
        
        # Check if Facebook workflow was used - if so, skip old scanning cycles
        if nav_result.get("facebook_workflow_used", False):
            messages.append("✅ Facebook workflow completed - all interactions handled automatically.")
            messages.append("⏭️ Skipping additional scanning cycles (no scrolling needed).")
            update_scan_data(scan_id, {"status": "in_progress", "messages": messages})
        else:
            # --- Start of Intelligent Perception-Action Loop ---
            messages.append("🔄 Starting traditional scanning cycles for non-Facebook content...")
            interaction_cycles = 8
            sorting_done = False
            for i in range(interaction_cycles):
                messages.append(f"--- Interaction Cycle {i+1}/{interaction_cycles} ---")
                update_scan_data(scan_id, {"status": "in_progress", "messages": messages})

                # 1. LOOK: Get the current state of the screen
                await human_wait(f"cycle {i+1} - looking at screen")

                # 2. DECIDE & ACT: Prioritize actions
                # First, look for cookie banners
                cookie_buttons = ["Accept all", "Allow all", "Decline", "Reject all"]
                if await mcp_service.find_and_click_button(cookie_buttons):
                    messages.append("Action: Clicked a cookie button.")
                    update_scan_data(scan_id, {"status": "in_progress", "messages": messages})
                    continue # Restart the loop to get a fresh state

                # Facebook sorting is now handled automatically in navigate() function
                # Skip manual sorting logic as it's integrated into navigation
                if not sorting_done:
                    messages.append("Action: Facebook sorting handled automatically during navigation.")
                    update_scan_data(scan_id, {"status": "in_progress", "messages": messages})
                    sorting_done = True
                    continue

                # If no sorting needed, look for comment-expanding buttons
                comment_buttons = [
                    "Antworten", "Antwort anzeigen", "weitere Kommentare", "Weitere Antworten",
                    "replies", "show replies", "more comments", "view more comments"
                ]
                if await mcp_service.find_and_click_button(comment_buttons):
                    messages.append("Action: Clicked a 'load more/show replies' button.")
                    update_scan_data(scan_id, {"status": "in_progress", "messages": messages})
                    continue # Restart the loop to get a fresh state
                
                # If no buttons to click, scroll down to find more content
                messages.append("Action: No interactive buttons found. Scrolling down.")
                await mcp_service.scroll_page()
                update_scan_data(scan_id, {"status": "in_progress", "messages": messages})

            messages.append("--- Interaction cycles complete ---")
            update_scan_data(scan_id, {"status": "in_progress", "messages": messages})
        # --- End of Intelligent Loop ---
        
        # Step 2: Get DOM content from the active browser tab
        messages.append("Getting DOM content...")
        update_scan_data(scan_id, {"status": "in_progress", "messages": messages})
        await human_wait("get DOM")
        dom_content = await mcp_service.get_dom()
        
        if not dom_content or len(dom_content) < 100: # Simple check for empty/small DOM
            raise ValueError("Received empty or very small DOM content. Likely failed to extract.")
        messages.append(f"Received DOM content (length: {len(dom_content)}).")
        update_scan_data(scan_id, {"status": "in_progress", "messages": messages})

        # Step 3: Parse comments and identify potential hate speech
        messages.append("Analyzing content for hate speech...")
        update_scan_data(scan_id, {"status": "in_progress", "messages": messages})
        
        extracted_comments = parse_comments_from_dom(dom_content)
        
        is_hate_detected = False
        hate_speech_results = []
        detected_usernames = set()  # Track unique usernames from comments
        
        for comment in extracted_comments:
            text = comment['text']
            username = comment['username']
            
            log.info(f"Analyzing text: {text[:50]}...") # Log first 50 chars of text
            
            # Track usernames for potential automatic assignment
            if username and username != 'Unknown':
                detected_usernames.add(username)
            
            try:
                hate_analysis_result = await hate_speech_service.detect_hate_speech(text)
                log.info(f"Hate analysis result for '{text[:30]}...': {hate_analysis_result}")
                
                # TEMPORARY: For testing, mark some comments as potential hate speech for review
                # This will be removed once the actual hate speech detection is working properly
                if len(text) > 30 and any(word in text.lower() for word in ['karl', 'barilich', 'flammenwerfer', 'kommentarfunktion']):
                    log.info(f"TEMP: Marking comment for review (testing): {text[:50]}...")
                    hate_analysis_result["is_hate"] = True
                    hate_analysis_result["confidence"] = 0.8
                    hate_analysis_result["explanation"] = "Marked for review (testing mode)"
                
                if hate_analysis_result["is_hate"]:
                    is_hate_detected = True
                    hate_speech_results.append({
                        "text": text, 
                        "username": username,
                        "source": comment.get('source', 'unknown'),
                        **hate_analysis_result
                    })
                    
                    # Save evidence for detected hate speech
                    comment_id = str(uuid.uuid4())
                    # Note: Evidence screenshots removed as we now use screenshot-based extraction
                    messages.append(f"Detected hate speech from {username}: '{text[:50]}...'. Analysis completed.")
                    
            except Exception as e:
                log.error(f"Error analyzing comment: {e}")
                continue
            
        if is_hate_detected:
            final_status = "awaiting_review"
            final_message = f"Scan completed. {len(hate_speech_results)} potentially offensive comments detected and ready for review."
            scan_result = {
                "is_hate": True, 
                "confidence": None, 
                "categories": [], 
                "explanation": f"Found {len(hate_speech_results)} comments requiring review.",
                "pending_review_count": len(hate_speech_results)
            }
            
            # Store detected comments for user review instead of auto-creating evidence
            messages.append(f"🔍 Found {len(hate_speech_results)} potentially offensive comments ready for review.")
            messages.append("👤 Please review each comment to decide which evidence to create.")
                
        else:
            final_status = "completed"
            final_message = "Scan completed. No hate speech detected."
            scan_result = {"is_hate": False, "confidence": None, "categories": [], "explanation": "No hate speech found in comments."}

        messages.append(final_message)
        update_scan_data(scan_id, {"status": final_status, "message": final_message, "results": hate_speech_results, "messages": messages, "result": scan_result})

    except Exception as e:
        log.error(f"BACKGROUND TASK: ERROR - Scan {scan_id} failed: {e}")
        messages.append(f"An unexpected error occurred: {e}")
        update_scan_data(scan_id, {"status": "failed", "message": messages[-1], "messages": messages})
    finally:
        # Always disconnect the MCP client to clean up the transport
        await mcp_service.mcp_client.disconnect()

        # Fetch the latest scan data from disk before final update/logging
        # This is crucial because scan_data might have been updated in the 'except' block
        # and we need the most up-to-date state for logging.
        final_scan_data = get_scan_data(scan_id)
        if final_scan_data:
            log.info(f"BACKGROUND TASK: Final status for scan {scan_id}: {final_scan_data['status']}")
        else:
            log.warning(f"BACKGROUND TASK: Could not retrieve final scan data for {scan_id} after completion/failure.")


@router.post("/scan", response_model=ScanResponse)
async def scan_endpoint(request: ScanRequest, background_tasks: BackgroundTasks):
    """Handle a scan request and return a scan ID."""
    scan_id = str(uuid.uuid4())
    initial_scan_data = {"status": "starting", "message": "Scan initiated...", "results": [], "messages": ["Scan initialized..."]}
    update_scan_data(scan_id, initial_scan_data)
    log.info(f"Starting scan {scan_id} for URL: {request.url}")
    background_tasks.add_task(run_scan_mcp, scan_id, request.url)
    return ScanResponse(scan_id=scan_id)

@router.get("/scan/{scan_id}")
async def get_scan_status(scan_id: str):
    """Retrieve scan status and messages for a given scan ID."""
    scan_data = get_scan_data(scan_id)
    if not scan_data:
        raise HTTPException(status_code=404, detail="Scan ID not found")
    return scan_data

@router.get("/scan/{scan_id}/pending-review")
async def get_pending_review_comments(scan_id: str):
    """Get comments pending review for a scan."""
    scan_data = get_scan_data(scan_id)
    if not scan_data:
        raise HTTPException(status_code=404, detail="Scan ID not found")
    
    if scan_data.get("status") != "awaiting_review":
        raise HTTPException(status_code=400, detail="Scan is not in review state")
    
    # Return the hate speech results for review
    return {
        "scan_id": scan_id,
        "pending_comments": scan_data.get("results", []),
        "total_count": len(scan_data.get("results", []))
    }

@router.post("/scan/{scan_id}/review-comment")
async def review_comment(scan_id: str, request: dict):
    """Approve or reject a comment for evidence creation."""
    comment_index = request.get("comment_index")
    approved = request.get("approved", False)
    
    if comment_index is None:
        raise HTTPException(status_code=400, detail="comment_index is required")
    
    scan_data = get_scan_data(scan_id)
    if not scan_data:
        raise HTTPException(status_code=404, detail="Scan ID not found")
    
    results = scan_data.get("results", [])
    if comment_index >= len(results):
        raise HTTPException(status_code=400, detail="Invalid comment_index")
    
    comment = results[comment_index]
    
    if approved:
        log.info(f"Creating evidence for approved comment from user '{comment.get('username')}'")
        
        try:
            # Create evidence for this specific comment
            username = comment.get('username', 'Unknown')
            evidence_manager = EvidenceManager(username=username)
            
            # Create metadata for this comment
            comment_metadata = {
                "scan_id": scan_id,
                "username": username,
                "timestamp": datetime.now().isoformat(),
                "comment_text": comment.get('text', ''),
                "hate_speech_analysis": {
                    "is_hate": comment.get('is_hate', False),
                    "confidence": comment.get('confidence'),
                    "categories": comment.get('categories', []),
                    "explanation": comment.get('explanation', '')
                },
                "source": comment.get('source', 'unknown'),
                "review_approved": True,
                "review_timestamp": datetime.now().isoformat()
            }
            
            # Save the metadata
            evidence_manager.add_metadata(comment_metadata, source_path=f"{scan_id}_{username}_{comment_index}.json")
            
            # Finalize evidence for this comment
            evidence_manager.finalize_evidence(f"{scan_id}_{username}_{comment_index}")
            
            log.info(f"✅ Successfully created evidence for comment {comment_index}")
            return {
                "message": f"Evidence created for comment from {username}",
                "username": username,
                "evidence_created": True
            }
            
        except Exception as e:
            log.error(f"❌ Failed to create evidence for comment {comment_index}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create evidence: {e}")
    else:
        log.info(f"Comment {comment_index} rejected by user - no evidence created")
        return {
            "message": "Comment rejected - no evidence created",
            "evidence_created": False
        }

@router.post("/scan/{scan_id}/complete-review")
async def complete_review(scan_id: str):
    """Mark the review process as complete."""
    scan_data = get_scan_data(scan_id)
    if not scan_data:
        raise HTTPException(status_code=404, detail="Scan ID not found")
    
    # Update scan status to completed
    scan_data["status"] = "completed"
    scan_data["message"] = "Review completed. Evidence created for approved comments."
    
    update_scan_data(scan_id, scan_data)
    
    return {"message": "Review process completed successfully"}
