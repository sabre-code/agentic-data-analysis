"""
Code Interpreter Agent

Responsibilities:
  1. Analyze the user's query and data schema
  2. Ask Gemini to write appropriate Python analysis code
  3. Send code to the executor sidecar
  4. If execution fails, feed stderr back to Gemini for self-correction (max 2 retries)
  5. Return stdout, result dict, and the final working code as artifacts
"""
from __future__ import annotations

import logging
from typing import Any

from app.agents.base import BaseAgent
from app.models.handoff import AgentHandoff, AgentResult
from app.services.executor_client import ExecutorClient
from app.services.gemini_client import GeminiClient

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an expert Python data analyst. Your job is to write clean, 
correct pandas code to answer the user's question about their dataset.

Rules:
- Write ONLY executable Python code, no markdown fences, no explanations
- The DataFrame is already loaded as `df` — do not load it yourself
- Use `print()` to output key findings with clear, business-friendly labels
- Store your final computed results in a dict called `result` with descriptive keys
- result values must be JSON-serializable (str, int, float, list, dict)
- For lists/arrays, convert to Python lists: list(series.values)
- For nested dicts (like category breakdowns), structure them clearly: {"category_name": value}
- Handle NaN/None values gracefully
- Do NOT use matplotlib, seaborn, plotly, or any plotting library
- Do NOT try to create charts or visualizations — focus only on data analysis and computation
- Keep code concise and focused on the specific question asked

IMPORTANT - Return data structures that are presentation-ready:
- For top N analyses: return dict with actual names/values, not just counts
  Example: {"Product A": 5000, "Product B": 4500, "Product C": 3000}
- For trend data: return dict with time periods as keys and values
  Example: {"2024-01": 10000, "2024-02": 12000, "2024-03": 11500}
- For categorical breakdowns: return dict with categories and their values
  Example: {"North": 50000, "South": 45000, "East": 40000, "West": 35000}
- For metrics: include actual values, not just metadata
  Example: {"total_revenue": 170000, "avg_order_value": 250.5, "total_orders": 680}

Print clear, business-friendly insights during analysis, like:
  print("Total Revenue: $170,000")
  print("Top 3 Products by Sales: Product A ($50k), Product B ($45k), Product C ($40k)")
  print("Average customer spent $250.50 per order")
"""


class CodeInterpreterAgent(BaseAgent):
    def __init__(
        self,
        gemini: GeminiClient,
        executor: ExecutorClient,
        max_retries: int = 2,
    ) -> None:
        self._gemini = gemini
        self._executor = executor
        self._max_retries = max_retries

    @property
    def name(self) -> str:
        return "Code Interpreter"

    @property
    def description(self) -> str:
        return (
            "Executes Python code to analyze CSV data. Use this to compute statistics, "
            "filter rows, aggregate data, calculate metrics, find top/bottom values, "
            "detect trends, and answer any quantitative question about the dataset."
        )

    async def run(self, handoff: AgentHandoff) -> AgentResult:
        logger.info("🔧 CODE INTERPRETER AGENT started — task: %s", handoff.instructions or handoff.user_query[:80])
        
        # ── Build schema context for the prompt ──────────────────────────
        schema_info = self._build_schema_context(handoff)

        # ── Ask Gemini to write the analysis code ─────────────────────────
        messages = self._build_code_generation_messages(handoff, schema_info)

        last_error: str | None = None
        generated_code: str | None = None

        for attempt in range(self._max_retries + 1):
            # Generate (or regenerate with error context)
            if attempt == 0:
                response = await self._gemini.generate(
                    messages=messages,
                    system_prompt=SYSTEM_PROMPT,
                    temperature=0.1,
                )
            else:
                # Self-correction: inject the error and ask for a fix
                logger.info(
                    "Code Interpreter self-correction attempt %d/%d",
                    attempt, self._max_retries
                )
                correction_messages = messages + [
                    {"role": "assistant", "content": generated_code or ""},
                    {
                        "role": "user",
                        "content": (
                            f"The code failed with this error:\n\n```\n{last_error}\n```\n\n"
                            "Please fix the code. Return ONLY the corrected Python code, "
                            "no explanations."
                        ),
                    },
                ]
                response = await self._gemini.generate(
                    messages=correction_messages,
                    system_prompt=SYSTEM_PROMPT,
                    temperature=0.1,
                )

            generated_code = self._clean_code(response.text or "")
            if not generated_code:
                return AgentResult(
                    agent_name=self.name,
                    success=False,
                    error_message="Gemini returned empty code.",
                )

            # ── Execute the code ──────────────────────────────────────────
            exec_result = await self._executor.execute(
                code=generated_code,
                file_path=handoff.file_path,
            )

            stdout: str = exec_result.get("stdout", "")
            result_dict: dict[str, Any] = exec_result.get("result", {})
            error: str | None = exec_result.get("error")

            if not error:
                # Success
                logger.info(
                    "Code execution succeeded on attempt %d. stdout_len=%d",
                    attempt + 1, len(stdout)
                )
                return AgentResult(
                    agent_name=self.name,
                    success=True,
                    text_content=self._format_output(stdout, result_dict),
                    generated_code=generated_code,
                    code_stdout=stdout,
                    code_result=result_dict,
                )

            # Execution failed — store error for next retry
            last_error = error
            logger.warning(
                "Code execution failed (attempt %d/%d): %s",
                attempt + 1, self._max_retries + 1, error
            )

        # All retries exhausted
        return AgentResult(
            agent_name=self.name,
            success=False,
            generated_code=generated_code,
            code_error=last_error,
            error_message=(
                f"Code execution failed after {self._max_retries + 1} attempts.\n"
                f"Last error:\n{last_error}"
            ),
        )

    def _build_schema_context(self, handoff: AgentHandoff) -> str:
        if not handoff.file_schema:
            return "No dataset is currently loaded."
        schema = handoff.file_schema
        lines = [
            f"Dataset: {schema.get('original_filename', 'unknown')}",
            f"Rows: {schema.get('row_count', 'unknown'):,}" if isinstance(schema.get('row_count'), int) else f"Rows: {schema.get('row_count', 'unknown')}",
            f"Columns ({len(schema.get('columns', []))}):",
        ]
        for col in schema.get("columns", []):
            dtype = schema.get("dtypes", {}).get(col, "unknown")
            lines.append(f"  - {col} ({dtype})")
        return "\n".join(lines)

    def _build_code_generation_messages(
        self, handoff: AgentHandoff, schema_info: str
    ) -> list[dict[str, Any]]:
        """Build the message history for code generation."""
        messages = []

        # Add relevant conversation history
        for msg in handoff.conversation_history[-6:]:  # last 3 turns
            messages.append(msg)

        # Current request
        messages.append({
            "role": "user",
            "content": (
                f"Dataset schema:\n{schema_info}\n\n"
                f"Task: {handoff.instructions or handoff.user_query}\n\n"
                "Write Python code to complete this task. "
                "Remember: df is already loaded. Output ONLY Python code."
            ),
        })
        return messages

    def _clean_code(self, raw: str) -> str:
        """Strip markdown code fences if Gemini wrapped the code."""
        raw = raw.strip()
        if raw.startswith("```python"):
            raw = raw[9:]
        elif raw.startswith("```"):
            raw = raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        return raw.strip()

    def _format_output(self, stdout: str, result_dict: dict[str, Any]) -> str:
        """Format execution output as readable text."""
        parts = []
        if stdout.strip():
            parts.append(stdout.strip())
        if result_dict:
            import json
            parts.append(f"\nComputed results:\n{json.dumps(result_dict, indent=2, default=str)}")
        return "\n".join(parts) if parts else "Analysis complete (no output)."
