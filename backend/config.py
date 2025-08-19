import os
from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path
from dotenv import load_dotenv

# Load the .env file in this directory
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

class Settings(BaseSettings):
    # Server Configuration
    HOST: str = "localhost"
    PORT: int = 8000
    DEBUG: bool = True

    # MCP Configuration
    MCP_ENDPOINT: str = "http://localhost:5000"
    MCP_MAX_ACTIONS_PER_MIN: int = 20

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "app.log"

    # Security
    API_KEY: str
    evidence_signature_key: str

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

@lru_cache()
def get_settings():
    return Settings()

# Create base paths
BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

settings = get_settings()
print('[DEBUG config] Settings.API_KEY:', settings.API_KEY)


