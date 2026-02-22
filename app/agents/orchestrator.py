"""
Orchestrator Agent — the brain of the multi-agent system.

Uses Gemini's native function calling (tool use) to dynamically decide:
  - Which agents to invoke
  - In what order
  - With what instructions

This is NOT a hardcoded pipeline. Gemini reasons about the user's intent
and calls the appropriate agent tools. The Orchestrator executes those calls,
feeds results back as FunctionResponse, and lets Gemini decide next steps
until it returns a final text answer.

Flow example for "analyze sales and show me a chart":
  1. Orchestrator sends query + 3 tool declarations to Gemini
  2. Gemini → FunctionCall("run_code_interpreter", {"task": "compute sales metrics"})
  3. Orchestrator → CodeInterpreterAgent.run(handoff) → artifacts
  4. Orchestrator feeds FunctionResponse back to Gemini
  5. Gemini → FunctionCall("run_visualization_agent", {"chart_type": "bar"})
  6. Orchestrator → VisualizationAgent.run(handoff_with_artifacts) → chart_json
  7. Orchestrator feeds FunctionResponse back to Gemini
  8. Gemini → FunctionCall("run_presentation_agent", {...})
  9. Orchestrator → PresentationAgent.stream(handoff) → streamed markdown
  10. Gemini synthesizes final answer (or the presentation IS the final answer)
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

from app.agents.base import BaseAgent
from app.agents.code_interpreter import CodeInterpreterAgent
from app.agents.visualization import VisualizationAgent
from app.agents.presentation import PresentationAgent
from app.models.handoff import AgentHandoff, AgentResult
from app.models.file import UploadedFile
from app.services.gemini_client import GeminiClient, ToolExecutor

logger = logging.getLogger(__name__)


def _sanitize_for_gemini(value: Any) -> Any:
    """
    Recursively replace NaN, Inf, and non-JSON-serializable values so that
    the payload sent to Gemini's API is always valid JSON.
    JSON spec forbids NaN/Inf — Gemini rejects them with 400 INVALID_ARGUMENT.
    """
    import math
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {str(k): _sanitize_for_gemini(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_for_gemini(i) for i in value]
    try:
        json.dumps(value, allow_nan=False)
        return value
    except (TypeError, ValueError):
        return str(value)


# ── Tool declarations (registered with Gemini) ─────────────────────────────
# Gemini reads these descriptions and decides which to call based on the query.

AGENT_TOOL_DECLARATIONS = [
    {
        "name": "run_code_interpreter",
        "description": (
            "Execute Python code to analyze the uploaded dataset. Use this to: "
            "compute statistics, calculate totals/averages/percentages, find top/bottom values, "
            "filter and aggregate data, detect trends, compare groups, answer any quantitative "
            "question. Always use this first before visualization or presentation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Specific analysis task to perform on the dataset",
                },
                "focus_columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Column names most relevant to this analysis task",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "run_visualization_agent",
        "description": (
            "Generate an interactive chart or graph from analysis results. Use this when: "
            "the user explicitly requests a chart/plot/graph/visualization, "
            "when comparing multiple values (bar chart), showing trends over time (line chart), "
            "showing distributions (histogram), or when a visual would significantly enhance "
            "understanding. Always run code_interpreter first to have data to visualize."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chart_type": {
                    "type": "string",
                    "description": "Recommended chart type: bar, line, scatter, pie, histogram, box, heatmap",
                },
                "chart_title": {
                    "type": "string",
                    "description": "Descriptive title for the chart",
                },
                "instructions": {
                    "type": "string",
                    "description": "Specific instructions for chart creation",
                },
            },
            "required": ["chart_title"],
        },
    },
    {
        "name": "run_presentation_agent",
        "description": (
            "Format and present analysis findings in a clear, structured, business-friendly response. "
            "Use this to synthesize results from code execution and/or visualization into "
            "a well-formatted markdown response with insights and recommendations. "
            "Use for comprehensive analysis requests, reports, or when the user wants "
            "a detailed explanation of findings."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "instructions": {
                    "type": "string",
                    "description": "How to structure or focus the presentation",
                },
                "include_recommendations": {
                    "type": "boolean",
                    "description": "Whether to include business recommendations",
                },
            },
            "required": [],
        },
    },
]


# ── Orchestrator ───────────────────────────────────────────────────────────

class OrchestratorAgent(ToolExecutor):
    """
    Routes user queries to appropriate specialist agents via Gemini function calling.
    Implements ToolExecutor so GeminiClient.run_with_tools() can dispatch calls here.
    """

    def __init__(
        self,
        gemini: GeminiClient,
        code_interpreter: CodeInterpreterAgent,
        visualization: VisualizationAgent,
        presentation: PresentationAgent,
    ) -> None:
        self._gemini = gemini
        self._agents: dict[str, BaseAgent] = {
            "run_code_interpreter": code_interpreter,
            "run_visualization_agent": visualization,
            "run_presentation_agent": presentation,
        }
        # Mutable handoff built up as agents execute
        self._current_handoff: AgentHandoff | None = None

    async def execute(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """
        Called by GeminiClient.run_with_tools() when Gemini issues a FunctionCall.
        Maps tool_name → agent, runs the agent, returns result as FunctionResponse dict.
        """
        logger.info("🔀 ORCHESTRATOR routing to: %s", tool_name)
        
        agent = self._agents.get(tool_name)
        if not agent:
            return {"error": f"Unknown tool: {tool_name}"}

        if self._current_handoff is None:
            return {"error": "No active handoff context"}

        # Inject tool-specific instructions from Gemini's args
        handoff = self._current_handoff
        if "task" in args:
            handoff.instructions = args["task"]
        elif "instructions" in args:
            handoff.instructions = args.get("instructions", "")
        if "chart_type" in args:
            handoff.instructions = (
                f"Create a {args['chart_type']} chart. "
                f"Title: {args.get('chart_title', '')}. "
                f"{args.get('instructions', '')}"
            )

        result = await agent.run(handoff)

        # Merge artifacts into the running handoff for subsequent agents
        result.to_handoff_update(handoff)

        # Return a summary for Gemini's FunctionResponse
        return self._result_to_function_response(result, tool_name)

    async def run_stream(
        self,
        user_query: str,
        conversation_history: list[dict[str, Any]],
        active_file: UploadedFile | None = None,
    ) -> AsyncGenerator[tuple[str, str], None]:
        """
        Main entry point. Streams (chunk_type, content) tuples to the caller.

        Chunk types:
          "agent_switch"  → show "Agent X is working..." banner
          "text"          → markdown text token
          "code"          → generated code block
          "chart_plotly"  → Plotly JSON string
          "error"         → error message
        """
        # ── Build initial handoff ──────────────────────────────────────────
        handoff = AgentHandoff(
            user_query=user_query,
            conversation_history=conversation_history,
        )
        if active_file:
            handoff.file_id = active_file.file_id
            handoff.file_path = active_file.storage_path
            handoff.file_schema = {
                "original_filename": active_file.original_filename,
                "row_count": active_file.row_count,
                "columns": active_file.columns,
                "dtypes": active_file.dtypes,
            }

        self._current_handoff = handoff

        # ── Build system prompt ────────────────────────────────────────────
        system_prompt = self._build_orchestrator_system_prompt(active_file)

        # ── Check if query needs agents or is pure conversation ────────────
        needs_agents = active_file is not None or self._is_analytical_query(user_query)

        if not needs_agents:
            # Pure conversational response — no agents needed
            yield ("agent_switch", "Thinking...")
            messages = self._build_messages_for_gemini(user_query, conversation_history)
            async for chunk in self._gemini.stream(
                messages=messages,
                system_prompt=system_prompt,
                temperature=0.7,
            ):
                yield ("text", chunk)
            return

        # ── Run the dynamic tool-calling loop ─────────────────────────────
        # We implement our own streaming-aware tool loop instead of using
        # run_with_tools, so we can yield SSE events in real-time
        
        try:
            messages = self._build_messages_for_gemini(user_query, conversation_history)
            
            # Manual tool-calling loop with real-time SSE emission
            working_messages = list(messages)
            max_iterations = 10
            
            for _ in range(max_iterations):
                # Generate with tools
                response = await self._gemini.generate(
                    messages=working_messages,
                    system_prompt=system_prompt,
                    tools=AGENT_TOOL_DECLARATIONS,
                    temperature=0.2,
                )
                
                # Extract function calls
                function_calls = self._gemini._extract_function_calls(response)
                
                if not function_calls:
                    # No more tool calls - model returned final text
                    final_text = response.text or ""
                    if final_text.strip():
                        # Stream the final text token by token
                        chunk_size = 50
                        for i in range(0, len(final_text), chunk_size):
                            yield ("text", final_text[i: i + chunk_size])
                    break
                
                # Execute each function call and emit events in real-time
                function_responses = []
                for fc in function_calls:
                    tool_name = fc["name"]
                    args = fc["args"]
                    
                    # Map tool names to display names
                    agent_display_names = {
                        "run_code_interpreter": "Code Interpreter",
                        "run_visualization_agent": "Visualization",
                        "run_presentation_agent": "Presentation",
                    }
                    display = agent_display_names.get(tool_name, tool_name)
                    
                    # EMIT AGENT SWITCH EVENT IMMEDIATELY
                    yield ("agent_switch", f"{display} Agent is working...")
                    
                    # Execute the tool
                    result = await self.execute(tool_name, args)
                    
                    # Emit artifacts immediately after execution
                    if tool_name == "run_code_interpreter" and self._current_handoff.generated_code:
                        yield ("code", self._current_handoff.generated_code)
                    
                    if tool_name == "run_visualization_agent" and self._current_handoff.chart_json:
                        yield ("chart_plotly", json.dumps(self._current_handoff.chart_json))
                    
                    function_responses.append({
                        "name": tool_name,
                        "response": result,
                        "id": fc.get("id"),
                    })
                
                # Add function call turn + responses to history
                working_messages.append({
                    "role": "model",
                    "parts": [{"function_call": fc} for fc in function_calls],
                })
                working_messages.append({
                    "role": "user",
                    "parts": [
                        {"function_response": {"name": fr["name"], "response": fr["response"]}}
                        for fr in function_responses
                    ],
                })

        except Exception as e:
            logger.error("Orchestrator error: %s", e, exc_info=True)
            yield ("error", f"An error occurred during analysis: {str(e)}")
        finally:
            self._current_handoff = None

    def _build_orchestrator_system_prompt(self, active_file: UploadedFile | None) -> str:
        base = (
            "You are an intelligent data analysis assistant. "
            "You have access to specialist tools to analyze data, create visualizations, "
            "and present findings. "
        )
        if active_file:
            base += (
                f"The user has uploaded a dataset: '{active_file.original_filename}' "
                f"({active_file.row_count:,} rows, {len(active_file.columns)} columns). "
                "Use the available tools to answer their questions about this data. "
                "Always use run_code_interpreter first before visualization. "
            )
        else:
            base += (
                "No dataset is currently uploaded. Answer conversational questions directly. "
                "If the user asks about data analysis, suggest they upload a CSV file. "
            )
        base += (
            "Be precise and concise. "
            "Only invoke tools that are genuinely needed for the user's request."
        )
        return base

    def _build_messages_for_gemini(
        self,
        user_query: str,
        conversation_history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build message list for Gemini. Include recent history for context."""
        messages = list(conversation_history)
        messages.append({"role": "user", "content": user_query})
        return messages

    def _is_analytical_query(self, query: str) -> bool:
        """
        Quick heuristic to decide if we need agents for a query with no file.
        Avoids spinning up the tool loop for pure conversation.
        """
        analytical_keywords = {
            "analyze", "analysis", "chart", "graph", "plot", "visualize",
            "calculate", "compute", "statistics", "average", "mean", "sum",
            "top", "bottom", "highest", "lowest", "trend", "compare",
            "show me", "how many", "what is", "distribution", "correlation",
        }
        query_lower = query.lower()
        return any(kw in query_lower for kw in analytical_keywords)

    def _result_to_function_response(
        self, result: AgentResult, tool_name: str
    ) -> dict[str, Any]:
        """Convert AgentResult to a dict Gemini can read as FunctionResponse."""
        if not result.success:
            return {
                "status": "error",
                "error": result.error_message,
            }

        response: dict[str, Any] = {"status": "success"}

        if result.code_stdout:
            response["analysis_output"] = result.code_stdout[:3000]
        if result.code_result:
            response["computed_metrics"] = _sanitize_for_gemini(result.code_result)
        if result.chart_json:
            response["chart_generated"] = True
            response["chart_title"] = (
                result.chart_json.get("layout", {})
                .get("title", {})
                .get("text", "Chart")
            )
        if result.text_content:
            response["summary"] = result.text_content[:2000]

        return response
