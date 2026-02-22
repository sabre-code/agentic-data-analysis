"""
Presentation Agent

Responsibilities:
  1. Synthesize all artifacts (code output, chart, schema) into a clear,
     structured markdown response for the user
  2. Stream the response token-by-token for real-time UX
  3. Include key insights, numbered findings, and contextual interpretation
  4. Generate PDF and/or PPTX reports when requested
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

from app.agents.base import BaseAgent
from app.models.handoff import AgentHandoff, AgentResult
from app.services.gemini_client import GeminiClient
from app.services.report_manager import ReportManager
from app.services.pdf_generator import PDFGenerator
from app.services.pptx_generator import PPTXGenerator

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
- If charts were generated, reference them naturally ("as shown in the charts")

IMPORTANT: When users request PDF reports or PowerPoint presentations:
- Reports ARE being generated automatically by the system - you don't need to say you "cannot" generate them
- Simply provide a brief executive summary of the key findings
- Do NOT say "I cannot generate PDF/PPTX" - the system handles this automatically
- Focus on delivering the insights, not explaining technical limitations
"""


class PresentationAgent(BaseAgent):
    def __init__(
        self, 
        gemini: GeminiClient,
        report_manager: ReportManager | None = None,
        pdf_generator: PDFGenerator | None = None,
        pptx_generator: PPTXGenerator | None = None,
    ) -> None:
        self._gemini = gemini
        self._report_manager = report_manager
        self._pdf_generator = pdf_generator or PDFGenerator()
        self._pptx_generator = pptx_generator or PPTXGenerator()

    @property
    def name(self) -> str:
        return "Presentation"

    @property
    def description(self) -> str:
        return (
            "Synthesizes analysis results and charts into a clear, structured, "
            "business-friendly response. Generates PDF reports or PowerPoint presentations "
            "when requested. Use this to format and present findings after analysis "
            "and/or visualization is complete."
        )

    async def run(self, handoff: AgentHandoff) -> AgentResult:
        """Non-streaming version — collects full response then generates reports if needed."""
        logger.info("📝 PRESENTATION AGENT started — synthesizing report")
        
        # Check if we have sufficient data for report generation
        format_intent = None
        if self._report_manager:
            format_intent = self._report_manager.detect_format_intent(handoff.user_query)
        
        # If report requested, validate data quality and relevance
        if format_intent:
            validation_result = await self._validate_data_for_report(handoff)
            
            if not validation_result["is_sufficient"]:
                logger.warning("Data validation failed: %s", validation_result["reason"])
                return AgentResult(
                    agent_name=self.name,
                    success=False,
                    needs_more_analysis=True,
                    text_content=validation_result["message_to_user"],
                    error_message=validation_result.get("analysis_request"),
                )
        
        messages = self._build_messages(handoff)
        full_text = ""
        async for chunk in self._gemini.stream(
            messages=messages,
            system_prompt=SYSTEM_PROMPT,
            temperature=0.5,
        ):
            full_text += chunk

        # Generate report files if requested and we have charts or analysis data
        report_files = []
        has_charts = len(handoff.get_all_charts()) > 0
        has_analysis = bool(handoff.code_result or handoff.code_output)
        
        if format_intent and (has_charts or has_analysis):
            logger.info("Detected report format intent: %s (charts=%d, has_analysis=%s)", 
                       format_intent, len(handoff.get_all_charts()), has_analysis)
            report_files = await self._generate_reports(handoff, format_intent)
            
            # If report generation failed, append message
            if not report_files:
                full_text += (
                    "\n\n⚠️ **Note**: I couldn't generate the report files. "
                    "The analysis may need more detailed metrics or insights."
                )

        logger.info("✅ PRESENTATION AGENT completed — %d chars, %d report(s)", len(full_text), len(report_files))
        
        return AgentResult(
            agent_name=self.name,
            success=True,
            text_content=full_text,
            report_files=report_files if report_files else None,
        )
    
    async def _validate_data_for_report(self, handoff: AgentHandoff) -> dict[str, Any]:
        """
        Use Gemini to validate if we have sufficient, relevant data for the report.
        
        Returns:
            dict with keys:
                - is_sufficient: bool
                - reason: str (why it's insufficient)
                - message_to_user: str (what to tell user)
                - analysis_request: str (what specific analysis to run)
        """
        # Basic checks first
        if not handoff.file_schema:
            return {
                "is_sufficient": False,
                "reason": "No dataset loaded",
                "message_to_user": "I need a dataset to be uploaded first before I can create a report.",
                "analysis_request": None,
            }
        
        # Check for analysis data OR session artifacts (charts from previous requests)
        has_current_analysis = bool(handoff.code_result or handoff.code_output)
        has_session_charts = len(handoff.session_artifacts) > 0
        has_current_charts = len(handoff.charts) > 0 or handoff.chart_json is not None
        
        # If we have session charts, that's sufficient for a report
        if has_session_charts or has_current_charts:
            all_charts = handoff.get_all_charts()
            logger.info("Found %d chart(s) available for report generation", len(all_charts))
            return {
                "is_sufficient": True,
                "reason": f"Found {len(all_charts)} chart(s) from analysis",
                "message_to_user": None,
                "analysis_request": None,
            }
        
        if not has_current_analysis:
            return {
                "is_sufficient": False,
                "reason": "No analysis performed",
                "message_to_user": (
                    "I need to analyze the data first before creating the report. "
                    "Let me examine the dataset to gather meaningful insights."
                ),
                "analysis_request": (
                    f"Analyze the dataset thoroughly to answer: {handoff.user_query}. "
                    "Compute relevant metrics, identify trends, and generate insights."
                ),
            }
        
        # Smart validation using Gemini
        validation_prompt = f"""You are evaluating if the available analysis data is sufficient to create a comprehensive {format_intent.upper() if (format_intent := self._report_manager.detect_format_intent(handoff.user_query)) else 'REPORT'}.

USER REQUEST: {handoff.user_query}

DATASET INFO:
- Name: {handoff.file_schema.get('original_filename', 'Unknown')}
- Rows: {handoff.file_schema.get('row_count', 'Unknown')}
- Columns: {', '.join(handoff.file_schema.get('columns', [])[:10])}

AVAILABLE ANALYSIS RESULTS:
Code Result (structured data): {handoff.code_result if handoff.code_result else 'None'}
Code Output (printed insights): {handoff.code_output[:500] if handoff.code_output else 'None'}...

EVALUATION CRITERIA:
1. Does the analysis directly answer the user's question?
2. Are there specific metrics, trends, or insights that address the request?
3. Is the data detailed enough for a professional presentation/report?
4. Are there key business metrics (totals, averages, trends, comparisons, etc.)?
5. For presentation requests: Do we have clear talking points and insights?

Respond in JSON format:
{{
    "is_sufficient": true/false,
    "confidence": "high"/"medium"/"low",
    "reason": "Brief explanation of why data is/isn't sufficient",
    "missing_elements": ["list of specific missing analyses if insufficient"],
    "suggested_analysis": "Specific code interpreter request to get missing data (if needed)"
}}

Examples of INSUFFICIENT data:
- User asks for "trends" but we only have totals
- User asks for "top products" but we have aggregate revenue only
- User asks for "comparison" but we only analyzed one dimension
- Code result is just {{"total": 1000}} without breakdown
- No temporal analysis when user asks about "over time"

Examples of SUFFICIENT data:
- User asks for revenue analysis, we have monthly breakdown, top products, regional split
- User asks for trends, we have time-series data with clear patterns
- User asks for summary, we have key metrics and categorical breakdowns"""

        try:
            response = await self._gemini.generate(
                messages=[{"role": "user", "content": validation_prompt}],
                temperature=0.2,
            )
            
            # Parse JSON response
            import json
            import re
            # Extract JSON from response (in case wrapped in markdown)
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                validation = json.loads(json_match.group())
            else:
                logger.error("Failed to parse validation response: %s", response)
                validation = {"is_sufficient": True, "confidence": "low"}  # Default to proceed
            
            if not validation.get("is_sufficient", False):
                missing = validation.get("missing_elements", [])
                suggested = validation.get("suggested_analysis", "")
                
                message_to_user = (
                    f"I've reviewed the analysis, but it doesn't have enough detail for a comprehensive report. "
                    f"Here's what's missing: {', '.join(missing)}.\n\n"
                    f"Let me gather more detailed insights to create a better presentation."
                )
                
                return {
                    "is_sufficient": False,
                    "reason": validation.get("reason", "Insufficient detail"),
                    "message_to_user": message_to_user,
                    "analysis_request": suggested or f"Perform detailed analysis of {handoff.user_query}",
                }
            
            # Data is sufficient
            return {
                "is_sufficient": True,
                "reason": validation.get("reason", "Data looks good"),
                "message_to_user": None,
                "analysis_request": None,
            }
            
        except Exception as e:
            logger.error("Validation failed: %s", e, exc_info=True)
            # On error, default to proceeding (avoid blocking)
            return {
                "is_sufficient": True,
                "reason": "Validation error - proceeding anyway",
                "message_to_user": None,
                "analysis_request": None,
            }

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
        
        # After streaming text, check if we should generate reports or ask user
        if self._report_manager:
            format_intent = self._report_manager.detect_format_intent(handoff.user_query)
            
            if format_intent is None:
                # Unclear - ask user if they want a report
                yield ("text", "\n\n---\n\nWould you like a **PDF report**, **PowerPoint presentation**, or **both**?")
            elif format_intent:
                # Generate requested format(s)
                # Note: This won't work in streaming context as we can't return report_files
                # The orchestrator will need to call run() separately for report generation
                # For now, just indicate reports are being generated
                yield ("text", f"\n\n*Generating {format_intent.upper()} report...*")

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

        # Check for all charts (current + session artifacts)
        all_charts = handoff.get_all_charts()
        if all_charts:
            context_parts.append(
                f"{len(all_charts)} chart(s) are available and will be included in the report/presentation."
            )
            # Add chart titles for context
            chart_info = handoff.get_charts_summary()
            context_parts.append(chart_info)

        if handoff.code_error:
            context_parts.append(
                f"Note: Analysis encountered an error: {handoff.code_error[:500]}"
            )

        context = "\n\n".join(context_parts) if context_parts else "No analysis data available."
        
        # Check if user wants reports - if so, add instruction that they're being auto-generated
        report_instruction = ""
        if self._report_manager:
            format_intent = self._report_manager.detect_format_intent(handoff.user_query)
            if format_intent:
                report_instruction = (
                    f"\n\nNOTE: The system IS automatically generating the requested {format_intent.upper()} report(s) with all charts included. "
                    "Do NOT say you cannot generate reports. Simply provide a brief summary of the key findings from the analysis. "
                    "Keep your response concise (2-3 paragraphs max) as the detailed report is being generated separately."
                )

        return [
            {
                "role": "user",
                "content": (
                    f"User's question: {handoff.user_query}\n\n"
                    f"Analysis results:\n{context}\n\n"
                    f"Additional instructions: {handoff.instructions or 'Provide a clear summary.'}"
                    f"{report_instruction}\n\n"
                    "Please provide a clear, insightful response."
                ),
            }
        ]
    
    async def _generate_reports(
        self, 
        handoff: AgentHandoff, 
        format_intent: str
    ) -> list[dict[str, Any]]:
        """
        Generate PDF and/or PPTX reports based on format intent.
        
        Args:
            handoff: Current agent handoff with all artifacts
            format_intent: "pdf", "pptx", or "both"
            
        Returns:
            List of report file metadata dicts
        """
        if not self._report_manager:
            logger.warning("Cannot generate report: No report_manager configured")
            return []
        
        # Validate that we have sufficient data for a meaningful report
        has_analysis = bool(handoff.code_result or handoff.code_output)
        has_dataset = bool(handoff.file_schema)
        has_charts = len(handoff.get_all_charts()) > 0
        
        logger.info("Report generation check: dataset=%s, analysis=%s, charts=%d",
                   has_dataset, has_analysis, len(handoff.get_all_charts()))
        
        if not has_dataset:
            logger.warning("Cannot generate report: No dataset loaded")
            return []
        
        if not has_analysis and not has_charts:
            logger.warning("Cannot generate report: No analysis results or charts available")
            return []
        
        report_files = []
        
        # Get ALL charts: current request + session artifacts
        all_charts = handoff.get_all_charts()
        
        chart_images_pdf = []
        chart_images_pptx = []
        chart_titles = []
        
        if all_charts:
            logger.info("Generating report with %d chart(s)", len(all_charts))
            # Convert charts to images at appropriate resolutions
            chart_images_pdf = self._report_manager.convert_charts_to_images(
                all_charts, width=1200, height=800
            )
            chart_images_pptx = self._report_manager.convert_charts_to_images(
                all_charts, width=1920, height=1080
            )
            chart_titles = [self._report_manager.get_chart_title(chart) for chart in all_charts]
        
        # Generate AI executive summary for reports
        executive_summary = await self._generate_executive_summary(handoff, chart_titles)
        
        # Generate PDF if requested
        if format_intent in ["pdf", "both"]:
            try:
                filename, display_name = await self._report_manager.generate_filename(
                    handoff.user_query, "pdf", handoff.file_schema, self._gemini
                )
                output_path = self._report_manager.get_file_path(filename)
                
                self._pdf_generator.generate(
                    output_path=output_path,
                    user_query=handoff.user_query,
                    file_schema=handoff.file_schema,
                    code_result=handoff.code_result,
                    code_output=handoff.code_output,
                    chart_images=chart_images_pdf,
                    chart_titles=chart_titles,
                    executive_summary=executive_summary,
                )
                
                report_files.append({
                    "path": str(output_path),
                    "format": "pdf",
                    "filename": filename,
                    "display_name": display_name,
                })
                logger.info("✅ PDF report generated: %s", filename)
                
            except Exception as e:
                logger.error("Failed to generate PDF report: %s", e, exc_info=True)
        
        # Generate PPTX if requested
        if format_intent in ["pptx", "both"]:
            try:
                filename, display_name = await self._report_manager.generate_filename(
                    handoff.user_query, "pptx", handoff.file_schema, self._gemini
                )
                output_path = self._report_manager.get_file_path(filename)
                
                self._pptx_generator.generate(
                    output_path=output_path,
                    user_query=handoff.user_query,
                    file_schema=handoff.file_schema,
                    code_result=handoff.code_result,
                    code_output=handoff.code_output,
                    chart_images=chart_images_pptx,
                    chart_titles=chart_titles,
                    generated_code=handoff.generated_code,
                    executive_summary=executive_summary,
                )
                
                report_files.append({
                    "path": str(output_path),
                    "format": "pptx",
                    "filename": filename,
                    "display_name": display_name,
                })
                logger.info("✅ PPTX report generated: %s", filename)
                
            except Exception as e:
                logger.error("Failed to generate PPTX report: %s", e, exc_info=True)
        
        return report_files

    async def _generate_executive_summary(
        self,
        handoff: AgentHandoff,
        chart_titles: list[str],
    ) -> str:
        """
        Generate an AI-powered executive summary for reports.
        
        Creates a crisp, readable summary of the analysis findings suitable
        for business stakeholders.
        """
        # Build context for summary generation
        context_parts = []
        
        if handoff.file_schema:
            context_parts.append(
                f"Dataset: {handoff.file_schema.get('original_filename', 'Unknown')} "
                f"({handoff.file_schema.get('row_count', 'Unknown')} rows)"
            )
        
        if handoff.code_result:
            context_parts.append(f"Analysis metrics: {json.dumps(handoff.code_result, indent=2, default=str)[:2000]}")
        
        if handoff.code_output:
            context_parts.append(f"Analysis output: {handoff.code_output[:1500]}")
        
        if chart_titles:
            context_parts.append(f"Charts generated: {', '.join(chart_titles)}")
        
        context = "\n\n".join(context_parts)
        
        prompt = f"""Write an executive summary for a data analysis report. The summary should be:
- 3-4 paragraphs maximum
- Written for business stakeholders (no technical jargon)
- Highlighting key findings, trends, and insights
- Crisp, clear, and actionable
- Include specific numbers and percentages where relevant

Analysis Context:
{context}

Write the executive summary now. Do not include any headers or titles - just the summary paragraphs."""

        try:
            response = await self._gemini.generate(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
            )
            
            summary = response.text.strip() if response.text else ""
            logger.info("Generated executive summary: %d chars", len(summary))
            return summary
            
        except Exception as e:
            logger.error("Failed to generate executive summary: %s", e)
            # Fallback to a basic summary
            return "This report presents the findings from our data analysis, including key metrics and visualizations."
