"""
Application entry point.

Lifespan: creates upload dir on startup.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.api.routes import chat, files, sessions
from app.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    settings = get_settings()

    # ── Startup ───────────────────────────────────────────────────────────
    logger.info("Starting Agentic Data Analysis API")
    logger.info("Model: %s", settings.GEMINI_MODEL)

    # Ensure upload directory exists
    Path(settings.DATA_DIR).mkdir(parents=True, exist_ok=True)
    logger.info("Upload directory: %s", settings.DATA_DIR)

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    logger.info("Shutting down...")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agentic Data Analysis",
        description="Multi-agent CSV analysis powered by Gemini 2.5 Flash",
        version="1.0.0",
        lifespan=lifespan,
    )

    # API routes
    app.include_router(sessions.router)
    app.include_router(files.router)
    app.include_router(chat.router)

    # Serve frontend static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/")
        async def index():
            return FileResponse(str(static_dir / "index.html"))

    return app


app = create_app()
