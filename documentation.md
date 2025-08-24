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
- backend/mcp_service.py: high-level helpers that call MCP tools (navigate/get_dom/scroll/click); includes new progressive screenshot extraction functions for Facebook comment scraping.
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

## Recent changes (2025-08-24)

### Enhanced Facebook Comment Extraction
- **Progressive Screenshot Strategy**: Implemented `extract_comments_via_screenshots()` function using OCR + template matching
- **Multi-Method Detection**: Combines OCR text extraction with OpenCV template matching for robust button detection
- **Automatic Expansion**: Finds and clicks "alle XX Kommentare ansehen" and "Antwort ansehen" buttons to expand comment threads
- **Anti-Bot Bypass**: Uses screenshot-based extraction when Facebook blocks DOM/State-Tool access

### Safety Improvements
- **Enhanced Cursor Safety**: Added multi-layer safety movements after each expansion button click:
  - Primary safety movement: 150-250px away from click area
  - Secondary safety reset: `Safe-Center-Move-Tool` to screen center
  - Emergency fallback: Manual cursor positioning if safety measures fail
- **Cycle-Level Resets**: Cursor automatically resets to safe position at start of each extraction cycle
- **Error Handling**: Robust try-catch blocks around all safety movements with detailed logging

### Technical Implementation
- **New Functions Added**:
  - `extract_comments_via_screenshots()`: Main progressive extraction controller
  - `extract_visible_comments_ocr()`: OCR-based comment text extraction  
  - `find_and_click_expansion_buttons()`: Template matching for UI button detection
  - `parse_comments_from_ocr()`: Structured comment data parsing from OCR text
- **Template Matching**: Uses pre-defined PNG templates for button recognition
- **OCR Integration**: pytesseract for text extraction from screenshots
- **Human-like Behavior**: Random delays, natural scroll patterns, hesitation before clicks

### Bug Fixes
- **Missing Function Error**: Removed broken `screenshot_element()` call that caused scan crashes
- **Import Errors**: Fixed OCR and template service import issues
- **Indentation Issues**: Resolved syntax errors preventing backend startup

### Facebook Workflow Updates
- **State-Tool Fallback**: When State-Tool returns empty (Facebook blocking), automatically switches to OCR/template methods
- **Progressive Scrolling**: Intelligently scrolls to find more content when no expansion buttons are visible
- **Comment Integration**: Screenshot-extracted comments are properly integrated into final DOM output
- **Extraction Limits**: Configurable cycle limits (default: 15) with consecutive scroll attempt limits (2)
