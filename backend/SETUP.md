# MCP Server Setup

## Starten des MCP Servers

Der MCP Server muss in einem separaten PowerShell-Fenster gestartet werden. Führen Sie dazu die folgenden Befehle aus:

```powershell
# Wechseln Sie in das Windows-MCP Verzeichnis
cd "C:\Users\domin\Desktop\Domi APP\ki-held\Windows-MCP"

# Aktivieren Sie die virtuelle Umgebung
.\venv\Scripts\activate

# Installieren Sie die Dependencies (nur beim ersten Mal oder nach Updates nötig)
uv pip install -e .

# Starten Sie den MCP Server
python main.py
```

Der Server ist bereit, wenn Sie das FastMCP-Banner und die Meldung "Starting MCP server" sehen.

## Verwendung des MCP Clients

```python
from mcp_client import MCPClient

async def example():
    # Verbindung zum MCP Server herstellen
    client = MCPClient()
    await client.connect()
    
    try:
        # Befehle ausführen
        response = await client.send_command('State-Tool', {'use_vision': False})
        print(response)
        
    finally:
        # Verbindung trennen
        await client.disconnect()
```

## Verfügbare Befehle

Der MCP Server unterstützt folgende Hauptbefehle:

- `State-Tool`: Erfasst den Desktop-Zustand
- `Launch-Tool`: Startet eine Anwendung
- `Click-Tool`: Klickt an einer bestimmten Position
- `Type-Tool`: Gibt Text ein
- `Scroll-Tool`: Scrollt an einer Position
- `Move-Tool`: Bewegt den Mauszeiger
- `Shortcut-Tool`: Führt Tastenkombinationen aus
- `Key-Tool`: Drückt einzelne Tasten
- `Wait-Tool`: Wartet eine bestimmte Zeit
