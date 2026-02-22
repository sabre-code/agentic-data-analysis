"""
FastAPI dependency injection providers.
"""
from __future__ import annotations

from app.agents.code_interpreter import CodeInterpreterAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.presentation import PresentationAgent
from app.agents.visualization import VisualizationAgent
from app.services.executor_client import ExecutorClient
from app.services.gemini_client import GeminiClient
from app.services.redis_client import RedisClient


# ── Service dependencies ──────────────────────────────────────────────────────

def get_gemini_client() -> GeminiClient:
    return GeminiClient()


def get_executor_client() -> ExecutorClient:
    return ExecutorClient()


def get_redis_client() -> RedisClient:
    """Get Redis client for session management."""
    return RedisClient()


# ── Agent dependencies ────────────────────────────────────────────────────────

def get_orchestrator() -> OrchestratorAgent:
    gemini = get_gemini_client()
    executor = get_executor_client()
    
    code_interpreter = CodeInterpreterAgent(gemini, executor)
    visualization = VisualizationAgent(gemini)
    presentation = PresentationAgent(gemini)
    
    return OrchestratorAgent(gemini, code_interpreter, visualization, presentation)
