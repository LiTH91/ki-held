$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition

param(
    [switch]$ForceInstall,
    [switch]$TailLogs
)

# Compute defaults: TailLogs defaults to true if not provided
$Force = $ForceInstall.IsPresent
$EnableTail = if ($PSBoundParameters.ContainsKey('TailLogs')) { $TailLogs.IsPresent } else { $true }

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

function Ensure-BackendEnv {
    Write-Host "Ensuring backend uv venv and dependencies..."
    Push-Location $scriptRoot
    if (-not (Test-Path ".venv") -or $Force) {
        uv venv | Out-Null
        uv pip install -r (Join-Path $backendPath "requirements.txt") | Out-Null
    } else {
        # Quick import probe; install if imports fail
        & uv run python -c "import fastapi, fastmcp" 2>$null
        if ($LASTEXITCODE -ne 0) {
            uv pip install -r (Join-Path $backendPath "requirements.txt") | Out-Null
        }
    }
    Pop-Location
}

function Ensure-McpEnv {
    if (-not (Test-Path $mcpPath)) {
        Write-Host "WARNING: Windows-MCP directory not found at $mcpPath"
        return
    }
    Write-Host "Ensuring Windows-MCP uv venv and dependencies..."
    Push-Location $mcpPath
    if (-not (Test-Path ".venv") -or $Force) {
        uv venv | Out-Null
        uv pip install fastmcp uiautomation pyautogui pyperclip markdownify humancursor requests | Out-Null
    } else {
        & uv run python -c "import fastmcp, uiautomation, pyautogui, pyperclip, markdownify, humancursor, requests" 2>$null
        if ($LASTEXITCODE -ne 0) {
            uv pip install fastmcp uiautomation pyautogui pyperclip markdownify humancursor requests | Out-Null
        }
    }
    Pop-Location
}

Ensure-BackendEnv
Ensure-McpEnv

# --- Process Startup ---

# Windows-MCP is started on-demand by the backend via STDIO transport
Write-Host "Windows-MCP will be launched on-demand by the backend..."

# Start Backend in a new PowerShell window (inherits env), using uv
if (Test-Path $backendPath) {
    Write-Host "Starting backend server in a new window..."
    $backendCmd = "Set-Location `"$scriptRoot`"; $env:PYTHONUTF8=1; $env:API_KEY=`"$($env:API_KEY -ne $null ? $env:API_KEY : 'dev')`"; $env:evidence_signature_key=`"$($env:evidence_signature_key -ne $null ? $env:evidence_signature_key : 'dev')`"; uv run python run_backend.py"
    Start-Process -FilePath "pwsh" -ArgumentList "-NoExit","-NoProfile","-Command",$backendCmd -WorkingDirectory $scriptRoot
    if ($EnableTail) {
        # Tail backend logs in a separate window
        $logPath = Join-Path $backendPath "logs/app.log"
        $logDir = Split-Path -Parent $logPath
        if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
        if (-not (Test-Path $logPath)) { New-Item -ItemType File -Path $logPath -Force | Out-Null }
        $tailCmd = "Set-Location `"$scriptRoot`"; Write-Host 'Tailing backend logs...'; Get-Content `"$logPath`" -Tail 100 -Wait"
        Start-Process -FilePath "pwsh" -ArgumentList "-NoExit","-NoProfile","-Command",$tailCmd -WorkingDirectory $scriptRoot
    }
} else {
    Write-Host "ERROR: Backend directory not found at $backendPath"
}

# Start Frontend in a new window (install if needed)
if (Test-Path $frontendPath) {
    Write-Host "Starting frontend application in a new window..."
    $frontendInit = if (-not (Test-Path (Join-Path $frontendPath "node_modules"))) { "npm install; " } else { "" }
    $frontendCmd = "Set-Location `"$frontendPath`"; ${frontendInit}npm run tauri dev"
    Start-Process -FilePath "pwsh" -ArgumentList "-NoExit","-NoProfile","-Command",$frontendCmd -WorkingDirectory $frontendPath
} else {
    Write-Host "ERROR: Frontend directory not found at $frontendPath"
}

Write-Host "All services have been launched in separate windows."
