from pathlib import Path
import shutil
from fastapi import APIRouter, HTTPException

router = APIRouter()

# Function to create a ZIP file from a case folder
def zip_case_folder(case_folder: Path) -> Path:
    zip_path = case_folder.with_suffix('.zip')
    shutil.make_archive(str(case_folder), 'zip', str(case_folder))
    return zip_path

# Endpoint to export a case folder as a ZIP file
@router.get("/export")
async def export_case(path: str):
    case_folder = Path(path)
    if not case_folder.exists() or not case_folder.is_dir():
        raise HTTPException(status_code=404, detail="Case folder not found")
    zip_path = zip_case_folder(case_folder)
    return {"zip_path": str(zip_path)}

