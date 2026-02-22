"""
File upload routes (simplified without session management).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, UploadFile, status

from app.config import get_settings
from app.models.schemas import UploadResponse
from app.services.file_manager import FileManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["files"])

_file_manager = FileManager()


@router.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile) -> UploadResponse:
    """
    Upload a CSV file for analysis.

    - Validates file type and size
    - Converts to Parquet and saves to shared volume
    - Returns file metadata
    
    No session tracking or TTL - files are ephemeral and cleaned up manually.
    """
    settings = get_settings()

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    # Read file content
    content = await file.read()

    # Enforce size limit
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size is {settings.MAX_UPLOAD_SIZE_MB}MB.",
        )

    try:
        uploaded_file = await _file_manager.save_upload(
            filename=file.filename,
            content=content,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    logger.info(
        "File uploaded: file_id=%s filename=%s rows=%d",
        uploaded_file.file_id,
        uploaded_file.original_filename,
        uploaded_file.row_count,
    )

    # Build preview
    preview = _file_manager.get_preview(uploaded_file.storage_path, n_rows=5)

    return UploadResponse(
        file_id=uploaded_file.file_id,
        original_filename=uploaded_file.original_filename,
        row_count=uploaded_file.row_count,
        columns=uploaded_file.columns,
        preview=preview,
    )
