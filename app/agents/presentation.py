"""
Presentation Agent

Responsibilities:
  1. Synthesize all artifacts (code output, chart, schema) into a clear,
     structured markdown response for the user
  2. Stream the response token-by-token for real-time UX
  3. Include key insights, numbered findings, and contextual interpretation
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

from app.agents.base import BaseAgent
from app.models.handoff import AgentHandoff, AgentResult
from app.services.gemini_client import GeminiClient

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a senior data analyst communicating findings to a business audience.
Your job is to synthesize analysis results into clear, insightful, well-structured responses.

Guidelines:
- Lead with the direct answer to the user's question
- Use numbered lists for rankings, bullet points for insights
- Include specific numbers and percentages from the analysis
- Use **bold** for key metrics and findings
- Add brief business interpretation ("this suggests...", "notably...")
- Keep it concise — aim for 150-300 words unless the topic warrants more
- Do NOT mention code, DataFrames, or technical implementation details
- Write as if presenting to a business stakeholder
- If a chart was generated, reference it naturally ("as shown in the chart above")
- End with 1-2 suggested follow-up analyses if relevant
"""


class PresentationAgent(BaseAgent):
    def __init__(self, gemini: GeminiClient) -> None:
        self._gemini = gemini

    @property
    def name(self) -> str:
        return "Presentation"

    @property
    def description(self) -> str:
        return (
            "Synthesizes analysis results and charts into a clear, structured, "
            "business-friendly response. Use this to format and present findings "
            "after analysis and/or visualization is complete."
        )

    async def run(self, handoff: AgentHandoff) -> AgentResult:
        """Non-streaming version — collects full response then returns."""
        logger.info("📝 PRESENTATION AGENT started — synthesizing report")
        
        messages = self._build_messages(handoff)
        full_text = ""
        async for chunk in self._gemini.stream(
            messages=messages,
            system_prompt=SYSTEM_PROMPT,
            temperature=0.5,
        ):
            full_text += chunk

        logger.info("✅ PRESENTATION AGENT completed — %d chars", len(full_text))
        return AgentResult(
            agent_name=self.name,
            success=True,
            text_content=full_text,
        )

    async def stream(
        self, handoff: AgentHandoff
    ) -> AsyncGenerator[tuple[str, str], None]:
        """
        Streaming version — yields ("text", chunk) tuples for SSE.
        This is the primary method called by the Orchestrator for live streaming.
        """
        logger.info("📝 PRESENTATION AGENT started (streaming) — synthesizing report")
        
        messages = self._build_messages(handoff)

        async for chunk in self._gemini.stream(
            messages=messages,
            system_prompt=SYSTEM_PROMPT,
            temperature=0.5,
        ):
            yield ("text", chunk)

    def _build_messages(self, handoff: AgentHandoff) -> list[dict[str, Any]]:
        """Build the synthesis prompt from all accumulated artifacts."""
        context_parts = []

        if handoff.file_schema:
            schema = handoff.file_schema
            context_parts.append(
                f"Dataset: {schema.get('original_filename', 'unknown')}, "
                f"{schema.get('row_count', '?')} rows"
            )

        if handoff.code_output:
            context_parts.append(f"Analysis output:\n{handoff.code_output[:3000]}")

        if handoff.code_result:
            context_parts.append(
                f"Computed metrics:\n"
                f"{json.dumps(handoff.code_result, indent=2, default=str)[:2000]}"
            )

        if handoff.chart_json:
            context_parts.append(
                "A chart has been generated and will be displayed to the user."
            )

        if handoff.code_error:
            context_parts.append(
                f"Note: Analysis encountered an error: {handoff.code_error[:500]}"
            )

        context = "\n\n".join(context_parts) if context_parts else "No analysis data available."

        return [
            {
                "role": "user",
                "content": (
                    f"User's question: {handoff.user_query}\n\n"
                    f"Analysis results:\n{context}\n\n"
                    f"Additional instructions: {handoff.instructions or 'Provide a clear summary.'}\n\n"
                    "Please provide a clear, insightful response."
                ),
            }
        ]
