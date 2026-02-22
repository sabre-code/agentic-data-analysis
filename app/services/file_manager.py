"""
File manager — handles CSV upload and Parquet conversion (simplified without Redis).

On upload:
  1. Validate file type and size
  2. Save original CSV to shared volume as Parquet (faster reads, typed columns)
  3. Build schema summary (columns, dtypes, row_count)
  4. Return UploadedFile model

The Parquet file path is passed to the executor on each code execution call.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import get_settings
from app.models.file import UploadedFile

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {"text/csv", "application/csv", "text/plain", "application/octet-stream"}
ALLOWED_EXTENSIONS = {".csv"}


class FileManager:
    def __init__(self) -> None:
        settings = get_settings()
        self._data_dir = Path(settings.DATA_DIR)
        self._max_size_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        self._data_dir.mkdir(parents=True, exist_ok=True)

    async def save_upload(
        self,
        filename: str,
        content: bytes,
    ) -> UploadedFile:
        """
        Validate, save, and profile an uploaded CSV file.

        Returns an UploadedFile model.
        Raises ValueError for invalid files.
        """
        # ── Validate ──────────────────────────────────────────────────────
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(f"Only CSV files are supported. Got: {ext}")

        if len(content) > self._max_size_bytes:
            raise ValueError(
                f"File too large. Maximum size is {self._max_size_bytes // (1024*1024)}MB."
            )

        # ── Parse CSV ─────────────────────────────────────────────────────
        try:
            import io
            df = pd.read_csv(io.BytesIO(content))
        except Exception as e:
            raise ValueError(f"Could not parse CSV file: {e}") from e

        if df.empty:
            raise ValueError("CSV file is empty or has no data rows.")

        # ── Save as Parquet to shared volume ──────────────────────────────
        file_id = str(uuid.uuid4())
        parquet_path = self._data_dir / f"{file_id}.parquet"

        try:
            df.to_parquet(parquet_path, index=True, engine="pyarrow")
        except Exception as e:
            raise ValueError(f"Failed to save file: {e}") from e

        logger.info(
            "Saved upload: file_id=%s filename=%s rows=%d cols=%d path=%s",
            file_id, filename, len(df), len(df.columns), parquet_path,
        )

        # ── Build schema summary ──────────────────────────────────────────
        dtypes = {col: str(dtype) for col, dtype in df.dtypes.items()}

        return UploadedFile(
            file_id=file_id,
            original_filename=filename,
            storage_path=str(parquet_path),
            row_count=len(df),
            columns=df.columns.tolist(),
            dtypes=dtypes,
        )

    def get_preview(self, file_path: str, n_rows: int = 5) -> list[dict[str, Any]]:
        """Return first n_rows of the file as a list of dicts for API preview."""
        try:
            df = pd.read_parquet(file_path)
            return df.head(n_rows).to_dict(orient="records")
        except Exception as e:
            logger.warning("Could not generate preview for %s: %s", file_path, e)
            return []

    def get_schema_for_prompt(self, file: UploadedFile) -> str:
        """
        Return a compact schema description suitable for inclusion in an LLM prompt.
        """
        lines = [
            f"Dataset: {file.original_filename}",
            f"Rows: {file.row_count:,}",
            f"Columns ({len(file.columns)}):",
        ]
        for col in file.columns:
            dtype = file.dtypes.get(col, "unknown")
            lines.append(f"  - {col} ({dtype})")
        return "\n".join(lines)

    def cleanup_file(self, file_path: str) -> None:
        """Remove a file from the shared volume."""
        try:
            path = Path(file_path)
            if path.exists():
                path.unlink()
                logger.info("Cleaned up file: %s", file_path)
        except Exception as e:
            logger.warning("Failed to clean up %s: %s", file_path, e)
