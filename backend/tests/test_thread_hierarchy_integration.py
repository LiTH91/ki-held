import asyncio
import base64
from pathlib import Path
import pytest


def _b64_from_file(path: Path) -> str:
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii")


# Discover provided screenshots
SCREEN_DIR = Path("fb-screenshots-test")
PNG_FILES = sorted([p for p in SCREEN_DIR.glob("*.png")])


@pytest.mark.parametrize("png_path", PNG_FILES, ids=[p.stem for p in PNG_FILES])
def test_visual_thread_hierarchy_on_real_screenshots(png_path: Path):
    from backend import mcp_service

    assert png_path.exists(), f"Missing test image: {png_path}"

    img_b64 = _b64_from_file(png_path)

    # Run analyzer with provided screenshot (bypasses MCP screenshot tool)
    result = asyncio.run(mcp_service.analyze_comment_hierarchy_visual(img_b64))

    # Basic sanity checks
    assert isinstance(result, list)

    # If OCR finds anything, validate structure of first item
    if result:
        first = result[0]
        # Required structural keys produced by analyzer pipeline
        for key in ("username", "thread_level", "comment_id", "x_position", "y_position"):
            assert key in first, f"Expected key '{key}' in result item"


