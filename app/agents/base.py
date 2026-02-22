"""
BaseAgent — abstract contract that all specialist agents must implement.

Each agent is stateless. State lives in AgentHandoff (passed in) and
AgentResult (returned). The Orchestrator owns the state assembly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator

from app.models.handoff import AgentHandoff, AgentResult


class BaseAgent(ABC):
    """Abstract base for all specialist agents."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable agent name (used in SSE agent_switch events)."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """What this agent does — used in Orchestrator tool declarations."""
        ...

    @abstractmethod
    async def run(self, handoff: AgentHandoff) -> AgentResult:
        """
        Execute the agent's task.

        Args:
            handoff: Context from the Orchestrator including user query,
                     conversation history, file metadata, and prior artifacts.

        Returns:
            AgentResult with text_content, artifacts, and success status.
        """
        ...

    async def stream(
        self, handoff: AgentHandoff
    ) -> AsyncGenerator[tuple[str, str], None]:
        """
        Optional streaming variant. Yields (chunk_type, content) tuples.
        Default implementation runs .run() and yields the full result at once.
        Override in agents that support token-level streaming.
        """
        result = await self.run(handoff)
        if result.text_content:
            yield ("text", result.text_content)
        if result.chart_json:
            import json
            yield ("chart_plotly", json.dumps(result.chart_json))
        if result.generated_code:
            yield ("code", result.generated_code)
