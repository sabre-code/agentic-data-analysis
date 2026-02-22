"""
Agent handoff model — carries context between agents.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class GeneratedArtifact(BaseModel):
    """
    Represents a generated artifact (chart, report, etc.) that persists across requests.
    Stored in Redis and rehydrated into handoff for subsequent requests.
    """
    id: str
    type: str  # "chart" | "report" | "code_result"
    title: str
    description: str  # Brief description of what it shows/contains
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Chart-specific fields
    chart_json: dict[str, Any] | None = None
    chart_type: str | None = None  # bar, line, pie, etc.
    
    # Report-specific fields
    file_path: str | None = None
    format: str | None = None  # pdf, pptx
    
    # Code result metadata
    metrics: dict[str, Any] | None = None
    
    def to_redis_dict(self) -> dict[str, Any]:
        """Convert to dict for Redis storage."""
        data = self.model_dump()
        data["created_at"] = self.created_at.isoformat()
        return data
    
    @classmethod
    def from_redis_dict(cls, data: dict[str, Any]) -> "GeneratedArtifact":
        """Create from Redis dict."""
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        return cls.model_validate(data)


class AgentHandoff(BaseModel):
    """
    Context object passed between agents during orchestration.
    
    Accumulates analysis results as agents collaborate.
    """
    
    # User request
    user_query: str
    instructions: str | None = None  # Agent-specific task instructions
    conversation_history: list[dict[str, str]] = Field(default_factory=list)
    
    # Session context - persisted artifacts from previous requests
    session_id: str | None = None  # For artifact persistence
    session_artifacts: list[GeneratedArtifact] = Field(default_factory=list)  # Charts/reports from prior requests
    
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
    charts: list[dict[str, Any]] = Field(default_factory=list)  # Multiple charts support
    
    # Report files from Presentation Agent
    report_files: list[dict[str, Any]] = Field(default_factory=list)  # [{"path": str, "format": str, "filename": str, "display_name": str}]
    
    # Final answer from Presentation Agent
    final_answer: str | None = None
    
    def get_all_charts(self) -> list[dict[str, Any]]:
        """Get all charts: both from current request and persisted session artifacts."""
        all_charts = list(self.charts)  # Current request charts
        
        # Add charts from session artifacts
        for artifact in self.session_artifacts:
            if artifact.type == "chart" and artifact.chart_json:
                if artifact.chart_json not in all_charts:
                    all_charts.append(artifact.chart_json)
        
        return all_charts
    
    def get_charts_summary(self) -> str:
        """Get a text summary of all available charts for context."""
        all_charts = self.get_all_charts()
        if not all_charts:
            return "No charts available."
        
        summaries = []
        for i, chart in enumerate(all_charts, 1):
            title = chart.get("layout", {}).get("title", {})
            if isinstance(title, dict):
                title = title.get("text", f"Chart {i}")
            elif not title:
                title = f"Chart {i}"
            
            chart_type = "unknown"
            if "data" in chart and chart["data"]:
                chart_type = chart["data"][0].get("type", "unknown")
            
            summaries.append(f"- {title} ({chart_type} chart)")
        
        return "Available charts:\n" + "\n".join(summaries)


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
    charts: list[dict[str, Any]] | None = None  # Multiple charts
    
    # Report file artifacts
    report_files: list[dict[str, Any]] | None = None
    
    # Error handling and flow control
    error_message: str | None = None
    needs_more_analysis: bool = False  # Flag to indicate more analysis needed before reports
    
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
            # Also append to charts list for multi-chart support
            if self.chart_json not in handoff.charts:
                handoff.charts.append(self.chart_json)
        if self.charts:
            # Append multiple charts, avoiding duplicates
            for chart in self.charts:
                if chart not in handoff.charts:
                    handoff.charts.append(chart)
            # Update chart_json to most recent chart for backward compatibility
            if self.charts:
                handoff.chart_json = self.charts[-1]
                handoff.chart_spec = json.dumps(self.charts[-1])
        if self.report_files:
            # Append report files
            handoff.report_files.extend(self.report_files)
        if self.text_content:
            handoff.final_answer = self.text_content

