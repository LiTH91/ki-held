from pydantic import BaseModel, HttpUrl
from typing import Literal, Optional
from pathlib import Path

class ScanRequest(BaseModel):
    url: HttpUrl
    username: str
    mode: Literal["MCP"]

class Artifacts(BaseModel):
    user_save_path: Optional[str] = None
    manifest_path: Optional[str] = None
    report_path: Optional[str] = None

class ScanResponse(BaseModel):
    status: Literal["completed", "failed", "login_required"]
    hate_detected: bool = False
    error_message: Optional[str] = None
    artifacts: Optional[Artifacts] = None
    counts: Optional[int] = None

