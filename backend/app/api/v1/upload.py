from pathlib import Path
import os
from fastapi import HTTPException

from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from pydantic import BaseModel

class DeleteLogoRequest(BaseModel):
    logo_url: str

router = APIRouter(prefix="/upload", tags=["Upload"])

UPLOAD_DIR = Path("uploads/logos")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}

MAX_FILE_SIZE = 2 * 1024 * 1024


@router.post("/logo")
async def upload_logo(
    file: UploadFile = File(...)
):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PNG, JPEG and WebP images are allowed.",
        )

    content = await file.read()

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Logo must be smaller than 2 MB.",
        )

    extension = ALLOWED_TYPES[file.content_type]

    filename = f"{uuid4()}{extension}"

    destination = UPLOAD_DIR / filename

    destination.write_bytes(content)

    return {
        "logo_url": f"/uploads/logos/{filename}"
    }


@router.delete("/logo")
async def delete_logo(
    request: DeleteLogoRequest,
):
    if not request.logo_url:
        raise HTTPException(
            status_code=400,
            detail="Logo URL is required.",
        )

    if not request.logo_url.startswith("/uploads/logos/"):
        raise HTTPException(
            status_code=400,
            detail="Invalid logo path.",
        )

    filename = Path(request.logo_url).name

    file_path = UPLOAD_DIR / filename

    if file_path.exists():
        file_path.unlink()

    return {
        "message": "Logo deleted successfully."
    }