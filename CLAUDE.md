# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Mail Agent is an AI-powered Gmail and calendar management system with a **hybrid Python-Go microservice architecture**. The system fetches emails via Gmail API, stores them locally, generates semantic embeddings for intelligent search, and integrates with Google Calendar. It also exposes Model Context Protocol (MCP) tools for LLM integration.

**Tech Stack:**
- Python FastAPI (REST API, port 8000)
- Go microservice (high-performance email fetching, port 8001)
- SQLite database (user/email storage)
- ChromaDB vector database (semantic search)
- Vanilla JavaScript frontend (SPA)
- MCP Server for LLM tool integration

## Running the Application

### Prerequisites
1. **Python virtual environment**: Activate `.mail_venv` (or create with `python -m venv .mail_venv`)
2. **Google OAuth credentials**: Download `credentials.json` from Google Cloud Console
   - Create OAuth 2.0 Client (Web Application type)
   - Add redirect URIs: `http://localhost:8080/` and `http://localhost:8000/oauth/callback`
   - Enable Gmail API and Google Calendar API
3. **Environment variables** (.env file in project root):
   ```
   OPENAI_API_KEY=your_openai_key_here
   OLLAMA_BASE_URL=http://127.0.0.1:11434
   ```
4. **Optional**: Ollama running locally for embeddings and LLM features

### Startup Commands

**Start Go microservice (recommended for performance):**
```bash
cd backend/go-server
go run .
```
Runs on port 8001. Handles concurrent email fetching and batch embedding.

**Start Python FastAPI server:**
```bash
python -m uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000
```

**Start MCP Server (for LLM integration):**
```bash
python backend/mcp_server.py
# Or with MCP Inspector for testing:
npx @modelcontextprotocol/inspector python backend/mcp_server.py
```

**Access web interface:**
- Navigate to `http://localhost:8000`

### Adding Gmail Accounts

**Via Web UI:**
1. Click "+ Add Gmail Account" in dropdown
2. Enter email address
3. Complete OAuth flow in browser

**Via CLI:**
```bash
python backend/utilities/add_user.py
```

## Development Commands

### Testing

**Run all tests:**
```bash
pytest
```

**Run specific test file:**
```bash
pytest tests/test_mcp_tools.py -v
```

**Run specific test:**
```bash
pytest tests/test_mcp_tools.py::test_list_users -v
```

**Run with output:**
```bash
pytest -s
```

### Database Management

**List all users:**
```bash
python backend/utilities/list_users.py
```

**Re-authenticate user (refresh OAuth tokens):**
```bash
python backend/utilities/reauth_user.py          # Re-auth all users
python backend/utilities/reauth_user.py <user_id>  # Re-auth specific user
```

**Inspect SQLite database:**
```bash
sqlite3 gmail_agent.db
.tables
SELECT * FROM users;
SELECT * FROM emails LIMIT 10;
.quit
```

### Email Operations

**Test email fetching for all users:**
```bash
python -m backend.services.gmail_read
```

**List Moodle calendar events:**
```bash
python -m backend.services.moodle_calendar --user-id 1 --days-ahead 90
python -m backend.services.moodle_calendar --list-only
```

### Go Server

**Build Go server:**
```bash
cd backend/go-server
go build
```

**Run with verbose output:**
```bash
cd backend/go-server
go run . 2>&1 | tee server.log
```

**Install Go dependencies:**
```bash
cd backend/go-server
go mod download
```

## Architecture Overview

### Hybrid Python-Go Design

The application uses a microservice architecture for optimal performance:

**Python FastAPI (app.py)** - Port 8000
- REST API endpoints for frontend
- User management and OAuth flow
- Calendar operations (Google Calendar API)
- Vector database queries (ChromaDB)
- Falls back to Python implementation if Go server unavailable

**Go Microservice (go-server/)** - Port 8001
- Concurrent email fetching (10 workers)
- Batch embedding with Ollama (50 emails/batch)
- Direct SQLite writes with duplicate detection
- 3-5x faster than Python for email processing

**Email Sync Flow:**
1. Frontend calls `/api/sync?user_id=X`
2. Python FastAPI fetches email IDs from Gmail
3. Python POSTs IDs to `http://localhost:8001/fetch-emails`
4. Go server fetches emails concurrently, writes to DB, generates embeddings
5. Python receives emails with embeddings and stores in ChromaDB
6. Frontend displays success

### Key Backend Components

**backend/app.py** - Main FastAPI application with all REST endpoints
- `/api/emails` - Get user's cached emails
- `/api/sync` - Trigger email sync (routes to Go server)
- `/api/calendar/events` - CRUD for calendar events (includes Moodle calendar)
- `/api/query` - AI-powered semantic email search
- `/api/users` - User management
- `/oauth/callback` - OAuth redirect handler

**backend/mcp_server.py** - Model Context Protocol server
- 12 MCP tools: email search, calendar CRUD, date extraction, summarization
- 7 resources: read-only data sources (inbox, calendar, user info)
- 5 prompts: guided workflows (email triage, deadline tracking, meeting scheduler)
- Uses FastMCP framework

**backend/llm_integration.py** - LLM tool execution layer
- OpenAI function calling integration (gpt-4o-mini)
- Iterative tool execution (max 5 iterations)
- Date context injection for calendar operations
- Tool registry mapping to MCP functions

**backend/databases/database.py** - SQLAlchemy ORM
- Models: `User`, `UserToken`, `Email`
- `DatabaseManager` class with helper methods
- Handles OAuth credential storage/retrieval

**backend/databases/vector_database.py** - ChromaDB integration
- Uses LangChain + Ollama embeddings (mxbai-embed-large)
- `embed_and_store()` - Create embeddings and store
- `query_vector_db()` - Semantic similarity search

**backend/services/gmail_read.py** - Gmail API client
- `get_service()` - Create authenticated Gmail service
- `list_message_ids()` - Search Gmail with queries
- `prepare_email_data()` - Fetch email metadata and bodies
- Handles token refresh automatically

**backend/services/setup_calendar.py** - Google Calendar authentication
- `get_calendar_service()` - Get authenticated Calendar service
- Shares OAuth flow with Gmail

**backend/services/moodle_calendar.py** - Moodle calendar integration
- `get_moodle_events_for_api()` - Fetch events from subscribed Moodle calendar
- Returns normalized events merged with primary calendar

### Go Server Architecture (backend/go-server/)

**main.go** - HTTP server and concurrent pipeline
- `operateEmails()` - Handle `/fetch-emails` POST requests
- `fetchWorker()` - Orchestrate 3-stage concurrent pipeline:
  1. Fetch emails from Gmail (10 workers)
  2. Write to SQLite (single writer goroutine)
  3. Batch embed with Ollama (concurrent batches)
- Uses buffered channels for flow control

**chroma.go** - Ollama embedding integration
- `embedMails()` - Batch embed emails (50/batch)
- `getOllamaEmbeddings()` - Call Ollama API (mxbai-embed-large model)
- Returns 1024-dimension float32 vectors

**database.go** - SQLite operations
- `addMailToDB()` - Insert email with duplicate detection
- Returns `nil` for duplicates (skip embedding)
- Uses same schema as Python (gmail_agent.db)

**auth.go** - OAuth credentials and Gmail service
- `getCredentials()` - Fetch from DB
- `createGmailService()` - Create authenticated Gmail client
- Auto-refreshes expired tokens

**date.go** - RFC 2822 date parsing
- `parseEmailDate()` - Parse Gmail date headers
- Supports multiple RFC formats

### Frontend Architecture

**frontend/templates/index.html** - Single-page application
- 2x2 grid layout: Messages, Calendar, (future panels)
- Embedded JavaScript (~1,300 lines)
- Key functions:
  - `loadEmails()` / `syncEmails()` - Email management
  - `renderCalendar()` / `loadCalendarEvents()` - Calendar display
  - `submitEvent()` / `editEvent()` / `deleteEvent()` - Calendar CRUD
  - `onTopSearchSubmit()` - AI search with vector DB
  - `loadUsers()` / `selectUser()` - User management

**frontend/static/styles.css** - Styling
- Event color scheme: Academic (red), Career (teal), Social (yellow), Deadline (purple), Moodle (blue)

## Database Schema

**users table:**
- id (PK), email (unique), name, created_at, updated_at

**user_tokens table:**
- id (PK), user_id (FK, unique), access_token, refresh_token, token_uri, client_id, client_secret, scopes (JSON), expiry, created_at, updated_at

**emails table:**
- id (PK), user_id (FK), message_id, thread_id, subject, sender, recipient, date_sent, snippet, body_text, body_html, created_at
- Unique constraint: (message_id, user_id)

## Key Code Patterns

### Database Session Management
```python
with db_manager.get_session() as session:
    user = session.query(User).filter_by(id=user_id).first()
```

### Google API Service Creation
```python
creds = db_manager.get_user_credentials(user_id)
service = build("gmail", "v1", credentials=creds)
```

### Vector Database Operations
```python
# Store embeddings
await embed_and_store([{
    'message_id': 'abc123',
    'sender': 'user@example.com',
    'subject': 'Subject',
    'date_sent': '2025-01-01',
    'body_text': 'cleaned content'
}])

# Query with semantic search
results = await query_vector_db("meeting tomorrow", top_k=5)
```

### Go Concurrent Email Fetching
```go
// Create channels for pipeline
jobs := make(chan string, len(ids))
emailsToWrite := make(chan map[string]interface{}, len(ids))
newMails := make(chan map[string]interface{}, len(ids))

// Start workers
for w := 1; w <= MaxWorkers; w++ {
    go worker(service, jobs, emailsToWrite)
}

// Start DB writer
go dbWriter(emailsToWrite, newMails, db, userID)

// Start embedding processor
go embeddingProcessor(newMails, &allEmails)
```

## Common Patterns and Conventions

### Error Handling
- FastAPI endpoints return `HTTPException(status_code=4xx/5xx, detail="message")`
- MCP tools return `{"status": "error", "message": "error details"}`
- Go server logs errors and returns JSON with error field

### Date Formats
- Database: SQLite datetime format `YYYY-MM-DD HH:MM:SS.ffffff`
- API responses: ISO 8601 `YYYY-MM-DDTHH:MM:SSZ`
- Calendar events: Date string `YYYY-MM-DD`, Time string `HH:MM AM/PM`
- Gmail API: RFC 2822 format (parsed by `parseEmailDate()`)

### OAuth Token Management
- Tokens stored in `user_tokens` table
- Automatic refresh via `creds.refresh(Request())`
- Re-authentication triggered on refresh failure
- Uses `reauth_user.py` utility for manual re-auth

### Email Processing Pipeline
1. Fetch from Gmail API (IDs → full messages)
2. Clean content (remove HTML, signatures, footers)
3. Store in SQLite (duplicate check by message_id)
4. Generate embeddings (batch of 50 via Ollama)
5. Store embeddings in ChromaDB

## Testing

The test suite uses pytest with custom fixtures:

**conftest.py** - Shared fixtures
- `test_db` - Temporary SQLite database for each test
- `test_user` / `second_user` - Test users
- `user_with_emails` - User with sample emails
- `mock_calendar_service` - Mock Google Calendar API
- `mock_vector_db` - Mock ChromaDB queries
- `mock_llm` - Mock LLM responses

**test_mcp_tools.py** - MCP tool integration tests
- Tests all 12 MCP tools
- Tests resources and prompts
- Uses mocked external services

**test_endpoints_integration.py** - API endpoint tests
- Tests FastAPI endpoints
- Uses TestClient from FastAPI

## Troubleshooting

**Go server not responding:**
- Check if running: `curl http://localhost:8001/fetch-emails` (should return method not allowed)
- Python will fall back to native implementation if Go server unavailable

**OAuth/Token issues:**
- Check token expiry in `user_tokens` table
- Re-authenticate: `python backend/utilities/reauth_user.py <user_id>`
- Verify `credentials.json` has correct redirect URIs

**Embedding/Vector search issues:**
- Verify Ollama is running: `curl http://localhost:11434/api/tags`
- Check model is installed: `ollama list | grep mxbai-embed-large`
- Install if needed: `ollama pull mxbai-embed-large`

**Calendar events not syncing:**
- Verify Calendar API enabled in Google Cloud Console
- Check user has calendar scope in OAuth
- For Moodle: Ensure calendar is subscribed in Google Calendar with name "Moodle"

**Database issues:**
- Check file exists: `ls -la gmail_agent.db`
- Check permissions: `chmod 644 gmail_agent.db`
- Verify schema: `sqlite3 gmail_agent.db ".schema"`

## Project-Specific Notes

### Performance Considerations
- Go server provides 3-5x speedup for email fetching vs Python
- Batch size of 50 emails balances memory and API efficiency
- ChromaDB queries are fast for <10k documents, consider pagination for larger datasets

### Security Notes
- OAuth tokens stored in SQLite (not encrypted at rest)
- `credentials.json` must not be committed to version control
- `.env` file must not be committed to version control

### Future Enhancement Areas
- Email composition and sending (UI exists, backend not implemented)
- Reminders panel (placeholder only)
- Attachment handling
- Email threading/conversations
- Multi-calendar sync beyond Moodle
