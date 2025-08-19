import hashlib
import json
from pathlib import Path
from datetime import datetime


def sha256_file(path: Path) -> str:
    """
    Computes the SHA-256 hash of a file.
    """
    sha256_hash = hashlib.sha256()
    with path.open("rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def create_manifest(platform, source_url, post_data: dict, comments: list[dict], files: dict[str, Path], classification_summary: dict, save_folder: Path) -> Path:
    """
    Creates a manifest JSON file with the given data and saves it to the specified folder.
    """
    manifest_data = {
        "platform": platform,
        "collected_at": datetime.utcnow().isoformat(),
        "source_url": source_url,
        "post": post_data,
        "comments": comments,
        "files": {name: sha256_file(path) for name, path in files.items()},
        "classification": classification_summary
    }

    manifest_path = save_folder / "manifest.json"
    with manifest_path.open("w") as manifest_file:
        json.dump(manifest_data, manifest_file, indent=4)

    return manifest_path

