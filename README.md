## ki-held (Cyberhero)

A Windows desktop app to scan social media pages, gather evidence, and fight online hate speech. The app consists of:
- FastAPI backend (Python)
- Tauri desktop frontend (React + TypeScript)
- Windows-MCP automation service (FastMCP over STDIO)

### Latest Updates (2025-08-26)
- ✅ **Fatal Abort on URL Change**: If navigation leaves the original Facebook post/modal, the workflow aborts immediately (no move/click/scroll/shortcut).
- ✅ **Hard-Stop Guard**: A global hard-stop flag blocks any further desktop interactions after a fatal event.
- ✅ **Modal-Safe Movements**: Cursor movements and clicks are constrained to the modal content area; expansion clicks are clamped using percentage-of-screenshot bounds.
- ✅ **Alle Kommentare Exemption**: The initial filter selection click is not clamped (prevents misses on varied layouts/zoom).
- ✅ **Start Script Hardening**: `start-app.ps1` now clears Python caches and kills lingering uvicorn processes before launch.
- ✅ **Unit Test**: Added test to ensure abort propagation stops extraction without scrolling.

### Prerequisites
- Windows 10/11
- Python 3.13 (on PATH)
- Node.js 18+ and npm
- Rust toolchain (for Tauri bundling)
- Git

Optional:
- uv (for Python workflows)

### Quick Start
1) Install backend deps
```powershell
cd backend
pip install -r requirements.txt
```

2) Start backend (project root) – recommended script
```powershell
cd ..
./start-app.ps1
```

3) Start desktop app (frontend)
```powershell
cd frontend
npm install
npm run tauri dev
```

Backend runs at `http://localhost:8000`. The desktop window opens via Tauri.

### Scan Flow (high level)
1) Frontend calls `POST /api/scan` to start a scan and receives a `scan_id`.
2) Frontend polls `GET /api/scan/{scan_id}` for status.
3) Backend launches Windows-MCP via STDIO, automates Chrome, captures evidence, and updates status.

### MCP Transport Note
We use FastMCP STDIO to launch `Windows-MCP/main.py` with the Windows-MCP venv’s Python. A version mismatch in FastMCP can print a CLI banner that breaks the STDIO handshake. We suppress the banner safely and write early startup breadcrumbs to `Windows-MCP/startup_debug.log`.

### Troubleshooting
- Cyberhero window doesn’t open: ensure `npm run tauri dev` is started from the `frontend` directory; verify Rust toolchain installed.
- MCP "Connection closed": check `Windows-MCP/startup_debug.log` and confirm `backend/mcp_client.py` logs show the correct venv Python and working dir.
- CORS issues: `backend/app.py` enables permissive CORS; ensure frontend calls endpoints under `/api`.
