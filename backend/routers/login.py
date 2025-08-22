from fastapi import APIRouter, HTTPException
from backend.models import LoginGuide
from backend.logger import setup_logging

log = setup_logging()

router = APIRouter()

@router.get("/login-guide/{platform}", response_model=LoginGuide)
async def get_login_guide(platform: str):
    log.info(f"[REQUEST] Login guide requested for: {platform}")
    guides = {
        "facebook": LoginGuide(
            platform="Facebook",
            steps=[
                "Open Facebook in your browser",
                "Click 'Log In'",
                "Enter your credentials",
                "Complete any security checks",
                "Return to the application"
            ],
            notes="Make sure to use a supported browser"
        )
    }
    
    if platform not in guides:
        raise HTTPException(status_code=404, detail="Platform guide not found")
        
    return guides[platform]
