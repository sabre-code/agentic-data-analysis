"""
Visualization Agent

Responsibilities:
  1. Take analysis artifacts from CodeInterpreterAgent (stdout, result_dict, schema)
  2. Ask Gemini to produce a Plotly JSON chart specification
  3. Return the Plotly JSON as a chart_json artifact
     (rendered as an interactive chart in the browser via Plotly.js CDN)
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.agents.base import BaseAgent
from app.models.handoff import AgentHandoff, AgentResult
from app.services.gemini_client import GeminiClient

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an expert data visualization specialist. Your job is to create 
a Plotly chart specification (JSON) that best visualizes the provided data analysis results.

Rules:
- Return ONLY valid JSON — a complete Plotly figure object with "data" and "layout" keys
- No markdown fences, no explanations, just raw JSON
- Use the most appropriate chart type (bar, line, scatter, pie, histogram, box, heatmap, etc.)
- Make the chart visually clear: proper titles, axis labels, colors
- Use a professional color scheme (prefer: #636EFA, #EF553B, #00CC96, #AB63FA, #FFA15A)
- The "layout" must include: title, xaxis.title, yaxis.title (where applicable)
- Set layout.template to "plotly_dark" for dark theme consistency
- Use actual data values from the analysis results provided
- Keep the JSON compact but complete

Example structure:
{
  "data": [{"type": "bar", "x": [...], "y": [...], "name": "..."}],
  "layout": {
    "title": {"text": "..."},
    "xaxis": {"title": {"text": "..."}},
    "yaxis": {"title": {"text": "..."}},
    "template": "plotly_dark"
  }
}
"""


class VisualizationAgent(BaseAgent):
    def __init__(self, gemini: GeminiClient) -> None:
        self._gemini = gemini

    @property
    def name(self) -> str:
        return "Visualization"

    @property
    def description(self) -> str:
        return (
            "Generates interactive charts and graphs from analysis results. "
            "Use this when the user asks for a chart, graph, plot, visualization, "
            "or when visual representation would significantly enhance understanding "
            "of the data (e.g., comparisons, trends over time, distributions)."
        )

    async def run(self, handoff: AgentHandoff) -> AgentResult:
        logger.info("📊 VISUALIZATION AGENT started — generating chart")
        
        # Build context for chart generation
        context = self._build_chart_context(handoff)

        messages = [
            {
                "role": "user",
                "content": (
                    f"Analysis results to visualize:\n\n{context}\n\n"
                    f"User's original request: {handoff.user_query}\n\n"
                    f"Chart instructions: {handoff.instructions or 'Create the most appropriate chart'}\n\n"
                    "Return ONLY the Plotly JSON figure specification."
                ),
            }
        ]

        response = await self._gemini.generate(
            messages=messages,
            system_prompt=SYSTEM_PROMPT,
            temperature=0.2,
        )

        raw = (response.text or "").strip()
        chart_json = self._parse_plotly_json(raw)

        if not chart_json:
            logger.error("❌ VISUALIZATION AGENT failed — invalid Plotly JSON")
            return AgentResult(
                agent_name=self.name,
                success=False,
                error_message="Failed to generate a valid Plotly chart specification.",
            )

        logger.info("✅ VISUALIZATION AGENT completed — chart: %s", chart_json.get("layout", {}).get("title", {}).get("text", "untitled"))

        return AgentResult(
            agent_name=self.name,
            success=True,
            chart_json=chart_json,
            text_content="",  # Chart speaks for itself; Presentation Agent adds text context
        )

    def _build_chart_context(self, handoff: AgentHandoff) -> str:
        """Assemble all available analysis artifacts into a context string."""
        parts = []

        if handoff.file_schema:
            schema = handoff.file_schema
            parts.append(
                f"Dataset: {schema.get('original_filename', 'unknown')}, "
                f"{schema.get('row_count', '?')} rows, "
                f"columns: {', '.join(schema.get('columns', []))}"
            )

        if handoff.code_output:
            parts.append(f"Analysis output:\n{handoff.code_output[:2000]}")

        if handoff.code_result:
            parts.append(
                f"Computed metrics:\n{json.dumps(handoff.code_result, indent=2, default=str)[:2000]}"
            )

        if not parts:
            parts.append(f"User query: {handoff.user_query}")

        return "\n\n".join(parts)

    def _parse_plotly_json(self, raw: str) -> dict[str, Any] | None:
        """
        Parse Plotly JSON from Gemini's response.
        Handles cases where Gemini wraps JSON in markdown code fences.
        """
        # Strip markdown fences
        if "```json" in raw:
            start = raw.find("```json") + 7
            end = raw.find("```", start)
            raw = raw[start:end].strip()
        elif "```" in raw:
            start = raw.find("```") + 3
            end = raw.find("```", start)
            raw = raw[start:end].strip()

        # Find JSON object boundaries
        start_brace = raw.find("{")
        end_brace = raw.rfind("}")
        if start_brace != -1 and end_brace != -1:
            raw = raw[start_brace: end_brace + 1]

        try:
            parsed = json.loads(raw)
            # Validate minimum structure
            if "data" in parsed:
                return parsed
            logger.warning("Plotly JSON missing 'data' key: %s", list(parsed.keys()))
            return None
        except json.JSONDecodeError as e:
            logger.error("Failed to parse Plotly JSON: %s\nRaw: %s", e, raw[:500])
            return None
