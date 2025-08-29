## ki-held (Cyberhero)

A Windows desktop app to scan social media pages, gather evidence, and fight online hate speech. The app consists of:
- FastAPI backend (Python)
- Tauri desktop frontend (React + TypeScript)
- Windows-MCP automation service (FastMCP over STDIO)

### Latest Updates (2025-08-29)
- ✅ **Dynamic Content Loading**: Screenshot-based end detection for automatic lazy-content loading (Facebook comments)
- ✅ **Adaptive Scroll Strategy**: Three-phase approach (pre-load → anchor return → adaptive extraction) with no fixed limits
- ✅ **Enhanced Button Detection**: State-Tool fallback with OCR/Template matching, bounding-box filtering, and pre-click validation
- ✅ **Screenshot-Based Navigation**: Content-aware scrolling using screenshot hashing to detect page top/bottom
- ✅ **Robust Template Matching**: High-confidence thresholds (0.8+) with double validation and comprehensive error handling
- ✅ **Intelligent Profile Safety**: Smart whitelist system prevents false-positive profile clicks while preserving real button detection
- ✅ **Chrome-MCP Integration**: Hybrid architecture with pixel-perfect scroll measurement and browser API access
- ✅ **Performance Optimizations**: Reduced screenshot detection cycles, parallel tool execution, efficient hashing
- ✅ **Bug Fixes**: Fixed numpy serialization, AttributeError handling, IndexError protection, and template access issues

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
