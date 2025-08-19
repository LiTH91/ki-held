import asyncio
import random
from backend.logging_service import log

async def move_mouse_to(target_x: int, target_y: int):
    """
    Simulates human-like mouse movement to a target coordinate.
    """
    log(f"[MOUSE] Moving to ({target_x}, {target_y})")
    # This is a placeholder; actual MCP calls would be here.
    await asyncio.sleep(random.uniform(0.3, 1.2))

async def click_mouse():
    """
    Simulates a human-like mouse click with variability.
    """
    log("[MOUSE] Shaking cursor before click")
    await asyncio.sleep(random.uniform(0.1, 0.25))
    log("[MOUSE] Performing click")
    await asyncio.sleep(random.uniform(0.05, 0.18))

async def scroll_mouse(amount: int):
    """
    Simulates human-like mouse scrolling.
    """
    log(f"[MOUSE] Scrolling by {amount}px")
    await asyncio.sleep(random.uniform(0.3, 0.7))
