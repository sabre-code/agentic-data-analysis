# 🤖 Agentic Data Analysis

A production-grade **Multi-Agent AI System** for conversational data analysis. Upload a CSV, ask questions in natural language — specialized AI agents collaborate to analyze data, generate interactive visualizations, and create professional PDF/PowerPoint reports in real time.

> **Key Feature**: Agents maintain context across the conversation. Generate charts, then ask for a report with "these charts" — the system remembers and reuses them intelligently.

---

## 🎯 What It Does

```
User: "Analyze this sales data and create some insightful charts"
  → Code Interpreter Agent computes metrics (revenue, trends, categories)
  → Visualization Agent creates interactive Plotly charts
  → Charts are persisted to session for future use

User: "Create a PDF and PPT report from these charts with executive summary"
  → Orchestrator recognizes existing charts, skips regeneration
  → Presentation Agent generates professional reports with AI summaries
  → PDF and PowerPoint files ready for download
```

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Browser (index.html)                           │
│   • Single-page chat interface                                              │
│   • Session ID persisted in memory                                          │
│   • Real-time SSE streaming                                                 │
│   • Interactive Plotly charts                                               │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                    POST /api/chat?session_id=xxx&file_id=xxx
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          FastAPI Backend (api container)                     │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      🧠 Orchestrator Agent                           │   │
│  │                                                                      │   │
│  │  • Receives user query + conversation history                        │   │
│  │  • Loads session artifacts (charts) from Redis                       │   │
│  │  • Builds system prompt with context                                 │   │
│  │  • Uses Gemini function calling to route to specialists              │   │
│  │  • Implements smart chart reuse logic                                │   │
│  │  • Streams SSE events back to browser                                │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                   │                                         │
│            ┌──────────────────────┼──────────────────────┐                 │
│            │                      │                      │                 │
│            ▼                      ▼                      ▼                 │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐         │
│  │ 🔧 Code          │  │ 📊 Visualization │  │ 📝 Presentation  │         │
│  │    Interpreter   │  │    Agent         │  │    Agent         │         │
│  │                  │  │                  │  │                  │         │
│  │ • Gemini writes  │  │ • Gemini writes  │  │ • Synthesizes    │         │
│  │   Python code    │  │   Plotly specs   │  │   findings       │         │
│  │ • Executor runs  │  │ • Multi-chart    │  │ • AI executive   │         │
│  │   in sandbox     │  │   support        │  │   summaries      │         │
│  │ • Self-corrects  │  │ • Persists to    │  │ • Generates      │         │
│  │   on errors      │  │   session        │  │   PDF/PPTX       │         │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘         │
│           │                     │                     │                    │
└───────────┼─────────────────────┼─────────────────────┼────────────────────┘
            │                     │                     │
            ▼                     │                     │
┌──────────────────────┐          │                     │
│  Executor Sidecar    │          │                     │
│  (Docker sandbox)    │          │                     │
│                      │          │                     │
│  • 256MB RAM limit   │          │                     │
│  • No network access │          │                     │
│  • Read-only FS      │          │                     │
│  • 30s timeout       │          │                     │
│  • pandas + numpy    │          │                     │
└──────────────────────┘          │                     │
                                  │                     │
┌─────────────────────────────────┴─────────────────────┴────────────────────┐
│                               Redis 7                                       │
│                                                                             │
│  session:{sid}:meta       → Session metadata (30-day TTL)                  │
│  session:{sid}:messages   → Conversation history                           │
│  session:{sid}:artifacts  → Generated charts (for cross-request reuse)     │
│  session:{sid}:active_file→ Current file reference                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
┌─────────────────────────────────┴───────────────────────────────────────────┐
│                         Shared Volume (/data)                               │
│                                                                             │
│  /data/uploads/{file_id}.parquet  → Uploaded CSVs converted to Parquet     │
│  /data/reports/{session_id}/      → Generated PDF/PPTX reports             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🔄 Query Flow Diagram

This diagram shows how a multi-turn conversation flows through the system:

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ TURN 1: "Analyze the data and create some insightful charts"                            │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
                          ┌───────────────────────────────┐
                          │     Orchestrator Agent        │
                          │  • Loads empty session        │
                          │  • Builds system prompt       │
                          │  • Calls Gemini with tools    │
                          └───────────────────────────────┘
                                          │
                    Gemini decides: "Need to analyze data first"
                                          │
                                          ▼
                          ┌───────────────────────────────┐
                          │  FunctionCall:                │
                          │  run_code_interpreter         │
                          │  {task: "analyze sales data,  │
                          │   compute key metrics"}       │
                          └───────────────────────────────┘
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              CODE INTERPRETER AGENT                                      │
│                                                                                         │
│  1. Build schema context (columns, dtypes, row count)                                   │
│  2. Ask Gemini to write Python analysis code                                            │
│  3. Send code to Executor sidecar                                                       │
│                                                                                         │
│     ┌─────────────────────────────────────────────────────────────────────────────┐    │
│     │ EXECUTION (in sandbox)                                                       │    │
│     │                                                                              │    │
│     │  df = pd.read_parquet('/data/uploads/{file_id}.parquet')                    │    │
│     │  # ... analysis code ...                                                     │    │
│     │  result = {"total_revenue": 150000, "top_products": [...]}                  │    │
│     └─────────────────────────────────────────────────────────────────────────────┘    │
│                                          │                                              │
│                              ┌───────────┴───────────┐                                 │
│                              │                       │                                 │
│                         SUCCESS                   ERROR                                │
│                              │                       │                                 │
│                              │              ┌────────▼────────┐                        │
│                              │              │ SELF-CORRECTION │◄─────────────┐         │
│                              │              │ (max 2 retries) │              │         │
│                              │              │                 │              │         │
│                              │              │ Feed error back │──► Retry ───►│         │
│                              │              │ to Gemini       │              │         │
│                              │              └─────────────────┘              │         │
│                              │                                               │         │
│                              ▼                                               │         │
│                    Return: stdout, result_dict, code                         │         │
│                                                                              │         │
└──────────────────────────────────────────────────────────────────────────────┘
                                          │
                          ┌───────────────┴───────────────┐
                          │ FunctionResponse to Gemini    │
                          │ {analysis_output: "...",      │
                          │  computed_metrics: {...}}     │
                          └───────────────────────────────┘
                                          │
                    Gemini decides: "Now create visualizations"
                                          │
                                          ▼
                          ┌───────────────────────────────┐
                          │  FunctionCall:                │
                          │  run_visualization_agent      │
                          │  {chart_type: "bar",          │
                          │   chart_title: "Revenue..."}  │
                          └───────────────────────────────┘
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                             VISUALIZATION AGENT                                          │
│                                                                                         │
│  1. Build prompt with analysis results + chart requirements                             │
│  2. Ask Gemini to write Plotly chart specifications                                     │
│  3. Parse and validate JSON specs                                                       │
│  4. Convert to PNG for reports (using Kaleido)                                          │
│                                                                                         │
│     ┌─────────────────────────────────────────────────────────────────────────────┐    │
│     │ CHART GENERATION                                                             │    │
│     │                                                                              │    │
│     │  {"data": [{"type": "bar", "x": [...], "y": [...]}],                        │    │
│     │   "layout": {"title": {"text": "Monthly Revenue Trend"}}}                   │    │
│     │                                                                              │    │
│     │  → Emitted to browser as SSE event (interactive)                            │    │
│     │  → Saved to Redis session (for future requests)                             │    │
│     └─────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                  ┌───────────────────────┴───────────────────────┐
                  │                                               │
         SSE: chart_plotly                              Save to Redis
         (to browser)                                   session:{sid}:artifacts
                  │                                               │
                  │              ┌────────────────────────────────┘
                  │              │
                  ▼              ▼
          ┌───────────────────────────────────────────┐
          │     FunctionResponse to Gemini           │
          │     {chart_generated: true,              │
          │      chart_title: "Monthly Revenue..."}  │
          └───────────────────────────────────────────┘
                                          │
                    Gemini decides: "Analysis complete, return summary"
                                          │
                                          ▼
          ┌───────────────────────────────────────────┐
          │           FINAL TEXT RESPONSE             │
          │  "I've analyzed the data and created 2    │
          │   insightful charts: 1. Monthly Revenue   │
          │   Trend... 2. Revenue by Category..."     │
          └───────────────────────────────────────────┘
                                          │
                                SSE: text (streamed)
                                          │
                                          ▼
                              ════════════════════════
                              ║   END OF TURN 1     ║
                              ════════════════════════


┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ TURN 2: "Create a PDF and PPT report from these charts with executive summary"          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
                          ┌───────────────────────────────┐
                          │     Orchestrator Agent        │
                          │                               │
                          │  📦 LOADS SESSION ARTIFACTS   │
                          │  • 2 charts from Turn 1       │
                          │  • Adds to system prompt      │
                          │                               │
                          │  System prompt now includes:  │
                          │  "Session has 2 charts:       │
                          │   1. Monthly Revenue Trend    │
                          │   2. Revenue by Category"     │
                          └───────────────────────────────┘
                                          │
                    Gemini sees: "User wants report with EXISTING charts"
                    Gemini decides: "Skip visualization, go to presentation"
                                          │
                                          ▼
                          ┌───────────────────────────────┐
                          │  FunctionCall:                │
                          │  run_presentation_agent       │
                          │  {instructions: "Create PDF   │
                          │   and PPTX with executive     │
                          │   summary using existing      │
                          │   charts"}                    │
                          └───────────────────────────────┘
                                          │
                                          ▼
         ╔════════════════════════════════════════════════════════════════════════════════╗
         ║                              SMART CHART REUSE                                  ║
         ║                                                                                 ║
         ║  Orchestrator checks: Should we reuse existing charts?                         ║
         ║                                                                                 ║
         ║  Query analysis:                                                               ║
         ║  • "these charts" → REUSE INDICATOR ✓                                          ║
         ║  • "create a report" → REUSE INDICATOR ✓                                       ║
         ║  • No "new chart", "different", "another" → NOT regenerate ✓                   ║
         ║                                                                                 ║
         ║  Decision: ♻️ REUSE EXISTING CHARTS                                            ║
         ║                                                                                 ║
         ╚════════════════════════════════════════════════════════════════════════════════╝
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              PRESENTATION AGENT                                          │
│                                                                                         │
│  1. Validate data sufficiency                                                           │
│     • Has 2 charts from session artifacts ✓                                             │
│     • Sufficient for report generation                                                  │
│                                                                                         │
│  2. Detect format intent: PDF + PPTX                                                    │
│                                                                                         │
│  3. Generate AI Executive Summary                                                       │
│     ┌─────────────────────────────────────────────────────────────────────────────┐    │
│     │ Gemini generates business-focused summary:                                   │    │
│     │ "This analysis reveals strong revenue growth with Electronics leading..."   │    │
│     └─────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                         │
│  4. Generate Reports                                                                    │
│     ┌─────────────────────────────────────────────────────────────────────────────┐    │
│     │ PDF GENERATION (ReportLab)                                                   │    │
│     │ • Title page with timestamp                                                  │    │
│     │ • Executive summary section                                                  │    │
│     │ • Chart images (PNG via Kaleido)                                            │    │
│     │ • Key metrics and insights                                                   │    │
│     │                                                                              │    │
│     │ PPTX GENERATION (python-pptx)                                               │    │
│     │ • Title slide                                                                │    │
│     │ • Executive summary slide                                                    │    │
│     │ • One slide per chart                                                        │    │
│     │ • Key findings slide                                                         │    │
│     └─────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                         │
│  5. Save to /data/reports/{session_id}/                                                │
│                                                                                         │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
          ┌───────────────────────────────────────────┐
          │          SSE Events Emitted               │
          │                                           │
          │  • agent_switch: "Presentation Agent..."  │
          │  • text: "I'm generating the PDF and      │
          │    PowerPoint reports..."                 │
          │  • report_files: [{pdf_url}, {pptx_url}]  │
          │  • done                                   │
          └───────────────────────────────────────────┘
                                          │
                                          ▼
                              ════════════════════════
                              ║   END OF TURN 2     ║
                              ════════════════════════
```

---

## 🧠 Key Decision Points

### Orchestrator Routing Logic

```
User Query
    │
    ▼
┌─────────────────────────────────┐
│ Is there an active dataset?     │
│ OR analytical keywords?         │
│ OR existing session artifacts?  │
└─────────────────────────────────┘
    │                    │
   YES                   NO
    │                    │
    ▼                    ▼
┌─────────────┐    ┌─────────────┐
│ Tool Loop   │    │ Direct Chat │
│ (Agents)    │    │ Response    │
└─────────────┘    └─────────────┘
```

### Chart Reuse Logic

```
Visualization Agent Called
         │
         ▼
┌────────────────────────────────────┐
│ Do we have existing session charts?│
└────────────────────────────────────┘
    │                    │
   YES                   NO
    │                    │
    ▼                    ▼
┌─────────────────┐      │
│ Analyze Query:  │      │
│ Reuse or New?   │      │
└─────────────────┘      │
    │         │          │
 REUSE      NEW          │
    │         │          │
    ▼         └──────────┴──────┐
┌─────────────────┐             │
│ Return existing │             │
│ charts, skip    │             ▼
│ regeneration    │    ┌─────────────────┐
└─────────────────┘    │ Generate new    │
                       │ charts via      │
                       │ Gemini + Plotly │
                       └─────────────────┘

REUSE INDICATORS:                    NEW CHART INDICATORS:
• "these charts"                     • "new chart"
• "the charts"                       • "different chart"  
• "create a report"                  • "show me a histogram"
• "generate presentation"            • "create a pie chart"
• "pdf with"                         • "visualize X"
```

### Code Interpreter Self-Correction

```
Execute Code
    │
    ▼
┌──────────┐
│ Success? │
└──────────┘
    │      │
   YES     NO
    │      │
    │      ▼
    │  ┌─────────────────────┐
    │  │ Attempt < 3?        │
    │  └─────────────────────┘
    │      │            │
    │     YES           NO
    │      │            │
    │      ▼            ▼
    │  ┌─────────┐  ┌─────────┐
    │  │Feed err │  │ Return  │
    │  │to Gemini│  │ Error   │
    │  │for fix  │  │ Result  │
    │  └────┬────┘  └─────────┘
    │       │
    │       └──────► Execute Fixed Code
    │                     │
    │                     ▼
    │               [Loop back to "Success?"]
    │
    ▼
┌─────────────────┐
│ Return Results: │
│ stdout, result, │
│ code            │
└─────────────────┘
```

---

## 📦 SSE Event Types

The chat endpoint streams Server-Sent Events with typed JSON payloads:

| Event Type | Description | Example |
|------------|-------------|---------|
| `agent_switch` | Agent started working | `{"type": "agent_switch", "content": "Code Interpreter Agent is working..."}` |
| `text` | Markdown text chunk | `{"type": "text", "content": "The analysis shows..."}` |
| `code` | Generated Python code | `{"type": "code", "content": "df.groupby('category')..."}` |
| `chart_plotly` | Interactive chart JSON | `{"type": "chart_plotly", "content": "{\"data\": [...]}"}` |
| `report_files` | Generated report URLs | `{"type": "report_files", "content": "[{\"type\": \"pdf\", \"url\": \"...\"}]"}` |
| `error` | Error message | `{"type": "error", "content": "Execution failed: ..."}` |
| `done` | Stream complete | `{"type": "done", "content": ""}` |

---

## 🚀 Setup

### Prerequisites
- Python 3.12+
- Docker + Docker Compose
- Gemini API key from [Google AI Studio](https://aistudio.google.com)

### Quick Start

```bash
# 1. Clone the repository
git clone <repo>
cd agentic-data-analysis

# 2. Create .env file
echo "GEMINI_API_KEY=your_key_here" > .env

# 3. Start all services
docker compose up --build

# 4. Open browser
open http://localhost:8000
```

---

## 💬 Example Queries

| Query | Agent Flow |
|-------|------------|
| `"Summarize this dataset"` | Code Interpreter → Presentation |
| `"What are the top 5 products by revenue?"` | Code Interpreter |
| `"Show a bar chart of sales by category"` | Code Interpreter → Visualization |
| `"Analyze trends and create charts"` | Code Interpreter → Visualization |
| `"Create a PDF report from these charts"` | Presentation (reuses existing charts) |
| `"Generate a PowerPoint with executive summary"` | Presentation (with AI summary) |
| `"Create a new pie chart of market share"` | Visualization (generates new) |

---

## 🔌 API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Chat UI |
| `/api/sessions` | POST | Create new session |
| `/api/sessions/{id}/messages` | GET | Get conversation history |
| `/api/upload` | POST | Upload CSV file |
| `/api/chat` | POST | Chat with SSE streaming |
| `/api/health` | GET | Health check |
| `/api/reports/{session_id}/{filename}` | GET | Download generated report |

### Chat Request

```bash
curl -X POST "http://localhost:8000/api/chat?session_id=xxx&file_id=xxx" \
  -H "Content-Type: application/json" \
  -d '{"query": "Analyze this data and create charts"}'
```

Response: SSE stream with typed events (see SSE Event Types above).

The `session_id` is returned in the `X-Session-ID` response header on first request and should be passed on subsequent requests to maintain conversation context.

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | (required) | Google AI Studio API key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model to use |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `EXECUTOR_URL` | `http://localhost:8080` | Code executor URL |
| `DATA_DIR` | `./uploads` | Upload directory |
| `MAX_UPLOAD_SIZE_MB` | `50` | Max file size |

---

## 📁 Project Structure

```
agentic-data-analysis/
├── app/
│   ├── agents/
│   │   ├── base.py              # BaseAgent abstract class
│   │   ├── orchestrator.py      # 🧠 Main coordinator + tool loop
│   │   ├── code_interpreter.py  # 🔧 Python code generation & execution
│   │   ├── visualization.py     # 📊 Plotly chart generation
│   │   └── presentation.py      # 📝 Report synthesis & PDF/PPTX
│   ├── api/routes/
│   │   ├── chat.py              # SSE streaming chat endpoint
│   │   ├── files.py             # File upload endpoint
│   │   └── sessions.py          # Session management
│   ├── models/
│   │   ├── handoff.py           # AgentHandoff, AgentResult, GeneratedArtifact
│   │   ├── file.py              # UploadedFile model
│   │   └── schemas.py           # API schemas
│   ├── services/
│   │   ├── gemini_client.py     # Gemini API wrapper
│   │   ├── redis_client.py      # Session & artifact storage
│   │   ├── executor_client.py   # Executor HTTP client
│   │   ├── file_manager.py      # CSV → Parquet conversion
│   │   ├── report_manager.py    # Report orchestration
│   │   ├── pdf_generator.py     # ReportLab PDF generation
│   │   └── pptx_generator.py    # python-pptx PowerPoint generation
│   ├── static/
│   │   └── index.html           # Single-page chat UI
│   ├── config.py                # Settings
│   ├── dependencies.py          # FastAPI DI
│   └── main.py                  # App entry point
├── executor/
│   ├── server.py                # Flask sandbox server
│   └── Dockerfile               # Executor container
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

---

## 🔒 Security

- **Sandboxed Execution**: Code runs in isolated Docker container
  - 256MB RAM limit
  - 0.5 CPU limit  
  - No network access
  - Read-only filesystem (except /tmp)
  - 30-second timeout
  - Whitelisted imports only (pandas, numpy, etc.)
- **Session Isolation**: Each session has isolated artifacts
- **File Validation**: CSV-only uploads with size limits

---

## 🎨 Key Design Decisions

| Concern | Decision |
|---------|----------|
| **Agent Dispatch** | Gemini function calling with `AUTO` mode — model decides routing |
| **Chart Persistence** | Redis session artifacts — charts survive across requests |
| **Smart Reuse** | Query analysis determines reuse vs. regenerate charts |
| **Self-Correction** | Up to 2 automatic retry attempts on code execution errors |
| **Streaming** | SSE with typed chunks for real-time UX |
| **Reports** | ReportLab (PDF) + python-pptx (PPTX) with Kaleido PNG conversion |
| **Executive Summary** | AI-generated business-focused summary via Gemini |

---


