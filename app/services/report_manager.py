"""
Report Manager Service

Handles storage and management of generated PDF/PPTX reports.
Converts Plotly charts to PNG images for embedding in documents.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from ulid import ULID

logger = logging.getLogger(__name__)


class ReportManager:
    """Manages report file storage and chart image conversion."""

    def __init__(self, reports_dir: str) -> None:
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        logger.info("ReportManager initialized: %s", self.reports_dir)

    async def generate_filename(
        self, 
        user_query: str, 
        format_type: str,
        file_schema: dict[str, Any] | None = None,
        gemini_client: Any | None = None
    ) -> tuple[str, str]:
        """
        Generate a descriptive, business-friendly filename using Gemini.
        
        Args:
            user_query: The user's question/request
            format_type: "pdf" or "pptx"
            file_schema: Optional dataset schema for context
            gemini_client: GeminiClient instance for filename generation
            
        Returns:
            Tuple of (filename, display_name)
        """
        # Build context for Gemini
        context_parts = [f"User query: {user_query}"]
        if file_schema:
            dataset_name = file_schema.get('original_filename', 'Unknown Dataset')
            context_parts.append(f"Dataset: {dataset_name}")
        
        context = "\n".join(context_parts)
        
        # Ask Gemini to generate a descriptive filename
        descriptive_name = "Data Analysis Report"
        if gemini_client:
            try:
                prompt = f"""{context}

Generate a short, descriptive, professional filename (3-6 words) for this {format_type.upper()} report.
Rules:
- Use title case (e.g., "Sales Performance Analysis")
- Be specific to the analysis type
- Include dataset name if relevant
- NO file extensions, NO special characters
- Return ONLY the filename, nothing else

Examples:
- "Monthly Revenue Trend Analysis"
- "Customer Segmentation Report"
- "Q1 Sales Performance Review"
"""
                response = await gemini_client.generate(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
                # Extract text from response (handle both string and GenerateContentResponse)
                if hasattr(response, 'text'):
                    response_text = response.text
                elif isinstance(response, str):
                    response_text = response
                else:
                    response_text = str(response)
                
                descriptive_name = response_text.strip().replace(f'.{format_type}', '').strip()
                # Clean up any special chars
                descriptive_name = re.sub(r'[^\w\s-]', '', descriptive_name)
                descriptive_name = re.sub(r'\s+', ' ', descriptive_name).strip()
            except Exception as e:
                logger.warning("Failed to generate filename with Gemini: %s", e)
        
        # Fallback: sanitize query
        if not descriptive_name or len(descriptive_name) < 3:
            descriptive_name = re.sub(r'[^\w\s-]', '', user_query)[:50]
            descriptive_name = re.sub(r'\s+', ' ', descriptive_name).strip() or "Report"
        
        # Generate ULID for uniqueness
        ulid_str = str(ULID())
        
        # Filename version (with ULID and underscores for filesystem)
        filename_base = re.sub(r'\s+', '_', descriptive_name)
        filename = f"{filename_base}_{ulid_str}.{format_type}"
        
        # Display name version (human-friendly with spaces)
        display_name = f"{descriptive_name}.{format_type}"
        
        return filename, display_name

    def get_file_path(self, filename: str) -> Path:
        """Get full path for a report file."""
        return self.reports_dir / filename

    def file_exists(self, filename: str) -> bool:
        """Check if a report file exists."""
        return self.get_file_path(filename).exists()

    def detect_format_intent(self, user_query: str) -> str | None:
        """
        Detect if user wants PDF, PPTX, or both based on query keywords.
        
        Args:
            user_query: The user's question/request
            
        Returns:
            "pdf", "pptx", "both", or None if unclear
        """
        query_lower = user_query.lower()
        
        # Check for explicit format requests
        has_pdf = any(kw in query_lower for kw in ["pdf", "report", "document"])
        has_pptx = any(kw in query_lower for kw in ["ppt", "pptx", "powerpoint", "presentation", "slides", "slide deck"])
        
        if has_pdf and has_pptx:
            return "both"
        elif has_pptx:
            return "pptx"
        elif has_pdf:
            return "pdf"
        
        return None

    def plotly_to_png(
        self, 
        plotly_json: dict[str, Any], 
        width: int = 1200, 
        height: int = 800
    ) -> bytes:
        """
        Convert Plotly JSON to PNG bytes using kaleido.
        
        Args:
            plotly_json: Plotly figure spec with "data" and "layout" keys
            width: Image width in pixels
            height: Image height in pixels
            
        Returns:
            PNG image as bytes
        """
        try:
            import plotly.graph_objects as go
            import plotly.io as pio
            
            # Sanitize Plotly JSON to remove invalid properties
            sanitized_json = self._sanitize_plotly_json(plotly_json)
            
            # Create Plotly figure from JSON
            fig = go.Figure(sanitized_json)
            
            # Convert to PNG using kaleido
            img_bytes = pio.to_image(
                fig, 
                format="png", 
                width=width, 
                height=height,
                engine="kaleido"
            )
            
            logger.info(
                "Converted Plotly chart to PNG: %dx%d, %d bytes",
                width, height, len(img_bytes)
            )
            return img_bytes
            
        except Exception as e:
            logger.error("Failed to convert Plotly to PNG: %s", e, exc_info=True)
            raise
    
    def _sanitize_plotly_json(self, plotly_json: dict[str, Any]) -> dict[str, Any]:
        """
        Remove invalid or trace-type-mismatched properties from Plotly JSON.
        
        Common issues:
        - 'type': 'line' is NOT a valid Plotly type — must use 'scatter' with mode='lines'
        - 'lowerfence', 'upperfence' are box plot properties, not valid for scatter
        - AI-generated specs sometimes include incompatible properties
        """
        import copy
        sanitized = copy.deepcopy(plotly_json)
        
        # Invalid properties that commonly appear in wrong trace types
        invalid_props_by_type = {
            'scatter': ['lowerfence', 'upperfence', 'q1', 'q3', 'median', 'whiskerwidth'],
            'bar': ['lowerfence', 'upperfence', 'q1', 'q3', 'median'],
        }
        
        # Clean up data traces
        if 'data' in sanitized and isinstance(sanitized['data'], list):
            cleaned_data = []
            for trace in sanitized['data']:
                if isinstance(trace, dict):
                    trace_type = trace.get('type', 'scatter')
                    
                    # FIX: 'line' is not a valid Plotly type - convert to scatter
                    if trace_type == 'line':
                        trace['type'] = 'scatter'
                        # Ensure mode includes 'lines' for line chart appearance
                        if 'mode' not in trace:
                            trace['mode'] = 'lines+markers'
                        elif 'lines' not in trace.get('mode', ''):
                            trace['mode'] = 'lines+markers'
                        trace_type = 'scatter'
                        logger.debug("Converted invalid 'line' type to 'scatter' with mode='%s'", trace['mode'])
                    
                    invalid_props = invalid_props_by_type.get(trace_type, [])
                    
                    # Remove invalid properties
                    cleaned_trace = {k: v for k, v in trace.items() if k not in invalid_props}
                    cleaned_data.append(cleaned_trace)
                else:
                    cleaned_data.append(trace)
            sanitized['data'] = cleaned_data
        
        return sanitized

    def convert_charts_to_images(
        self, 
        charts: list[dict[str, Any]], 
        width: int = 1200, 
        height: int = 800
    ) -> list[bytes]:
        """
        Convert multiple Plotly charts to PNG images.
        
        Args:
            charts: List of Plotly JSON specs
            width: Image width in pixels
            height: Image height in pixels
            
        Returns:
            List of PNG image bytes
        """
        images = []
        for i, chart in enumerate(charts):
            try:
                img_bytes = self.plotly_to_png(chart, width, height)
                images.append(img_bytes)
            except Exception as e:
                logger.warning("Failed to convert chart %d: %s", i, e)
                # Continue with other charts
        
        return images

    def get_chart_title(self, chart_json: dict[str, Any]) -> str:
        """Extract title from Plotly chart JSON."""
        try:
            title = chart_json.get("layout", {}).get("title", {})
            if isinstance(title, dict):
                return title.get("text", "Chart")
            return str(title) if title else "Chart"
        except Exception:
            return "Chart"
