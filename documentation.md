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
- backend/mcp_service.py: high-level helpers that call MCP tools (navigate/get_dom/scroll/click).
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

## Recent changes
- Windows-MCP `Type-Tool` (in `Windows-MCP/main.py`):
  - Applies the same safe-click guards as `Click-Tool` (clamps to safe rect, avoids desktop/background, forbids top-corner/X regions, skips images/hyperlinks, blocks Close/Schließen buttons).
  - Uses boolean `clear` parameter (`if clear:`) instead of checking string `'True'`.
  - Impact: prevents accidental clicks on window close buttons and unintended navigation when focusing inputs before typing.
