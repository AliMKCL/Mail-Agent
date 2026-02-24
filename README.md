# Mail Agent

NOTE: Currently the mcp server part is not working properly due to a change I did somewhere (known). I made the project public anyway to display the code.

An AI-powered Gmail and calendar manager that caches emails locally, builds semantic embeddings for search, and integrates with Google Calendar. It uses a **hybrid Python–Go** setup: FastAPI serves the API and UI, and an optional Go microservice handles high-throughput email fetching and embedding.

## Tech stack

- **Backend:** FastAPI (Python), optional Go server for email sync
- **Data:** SQLite (users, emails), ChromaDB (vector embeddings)
- **APIs:** Gmail API, Google Calendar API
- **AI:** Ollama (embeddings / LLM), optional OpenAI for MCP tools
- **Frontend:** Vanilla HTML/CSS/JS SPA, served by FastAPI

## Prerequisites

1. **Python 3** with venv (e.g. `python -m venv .mail_venv` then activate it).
2. **Google OAuth:** `credentials.json` in the project root (Web client, redirect URIs: `http://localhost:8080/`, `http://localhost:8000/oauth/callback`). Enable Gmail API and Google Calendar API.
3. **`.env`** in project root (optional but useful):
   ```env
   OPENAI_API_KEY=your_key
   OLLAMA_BASE_URL=http://127.0.0.1:11434
   ```
4. **Ollama** (optional): for embeddings and local LLM. Install from [ollama.ai](https://ollama.ai), then e.g. `ollama pull mxbai-embed-large`.

## How to run

Install Python deps (from project root):

```bash
pip install -r requirements.txt
```

**1. Python API + web UI (required)**

```bash
python -m uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** for the app. No separate JavaScript server; the frontend is served by FastAPI.

**2. Go email-sync server (optional, recommended for sync)**

```bash
cd backend/go-server
go build .
go run .
```

Runs on port **8001**. If it’s not running, the Python app falls back to its own sync implementation.

**3. Ollama (optional, for embeddings / local LLM)**

```bash
ollama serve
# and e.g.:
ollama pull mxbai-embed-large
```

**4. MCP server (optional, for LLM tool integration)**

```bash
python backend/mcp_server.py
```

## Quick reference

| What              | Command |
|-------------------|--------|
| Web app           | `python -m uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000` |
| Go sync server    | `cd backend/go-server && go run .` |
| Ollama            | `ollama serve` (and pull embedding model) |
| Add Gmail account | Web UI or `python backend/utilities/add_user.py` |
| Run tests         | `pytest` |

Add accounts via the UI at http://localhost:8000, then sync and use the calendar and email search from the same interface.
