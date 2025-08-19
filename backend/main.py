import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))
from backend.app import create_app
from backend.logger import setup_logging
log = setup_logging()
log.info(f"[MAIN STARTUP] cwd={os.getcwd()}, sys.path={sys.path}")

app = create_app()