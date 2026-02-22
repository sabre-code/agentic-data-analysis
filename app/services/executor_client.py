"""
Executor client — calls the Docker sidecar executor service.

Sends code + file_path (never the data itself) via HTTP to the executor.
The executor loads the file from the shared volume and runs the code.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class ExecutorClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._url = settings.EXECUTOR_URL
        self._default_timeout = settings.EXECUTOR_TIMEOUT_SECONDS

    async def execute(
        self,
        code: str,
        file_path: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """
        Send code to the executor sidecar.

        Args:
            code: Python code string to execute
            file_path: Absolute path to Parquet file on shared volume.
                       The executor loads df = pd.read_parquet(file_path).
                       If None, no DataFrame is injected.
            timeout_seconds: Execution timeout (defaults to config value)

        Returns:
            {
                "stdout": str,          # captured print() output
                "result": dict,         # variables named `result` set by code
                "error": str | None     # traceback string if execution failed
            }
        """
        timeout = timeout_seconds or self._default_timeout
        payload: dict[str, Any] = {
            "code": code,
            "timeout": timeout,
        }
        if file_path:
            payload["file_path"] = file_path

        try:
            async with httpx.AsyncClient(
                timeout=timeout + 10  # slightly longer than exec timeout
            ) as client:
                response = await client.post(
                    f"{self._url}/execute",
                    json=payload,
                )
                response.raise_for_status()
                return response.json()

        except httpx.TimeoutException:
            logger.error("Executor request timed out after %ds", timeout + 10)
            return {
                "stdout": "",
                "result": {},
                "error": f"Executor service timed out. The code may have exceeded {timeout}s.",
            }
        except httpx.ConnectError:
            logger.error("Cannot connect to executor at %s", self._url)
            return {
                "stdout": "",
                "result": {},
                "error": "Code executor is unavailable. Please try again shortly.",
            }
        except httpx.HTTPStatusError as e:
            logger.error("Executor HTTP error: %s", e)
            return {
                "stdout": "",
                "result": {},
                "error": f"Executor service error: {e.response.status_code}",
            }

    async def health_check(self) -> bool:
        """Returns True if executor sidecar is reachable."""
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(f"{self._url}/health")
                return r.status_code == 200
        except Exception:
            return False
