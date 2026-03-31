# Agent-Scope

A local AI agent chat application with persistent memory, versioned artifacts, streaming responses, and a skill system. Built on FastAPI with a WebSocket-driven frontend, PostgreSQL for storage, and Qdrant for semantic memory search.

## Features

- **Streaming chat** — token-by-token response streaming over WebSocket, including tool-call progress and thinking blocks
- **Long-term memory** — facts are automatically extracted from conversations, embedded, and retrieved semantically on every future turn
- **Versioned artifacts** — code, documents, HTML pages, and other content are saved as artifacts with full version history, viewable in a split-panel UI
- **Skill system** — expert instruction documents the agent loads on demand for complex tasks (HTML generation, code, research, resumes, etc.)
- **Tool calling** — web search, file read/write, code execution, datetime, and memory tools
- **Multi-provider** — swap between Anthropic Claude (default) and Groq via a single env variable
- **Session management** — multiple named chat sessions with full history persistence

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, Uvicorn |
| Agent loop | Custom ReAct loop (no framework) |
| LLM | Anthropic Claude (`claude-sonnet-4`) / Groq |
| Embeddings | OpenAI `text-embedding-3-small` |
| Database | PostgreSQL 16 (via asyncpg + SQLAlchemy async) |
| Vector store | Qdrant |
| Frontend | Vanilla HTML/CSS/JS, marked.js |
| Infra | Docker Compose (Postgres + Qdrant) |

## Project Structure

```
Agent-Scope/
├── app.py                  # FastAPI server, WebSocket handler, REST endpoints
├── agent.py                # ReAct agent loop, event streaming
├── memory.py               # Session, message, fact, and artifact persistence
├── vector_store.py         # Qdrant collection management
├── embeddings.py           # OpenAI embedding calls
├── models.py               # LLM provider abstraction (Anthropic / Groq)
├── config.py               # Settings loaded from .env
├── tool_context.py         # Per-turn session/user context for tools
├── tools/
│   ├── __init__.py         # Tool registry and autodiscovery
│   ├── artifacts.py        # create_artifact, update_artifact, get_artifact_content
│   ├── code_exec.py        # execute_python
│   ├── datetime_tools.py   # get_current_datetime
│   ├── file_ops.py         # read_file, write_file, list_files
│   ├── memory_tools.py     # remember_fact, recall_facts
│   ├── skill_tools.py      # load_skill, list_skills
│   └── web_search.py       # web_search
├── skills/
│   ├── _registry.json      # Skill metadata index
│   ├── code_generation.md
│   ├── data_analysis.md
│   ├── docx_writing.md
│   ├── html_css.md
│   ├── presentation.md
│   └── web_search.md
├── prompts/
│   └── system.md           # System prompt with memory and artifact injection points
├── static/
│   └── index.html          # Single-file frontend (chat UI + artifact panel)
├── docker-compose.yml      # Postgres + Qdrant services
├── requirements.txt
└── .env.example
```

## Prerequisites

- Python 3.11+
- Docker Desktop (for Postgres and Qdrant)
- API keys: Anthropic, OpenAI (embeddings)

## Setup

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd Agent-Scope
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

```env
MODEL_PROVIDER=anthropic          # or "groq"

ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-20250514

GROQ_API_KEY=                     # only needed if MODEL_PROVIDER=groq
GROQ_MODEL=llama-3.3-70b-versatile

OPENAI_API_KEY=sk-proj-...        # used for embeddings only

DATABASE_URL=postgresql+asyncpg://agent:agent_dev@localhost:5432/agent_chat
QDRANT_HOST=localhost
QDRANT_PORT=6333

MAX_ITERATIONS=10
DATA_DIR=data
HOST=127.0.0.1
PORT=8000
```

### 3. Start the databases

```bash
docker compose up -d
```

This starts:
- **PostgreSQL 16** on port `5432`
- **Qdrant** on port `6333`

### 4. Run the server

```bash
python app.py
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

## How It Works

### Agent Loop

Each user message triggers a full ReAct turn in `agent.py`:

1. Relevant memory facts are retrieved from Qdrant via semantic search and injected into the system prompt
2. The model streams a response; tool calls are intercepted and executed in sequence
3. Tool results are fed back to the model for the next iteration (up to `MAX_ITERATIONS`)
4. All events (text deltas, thinking, tool start/result, artifacts) stream over WebSocket to the frontend in real time

### Memory

- Facts are extracted automatically from conversations (on disconnect and every 10 turns) using a separate LLM call
- Each fact is embedded with OpenAI and stored in both PostgreSQL (full text) and Qdrant (vector index)
- On every new message, the top-K most semantically similar facts are retrieved and prepended to the system prompt
- The Memory tab in the sidebar lets you search, edit, and delete stored facts

### Artifacts

- The agent calls `create_artifact` or `update_artifact` for any substantial content (code, documents, HTML, CSV, etc.)
- Artifacts are stored in PostgreSQL with full version history
- The split panel in the UI shows artifacts live as they stream, then renders them properly (HTML preview in iframe, markdown rendered, code in monospace)
- Users can navigate between versions using the `v1/v2/...` controls

### Skills

Skills are markdown instruction files in `skills/` that the agent loads via `load_skill(name)` before complex tasks. They provide step-by-step templates and quality criteria. Add a new skill by dropping a `.md` file in `skills/` and registering it in `skills/_registry.json`.

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Serve the chat UI |
| `GET` | `/api/sessions` | List all sessions |
| `POST` | `/api/sessions` | Create a new session |
| `GET` | `/api/sessions/{id}/messages` | Load message history |
| `POST` | `/api/sessions/{id}/extract` | Manually trigger fact extraction |
| `GET` | `/api/facts` | List all stored facts |
| `GET` | `/api/facts/search?q=...` | Semantic fact search |
| `PUT` | `/api/facts/{id}` | Update a fact |
| `DELETE` | `/api/facts/{id}` | Delete a fact |
| `GET` | `/api/artifacts/{id}` | Get artifact content (latest or `?version=N`) |
| `GET` | `/api/artifacts/{id}/versions` | List all versions |
| `WS` | `/ws/{session_id}` | WebSocket chat stream |

## Adding a Tool

1. Create a file in `tools/`, e.g. `tools/my_tool.py`
2. Export `TOOL_DEFINITION` (JSON Schema dict) and an async `execute(**kwargs) -> str`
3. The tool is auto-discovered on server start — no registration needed

```python
TOOL_DEFINITION = {
    "name": "my_tool",
    "description": "Does something useful.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The input"}
        },
        "required": ["query"]
    }
}

async def execute(query: str) -> str:
    return f"Result for: {query}"
```

## Switching LLM Provider

Set `MODEL_PROVIDER` in `.env`:

```env
MODEL_PROVIDER=groq
GROQ_API_KEY=gsk_...
```

Groq uses `llama-3.3-70b-versatile` by default. Note: Groq does not support extended thinking.
