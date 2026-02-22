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
from app.models.handoff import AgentHandoff, AgentResult, GeneratedArtifact
from app.models.file import UploadedFile
from app.services.gemini_client import GeminiClient, ToolExecutor
from app.services.redis_client import RedisClient

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
            "IMPORTANT: ONLY call this AFTER running code_interpreter to analyze data. "
            "This agent needs actual analytical results (metrics, insights, findings) to create meaningful content. "
            "For report/presentation requests: 1) Run code_interpreter to deeply analyze data and compute metrics, "
            "2) Optionally run visualization_agent to create charts, 3) THEN call this agent to format results. "
            "Automatically generates PDF/PowerPoint files when user requests reports or presentations. "
            "DO NOT call this if you haven't analyzed the data yet - it will generate empty reports."
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
        redis_client: RedisClient | None = None,
    ) -> None:
        self._gemini = gemini
        self._agents: dict[str, BaseAgent] = {
            "run_code_interpreter": code_interpreter,
            "run_visualization_agent": visualization,
            "run_presentation_agent": presentation,
        }
        self._redis = redis_client
        # Mutable handoff built up as agents execute
        self._current_handoff: AgentHandoff | None = None
        self._current_session_id: str | None = None

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

        handoff = self._current_handoff
        
        # ── Smart chart reuse check ────────────────────────────────────────
        # If visualization is requested but we already have charts AND user
        # wants to use existing charts (not create new/different ones), skip.
        if tool_name == "run_visualization_agent":
            existing_charts = handoff.get_all_charts()
            if existing_charts and self._should_reuse_existing_charts(handoff.user_query, args):
                logger.info("♻️ REUSING %d existing chart(s) instead of regenerating", len(existing_charts))
                return {
                    "status": "success",
                    "charts_reused": True,
                    "chart_count": len(existing_charts),
                    "message": f"Using {len(existing_charts)} existing chart(s) from previous analysis. No need to regenerate.",
                }

        # Inject tool-specific instructions from Gemini's args
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
    
    def _should_reuse_existing_charts(self, user_query: str, viz_args: dict[str, Any]) -> bool:
        """
        Determine if we should reuse existing charts or generate new ones.
        
        Returns True (reuse) when:
        - User asks for report/presentation with "these charts" / "the charts" / existing charts
        - User doesn't specify new/different chart requirements
        
        Returns False (regenerate) when:
        - User asks for a specific new chart type not in existing charts
        - User explicitly asks for "new", "different", "another" chart
        - User specifies different data to visualize
        """
        query_lower = user_query.lower()
        
        # Phrases indicating user wants to USE existing charts (reuse)
        reuse_indicators = [
            "these charts", "the charts", "those charts", "existing charts",
            "same charts", "charts above", "charts you created", "charts generated",
            "with these", "include these", "use these", "add these",
            "create a report", "create report", "generate report", "make a report",
            "create a presentation", "create presentation", "generate presentation",
            "pdf with", "pptx with", "powerpoint with",
            "report with the", "presentation with the",
        ]
        
        # Phrases indicating user wants NEW/DIFFERENT charts (don't reuse)
        new_chart_indicators = [
            "new chart", "different chart", "another chart", "create a chart",
            "generate a chart", "make a chart", "show me a chart",
            "visualize", "plot", "graph this", "chart this",
            "bar chart", "line chart", "pie chart", "scatter", "histogram",
            "compare", "trend of", "distribution of",
        ]
        
        # Check for reuse indicators
        wants_reuse = any(indicator in query_lower for indicator in reuse_indicators)
        
        # Check for new chart indicators
        wants_new = any(indicator in query_lower for indicator in new_chart_indicators)
        
        # If user explicitly wants existing charts for a report, reuse
        if wants_reuse and not wants_new:
            return True
        
        # If user is asking for specific new visualization, don't reuse
        if wants_new and not wants_reuse:
            return False
        
        # Ambiguous case - if we have charts and viz_args doesn't specify something concrete new
        # Default to reuse if user seems to want a report/presentation
        report_keywords = ["report", "pdf", "presentation", "pptx", "powerpoint", "summary"]
        if any(kw in query_lower for kw in report_keywords):
            return True
        
        # Default: don't reuse (generate new charts)
        return False

    async def run_stream(
        self,
        user_query: str,
        conversation_history: list[dict[str, Any]],
        active_file: UploadedFile | None = None,
        session_id: str | None = None,
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
        self._current_session_id = session_id
        
        logger.info("🔧 run_stream started - session_id: %s, redis_client: %s", session_id, self._redis is not None)
        
        # ── Load session artifacts (charts from previous requests) ─────────
        session_artifacts: list[GeneratedArtifact] = []
        if session_id and self._redis:
            try:
                artifact_dicts = await self._redis.get_session_artifacts(session_id)
                logger.info("📦 Loaded %d artifact(s) from Redis for session %s", len(artifact_dicts), session_id)
                session_artifacts = [
                    GeneratedArtifact.from_redis_dict(a) for a in artifact_dicts
                ]
                if session_artifacts:
                    logger.info("Loaded %d session artifacts for session %s", len(session_artifacts), session_id)
            except Exception as e:
                logger.warning("Failed to load session artifacts: %s", e)
        
        # ── Build initial handoff ──────────────────────────────────────────
        handoff = AgentHandoff(
            user_query=user_query,
            conversation_history=conversation_history,
            session_id=session_id,
            session_artifacts=session_artifacts,
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

        # ── Build system prompt with session artifact context ──────────────
        system_prompt = self._build_orchestrator_system_prompt(active_file, session_artifacts)

        # ── Check if query needs agents or is pure conversation ────────────
        # Also trigger agents if we have session artifacts (for report generation)
        needs_agents = (
            active_file is not None 
            or self._is_analytical_query(user_query)
            or len(session_artifacts) > 0  # Have prior analysis to work with
        )

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
                    
                    if tool_name == "run_visualization_agent":
                        # Emit all charts and save to session for future requests
                        charts_to_save = []
                        if self._current_handoff.charts:
                            for chart in self._current_handoff.charts:
                                yield ("chart_plotly", json.dumps(chart))
                                charts_to_save.append(chart)
                        elif self._current_handoff.chart_json:
                            yield ("chart_plotly", json.dumps(self._current_handoff.chart_json))
                            charts_to_save.append(self._current_handoff.chart_json)
                        
                        # Persist charts to session for follow-up requests
                        if charts_to_save and self._current_session_id and self._redis:
                            await self._save_charts_to_session(charts_to_save)
                    
                    if tool_name == "run_presentation_agent" and self._current_handoff.report_files:
                        # Emit report files for download
                        yield ("report_files", json.dumps(self._current_handoff.report_files))
                    
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
            self._current_session_id = None

    async def _save_charts_to_session(self, charts: list[dict[str, Any]]) -> None:
        """Save generated charts to Redis session for future requests."""
        if not self._redis or not self._current_session_id:
            return
        
        import uuid
        from datetime import datetime, timezone
        
        for chart in charts:
            # Extract chart metadata
            layout = chart.get("layout", {})
            title = layout.get("title", {})
            if isinstance(title, dict):
                title_text = title.get("text", "Untitled Chart")
            else:
                title_text = title or "Untitled Chart"
            
            chart_type = "unknown"
            if "data" in chart and chart["data"]:
                chart_type = chart["data"][0].get("type", "unknown")
            
            # Build description from chart data
            description_parts = [f"{chart_type} chart"]
            if chart.get("data"):
                data = chart["data"][0]
                if "name" in data:
                    description_parts.append(f"showing {data['name']}")
                x_axis = layout.get("xaxis", {}).get("title", {})
                y_axis = layout.get("yaxis", {}).get("title", {})
                if isinstance(x_axis, dict):
                    x_axis = x_axis.get("text", "")
                if isinstance(y_axis, dict):
                    y_axis = y_axis.get("text", "")
                if x_axis and y_axis:
                    description_parts.append(f"({x_axis} vs {y_axis})")
            
            artifact = {
                "id": str(uuid.uuid4()),
                "type": "chart",
                "title": title_text,
                "description": " ".join(description_parts),
                "chart_json": chart,
                "chart_type": chart_type,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            
            try:
                await self._redis.save_session_artifact(self._current_session_id, artifact)
                logger.info("Saved chart '%s' to session %s", title_text, self._current_session_id)
            except Exception as e:
                logger.warning("Failed to save chart to session: %s", e)

    def _build_orchestrator_system_prompt(
        self, 
        active_file: UploadedFile | None,
        session_artifacts: list[GeneratedArtifact] | None = None,
    ) -> str:
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
        
        # Add context about existing charts from previous requests
        if session_artifacts:
            charts = [a for a in session_artifacts if a.type == "chart"]
            if charts:
                base += (
                    f"\n\n=== EXISTING CHARTS (IMPORTANT) ===\n"
                    f"The session has {len(charts)} chart(s) already generated:\n"
                )
                for i, chart in enumerate(charts, 1):
                    base += f"  {i}. {chart.title}: {chart.description}\n"
                base += (
                    "\nCHART REUSE RULES:\n"
                    "• If user wants a report/presentation WITH 'these charts', 'the charts', or 'existing charts' "
                    "→ Go DIRECTLY to run_presentation_agent. Do NOT call run_visualization_agent.\n"
                    "• If user asks for NEW/DIFFERENT charts (e.g., 'create a pie chart', 'show me a histogram') "
                    "→ Call run_visualization_agent to generate the new charts.\n"
                    "• If user asks for both existing AND new charts → Call visualization only for new charts.\n"
                    "\nThe presentation agent automatically has access to all existing charts.\n"
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
            # Check if it's a "needs more analysis" scenario
            if result.needs_more_analysis and result.error_message:
                return {
                    "status": "needs_more_data",
                    "message": result.text_content or "Insufficient data for comprehensive report",
                    "suggested_action": result.error_message,  # Contains specific analysis request
                    "instruction": (
                        "The presentation agent needs more detailed analysis. "
                        f"Please run code_interpreter with this task: {result.error_message}. "
                        "Then call presentation_agent again to create the report."
                    ),
                }
            
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
        if result.needs_more_analysis:
            response["needs_more_analysis"] = True

        return response
