import pytest
import asyncio

from backend import mcp_service


@pytest.mark.asyncio
async def test_abort_propagates_and_stops_extraction(monkeypatch):
    """Ensure AbortExtractionError stops the extraction loop without scrolling."""

    # Arrange: stub find_and_click_expansion_buttons to raise fatal abort
    async def raise_abort():
        raise mcp_service.AbortExtractionError("simulated URL change")

    # Track if intelligent_scroll_and_search would be called
    called_scroll = {"value": False}

    async def stub_scroll():
        called_scroll["value"] = True
        return False

    monkeypatch.setattr(mcp_service, "find_and_click_expansion_buttons", raise_abort)
    monkeypatch.setattr(mcp_service, "intelligent_scroll_and_search", stub_scroll)

    # Act
    result = await mcp_service.extract_comments_via_screenshots()

    # Assert: extraction returned (likely empty) and did NOT scroll after abort
    assert isinstance(result, list)
    assert called_scroll["value"] is False


