print("[DEBUG mcp_service] Starting import of mcp_service.py")
import asyncio
import os
from collections import deque
from datetime import datetime, timedelta
import random
print("[DEBUG mcp_service] About to import human_wait from utils")
from .utils import human_wait
from .logger import setup_logging
log = setup_logging()
from .utils.human_mouse import move_mouse_to, click_mouse, scroll_mouse
from pathlib import Path

# Instantiate MCP client for default navigation/content mode
from .mcp_client import MCPClient

mcp_client = MCPClient()
# Remember the last meaningful on-page click to reliably refocus before scrolling
last_focus_point: list[int] | None = None

# TODO: Replace with actual Windows-MCP API integration

MAX_ACTIONS_PER_MIN = int(os.getenv('MCP_MAX_ACTIONS_PER_MIN', 20))
action_timestamps = deque(maxlen=MAX_ACTIONS_PER_MIN)

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

async def navigate(url: str):
    await _rate_limited("navigate")
    log.info(f"Navigating to {url}")
    
    try:
        # Always launch a new instance of Chrome to ensure a clean state
        await mcp_client.send_command("Launch-Tool", {"name": "chrome"})
        log.info("Launched Chrome. Waiting for it to open...")
        await asyncio.sleep(5) # Give Chrome time to open
        
        # Pre-scan Check: Look for login/verification screens before proceeding
        log.info("Performing pre-scan check for login/verification screens...")
        await human_wait("pre-scan check")
        verification_keywords = ["Verify your identity", "Bestätigen Sie Ihre Identität", "Login", "Anmelden"]
        state_result = await mcp_client.send_command("State-Tool", {"use_vision": False})
        state_data = state_result.data if hasattr(state_result, 'data') and state_result.data else ''
        
        for keyword in verification_keywords:
            if keyword.lower() in state_data.lower():
                log.warning(f"Login or verification screen detected with keyword: '{keyword}'.")
                raise RuntimeError("Login or identity verification required. Please log in manually and start a new scan.")

        # Automate navigating to the URL
        log.info(f"Typing URL '{url}' into the address bar.")
        # Use a shortcut to focus the address bar (Alt+D)
        await mcp_client.send_command("Shortcut-Tool", {"shortcut": ["alt", "d"]})
        await asyncio.sleep(1)
        # Type the URL and press Enter
        await mcp_client.send_command("Type-Tool", {"text": url, "press_enter": True})
        # Allow initial navigation; then try to focus page content
        await asyncio.sleep(2)
        try:
            await mcp_client.send_command("Shortcut-Tool", {"shortcut": ["esc"]})
            await asyncio.sleep(0.2)
            await mcp_client.send_command("Shortcut-Tool", {"shortcut": ["tab"]})
        except Exception:
            pass

    except Exception as e:
        log.error(f"Failed to launch or navigate in Chrome: {e}")
        raise RuntimeError("Could not launch or navigate in Chrome for the scan.") from e

    log.info(f"Navigation to {url} complete.")

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
    
    log.info(f"[MCP] Received DOM (length: {len(dom_content or '')}).")
    return dom_content

async def scroll_page(wheel_times: int = 2):
    """Scrolls the page down by a specified amount."""
    await _rate_limited("scroll")
    # Try to ensure the browser content has focus before scrolling
    try:
        if last_focus_point:
            await mcp_client.send_command("Click-Tool", {"loc": last_focus_point})
            await asyncio.sleep(0.2)
        else:
            await mcp_client.send_command("Shortcut-Tool", {"shortcut": ["esc"]})
            await asyncio.sleep(0.1)
            await mcp_client.send_command("Shortcut-Tool", {"shortcut": ["tab"]})
            await asyncio.sleep(0.1)
    except Exception:
        pass
    log.info(f"Scrolling page down by {wheel_times} wheel times.")
    await mcp_client.send_command("Scroll-Tool", {"direction": "down", "wheel_times": wheel_times})
    log.info("Scroll complete.")

async def click(coords: list[int]):
    await _rate_limited("click")
    log.info(f"Clicking at coordinates: {coords}")
    # Simulate moving to the element before clicking
    await move_mouse_to(coords[0], coords[1])
    await click_mouse()

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