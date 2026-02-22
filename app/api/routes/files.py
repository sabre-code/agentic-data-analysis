"""
File upload and download routes.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

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


@router.get("/reports/{filename}")
async def download_report(filename: str) -> FileResponse:
    """
    Download a generated PDF or PPTX report.
    
    Args:
        filename: The report filename (e.g., "Sales_Analysis_abc123.pdf")
        
    Returns:
        FileResponse with appropriate MIME type and content-disposition headers
    """
    settings = get_settings()
    reports_dir = Path(settings.REPORTS_DIR)
    
    # Security: Prevent path traversal attacks
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename"
        )
    
    file_path = reports_dir / filename
    
    # Check if file exists
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report file not found"
        )
    
    # Determine MIME type based on extension
    mime_types = {
        ".pdf": "application/pdf",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    
    file_extension = file_path.suffix.lower()
    media_type = mime_types.get(file_extension, "application/octet-stream")
    
    # Generate friendly display name (remove ULID from filename)
    # e.g., "Sales_Analysis_01H2X3Y4Z5.pdf" -> "Sales Analysis.pdf"
    display_name = filename
    if "_" in filename:
        parts = filename.rsplit("_", 1)
        if len(parts) == 2 and len(parts[1]) > 20:  # ULID is 26 chars + extension
            # Remove ULID, keep original name
            display_name = parts[0].replace("_", " ") + file_extension
    
    logger.info("Serving report file: %s (display: %s)", filename, display_name)
    
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=display_name,
        headers={
            "Content-Disposition": f'attachment; filename="{display_name}"'
        }
    )
