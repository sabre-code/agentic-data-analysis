"""
Redis client for session and conversation history management.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)

# Session TTL: 30 days
SESSION_TTL = 30 * 24 * 60 * 60


class RedisClient:
    """Async Redis client for session management."""
    
    def __init__(self) -> None:
        settings = get_settings()
        self._client: redis.Redis | None = None
        self._redis_url = settings.REDIS_URL
    
    async def _get_client(self) -> redis.Redis:
        """Get or create Redis connection."""
        if self._client is None:
            self._client = redis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._client
    
    async def close(self) -> None:
        """Close Redis connection."""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    # ── Session Management ────────────────────────────────────────────────
    
    async def create_session(self) -> str:
        """Create a new session and return session_id."""
        client = await self._get_client()
        session_id = str(uuid.uuid4())
        
        # Create session metadata
        session_key = f"session:{session_id}:meta"
        meta = {
            "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_active": datetime.now(timezone.utc).isoformat(),
            "message_count": "0",
        }
        
        await client.hset(session_key, mapping=meta)  # type: ignore
        await client.expire(session_key, SESSION_TTL)
        
        logger.info("Created session: %s", session_id)
        return session_id
    
    async def session_exists(self, session_id: str) -> bool:
        """Check if session exists."""
        client = await self._get_client()
        session_key = f"session:{session_id}:meta"
        exists = await client.exists(session_key)
        return bool(exists)
    
    async def touch_session(self, session_id: str) -> None:
        """Update session last_active timestamp and reset TTL."""
        client = await self._get_client()
        session_key = f"session:{session_id}:meta"
        
        await client.hset(
            session_key,
            "last_active",
            datetime.now(timezone.utc).isoformat(),
        )
        await client.expire(session_key, SESSION_TTL)
        
        # Also extend messages TTL
        messages_key = f"session:{session_id}:messages"
        await client.expire(messages_key, SESSION_TTL)
    
    # ── Message History ───────────────────────────────────────────────────
    
    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Save a message to session history."""
        client = await self._get_client()
        messages_key = f"session:{session_id}:messages"
        
        message = {
            "id": str(uuid.uuid4()),
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        
        # Store as JSON string in Redis list
        await client.rpush(messages_key, json.dumps(message))
        await client.expire(messages_key, SESSION_TTL)
        
        # Update message count
        session_key = f"session:{session_id}:meta"
        await client.hincrby(session_key, "message_count", 1)
    
    async def get_messages(
        self,
        session_id: str,
        last_n: int = -1,
    ) -> list[dict[str, Any]]:
        """
        Get message history for a session.
        
        Args:
            session_id: Session identifier
            last_n: Number of recent messages to retrieve (-1 = all)
        
        Returns:
            List of message dicts with role, content, timestamp, metadata
        """
        client = await self._get_client()
        messages_key = f"session:{session_id}:messages"
        
        if last_n == -1:
            # Get all messages
            raw_messages = await client.lrange(messages_key, 0, -1)
        else:
            # Get last N messages
            raw_messages = await client.lrange(messages_key, -last_n, -1)
        
        messages = [json.loads(msg) for msg in raw_messages]
        return messages
    
    async def get_conversation_for_gemini(
        self,
        session_id: str,
        max_messages: int = 20,
    ) -> list[dict[str, str]]:
        """
        Get conversation history formatted for Gemini API.
        
        Returns simplified format: [{"role": "user|model", "content": "..."}]
        Only includes user/assistant messages, skips metadata.
        """
        messages = await self.get_messages(session_id, last_n=max_messages)
        
        gemini_messages = []
        for msg in messages:
            role = msg["role"]
            # Map "assistant" to "model" for Gemini
            if role == "assistant":
                role = "model"
            elif role == "user":
                pass
            else:
                continue  # Skip agent/system messages
            
            gemini_messages.append({
                "role": role,
                "content": msg["content"],
            })
        
        return gemini_messages
    
    # ── Active File Tracking ──────────────────────────────────────────────
    
    async def set_active_file(self, session_id: str, file_id: str) -> None:
        """Set the active file for a session."""
        client = await self._get_client()
        session_key = f"session:{session_id}:meta"
        await client.hset(session_key, "active_file_id", file_id)
    
    async def get_active_file(self, session_id: str) -> str | None:
        """Get the active file ID for a session."""
        client = await self._get_client()
        session_key = f"session:{session_id}:meta"
        file_id = await client.hget(session_key, "active_file_id")
        return file_id if file_id else None
