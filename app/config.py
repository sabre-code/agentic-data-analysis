from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── LLM ─────────────────────────────────────────────────────────────────
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # ── Executor sidecar ────────────────────────────────────────────────────
    EXECUTOR_URL: str = "http://localhost:8080"
    EXECUTOR_TIMEOUT_SECONDS: int = 30
    EXECUTOR_MAX_RETRIES: int = 2  # self-correction retries on code error

    # ── Redis ────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379"

    # ── File storage ─────────────────────────────────────────────────────────
    DATA_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 50

    # ── App ───────────────────────────────────────────────────────────────────
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    DEBUG: bool = False
    ALLOWED_ORIGINS: list[str] = ["*"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
