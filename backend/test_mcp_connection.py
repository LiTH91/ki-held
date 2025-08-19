import asyncio
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))
from backend.mcp_client import MCPClient

async def test_connection():
    client = MCPClient()

    # Test State-Tool command (async context manager handles connect/disconnect)
    response = await client.send_command('State-Tool', {'use_vision': False})
    # Print the raw result string
    print('State-Tool:', getattr(response, 'data', response))
    
    # Test Launch-Tool command
    response = await client.send_command('Launch-Tool', {'name': 'notepad'})
    print('Launch-Tool:', getattr(response, 'data', response))
    
    # Test Click-Tool command
    response = await client.send_command('Click-Tool', {
        'loc': [100, 100],
        'button': 'left',
        'clicks': 1
    })
    print('Click-Tool:', getattr(response, 'data', response))
    
    # Wait a bit to see the effects (optional)
    await asyncio.sleep(2)
    
if __name__ == "__main__":
    import asyncio
    asyncio.run(test_connection())