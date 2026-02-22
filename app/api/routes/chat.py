"""
Chat route — SSE streaming endpoint for multi-agent responses with session support.

POST /api/chat?session_id=xxx&file_id=xxx  →  streams Server-Sent Events back to the browser.

Each SSE data payload is a JSON object:
  {"type": "agent_switch", "content": "Code Interpreter Agent is working...", "metadata": {}}
  {"type": "text",         "content": "Here are the top 3 products...",        "metadata": {}}
  {"type": "code",         "content": "import pandas as pd\n...",               "metadata": {}}
  {"type": "chart_plotly", "content": "{\"data\": [...], \"layout\": {...}}",   "metadata": {}}
  {"type": "error",        "content": "Something went wrong: ...",              "metadata": {}}
  {"type": "done",         "content": "",                                        "metadata": {}}
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.dependencies import get_orchestrator, get_redis_client
from app.models.schemas import ChatRequest, SSEChunk
from app.models.file import UploadedFile
from app.services.executor_client import ExecutorClient
from app.services.redis_client import RedisClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])

_executor = ExecutorClient()


@router.post("/chat")
async def chat(
    request: ChatRequest,
    session_id: str | None = Query(None, description="Session ID (optional - will auto-create if missing)"),
    file_id: str | None = Query(None, description="Optional file ID from upload response"),
    redis: RedisClient = Depends(get_redis_client),
) -> StreamingResponse:
    """
    Main chat endpoint. Returns a Server-Sent Events stream.
    
    If no session_id provided, creates a temporary session automatically.
    Optional file_id if analyzing uploaded data.
    
    Conversation history is loaded from Redis and new messages are saved.
    """
    # Auto-create session if not provided (backward compatibility)
    if not session_id:
        session_id = await redis.create_session()
        logger.info("Auto-created session: %s", session_id)
    
    # Verify session exists
    elif not await redis.session_exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found. Create a new session first.")
    
    # Touch session to update last_active and reset TTL
    await redis.touch_session(session_id)
    
    # Resolve file if provided
    active_file: UploadedFile | None = None
    if file_id:
        from app.config import get_settings
        settings = get_settings()
        file_path = Path(settings.DATA_DIR) / f"{file_id}.parquet"
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found. Please upload again.")
        
        # Build UploadedFile from existing parquet
        import pandas as pd
        try:
            df = pd.read_parquet(file_path)
            active_file = UploadedFile(
                file_id=file_id,
                original_filename=f"{file_id}.parquet",
                storage_path=str(file_path),
                row_count=len(df),
                columns=df.columns.tolist(),
                dtypes={col: str(dtype) for col, dtype in df.dtypes.items()},
            )
            # Track active file in session
            await redis.set_active_file(session_id, file_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Could not read file: {e}") from e
    
    # Load conversation history from Redis
    conversation_history = await redis.get_conversation_for_gemini(session_id, max_messages=20)

    # Build orchestrator with Redis client for artifact persistence
    orchestrator = get_orchestrator(redis_client=redis)

    # SSE generator with history persistence
    async def event_stream():
        assistant_response = ""  # Accumulate response for saving
        
        try:
            # Save user message
            await redis.save_message(session_id, "user", request.query)
            
            async for chunk_type, content in orchestrator.run_stream(
                user_query=request.query,
                conversation_history=conversation_history,
                active_file=active_file,
                session_id=session_id,
            ):
                chunk = SSEChunk(type=chunk_type, content=content)  # type: ignore[arg-type]
                yield chunk.to_sse()
                
                # Accumulate assistant text response
                if chunk_type == "text":
                    assistant_response += content

        except asyncio.CancelledError:
            logger.info("Client disconnected from SSE stream")
            raise
        except Exception as e:
            logger.error("SSE stream error: %s", e, exc_info=True)
            yield SSEChunk(type="error", content=f"Stream error: {str(e)}").to_sse()
        finally:
            # Save assistant response to history (with chart metadata if available)
            if assistant_response.strip():
                await redis.save_message(session_id, "assistant", assistant_response)
            
            yield SSEChunk(type="done").to_sse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Session-ID": session_id,  # Return session_id so frontend can persist it
        },
    )


@router.get("/health")
async def health() -> dict:
    """Health check endpoint — verifies executor connectivity."""
    executor_ok = False

    try:
        executor_ok = await _executor.health_check()
    except Exception:
        pass

    status = "ok" if executor_ok else "degraded"
    return {
        "status": status,
        "executor": executor_ok,
    }
