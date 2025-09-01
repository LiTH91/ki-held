# Project Documentation

## General App Information + Feature list
- Desktop app (Tauri + React) with a FastAPI backend and a Windows-MCP automation service (FastMCP over STDIO).
- Features:
  - Start and monitor scans via `/api/scan` endpoints
  - Browser automation targeting Chrome (fallback to Edge in tool)
  - Evidence collection and export
  - Windows automation tools: State/Launch/Click/Type/Scroll/Get-Browser-DOM/Screenshot

## App structure
```
ki-held/
├─ backend/
│  ├─ app.py                   # App factory, CORS, logging, router mounts (/api)
│  ├─ main.py                  # Uvicorn entry point
│  ├─ mcp_client.py            # FastMCP client via STDIO (StdioTransport)
│  ├─ mcp_service.py           # Navigation/DOM helpers calling MCP tools
│  ├─ routers/                 # API routers (login, scan)
│  ├─ utils/                   # human_mouse, helpers
│  ├─ logs/                    # app.log (created at runtime)
│  └─ ...
├─ Windows-MCP/
│  └─ main.py                  # FastMCP server, banner suppressed, startup_debug.log
├─ frontend/
│  └─ ...                      # Tauri + React app
└─ README.md
```

## File list (purpose + external calls)
- backend/app.py: creates FastAPI app, sets CORS, mounts routers under `/api`.
- backend/main.py: uvicorn entry to run the app.
- backend/mcp_client.py: constructs `Client(StdioTransport(...))` to start `Windows-MCP/main.py` using the Windows-MCP venv python; exposes `send_command` with retries.
- backend/mcp_service.py: high-level helpers that call MCP tools (navigate/get_dom/scroll/click); includes progressive screenshot-based extraction for Facebook comment scraping and structured OCR parsing.
- backend/api_models.py: shared Pydantic schemas for API requests/responses (types for scans, comments, evidence metadata).
- backend/ocr_service.py: OCR service using pytesseract for text extraction from screenshots; provides `extract_text_from_base64()` method.
- backend/template_service.py: Template matching service using OpenCV; provides `match_template_in_base64()` for UI element detection.
- backend/routers/scan.py: scan endpoint implementation; handles hate speech analysis and evidence collection.
- backend/logger.py: sets console + file loggers into `backend/logs/app.log`.
- backend/config.py: pydantic settings and `LOGS_DIR` creation.
- Windows-MCP/main.py: defines tools; suppresses FastMCP CLI banner compatibly; writes early `startup_debug.log` for diagnostics; guards `SetProcessDPIAware`. Includes `Screenshot-Tool` returning base64 PNG (full-screen or region) used by OCR and template matching.
- frontend/src/components/ScanStatus.tsx: scan create + poll logic; uses `/api` routes.

External function calls/definitions (projectwide):
- Function `MCPClient.send_command(cmd, params)`: called by backend services to execute MCP tools.
- Function `setup_logging()` from backend/logger.py: used by backend startup.
- Variable `settings` from backend/config.py: used throughout backend.

## Project-wide variables and functions
- settings (backend/config.py): global config and paths
- MCPClient (backend/mcp_client.py): main interface to MCP
- log (backend/logger.py): shared logger

## Run instructions
- One-command start (project root, PowerShell):
```
./start-app.ps1
```
- Backend only (project root):
```
python run_backend.py
```
- Frontend (from frontend/):
```
npm install
npm run tauri dev
```

## MCP transport fix (root cause + resolution)
- Root cause: `fastmcp.utilities.cli` availability differs between versions; banner output on STDIO broke the handshake and caused "Connection closed".
- Resolution: define a no-op `log_server_banner` when the module is missing; add early `startup_debug.log` breadcrumbs; ensure `mcp_client.py` launches with the Windows-MCP venv python and correct cwd.

## Recent changes (2025-09-01)

### Facebook OCR Parsing & Reply Hierarchy
- New structured OCR parsing pipeline to reliably extract:
  - **author/username**
  - **content** (multi-line, stops on timestamp/engagement/next author)
  - **reply hierarchy**: `is_reply`, `reply_target`
  - **timestamp** (relative forms like "vor 2 Std", shorthand like `2h`, and English variants)
  - **engagement** ("Gefällt mir", "Antworten", counts like `17 Kommentare`)
- Key functions (backend/mcp_service.py):
  - `parse_comments_from_ocr(ocr_text: str) -> list[dict]`
  - `parse_facebook_comment_structure(lines: list[str]) -> list[dict]`
  - `extract_comment_block(lines: list[str], start_index: int) -> dict`
  - `parse_facebook_author_line(line: str) -> dict`
  - `is_timestamp_line(line: str) -> bool`
  - `is_engagement_line(line: str) -> bool`
  - `is_ui_element(line: str) -> bool`
  - `validate_comment_structure(comment: dict) -> bool`
- Output schema (parsed comment dict):
  - `author` (string)
  - `content` (string)
  - `is_reply` (bool)
  - `reply_target` (string | None)
  - `timestamp` (string)
  - `reactions` (string)
  - `reaction_count`, `comment_count` (ints)
  - `source` (e.g., `facebook_ocr_structured`)

### Backend ↔ Frontend Interop updates
- Router `backend/routers/scan.py` augments each comment payload with reply info:
  - `is_reply`, `parent_comment_id`, `reply_target`, `context_content`
- Frontend `frontend/src/components/CommentReview.tsx` renders a reply indicator when `is_reply` and `reply_target` are present.

### Notes on heuristics and reliability
- Author detection ignores UI elements (e.g., "Alle Kommentare", "Antworten") and precise timestamp patterns.
- Engagement detection requires specific markers (e.g., `Gefällt mir`, `Antworten`, numeric `… Kommentare`).
- Conservative mapping for ambiguous 2–3 word lines to reduce false reply detection.

## Changes (2025-08-29)

### Dynamic Scroll-Based Content Loading
- **Screenshot-Based End Detection**: Pre-scroll phase uses screenshot hashing to detect when page bottom is reached (5 consecutive unchanged screens)
- **Adaptive Content Loading**: Aggressive pre-scrolling to load all lazy-loaded Facebook comments before extraction begins
- **Screenshot-Based Anchor Return**: Dynamic return to "Alle Kommentare" anchor using template matching with screenshot-based top detection
- **No More Fixed Limits**: Replaced all artificial scroll limits with content-aware detection mechanisms

### Robust Button Detection & Validation
- **Enhanced find_and_click_button_robust()**: State-Tool fallback with immediate OCR/Template switching when State-Tool returns empty
- **Bounding-Box Filtering**: Click coordinates filtered to modal area (x: 20%-80%, y: 15%-88% of screen)
- **Pre-Click Validation**: Crop screenshots around target coordinates for secondary validation before clicking
- **Template Match Debugging**: Comprehensive logging for template confidence, coordinates, and validation results

### Advanced Scroll Strategy
- **Three-Phase Approach**:
  1. **Pre-Load Phase**: Aggressive downward scrolling until screenshot-based end detection
  2. **Anchor Return Phase**: Dynamic upward scrolling with template matching until "Alle Kommentare" found
  3. **Adaptive Extraction Phase**: Dynamic screenshot-based extraction with button detection
- **Content-Aware Termination**: Uses screenshot hashing instead of arbitrary attempt limits

### Technical Implementation
- **New Functions Added**:
  - `preload_all_facebook_comments()`: Aggressive pre-scrolling with screenshot-based end detection
  - `return_to_alle_kommentare_anchor()`: Dynamic anchor return with template matching
  - `extract_comments_adaptive()`: Adaptive extraction replacing fixed-cycle approach
  - `is_within_modal_bounds()`: Bounding-box validation for click coordinates
  - `pre_click_validate()`: Secondary validation using cropped screenshots
  - `find_button_with_ocr_enhanced()` & `find_button_with_template_matching_enhanced()`: Enhanced detection with validation
- **Screenshot Hashing**: Content-based detection using `hash(base64_data[:1000])` for performance
- **Dynamic Logging**: Progress tracking with unchanged screen counters and attempt statistics

### Bug Fixes & Optimizations
- **False Positive Reduction**: Increased template matching thresholds and added double validation
- **Numpy Serialization Fix**: Convert `numpy.int64` coordinates to native Python `int` before JSON serialization
- **AttributeError Fixes**: Corrected `MatchResult` attribute access (`.center[0]` instead of `.get("x")`)
- **IndexError Protection**: Added bounds checking in OCR word processing
- **Fallback Mechanisms**: Enhanced functions fall back to original implementations on errors

### Facebook Workflow Updates (2025-08-29)
- **Dynamic Content Discovery**: No more fixed cycle limits - adapts to actual post length
- **Intelligent Scroll Distance**: 8 wheel_times for upward scrolling (faster return), 5 for downward (consistent loading)
- **Content-Aware Stopping**: Screenshot comparison determines when scrolling should stop
- **Robust Anchor Detection**: High-confidence template matching (0.8+ threshold) with multiple validation layers

### Performance Improvements
- **Reduced Screenshot Detection**: Optimized from 10 to 5 consecutive unchanged screens for faster end detection
- **Parallel Tool Execution**: Maximized concurrent operations where possible
- **Efficient Hashing**: Screenshot hashing uses first 1000 characters for speed while maintaining accuracy

### Enhanced Profile Detection & Safety (2025-08-29)
- **Intelligent Profile Recognition**: Smart filtering system that distinguishes between real buttons and profile links
- **Positive Button Whitelist**: Immediate recognition of valid button text ('antwort', 'ansehen', 'kommentar', etc.) bypasses profile detection
- **False Positive Prevention**: Removed short time indicators ('h', 'm', 'd', 'w') that caused false profile matches
- **Two-Stage Validation**: 
  1. **Whitelist Check**: If crop contains known button phrases → immediate approval
  2. **Profile Check**: Only if not a clear button → check for profile indicators (names, long time phrases)
- **Context-Aware Detection**: Considers word length (≥3 characters) and context to avoid misidentifying button text as profiles
- **Comprehensive Logging**: Detailed OCR safety logs showing exactly why buttons are approved or rejected

### Chrome-MCP Integration (2025-08-29)
- **Hybrid MCP Architecture**: Windows-MCP for UI automation + Chrome-MCP for precise browser API access
- **Pixel-Perfect Scroll Measurement**: `window.pageYOffset` tracking for exact distance measurement during pre-scrolling
- **Advanced Navigation Strategy**:
  - `measure_scroll_distance_and_preload()`: Measures actual pixel distance during content loading
  - `return_to_anchor_with_distance()`: Uses measured distance for fast, precise anchor return
- **Robust Fallback System**: Chrome-MCP failures automatically fall back to Windows-MCP screenshot-based methods
- **Browser API Access**: Direct JavaScript injection for scroll position, page dimensions, and precise navigation
- **Dependency Management**: Seamless integration with existing `uv` virtual environment setup

### Direct Comment URL Enrichment (2025-08-30)
- **DOM Anchor Collection**: Via Chrome-MCP `chrome_inject_script`, we collect anchors likely representing comment permalinks (href contains `comment_id`, `permalink_comment_id`, `reply_comment_id`, `pfbid`).
- **Matching Strategy**: We attach URLs to OCR/CV-detected comments using:
  - Author token overlap (first/last name)
  - Vertical proximity (comment y vs. anchor rect.top, DPR-aware)
  - Bounded Jaccard overlap on content
- **API Exposure**: Fields `direct_url`, `url_confidence`, `url_source` appear in scan results and evidence metadata.
- **Frontend Integration**: 
  - **CommentReview.tsx**: Direct URLs shown as clickable links; URL confidence as color-coded badges; thread hierarchy as level badges and reply context
  - **ScanStatus.tsx**: Enhanced statistics showing URL extraction coverage, confidence averages, thread distribution (main vs replies), and extraction source breakdown
