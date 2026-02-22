"""
Agent handoff model — carries context between agents.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentHandoff(BaseModel):
    """
    Context object passed between agents during orchestration.
    
    Accumulates analysis results as agents collaborate.
    """
    
    # User request
    user_query: str
    instructions: str | None = None  # Agent-specific task instructions
    conversation_history: list[dict[str, str]] = Field(default_factory=list)
    
    # File context
    file_id: str | None = None
    file_path: str | None = None
    file_schema: dict[str, Any] | None = None
    
    # Analysis results from Code Interpreter
    code_output: str | None = None
    code_executed: str | None = None
    generated_code: str | None = None  # Alias for code_executed, used by orchestrator
    code_result: dict[str, Any] | None = None  # Structured results from code execution
    code_error: str | None = None  # Error message if code execution failed
    
    # Visualization spec from Visualization Agent
    chart_spec: str | None = None  # Plotly JSON string
    chart_json: dict[str, Any] | None = None  # Parsed Plotly JSON, used by orchestrator
    
    # Final answer from Presentation Agent
    final_answer: str | None = None


class AgentResult(BaseModel):
    """Result returned by an agent after processing."""
    
    agent_name: str | None = None
    success: bool
    
    # Content fields
    text_content: str | None = None
    
    # Code Interpreter artifacts
    generated_code: str | None = None
    code_stdout: str | None = None
    code_result: dict[str, Any] | None = None
    code_error: str | None = None
    
    # Visualization artifacts
    chart_json: dict[str, Any] | None = None
    
    # Error handling
    error_message: str | None = None
    
    def to_handoff_update(self, handoff: AgentHandoff) -> None:
        """Update handoff with this result's artifacts."""
        if self.code_stdout:
            handoff.code_output = self.code_stdout
        if self.generated_code:
            handoff.code_executed = self.generated_code
            handoff.generated_code = self.generated_code  # Keep both for compatibility
        if self.code_result:
            handoff.code_result = self.code_result
        if self.code_error:
            handoff.code_error = self.code_error
        if self.chart_json:
            import json
            handoff.chart_spec = json.dumps(self.chart_json)
            handoff.chart_json = self.chart_json  # Keep parsed version too
        if self.text_content:
            handoff.final_answer = self.text_content

