$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition

# Define Paths
$mcpPath = Join-Path $scriptRoot "Windows-MCP"
$backendPath = Join-Path $scriptRoot "backend"
$frontendPath = Join-Path $scriptRoot "frontend"

# --- Cleanup ---
Write-Host "Killing all existing Python, Node.js, and cmd processes..."
Get-Process python, node, cmd -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host "Stopping any process listening on port 8000..."
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess |
  ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

# --- Process Validation and Startup ---

# Windows-MCP is started on-demand by the backend via STDIO transport
# No need to start it independently - it would crash without a client connection
Write-Host "Windows-MCP will be launched on-demand by the backend..."

# Start Backend in a new window
if (Test-Path $backendPath) {
    Write-Host "Starting backend server in a new window..."
    Start-Process -FilePath "cmd.exe" -ArgumentList "/k python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000" -WorkingDirectory $scriptRoot
} else {
    Write-Host "ERROR: Backend directory not found at $backendPath"
}

# Start Frontend in a new window
if (Test-Path $frontendPath) {
    Write-Host "Starting frontend application in a new window..."
    Start-Process -FilePath "cmd.exe" -ArgumentList "/k npm run tauri dev" -WorkingDirectory $frontendPath
} else {
    Write-Host "ERROR: Frontend directory not found at $frontendPath"
}

Write-Host "All services have been launched in separate windows."
