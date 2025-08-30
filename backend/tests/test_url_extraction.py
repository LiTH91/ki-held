import asyncio
from types import SimpleNamespace


class FakeChromeClient:
    def __init__(self, anchors):
        self.anchors = anchors
        self.is_connected = True

    async def send_command(self, name, params=None):
        assert name == "chrome_inject_script"
        return {"dpr": 1, "anchors": self.anchors}


def test_attach_urls_scoring(monkeypatch):
    from backend import mcp_service

    comments = [
        {"author": "Max Mustermann", "content": "Das ist mein Kommentar.", "y_position": 200},
        {"author": "Anna Schmidt", "content": "Antworte auf Max.", "y_position": 280},
    ]

    anchors = [
        {"href": "https://facebook.com/post?comment_id=123", "text": "Max Mustermann", "aria": "Kommentar-Link", "top": 190, "left": 100, "width": 50, "height": 10},
        {"href": "https://facebook.com/post?comment_id=456", "text": "Antwort von Anna", "aria": "Antwort-Link", "top": 275, "left": 120, "width": 50, "height": 10},
    ]

    enriched = mcp_service.attach_urls_to_comments(comments, anchors)

    assert enriched[0]["direct_url"].endswith("comment_id=123")
    assert enriched[1]["direct_url"].endswith("comment_id=456")
    assert enriched[0]["url_confidence"] >= 0.55
    assert enriched[1]["url_confidence"] >= 0.55


def test_get_comment_anchors_via_chrome(monkeypatch):
    from backend import mcp_service

    # Monkeypatch Chrome client factory to return fake client
    async def fake_get_client():
        return FakeChromeClient([
            {"href": "https://facebook.com/post?comment_id=789", "text": "Tom Weber", "aria": "Kommentar", "top": 340, "left": 140, "width": 60, "height": 12},
        ])

    monkeypatch.setattr(mcp_service, "get_chrome_mcp_client", fake_get_client)

    anchors = asyncio.run(mcp_service.get_comment_anchors_via_chrome())
    assert isinstance(anchors, list)
    assert anchors and anchors[0]["href"].endswith("comment_id=789")

