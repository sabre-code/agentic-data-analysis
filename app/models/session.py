"""
Core session and conversation data models.
Stored in Redis; serialized as JSON.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    AGENT = "agent"
    SYSTEM = "system"


# ── Message ───────────────────────────────────────────────────────────────────

class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Rich metadata — agent name, artifacts, chunk types surfaced to UI
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_redis(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_redis(cls, raw: str | bytes) -> "Message":
        if isinstance(raw, bytes):
            raw = raw.decode()
        return cls.model_validate_json(raw)


# ── Session metadata ──────────────────────────────────────────────────────────

class SessionMeta(BaseModel):
    session_id: str
    # Phase 2 hook — user_id populated by JWT auth middleware
    user_id: str | None = None
    title: str = "New Analysis"
    active_file_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    query_count: int = 0

    def to_redis_mapping(self) -> dict[str, str]:
        """Flatten to dict[str, str] for Redis HSET."""
        data = json.loads(self.model_dump_json())
        return {k: str(v) if v is not None else "" for k, v in data.items()}

    @classmethod
    def from_redis_mapping(cls, raw: dict[bytes, bytes]) -> "SessionMeta":
        decoded = {
            k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
            for k, v in raw.items()
        }
        # Convert empty strings back to None for optional fields
        for field in ("user_id", "active_file_id"):
            if decoded.get(field) == "":
                decoded[field] = None
        return cls.model_validate(decoded)
