"""File upload/download/delete endpoints."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse

from ..deps import require_user
from ..services.temp_files import (
    MAX_FILE_SIZE,
    delete_temp_file,
    get_file_by_id,
    list_temp_files,
    save_temp_file,
    validate_file,
    validate_file_content,
    _validate_attachment_id,
)
from priva_common.user_store import UserRecord

router = APIRouter(prefix="/api/sandbox/agent-attachments", tags=["agent-attachments"])


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user: UserRecord = Depends(require_user),
):
    # Bounded: validate_file() checks the size, but only once the whole body is
    # already resident. Read one byte past the ceiling so an oversized upload is
    # rejected without ever being held in memory.
    file_data = await file.read(MAX_FILE_SIZE + 1)
    if len(file_data) > MAX_FILE_SIZE:
        raise HTTPException(
            413, f"File exceeds the {MAX_FILE_SIZE // (1024 * 1024)}MB upload limit")
    filename = file.filename or "upload"
    validate_file(filename, len(file_data))
    validate_file_content(filename, file_data)
    attachment_id, safe_name, full_path, size = save_temp_file(
        user.username, filename, file_data
    )
    return {
        "attachment_id": attachment_id,
        "name": filename,
        "safe_name": safe_name,
        "path": full_path,
        "size": size,
        # Compatibility aliases for existing clients.
        "uuid": attachment_id,
        "filesystem_name": safe_name,
        "upload_name": filename,
    }


@router.delete("/{attachment_id}")
async def delete_file(
    attachment_id: str,
    user: UserRecord = Depends(require_user),
):
    _validate_attachment_id(attachment_id)
    delete_temp_file(user.username, attachment_id)
    return {"status": "ok"}


@router.get("/")
async def list_files(
    user: UserRecord = Depends(require_user),
    date: str | None = Query(default=None),
):
    return {"files": list_temp_files(user.username, date_filter=date)}


@router.get("/{attachment_id}")
async def download_file(
    attachment_id: str,
    user: UserRecord = Depends(require_user),
):
    _validate_attachment_id(attachment_id)
    file_path, original_name, mime_type = get_file_by_id(user.username, attachment_id)
    # RFC 5987: use filename* with UTF-8 encoding for non-ASCII filenames
    encoded_name = quote(original_name)
    disposition = f"attachment; filename*=UTF-8''{encoded_name}"
    return FileResponse(
        path=file_path,
        media_type=mime_type,
        headers={"Content-Disposition": disposition},
    )
