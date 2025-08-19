from pydantic import BaseModel
from typing import List, Optional

class ScanRequest(BaseModel):
    url: str

class ScanResponse(BaseModel):
    scan_id: str

class LoginGuide(BaseModel):
    platform: str
    steps: List[str]
    notes: Optional[str] = None

class AssignUserRequest(BaseModel):
    username: str


