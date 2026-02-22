"""Simple file model without Redis"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class UploadedFile(BaseModel):
    """Represents an uploaded CSV file"""
    file_id: str
    original_filename: str
    storage_path: str
    row_count: int
    columns: list[str]
    dtypes: dict[str, str]
    size_bytes: int = 0
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
