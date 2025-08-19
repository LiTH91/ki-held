import pytest
import asyncio
from backend.mcp_service import _rate_limited
import os
from datetime import datetime
import random
import logging

# Configure logging to display all levels
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Add a StreamHandler to ensure logs are displayed
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.DEBUG)
logger.addHandler(stream_handler)

@pytest.mark.asyncio
async def test_rate_limiting():
    # Set MAX_ACTIONS_PER_MIN to 2 for testing
    original_max_actions = os.getenv('MCP_MAX_ACTIONS_PER_MIN')
    os.environ['MCP_MAX_ACTIONS_PER_MIN'] = '2'

    start_time = datetime.now()
    logger.info(f"Test started at {start_time}")

    # Perform 3 quick actions
    await _rate_limited("dummy_action")
    await _rate_limited("dummy_action")
    await _rate_limited("dummy_action")

    end_time = datetime.now()
    total_time = (end_time - start_time).total_seconds()
    logger.info(f"Test ended at {end_time}, total time: {total_time:.2f}s")

    # Assert that the total runtime includes at least one enforced wait
    assert total_time >= 60, "Rate limiting did not enforce wait as expected"

    # Restore original MAX_ACTIONS_PER_MIN
    if original_max_actions is not None:
        os.environ['MCP_MAX_ACTIONS_PER_MIN'] = original_max_actions
    else:
        del os.environ['MCP_MAX_ACTIONS_PER_MIN']

