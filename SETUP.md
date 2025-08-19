# Windows-MCP Server Setup

## Voraussetzungen
- Python 3.13+
- Git

## Installation

1. Repository klonen:
```powershell
git clone https://github.com/CursorTouch/Windows-MCP
cd Windows-MCP
```

2. Virtuelle Umgebung erstellen und aktivieren:
```powershell
python -m venv venv
.\venv\Scripts\activate
```

3. Dependencies installieren:
```powershell
python -m pip install --upgrade pip
python -m pip install uv
uv pip install -e .
```

## Server starten

### Methode 1: Vordergrund
```powershell
.\venv\Scripts\python main.py
```

### Methode 2: Hintergrund
```powershell
Start-Process -NoNewWindow -FilePath ".\venv\Scripts\python.exe" -ArgumentList "main.py"
```

## Server stoppen
- Bei Methode 1: Ctrl+C
- Bei Methode 2: 
```powershell
Get-Process python | Where-Object {$_.MainWindowTitle -eq ""} | Stop-Process
```

## Konfiguration
Die Server-Konfiguration erfolgt über `config.json`:
- Host: localhost
- Port: 3000
- Log-Level: INFO

## Wichtige Hinweise
- Der Server muss mit Administrator-Rechten ausgeführt werden
- Alle Pfade sind Windows 10/11 kompatibel
- Die Installation ist idempotent und kann mehrfach ausgeführt werden

