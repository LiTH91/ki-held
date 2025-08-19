from pathlib import Path
from datetime import datetime
import shutil
import uuid

EVIDENCE_DIR = Path("../evidence")


def user_folder(username: str, create_if_missing: bool) -> Path:
    """
    Returns the path to the user's folder, creating it if it doesn't exist and create_if_missing is True.
    """
    user_dir = EVIDENCE_DIR / username
    if create_if_missing:
        user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def temp_case_folder() -> Path:
    """
    Creates and returns a temporary case folder path.
    """
    temp_dir = EVIDENCE_DIR / "tmp" / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def move_to_user_case(temp_folder: Path, username: str) -> Path:
    """
    Moves the contents of the temporary folder to a user-specific case folder.
    """
    timestamp_case_dir = user_folder(username, create_if_missing=True) / datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    shutil.move(str(temp_folder), str(timestamp_case_dir))
    return timestamp_case_dir

