# Agentic Data Analysis

A production-grade **Multi-Agent Chat Code Interpreter** built with FastAPI and Gemini 2.5 Flash. Upload a CSV, ask questions in natural language — specialized AI agents analyze your data, generate interactive visualizations, and present structured insights in real time.

---

## Architecture

```
Browser (index.html)
  │  Session stored in localStorage
  │  POST /api/sessions          →  Create new session
  │  POST /api/upload            →  CSV → shared volume (Parquet)
  │  POST /api/chat?session_id=  →  Server-Sent Events stream
  │  GET  /api/sessions/{id}/messages  →  restore history on reload
  ▼
FastAPI (api container)
  ▼
OrchestratorAgent  ← gemini-2.5-flash + NATIVE FUNCTION CALLING
  │
  │  Gemini dynamically decides which agents to call, in what order.
  │  This is NOT a hardcoded pipeline — the model reasons and routes.
  │
  ├──► CodeInterpreterAgent  ──► HTTP ──► executor sidecar
  │         └── 2 self-correction retries on stderr
  │         └── df already loaded from shared volume
  │
  ├──► VisualizationAgent
  │         └── Produces Plotly JSON (interactive in browser)
  │
  └──► PresentationAgent
            └── Streams markdown synthesis token-by-token (SSE)
  │
  ▼
Redis 7                               Shared Volume (dataset_store)
  session:{sid}:meta     (Hash, 30d)    /data/uploads/{file_id}.parquet
  session:{sid}:messages (List, 30d)    ↑ executor mounts read-only
```

### How Agents Interact

**Dynamic dispatch via Gemini function calling** — not a hardcoded pipeline:

1. User sends query → Orchestrator builds a prompt with 3 tool declarations
2. Gemini reasons about intent and returns a `FunctionCall` (or multiple)
3. Orchestrator executes the named agent, feeds `FunctionResponse` back
4. Gemini decides if more agents are needed (chain continues) or returns final answer
5. SSE stream delivers typed chunks to the browser in real time

**Example: "What are the top 3 products by revenue?"**
```
Gemini → FunctionCall("run_code_interpreter", {"task": "compute revenue by product, top 3"})
Orchestrator → CodeInterpreterAgent → executor sidecar → stdout + result_dict
Gemini reads result → returns plain text answer (no more tool calls needed)
```

**Example: "Show me a chart of sales trends"**
```
Gemini → FunctionCall("run_code_interpreter", {"task": "compute monthly sales"})
→ FunctionCall("run_visualization_agent", {"chart_type": "line", "chart_title": "Monthly Sales"})
→ FunctionCall("run_presentation_agent", {})   ← only if needed
→ Final text answer
```

### Key Design Decisions

| Concern | Decision |
|---|---|
| **Agent dispatch** | Gemini function calling with `AUTO` mode — model decides |
| **Code sandbox** | Docker sidecar: 256MB RAM, 0.5 CPU, no internet, read-only FS, `signal.alarm` timeout |
| **File storage** | Shared volume (Parquet). Path-only passed to executor — never the data |
| **Session persistence** | Redis with 30-day rolling TTL. Conversation history preserved across page reloads |
| **Streaming** | SSE with typed chunks: `agent_switch`, `text`, `code`, `chart_plotly`, `error`, `done` |
| **Charts** | Plotly JSON (not base64 PNG) — interactive, no encoding overhead |
| **Self-correction** | Code errors fed back to Gemini for up to 2 auto-fix retries |

---

## Setup

### Prerequisites
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Docker + Docker Compose (recommended)
- Gemini API key from [Google AI Studio](https://aistudio.google.com)

### Docker Deployment (recommended)

```bash
# 1. Clone the repository
git clone <repo>
cd agentic-data-analysis

# 2. Configure environment

# Edit .env and set GEMINI_API_KEY=your_key_here

# 3. Start all services (API + Redis + Executor)
docker compose up --build
```

Open http://localhost:8000


---

## Example Queries

Upload any CSV file, then try:

| Query | Agents Invoked |
|---|---|
| `"Summarize this dataset"` | Code Interpreter → Presentation |
| `"What are the top 5 rows by [column]?"` | Code Interpreter |
| `"Show a bar chart of [category] by [value]"` | Code Interpreter → Visualization |
| `"Analyze trends and create a full presentation"` | All 3 agents |
| `"What's the average [column]?"` | Code Interpreter |
| `"Show me the distribution of [column]"` | Code Interpreter → Visualization |
| `"Which [category] has the highest [metric]?"` | Code Interpreter |

---

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Chat UI |
| `/api/sessions` | POST | Create new session, returns `session_id` |
| `/api/sessions/{id}/messages` | GET | Full conversation history for a session |
| `/api/upload` | POST | Upload CSV file (multipart), returns `file_id` |
| `/api/chat` | POST | Send message → SSE stream. Query params: `session_id` (optional), `file_id` |
| `/api/health` | GET | Health check (executor connectivity) |

### Session Flow

1. **Create session** (optional - auto-created if omitted):
   ```bash
   curl -X POST http://localhost:8000/api/sessions
   # Returns: {"session_id": "uuid", "created_at": "..."}
   ```

2. **Upload file**:
   ```bash
   curl -X POST http://localhost:8000/api/upload \
     -F "file=@data.csv"
   # Returns: {"file_id": "uuid", "original_filename": "data.csv", ...}
   ```

3. **Chat** (SSE stream):
   ```bash
   curl -X POST "http://localhost:8000/api/chat?session_id=xxx&file_id=xxx" \
     -H "Content-Type: application/json" \
     -d '{"query": "Analyze this data"}'
   ```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | (required) | Google AI Studio API key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model to use |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `EXECUTOR_URL` | `http://localhost:8080` | Code executor sidecar URL |
| `DATA_DIR` | `./uploads` | Directory for uploaded files |
| `MAX_UPLOAD_SIZE_MB` | `50` | Maximum file upload size |

---

## Project Structure

```
agentic-data-analysis/
├── app/
│   ├── agents/
│   │   ├── orchestrator.py    # Main agent coordinator + Gemini tool loop
│   │   ├── code_interpreter.py # Generates & executes Python code
│   │   ├── visualization.py    # Creates Plotly charts
│   │   └── presentation.py     # Synthesizes markdown reports
│   ├── api/routes/
│   │   ├── chat.py            # SSE streaming chat endpoint
│   │   ├── files.py           # File upload endpoint
│   │   └── sessions.py        # Session management
│   ├── models/
│   │   ├── handoff.py         # AgentHandoff & AgentResult models
│   │   ├── file.py            # UploadedFile model
│   │   └── schemas.py         # API request/response schemas
│   ├── services/
│   │   ├── gemini_client.py   # Gemini API wrapper
│   │   ├── redis_client.py    # Redis session storage
│   │   ├── executor_client.py # HTTP client for executor sidecar
│   │   └── file_manager.py    # CSV → Parquet conversion
│   ├── static/
│   │   └── index.html         # Single-page chat UI
│   ├── config.py              # Settings from environment
│   ├── dependencies.py        # FastAPI dependency injection
│   └── main.py                # App entry point
├── executor/
│   ├── server.py              # Flask code execution sandbox
│   └── Dockerfile
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

---


