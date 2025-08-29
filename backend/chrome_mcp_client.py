"""
Chrome MCP Client for Browser-specific operations.
Provides scroll position tracking and browser automation features via Chrome Extension.
"""
import aiohttp
import asyncio
import logging
from typing import Dict, Any, Optional
from .logger import setup_logging
log = setup_logging()

class ChromeMCPClient:
    """Client for Chrome-MCP Server communication via HTTP."""
    
    def __init__(self, base_url: str = "http://127.0.0.1:12306"):
        """
        Initialize Chrome MCP Client.
        
        Args:
            base_url: Base URL for Chrome-MCP HTTP server (default: http://127.0.0.1:12306)
        """
        self.base_url = base_url
        self.mcp_url = f"{base_url}/mcp"
        self.session: Optional[aiohttp.ClientSession] = None
        self.is_connected = False
    
    async def _ensure_session(self):
        """Ensure aiohttp session is created."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
    
    async def connect(self) -> bool:
        """
        Test connection to Chrome-MCP server.
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            await self._ensure_session()
            
            # Test connection with a simple request
            async with self.session.get(f"{self.base_url}/health", timeout=5) as response:
                if response.status == 200:
                    self.is_connected = True
                    log.info("✅ Chrome-MCP connection established")
                    return True
                else:
                    log.warning(f"⚠️ Chrome-MCP server responded with status {response.status}")
                    return False
                    
        except Exception as e:
            log.warning(f"⚠️ Chrome-MCP connection failed: {e}")
            self.is_connected = False
            return False
    
    async def send_command(self, tool_name: str, parameters: Dict[str, Any] = None, max_retries: int = 3) -> Any:
        """
        Send command to Chrome-MCP server.
        
        Args:
            tool_name: Name of the Chrome-MCP tool to execute
            parameters: Parameters for the tool
            max_retries: Maximum number of retry attempts
            
        Returns:
            Tool execution result
        """
        if parameters is None:
            parameters = {}
            
        await self._ensure_session()
        
        # Prepare MCP request payload
        payload = {
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": parameters
            }
        }
        
        for attempt in range(1, max_retries + 1):
            try:
                log.debug(f"[ChromeMCP] Sending {tool_name} (attempt {attempt}/{max_retries})")
                
                async with self.session.post(
                    self.mcp_url,
                    json=payload,
                    timeout=30,
                    headers={"Content-Type": "application/json"}
                ) as response:
                    
                    if response.status == 200:
                        result = await response.json()
                        log.debug(f"[ChromeMCP] {tool_name} succeeded on attempt {attempt}")
                        return result.get("result", result)
                    else:
                        error_text = await response.text()
                        log.warning(f"[ChromeMCP] {tool_name} failed with status {response.status}: {error_text}")
                        
            except asyncio.TimeoutError:
                log.warning(f"[ChromeMCP] {tool_name} timeout on attempt {attempt}")
            except Exception as e:
                log.warning(f"[ChromeMCP] {tool_name} error on attempt {attempt}: {e}")
            
            if attempt < max_retries:
                await asyncio.sleep(1.0 * attempt)  # Exponential backoff
        
        log.error(f"❌ [ChromeMCP] {tool_name} failed after {max_retries} attempts")
        raise Exception(f"Chrome-MCP command '{tool_name}' failed after {max_retries} attempts")
    
    async def get_scroll_position(self) -> Dict[str, Any]:
        """
        Get current scroll position of the active tab.
        
        Returns:
            Dict with scroll position information:
            {
                "scrollTop": int,
                "scrollLeft": int, 
                "scrollHeight": int,
                "clientHeight": int,
                "scrollWidth": int,
                "clientWidth": int
            }
        """
        try:
            # Use chrome_inject_script to get scroll position
            script = """
            return {
                scrollTop: window.pageYOffset || document.documentElement.scrollTop,
                scrollLeft: window.pageXOffset || document.documentElement.scrollLeft,
                scrollHeight: document.documentElement.scrollHeight,
                clientHeight: document.documentElement.clientHeight,
                scrollWidth: document.documentElement.scrollWidth,
                clientWidth: document.documentElement.clientWidth
            };
            """
            
            result = await self.send_command("chrome_inject_script", {
                "script": script
            })
            
            log.debug(f"[ChromeMCP] Scroll position: {result}")
            return result
            
        except Exception as e:
            log.error(f"❌ [ChromeMCP] Failed to get scroll position: {e}")
            # Return fallback values
            return {
                "scrollTop": 0,
                "scrollLeft": 0,
                "scrollHeight": 0,
                "clientHeight": 0,
                "scrollWidth": 0,
                "clientWidth": 0
            }
    
    async def set_scroll_position(self, scroll_top: int, scroll_left: int = 0) -> bool:
        """
        Set scroll position of the active tab.
        
        Args:
            scroll_top: Vertical scroll position in pixels
            scroll_left: Horizontal scroll position in pixels
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            script = f"""
            window.scrollTo({scroll_left}, {scroll_top});
            return true;
            """
            
            await self.send_command("chrome_inject_script", {
                "script": script
            })
            
            log.debug(f"[ChromeMCP] Set scroll position to ({scroll_left}, {scroll_top})")
            return True
            
        except Exception as e:
            log.error(f"❌ [ChromeMCP] Failed to set scroll position: {e}")
            return False
    
    async def get_page_dimensions(self) -> Dict[str, int]:
        """
        Get page dimensions for percentage-based calculations.
        
        Returns:
            Dict with page dimensions:
            {
                "width": int,
                "height": int,
                "scrollHeight": int,
                "scrollWidth": int
            }
        """
        try:
            script = """
            return {
                width: window.innerWidth,
                height: window.innerHeight,
                scrollHeight: document.documentElement.scrollHeight,
                scrollWidth: document.documentElement.scrollWidth
            };
            """
            
            result = await self.send_command("chrome_inject_script", {
                "script": script
            })
            
            return result
            
        except Exception as e:
            log.error(f"❌ [ChromeMCP] Failed to get page dimensions: {e}")
            return {"width": 1920, "height": 1080, "scrollHeight": 1080, "scrollWidth": 1920}
    
    async def close(self):
        """Close the HTTP session."""
        if self.session and not self.session.closed:
            await self.session.close()
            log.debug("[ChromeMCP] Session closed")

# Global Chrome MCP client instance
chrome_mcp_client: Optional[ChromeMCPClient] = None

async def get_chrome_mcp_client() -> ChromeMCPClient:
    """Get or create global Chrome MCP client instance."""
    global chrome_mcp_client
    
    if chrome_mcp_client is None:
        chrome_mcp_client = ChromeMCPClient()
        
    # Test connection if not already connected
    if not chrome_mcp_client.is_connected:
        await chrome_mcp_client.connect()
    
    return chrome_mcp_client

async def cleanup_chrome_mcp():
    """Cleanup Chrome MCP client on shutdown."""
    global chrome_mcp_client
    
    if chrome_mcp_client:
        await chrome_mcp_client.close()
        chrome_mcp_client = None
