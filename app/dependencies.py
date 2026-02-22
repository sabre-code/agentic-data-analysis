"""
FastAPI dependency injection providers.
"""
from __future__ import annotations

from app.agents.code_interpreter import CodeInterpreterAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.presentation import PresentationAgent
from app.agents.visualization import VisualizationAgent
from app.config import get_settings
from app.services.executor_client import ExecutorClient
from app.services.gemini_client import GeminiClient
from app.services.redis_client import RedisClient
from app.services.report_manager import ReportManager
from app.services.pdf_generator import PDFGenerator
from app.services.pptx_generator import PPTXGenerator


# ── Service dependencies ──────────────────────────────────────────────────────

def get_gemini_client() -> GeminiClient:
    return GeminiClient()


def get_executor_client() -> ExecutorClient:
    return ExecutorClient()


def get_redis_client() -> RedisClient:
    """Get Redis client for session management."""
    return RedisClient()


def get_report_manager() -> ReportManager:
    """Get report manager for PDF/PPTX generation."""
    settings = get_settings()
    return ReportManager(settings.REPORTS_DIR)


# ── Agent dependencies ────────────────────────────────────────────────────────

def get_orchestrator(redis_client: RedisClient | None = None) -> OrchestratorAgent:
    """
    Build the orchestrator agent with all sub-agents.
    
    Args:
        redis_client: Optional Redis client for session artifact persistence.
                      If provided, charts and other artifacts will be saved
                      to Redis for follow-up requests.
    """
    gemini = get_gemini_client()
    executor = get_executor_client()
    report_manager = get_report_manager()
    
    code_interpreter = CodeInterpreterAgent(gemini, executor)
    visualization = VisualizationAgent(gemini)
    presentation = PresentationAgent(
        gemini, 
        report_manager=report_manager,
        pdf_generator=PDFGenerator(),
        pptx_generator=PPTXGenerator(),
    )
    
    return OrchestratorAgent(
        gemini, 
        code_interpreter, 
        visualization, 
        presentation,
        redis_client=redis_client,
    )
