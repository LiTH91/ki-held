from loguru import logger
import sys
from pathlib import Path
from .config import settings, LOGS_DIR

def setup_logging():
    # Remove default handler
    logger.remove()

    # Add console handler
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=settings.LOG_LEVEL
    )

    # Add file handler
    log_file = LOGS_DIR / settings.LOG_FILE
    logger.add(
        log_file,
        rotation="500 MB",
        retention="10 days",
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level=settings.LOG_LEVEL
    )

    return logger