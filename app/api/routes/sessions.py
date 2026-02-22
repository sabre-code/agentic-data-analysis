"""
Session routes — create and manage chat sessions.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_redis_client
from app.models.schemas import SessionCreateResponse, ConversationHistoryResponse, MessageResponse
from app.services.redis_client import RedisClient

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("", response_model=SessionCreateResponse)
async def create_session(
    redis: RedisClient = Depends(get_redis_client),
) -> SessionCreateResponse:
    """
    Create a new chat session.
    
    Returns a session_id that should be used for all subsequent /api/chat calls.
    Frontend should store this in localStorage.
    """
    session_id = await redis.create_session()
    
    # Get created_at from session metadata
    client = await redis._get_client()
    meta = await client.hgetall(f"session:{session_id}:meta")
    
    return SessionCreateResponse(
        session_id=session_id,
        created_at=meta.get("created_at", ""),
    )


@router.get("/{session_id}/messages", response_model=ConversationHistoryResponse)
async def get_conversation_history(
    session_id: str,
    redis: RedisClient = Depends(get_redis_client),
) -> ConversationHistoryResponse:
    """
    Retrieve full conversation history for a session.
    
    Used by frontend on page reload to restore prior conversation.
    """
    if not await redis.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    
    messages = await redis.get_messages(session_id)
    
    # Get active file if any
    active_file_id = await redis.get_active_file(session_id)
    active_file_info = None
    if active_file_id:
        active_file_info = {
            "file_id": active_file_id,
            "message": "File context available in this session",
        }
    
    return ConversationHistoryResponse(
        session_id=session_id,
        messages=[
            MessageResponse(
                id=m["id"],
                role=m["role"],
                content=m["content"],
                timestamp=m["timestamp"],
                metadata=m.get("metadata", {}),
            )
            for m in messages
        ],
        total=len(messages),
        active_file=active_file_info,
    )
