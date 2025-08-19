import asyncio
import random
from ..logging_service import log

async def human_wait(action_name: str) -> None:
    """
    Human-like wait helper.
    """
    # Determine sleep time
    sleep_time = random.uniform(1.5, 4.5)
    if random.random() < 0.1:
        sleep_time = random.uniform(6, 9)
    # Log the wait time
    log(f"[HUMAN_WAIT] Sleeping for {sleep_time:.2f}s before {action_name}")
    # Perform the sleep
    await asyncio.sleep(sleep_time)

# Re-export human_mouse utilities
from .human_mouse import move_mouse_to, click_mouse, scroll_mouse
