"""
Pydantic schemas for FastAPI request/response contracts (simplified).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Session ───────────────────────────────────────────────────────────────────

class SessionCreateResponse(BaseModel):
    session_id: str
    created_at: datetime


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationHistoryResponse(BaseModel):
    session_id: str
    messages: list[MessageResponse]
    total: int
    active_file: dict[str, Any] | None = None


# ── Upload ────────────────────────────────────────────────────────────────────

class UploadResponse(BaseModel):
    file_id: str
    original_filename: str
    row_count: int
    columns: list[str]
    preview: list[dict[str, Any]] = Field(default_factory=list)


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)


# ── SSE Chunk types ───────────────────────────────────────────────────────────

SSEChunkType = Literal[
    "agent_switch",   # "Code Interpreter is analyzing…" banner
    "text",           # Markdown text streamed token by token
    "code",           # Code block (syntax highlighted)
    "chart_plotly",   # Plotly JSON spec string → rendered as interactive chart
    "error",          # Error message
    "done",           # Stream complete signal
]


class SSEChunk(BaseModel):
    type: SSEChunkType
    content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_sse(self) -> str:
        """Format as SSE data line."""
        import json
        return f"data: {json.dumps(self.model_dump())}\n\n"


# ── Health ────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    executor: bool


# ── Error ─────────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    code: str | None = None
