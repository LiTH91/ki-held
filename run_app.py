import uvicorn
import sys
from pathlib import Path

# Add the project root to sys.path to ensure 'backend' package is discoverable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

