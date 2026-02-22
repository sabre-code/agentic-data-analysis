"""
PDF Generator Service

Creates professional executive-style PDF reports using reportlab.
Includes cover page, metrics tables, chart images, and insights.
"""
from __future__ import annotations

import logging
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    Image,
)
from reportlab.lib.enums import TA_CENTER

logger = logging.getLogger(__name__)


class PDFGenerator:
    """Generates executive-style PDF reports from analysis artifacts."""

    def __init__(self) -> None:
        self.page_width, self.page_height = A4
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

    def _setup_custom_styles(self) -> None:
        """Create custom paragraph styles for the report."""
        # Title style
        self.styles.add(ParagraphStyle(
            name='ReportTitle',
            parent=self.styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#1a1a1a'),
            spaceAfter=30,
            alignment=TA_CENTER,
        ))
        
        # Section heading
        self.styles.add(ParagraphStyle(
            name='SectionHeading',
            parent=self.styles['Heading2'],
            fontSize=16,
            textColor=colors.HexColor('#2c3e50'),
            spaceAfter=12,
            spaceBefore=12,
        ))
        
        # Body text
        self.styles.add(ParagraphStyle(
            name='ReportBody',
            parent=self.styles['Normal'],
            fontSize=11,
            leading=14,
            textColor=colors.HexColor('#333333'),
        ))
        
        # Code style
        self.styles.add(ParagraphStyle(
            name='CodeBlock',
            parent=self.styles['Code'],
            fontSize=9,
            leftIndent=20,
            textColor=colors.HexColor('#555555'),
            backColor=colors.HexColor('#f5f5f5'),
        ))

    def generate(
        self,
        output_path: Path,
        user_query: str,
        file_schema: dict[str, Any] | None,
        code_result: dict[str, Any] | None,
        code_output: str | None,
        chart_images: list[bytes],
        chart_titles: list[str],
        executive_summary: str | None = None,
    ) -> None:
        """
        Generate a PDF report.
        
        Args:
            output_path: Path where PDF will be saved
            user_query: The user's original question
            file_schema: Dataset metadata
            code_result: Structured metrics from code execution
            code_output: stdout from code execution
            chart_images: List of PNG chart images as bytes
            chart_titles: List of titles for each chart
            executive_summary: AI-generated executive summary text (optional)
        """
        logger.info("Generating PDF report: %s", output_path)
        
        # Create PDF document
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            rightMargin=0.75*inch,
            leftMargin=0.75*inch,
            topMargin=0.75*inch,
            bottomMargin=0.75*inch,
        )
        
        # Build content
        story = []
        
        # Cover page
        story.extend(self._build_cover_page(user_query, file_schema))
        story.append(PageBreak())
        
        # Executive summary - use AI-generated text if provided
        if executive_summary or code_result or code_output:
            story.extend(self._build_executive_summary(
                code_result, 
                code_output, 
                executive_summary=executive_summary
            ))
            story.append(Spacer(1, 0.3*inch))
        
        # Charts section
        if chart_images:
            story.extend(self._build_charts_section(chart_images, chart_titles))
        
        # Build PDF
        doc.build(story)
        logger.info("PDF report generated successfully: %d bytes", output_path.stat().st_size)
    
    def _create_metrics_table(self, items: list[tuple[str, Any]]) -> Table:
        """Create a formatted metrics table from key-value pairs."""
        table_data = [["Metric", "Value"]]
        
        for key, value in items:
            # Format values by actually unpacking data structures
            if isinstance(value, float):
                # Detect percentages (values between 0 and 1)
                if 0 <= value <= 1:
                    formatted_value = f"{value * 100:.1f}%"
                else:
                    formatted_value = f"{value:,.2f}"
            elif isinstance(value, int):
                formatted_value = f"{value:,}"
            elif isinstance(value, list):
                # Show actual list contents, not just count
                if len(value) > 0:
                    # Check if it's a list of numbers or strings
                    if all(isinstance(x, (int, float)) for x in value[:8]):
                        # Numeric list - show with formatting
                        items_formatted = [f"{x:,.2f}" if isinstance(x, float) else f"{x:,}" for x in value[:8]]
                    else:
                        items_formatted = [str(v)[:25] for v in value[:8]]
                    formatted_value = ", ".join(items_formatted)
                    if len(value) > 8:
                        formatted_value += f" ... (+{len(value) - 8} more)"
                else:
                    formatted_value = "(empty)"
            elif isinstance(value, dict):
                # Show actual dict contents as formatted string
                if len(value) > 0:
                    items_formatted = []
                    for k, v in list(value.items())[:6]:
                        if isinstance(v, float):
                            v_str = f"{v:,.2f}"
                        elif isinstance(v, int):
                            v_str = f"{v:,}"
                        else:
                            v_str = str(v)[:25]
                        items_formatted.append(f"{k}: {v_str}")
                    formatted_value = "; ".join(items_formatted)
                    if len(value) > 6:
                        formatted_value += f"; ... (+{len(value) - 6} more)"
                else:
                    formatted_value = "(empty)"
            else:
                formatted_value = str(value)[:80]  # Limit string length
            
            # Clean up metric names
            metric_name = key.replace('_', ' ').title()
            metric_name = metric_name.replace('Avg', 'Average')
            metric_name = metric_name.replace('Pct', 'Percent')
            
            table_data.append([metric_name, formatted_value])
        
        table = Table(table_data, colWidths=[3*inch, 3*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#cccccc')),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9f9f9')]),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),  # Top align for long values
        ]))
        
        return table

    def _build_cover_page(
        self, 
        user_query: str, 
        file_schema: dict[str, Any] | None
    ) -> list:
        """Build the cover page elements."""
        elements = []
        
        # Spacer to center content
        elements.append(Spacer(1, 2*inch))
        
        # Title
        elements.append(Paragraph(
            "Data Analysis Report",
            self.styles['ReportTitle']
        ))
        elements.append(Spacer(1, 0.5*inch))
        
        # Dataset info
        if file_schema:
            dataset_name = file_schema.get('original_filename', 'Unknown Dataset')
            row_count = file_schema.get('row_count', 'Unknown')
            col_count = len(file_schema.get('columns', []))
            
            elements.append(Paragraph(
                f"<b>Dataset:</b> {dataset_name}",
                self.styles['ReportBody']
            ))
            elements.append(Paragraph(
                f"<b>Rows:</b> {row_count:,}" if isinstance(row_count, int) else f"<b>Rows:</b> {row_count}",
                self.styles['ReportBody']
            ))
            elements.append(Paragraph(
                f"<b>Columns:</b> {col_count}",
                self.styles['ReportBody']
            ))
        
        # Date
        elements.append(Spacer(1, 0.5*inch))
        elements.append(Paragraph(
            f"<b>Generated:</b> {datetime.now().strftime('%B %d, %Y')}",
            self.styles['ReportBody']
        ))
        
        return elements

    def _build_executive_summary(
        self, 
        code_result: dict[str, Any] | None,
        code_output: str | None,
        executive_summary: str | None = None,
    ) -> list:
        """Build the executive summary section with readable narrative text."""
        elements = []
        
        elements.append(Paragraph(
            "Executive Summary",
            self.styles['SectionHeading']
        ))
        elements.append(Spacer(1, 0.2*inch))
        
        # If AI-generated executive summary is provided, use it
        if executive_summary:
            # Parse the executive summary into readable paragraphs
            # Handle markdown-style formatting
            paragraphs = executive_summary.strip().split('\n\n')
            for para in paragraphs:
                if para.strip():
                    # Clean up markdown formatting
                    clean_para = para.strip()
                    # Convert **bold** to <b>bold</b>
                    import re
                    clean_para = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', clean_para)
                    # Convert bullet points
                    if clean_para.startswith('- ') or clean_para.startswith('• '):
                        clean_para = '• ' + clean_para[2:]
                    # Escape XML chars (except our tags)
                    clean_para = clean_para.replace('&', '&amp;')
                    
                    elements.append(Paragraph(clean_para, self.styles['ReportBody']))
                    elements.append(Spacer(1, 0.1*inch))
            
            elements.append(Spacer(1, 0.2*inch))
        
        # Add key metrics table if available (as supporting data)
        if code_result and len(code_result) > 0:
            elements.append(Paragraph(
                "Key Metrics",
                self.styles['Heading3']
            ))
            elements.append(Spacer(1, 0.1*inch))
            
            # Limit to top 10 most important metrics
            items = list(code_result.items())[:10]
            table = self._create_metrics_table(items)
            elements.append(table)
        
        return elements

    def _build_charts_section(
        self, 
        chart_images: list[bytes], 
        chart_titles: list[str]
    ) -> list:
        """Build the charts section with images."""
        elements = []
        
        elements.append(Paragraph(
            "Visualizations",
            self.styles['SectionHeading']
        ))
        elements.append(Spacer(1, 0.2*inch))
        
        # Add each chart
        for i, (img_bytes, title) in enumerate(zip(chart_images, chart_titles)):
            # Chart title
            elements.append(Paragraph(
                f"<b>{title}</b>",
                self.styles['Heading3']
            ))
            elements.append(Spacer(1, 0.1*inch))
            
            # Chart image
            try:
                img = Image(BytesIO(img_bytes))
                # Resize to fit page width (6.5 inches max)
                img.drawWidth = 6.5*inch
                img.drawHeight = (6.5*inch) * (img.imageHeight / img.imageWidth)
                
                # Don't make it taller than 5 inches
                if img.drawHeight > 5*inch:
                    img.drawHeight = 5*inch
                    img.drawWidth = (5*inch) * (img.imageWidth / img.imageHeight)
                
                elements.append(img)
                elements.append(Spacer(1, 0.3*inch))
                
                # Page break after each chart except the last
                if i < len(chart_images) - 1:
                    elements.append(PageBreak())
                    
            except Exception as e:
                logger.error("Failed to add chart image %d: %s", i, e)
                elements.append(Paragraph(
                    "[Chart image could not be rendered]",
                    self.styles['ReportBody']
                ))
        
        return elements
