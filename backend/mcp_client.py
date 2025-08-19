import sys
import os
from pathlib import Path
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.client.transports import StdioTransport
import asyncio

class MCPClient:
    """
    MCPClient wraps the FastMCP Client using a Python STDIO transport.
    """
    def __init__(self):
        # Path to the Windows-MCP server directory
        mcp_dir = (Path(__file__).resolve().parent.parent / 'Windows-MCP').resolve()

        # Prefer Windows-MCP venv python if present; otherwise fall back to current python
        venv_python = mcp_dir / '.venv' / 'Scripts' / 'python.exe'
        python_exe = venv_python if venv_python.exists() else Path(sys.executable)

        # Minimal debug prints to surface pathing issues in logs
        print(f"[MCPClient] mcp_dir: {mcp_dir}")
        print(f"[MCPClient] python_exe: {python_exe}")
        print(f"[MCPClient] launch: '{python_exe}' main.py (cwd={mcp_dir})")

        # Ensure working directory exists
        if not mcp_dir.exists():
            raise RuntimeError(f"Windows-MCP directory not found: {mcp_dir}")

        # Pass through PATH so venv Scripts remain discoverable for child imports if needed
        env = os.environ.copy()

        transport = StdioTransport(
            command=str(python_exe),
            args=['main.py'],
            env=env,
            cwd=str(mcp_dir)
        )
        # Initialize the FastMCP client with the transport
        self.client = Client(transport)
        self._is_connected = False

    async def connect(self):
        # Log connection attempt
        print("[MCPClient] connect(): attempting to open MCP transport context...")
        if not self._is_connected:
            await self.client.__aenter__()
            self._is_connected = True
            print("[MCPClient] connect(): transport context opened and connected.")

    async def disconnect(self):
        if self._is_connected:
            print("[MCPClient] disconnect(): closing MCP transport context...")
            await self.client.__aexit__(None, None, None)
            self._is_connected = False
            print("[MCPClient] disconnect(): transport context closed.")

    async def send_command(self, command: str, params: dict | None = None, retries: int = 3, initial_delay: float = 0.5):
        """
        Call a tool on the MCP server with a retry mechanism.
        """
        for i in range(retries):
            attempt = i + 1
            print(f"[MCPClient] send_command(): attempt {attempt}/{retries} for command '{command}' with params {params}")
            try:
                # Open a new context for each call to ensure transport remains fresh
                async with self.client:
                    result = await self.client.call_tool(command, params or {})
                print(f"[MCPClient] send_command(): command '{command}' succeeded on attempt {attempt}")
                return result
            except ToolError as e:
                print(f"[MCPClient] send_command(): ToolError on attempt {attempt}: {e}")
                if attempt < retries:
                    delay = initial_delay * (2 ** (attempt - 1))
                    print(f"[MCPClient] send_command(): retrying after {delay:.2f}s...")
                    await asyncio.sleep(delay)
                    continue
                print(f"[MCPClient] send_command(): no more retries for command '{command}'")
                raise
            except Exception as e:
                print(f"[MCPClient] send_command(): unexpected error on attempt {attempt}: {e}", file=sys.stderr)
                raise

    # Example convenience method for screenshots (if implemented as tool)
    async def get_screenshot(self, path: str):
        """
        Retrieve a screenshot and save to the specified path.
        """
        result = await self.send_command('Screenshot-Tool', {'path': path})
        return bool(result)
