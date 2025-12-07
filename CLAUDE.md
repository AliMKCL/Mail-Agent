# Mail Agent - Intelligent Gmail & Calendar Manager

## Project Overview

Mail Agent is an AI-powered personal email assistant that automatically organizes emails, extracts important dates/events, and provides intelligent search across multiple Gmail accounts. The system combines local caching, vector embeddings, and LLM processing to help users manage their inbox and calendar efficiently.

This is a full-stack application with a FastAPI backend, SQLite database, ChromaDB vector store, and a vanilla JavaScript frontend.

---

## Directory Structure

```
Mail_agent/
├── backend/
│   ├── app.py                          # Main FastAPI application with all REST API endpoints
│   ├── mcp_server.py                   # MCP Server exposing all tools, resources, and prompts for LLM 
│   ├── llm_integration.py              # LLM integration layer with tool execution and OpenAI/Ollama support
│   ├── databases/
│   │   ├── database.py                 # SQLAlchemy ORM models (User, Email, UserToken) + DatabaseManager
│   │   └── vector_database.py          # ChromaDB integration for email embeddings and semantic search
│   ├── services/
│   │   ├── gmail_read.py               # Gmail API client with OAuth2 flow and email fetching
│   │   └── setup_calendar.py           # Google Calendar API authentication and service setup
│   ├── utilities/
│   │   ├── reauth_user.py              # Token refresh and re-authentication handler
│   │   ├── ask_ollama.py               # LLM integration (Ollama and OpenAI)
│   │   ├── clean_mails.py              # HTML email cleaning and content extraction
│   │   ├── add_user.py                 # CLI script to add new Gmail accounts
│   │   ├── list_users.py               # CLI script to list all users in database
│   │   └── data_recorder.py            # SLM response recording utility
│   └── data_utils/
│       ├── data_recorder.py            # Data recording helpers
│       └── data.json                   # Sample data storage
├── tests/
│   └── test_mcp_tools.py               # Comprehensive integration tests for all MCP tools
├── frontend/
│   ├── templates/
│   │   └── index.html                  # Main SPA with all HTML, CSS, and JavaScript
│   └── static/
│       └── styles.css                  # Comprehensive CSS styling
├── vector_database/                    # ChromaDB persistence directory
│   └── f4b68de9-5201-4c99-890b-69661c2c926c/
│       └── data_level0.bin             # Vector embeddings storage
├── gmail_agent.db                      # SQLite database
├── credentials.json                    # Google OAuth credentials (from Google Cloud Console)
├── .env                                # Environment variables (OPENAI_API_KEY, OLLAMA_BASE_URL)
├── requirements.txt                    # Python dependencies
└── CLAUDE.md                           # This file
```

---

## How to Run the Application

### Prerequisites
- Python 3.10+
- Virtual environment activated: `.mail_venv`
- Google OAuth credentials (`credentials.json`) from Google Cloud Console
- Optional: Ollama running locally for LLM features (http://localhost:11434)

### Setup & Installation

1. **Create credentials.json:**
   - Go to Google Cloud Console
   - Create a Web Application OAuth 2.0 Client ID
   - Add authorized redirect URIs:
     - `http://localhost:8080/`
     - `http://localhost:8000/oauth/callback`
   - Download the credentials and save as `credentials.json` in project root

2. **Set up environment variables (.env):**
   ```
   OPENAI_API_KEY=your_openai_key_here
   OLLAMA_BASE_URL=http://127.0.0.1:11434
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

### Running the Application

**Start the FastAPI server:**
```bash
cd /Users/alimuratkeceli/Desktop/Projects/Python/Mail_agent
python -m uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000
```

**Access the web interface:**
- Open browser to: `http://localhost:8000`

**Add Gmail accounts (one of these methods):**

Option 1 - Via Web UI:
- Click "+ Add Gmail Account" in the account dropdown
- Enter Gmail address
- Browser opens for OAuth authentication
- User signs in and grants permissions

Option 2 - Via CLI:
```bash
python backend/utilities/add_user.py
```

**Sync emails:**
- Select a user from the dropdown
- Click the Sync button (refresh icon)
- Fetches recent emails from Gmail Primary inbox
- Stores in local database and vector database

---

## Key Backend Files and Their Purposes

### 1. **backend/app.py** (36,969 bytes)
**Main FastAPI Application**
- Serves the web interface and static files
- Contains all REST API endpoints
- Integrates email syncing, calendar operations, and AI search

**Key Endpoints:**
- `GET /` - Serve main HTML page
- `GET /api/emails?user_id=<id>&limit=50` - Fetch user's stored emails
- `GET /api/sync?user_id=<id>` - Trigger Gmail sync (fetches, cleans, embeds new emails)
- `POST /api/users` - Create new user and trigger OAuth
- `GET /api/users` - List all users
- `GET /api/user/<user_id>` - Get specific user info
- `GET /api/calendar/events?user_id=<id>&start_date=<date>&end_date=<date>` - Get calendar events
- `POST /api/calendar/events` - Create calendar event
- `PUT /api/calendar/events/<event_id>` - Update calendar event
- `DELETE /api/calendar/events/<event_id>` - Delete calendar event
- `GET /api/query?query=<text>&top_k=3` - Query vector DB and get AI answer
- `GET /oauth/callback?code=<code>&state=<state>` - Handle OAuth callback

**Email Processing Pipeline in `/api/sync`:**
1. Fetch email IDs from Gmail (Primary inbox, filtered by date)
2. Prepare email data (headers, body_text, body_html)
3. Clean email content (HTML stripping, removing footers/signatures)
4. Embed cleaned emails in ChromaDB vector database
5. Batch emails and send to LLM for date extraction (currently commented out)
6. Return sync status to frontend

### 2. **backend/databases/database.py** (287 lines)
**SQLAlchemy ORM and Database Management**

**Models:**
- `User` - Stores user identity (id, email, name, timestamps)
- `UserToken` - Stores OAuth credentials per user
  - Methods: `to_credentials()`, `from_credentials()`
  - Handles conversion between DB and Google Credentials objects
- `Email` - Caches Gmail messages (message_id, subject, sender, body_text, body_html, etc.)

**DatabaseManager Class:**
- `get_session()` - Returns SQLAlchemy session for database operations
- `get_or_create_user(email, name)` - Upsert user
- `save_user_token(user_id, credentials)` - Store OAuth tokens
- `get_user_credentials(user_id)` - Retrieve credentials from DB
- `save_emails(user_id, email_data)` - Store fetched emails (idempotent by message_id)
- `get_user_emails(user_id, limit)` - Fetch user's cached emails
- `get_latest_email_date(user_id)` - Get most recent email date for incremental sync
- `get_all_users()` - List all users in database

### 3. **backend/databases/vector_database.py** (65 lines)
**ChromaDB Integration for Semantic Search**

Uses LangChain + ChromaDB with Ollama embeddings (mxbai-embed-large model)

**Functions:**
- `embed_and_store(mails)` - Create Document objects, generate embeddings, store in ChromaDB
  - Input: List of email dicts with cleaned body_text
  - Uses message_id as unique identifier
- `query_vector_db(query, top_k)` - Search vector database using semantic similarity
  - Input: Query string, number of results
  - Returns: List of Document objects with metadata

**Directory:** `/Users/alimuratkeceli/Desktop/Projects/Python/Mail_agent/vector_database/`

### 4. **backend/services/gmail_read.py** (295 lines)
**Gmail API Integration**

**Key Functions:**
- `get_service(user_id)` - Create authorized Gmail API client
  - Handles token refresh automatically
  - Falls back to re-authentication if token expired
  - Returns: Gmail API service object
  
- `list_message_ids(service, query, label_ids, max_results)` - Search Gmail
  - Query examples: "in:inbox category:primary", "newer_than:7d"
  - Handles pagination automatically
  - Returns: List of message IDs
  
- `get_message_metadata(service, msg_id, headers)` - Fetch email headers
  - Standard headers: From, To, Subject, Date
  - Returns: Dict with header values and snippet
  
- `get_message_body(service, msg_id, prefer_html)` - Download email body
  - Handles MIME type parsing (text/plain vs text/html)
  - Base64 decodes Gmail's URL-safe encoding
  - Returns: Text string of email body
  
- `parse_email_date(date_str)` - Convert RFC 2822 dates to datetime
  
- `prepare_email_data(service, message_ids)` - Prepare emails for database
  - Calls above functions for each email
  - Returns: List of email dicts with all fields

- `main()` - CLI function to test email fetching for all users

**OAuth Configuration:**
- Port: 8080 (must match Google Cloud Console redirect URI)
- Scopes: gmail.readonly, calendar

### 5. **backend/services/setup_calendar.py** (80+ lines)
**Google Calendar API Setup**

**Functions:**
- `authenticate_calendar(user_id)` - Authenticate user for calendar access
  - Checks DB for stored credentials
  - Refreshes if expired
  - Triggers re-auth if needed
  
- `get_calendar_service(user_id)` - Get authenticated Calendar service
  - Similar flow to gmail_read
  - Returns: (service, error) tuple
  
- `authenticate_google_calendar(user_id)` - Initiate OAuth flow

### 6. **backend/utilities/reauth_user.py** (237 lines)
**Token Refresh and Re-authentication**

**Functions:**
- `reauthenticate_user_token_failure(user_id)` - Handle expired/revoked tokens
  - Opens browser for OAuth consent
  - Runs local server on port 8080 to catch redirect
  - Saves new credentials to database
  - Called automatically when token refresh fails
  
- `force_reauth_for_user(user_id)` - Manually trigger re-auth
  
- `reauth_all_users()` - Re-auth all users in database
  
- `main()` - CLI interface for manual re-authentication

**Usage:**
```bash
python backend/utilities/reauth_user.py              # Re-auth all users
python backend/utilities/reauth_user.py <user_id>    # Re-auth specific user
```

### 7. **backend/utilities/clean_mails.py** (80+ lines)
**Email Content Cleaning and Extraction**

**Key Functions:**
- `html_to_text(html)` - Convert HTML to plain text using BeautifulSoup
  - Removes style, script, noscript tags
  - Preserves text structure with newlines
  
- `has_important_content(text)` - Detect if text contains important info
  - Matches deadlines, dates, event keywords
  - Prevents truncation of important content
  
- `truncate_at_markers(text, markers)` - Remove footers/signatures
  - Detects: footer markers, signature lines, contact info
  - Smart truncation that preserves important content after markers

**Markers Detected:**
- Footer patterns: "unsubscribe", "privacy policy", "careers@..."
- Signature patterns: "--", "Regards,", "Best wishes,"
- Contact patterns: phone numbers, email addresses

### 8. **backend/utilities/ask_ollama.py** (92 lines)
**LLM Integration**

**Functions:**
- `slm_response(query)` - Query local Ollama SLM
  - Model: mistral:latest
  - Base URL: From environment variable (default: http://127.0.0.1:11434)
  - Handles streaming responses
  - Returns: Full response text
  
- `llm_response(query)` - Query OpenAI GPT
  - Model: gpt-5-mini (note: may need updating)
  - Uses OpenAI API key from .env
  - Returns: Response text

### 9. **backend/utilities/add_user.py** (56 lines)
**CLI Script to Add Gmail Users**

Prompts for email/name, creates user in DB, triggers OAuth flow

**Usage:**
```bash
python backend/utilities/add_user.py
```

### 10. **backend/utilities/list_users.py**
CLI script to list all users in database

---

## Key Frontend Files and Their Purposes

### 1. **frontend/templates/index.html** (1,604 lines)
**Main Single-Page Application**

**Structure:**
- Top navigation bar with AI search input
- 2x2 grid layout:
  - Messages Panel (top-left): Email inbox with sync
  - Calendar Panel (top-right): Interactive calendar with CRUD
  - Reminders Panel (bottom-right): Placeholder for future features
- Modals for adding users, creating/editing/deleting events
- Embedded JavaScript (~1,300 lines)

**Key JavaScript Variables:**
- `emails` - Current email list
- `emailsBackup` - Original email list (for search restore)
- `users` - List of available users
- `currentUserId` - Selected user ID
- `currentCalendarDate` - Currently displayed month
- `calendarEvents` - Events by date (format: "YYYY-MM-DD")
- `selectedDateElement` - Currently selected calendar day

**Main JavaScript Functions:**

**User Management:**
- `loadUsers()` - Fetch from /api/users
- `selectUser(userId)` - Set current user, load emails/calendar
- `toggleAccountDropdown()` / `openAccountDropdown()` / `closeAccountDropdown()`
- `showAddUserModal()` / `hideAddUserModal()` / `addGmailAccount()`

**Email Functions:**
- `loadEmails()` - Fetch from /api/emails for current user
- `renderEmails()` - Display email list
- `selectEmail(emailId)` - Show detailed view of email
- `closeEmailDetail()` - Return to inbox
- `syncEmails()` - Trigger /api/sync
- `onTitleSearchSubmit(evt)` - Filter emails by subject
- `clearTitleSearch()` - Restore all emails
- `replyToEmail(emailId)` - Placeholder function
- `forwardEmail(emailId)` - Placeholder function

**Email Detail View:**
- Displays: Subject, From, To, Date, Body
- Handles HTML vs plain text rendering
- Compresses excessive spacing in emails
- Has Reply/Forward buttons (placeholders)

**Calendar Functions:**
- `renderCalendar()` - Generate calendar grid for current month
- `changeMonth(direction)` - Navigate months
- `selectDate(element, dateKey)` - Select a day
- `showEventDetails(dateKey)` - Display events for date
- `hideEventDetails()` - Hide event panel
- `loadCalendarEvents()` - Fetch from /api/calendar/events
- `initializeCalendar()` - Setup on page load

**Event CRUD:**
- `addEvent(dateKey)` - Show add event modal
- `submitEvent()` - POST to /api/calendar/events
- `editEvent(dateKey, eventIndex)` - Populate edit modal
- `submitEditEvent()` - PUT to /api/calendar/events/{id}
- `deleteEvent(dateKey, eventIndex)` - Show delete confirmation
- `confirmDeleteEvent()` - DELETE from /api/calendar/events/{id}

**AI Search (Vector Database):**
- `onTopSearchSubmit(evt)` - Query /api/query endpoint
- `showAIResponseModal(query, answer, sources)` - Display AI answer in modal
- `closeAIResponseModal()` - Hide modal

**Helper Functions:**
- `formatDate(dateString)` - Format date for display (e.g., "Yesterday", "5 days ago")
- `truncateText(text, maxLength)` - Shorten text with ellipsis
- `extractSenderName(sender)` - Parse "Name <email@domain.com>"
- `renderAccountDropdown()` - Build user dropdown menu
- `compressHtmlSpacing(html)` - Remove excessive <br> tags and empty paragraphs

**Modals:**
- Add User Modal: Email, name inputs + OAuth prompt
- Add Event Modal: Title, description, time, category selector
- Edit Event Modal: Same as add, but for editing
- Delete Event Modal: Confirmation dialog
- AI Response Modal: Dynamic modal showing query, answer, and source emails

**Event Categories & Colors:**
```javascript
{
  'Academic': '#ff6b6b',   // Red
  'Career': '#4ecdc4',     // Teal
  'Social': '#f7c23e',     // Yellow
  'Deadline': '#9b59b6'    // Purple
}
```

**Calendar Display:**
- Shows up to 4 event indicator dots per day
- Unique colors per category (max 4 unique categories shown)
- "Today" highlighted with special styling
- Interactive day selection

### 2. **frontend/static/styles.css** (100+ lines)
**CSS Styling**

**Key Styles:**
- Top bar: 60px height, search input for AI
- Container grid: 2 columns, 2 rows (93vh height)
- Panels: White background, border, shadow
- Messages panel: Blue header (#3f8efd)
- Calendar: Interactive grid with event indicators
- Modals: Centered, dark overlay background
- Email detail view: Formatted headers, safe HTML rendering
- Responsive event detail panel in calendar

**Color Scheme:**
- Primary blue: #3f8efd
- Borders: #dfdfdf, #ddd
- Text: #333
- Event colors: As listed above

---

## Database Schema

### SQLite Database: `gmail_agent.db`

**Table: users**
| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY, AUTOINCREMENT |
| email | VARCHAR(255) | UNIQUE, NOT NULL |
| name | VARCHAR(255) | NULLABLE |
| created_at | DATETIME | DEFAULT utcnow |
| updated_at | DATETIME | DEFAULT utcnow |

**Table: user_tokens**
| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY, AUTOINCREMENT |
| user_id | INTEGER | FOREIGN KEY(users.id), UNIQUE |
| access_token | TEXT | NOT NULL |
| refresh_token | TEXT | NULLABLE |
| token_uri | VARCHAR(255) | NULLABLE |
| client_id | VARCHAR(255) | NULLABLE |
| client_secret | VARCHAR(255) | NULLABLE |
| scopes | TEXT | NULLABLE (JSON array) |
| expiry | DATETIME | NULLABLE |
| created_at | DATETIME | DEFAULT utcnow |
| updated_at | DATETIME | DEFAULT utcnow |

**Table: emails**
| Column | Type | Constraints |
|--------|------|-------------|
| id | INTEGER | PRIMARY KEY, AUTOINCREMENT |
| user_id | INTEGER | FOREIGN KEY(users.id) |
| message_id | VARCHAR(255) | NOT NULL |
| thread_id | VARCHAR(255) | NULLABLE |
| subject | TEXT | NULLABLE |
| sender | VARCHAR(500) | NULLABLE |
| recipient | VARCHAR(500) | NULLABLE |
| date_sent | DATETIME | NULLABLE |
| snippet | TEXT | NULLABLE |
| body_text | TEXT | NULLABLE |
| body_html | TEXT | NULLABLE |
| created_at | DATETIME | DEFAULT utcnow |

**Relationships:**
- User --(1:1)-- UserToken
- User --(1:N)-- Email

---

## API Endpoints

All endpoints return JSON responses.

### Users
- `GET /api/users` - List all users
  - Response: Array of {id, email, name, created_at}
  
- `POST /api/users` - Create user and trigger OAuth
  - Request: {email: string, name?: string}
  - Response: User object + success message
  
- `GET /api/user/{user_id}` - Get user details
  - Response: {id, email, name, created_at}

### Emails
- `GET /api/emails?user_id={id}&limit=50` - Get user's stored emails
  - Response: Array of {id, subject, sender, recipient, date_sent, snippet, body_text, body_html, created_at}
  
- `GET /api/sync?user_id={id}` - Sync new emails from Gmail
  - Process: Fetch → Clean → Embed → (Extract dates with LLM)
  - Response: {status, message, total_fetched, new_emails}

### Calendar
- `GET /api/calendar/events?user_id={id}&start_date={date}&end_date={date}` - Get events
  - Date format: ISO 8601 (YYYY-MM-DDTHH:MM:SSZ)
  - Response: {status, events: {date: [{id, title, category, time, description, start, end}]}}
  
- `POST /api/calendar/events` - Create event
  - Request: {user_id, event_data: {title, description, date, time, category}}
  - Response: {status, message, event_id, event_link}
  
- `PUT /api/calendar/events/{event_id}` - Update event
  - Request: {user_id, event_data: {...}}
  - Response: {status, message, event_link}
  
- `DELETE /api/calendar/events/{event_id}?user_id={id}` - Delete event
  - Response: {status, message}

### AI Search
- `GET /api/query?query={text}&top_k=3` - Search emails and get AI answer
  - Response: {status, answer, sources: [{message_id, sender, subject, date_sent}], count}

### OAuth
- `GET /oauth/callback?code={code}&state={state}` - Handle OAuth callback
  - Response: HTML success/error page

### Diagnostics
- `GET /api/calendar/status` - Check calendar service
  - Response: {status, message, user_id, error?}

---

## Important Utilities and Services

### Authentication & OAuth Flow

**Initial Setup:**
1. User clicks "Add Gmail Account"
2. Frontend calls `POST /api/users` with email
3. Backend calls `get_service(user_id)` from `gmail_read.py`
4. `get_service()` checks DB for stored credentials
5. If none: Creates OAuth flow via `InstalledAppFlow`
6. Opens browser to OAuth consent screen
7. User grants permissions
8. Backend receives tokens, saves to DB via `DatabaseManager.save_user_token()`
9. Returns authorized Gmail service object

**Token Refresh:**
1. When token expires during use
2. `get_service()` detects expired token
3. Calls `creds.refresh(Request())` (silent refresh)
4. Saves refreshed token back to DB
5. Returns fresh service object

**Re-authentication on Token Failure:**
1. Token refresh fails (revoked, 6+ months inactive, scope mismatch)
2. `reauthenticate_user_token_failure(user_id)` is called
3. Shows user-friendly message about why re-auth needed
4. Opens browser for new OAuth consent
5. Saves new tokens to DB
6. Returns new credentials

### Email Syncing Process

1. **User clicks Sync** → Calls `/api/sync?user_id={id}`
2. **Fetch Latest Date** - Get most recent email date from DB for incremental sync
3. **Build Query** - Construct Gmail search query:
   - If has emails: "in:inbox category:primary newer_than:{Xh|Xd}"
   - If no emails: "in:inbox category:primary"
4. **List Message IDs** - Call `list_message_ids(service, query, max_results=50)`
   - Handles pagination internally
5. **Prepare Email Data** - Call `prepare_email_data(service, ids)`
   - Fetches headers, plain text, HTML for each email
   - Parses dates to datetime objects
6. **Save to Database** - `db_manager.save_emails(user_id, email_data)`
   - Upsert by message_id (idempotent)
7. **Clean Email Content** - `clean_email(body_text, body_html)`
   - Remove HTML tags, footers, signatures
   - Compress excessive spacing
8. **Embed in Vector DB** - `embed_and_store(cleaned_emails)`
   - Generate embeddings for cleaned content
   - Store with metadata (sender, subject, date, message_id)
9. **Date Extraction** (currently commented out)
   - Batch emails (3000 char limit)
   - Send to LLM: "Extract dates and deadlines from emails"
   - Parse JSON response with dates and events
10. **Return Status** - {status, total_fetched, new_emails}

### AI Search (Vector Database)

1. User types query in top search bar, presses Enter
2. Frontend calls `/api/query?query={text}&top_k=5`
3. **Vector Search:**
   - Embed query using same model as emails (mxbai-embed-large)
   - Find top-k similar emails using cosine similarity
   - Return Document objects with metadata
4. **Generate Answer:**
   - Build context from retrieved emails
   - Create prompt for LLM: "Answer this question using provided email context"
   - Call `slm_response()` or `llm_response()`
   - Format response with sources
5. **Display in Modal:**
   - Show original query
   - Show AI-generated answer
   - List source emails with sender, subject, date

### Calendar Integration

**Google Calendar Storage:**
- Events stored in user's Google Calendar (cloud)
- Event categories stored in `extendedProperties.private.category`
- Queried via `service.events().list()` for date ranges
- CRUD operations via Google Calendar API

**Frontend Calendar:**
- Displays month grid with interactive days
- Shows event indicators (colored dots)
- Click day to see event details
- Modal for creating/editing/deleting events
- Syncs with Google Calendar in real-time

---

## Configuration Files and Environment Setup

### credentials.json
Downloaded from Google Cloud Console
Contains:
- client_id
- client_secret
- auth_uri
- token_uri
- redirect_uris

**Setup:**
1. Go to Google Cloud Console
2. Create project or use existing
3. Enable Gmail API and Google Calendar API
4. Create OAuth 2.0 Client (Web Application type)
5. Add authorized redirect URIs:
   - `http://localhost:8080/` (for InstalledAppFlow)
   - `http://localhost:8000/oauth/callback` (for FastAPI callback)
6. Download credentials as JSON
7. Save in project root as `credentials.json`

### .env
Environment variables:
```
OPENAI_API_KEY=sk-xxx...         # OpenAI API key for GPT queries
OLLAMA_BASE_URL=http://127.0.0.1:11434  # Local Ollama server URL
```

### requirements.txt
```
google-auth-oauthlib==1.2.1
google-auth-httplib2==0.2.0
google-api-python-client==2.143.0
sqlalchemy==2.0.23
beautifulsoup4==4.12.2
lxml==4.9.3
```

Full dependencies (from venv):
- FastAPI, Uvicorn (web framework)
- SQLAlchemy (ORM)
- LangChain, LangChain-Chroma, LangChain-Ollama (vector DB)
- Google client libraries (Gmail, Calendar APIs)
- BeautifulSoup4, lxml (HTML parsing)
- Requests (HTTP)
- OpenAI (GPT integration)
- Python-dotenv (.env support)
- Chromadb (vector database)

---

## Testing Setup

Currently, no formal test suite exists. Manual testing approaches:

**Unit Testing Opportunities:**
- Email cleaning functions (`clean_mails.py`)
- Email date parsing (`gmail_read.py`)
- Database operations (`database.py`)

**Integration Testing Opportunities:**
- OAuth flow and token management
- Email fetching and storing
- Vector database embedding and search
- Calendar event CRUD

**Manual Testing:**
1. Add a user via UI or CLI
2. Click Sync to fetch emails
3. Verify emails appear in inbox
4. Click email to view detail
5. Search by subject
6. Use AI search to query emails
7. Add/edit/delete calendar events
8. Check calendar renders correctly

---

## Dependencies

### Core Framework
- **FastAPI** - Async web framework for REST API
- **Uvicorn** - ASGI server for FastAPI

### Database & ORM
- **SQLAlchemy** - ORM for database operations
- **SQLite3** - Lightweight database (built-in)

### Vector Database & Embeddings
- **LangChain** - LLM framework
- **LangChain-Chroma** - ChromaDB integration
- **LangChain-Ollama** - Ollama model support
- **ChromaDB** - Vector database

### Google APIs
- **google-auth-oauthlib** - OAuth 2.0 flow
- **google-auth-httplib2** - HTTP auth
- **google-api-python-client** - Gmail and Calendar API clients

### LLM Integration
- **Ollama** - Local LLM (not in requirements, run separately)
- **OpenAI** - GPT API

### HTML Parsing
- **BeautifulSoup4** - HTML/XML parsing
- **lxml** - XML/HTML processor

### Utilities
- **Requests** - HTTP client
- **python-dotenv** - .env file support
- **Pydantic** - Data validation

---

## Current Implementation Status

### Completed Features
- Multi-user Gmail OAuth authentication with token refresh
- Email inbox view with sync from Gmail
- Email detail view with HTML/plain text rendering
- Local email caching in SQLite
- Full CRUD for Google Calendar events
- Interactive calendar with event indicators
- Email search by subject
- Vector database integration for semantic search
- AI-powered email search with LLM responses
- Email cleaning pipeline (HTML removal, footer/signature stripping)
- User management (add, list users)

### In Progress / Partially Implemented
- Date extraction from emails (infrastructure ready, LLM call commented out)
- Reply and Forward email buttons (UI exists, no backend implementation)
- Reminders panel (placeholder only)

### Future Enhancements
- Email encryption at rest
- Moodle deadline tracking
- Email threading/conversations
- Attachment handling
- Advanced filtering and labeling
- Smart notifications
- Mobile-responsive design

---

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

### Email Embedding
```python
await embed_and_store([
    {
        'message_id': 'abc123',
        'sender': 'user@example.com',
        'subject': 'Subject',
        'date_sent': '2025-01-01',
        'body_text': 'cleaned content'
    }
])
```

### Vector Search with Context for LLM
```python
results = await query_vector_db(query, top_k=5)
context = "\n---\n".join([
    f"From: {doc.metadata['sender']}\nSubject: {doc.metadata['subject']}\nContent: {doc.page_content}"
    for doc in results
])
response = slm_response(f"[INST]Answer based on context:\n{context}\n[/INST]")
```

### Modal Management in Frontend
```javascript
function showModal() {
    document.getElementById('myModal').style.display = 'block';
}

function hideModal() {
    document.getElementById('myModal').style.display = 'none';
}

window.onclick = function(event) {
    if (event.target == modal) {
        hideModal();
    }
}
```

---

## Common Development Tasks

### Add a New API Endpoint
1. Create route in `app.py` with `@app.get/post/put/delete`
2. Define request/response models with Pydantic
3. Add database query logic using `DatabaseManager`
4. Handle errors with HTTPException
5. Test with curl or Postman
6. Add frontend JavaScript to call endpoint

### Add a New Database Table
1. Define SQLAlchemy model class in `database.py`
2. Create relationships if needed
3. Add helper methods to `DatabaseManager`
4. Create migration or let SQLAlchemy create it
5. Update code that saves/retrieves data

### Debug Email Issues
1. Check `gmail_agent.db` for stored emails
2. Look at console output when syncing
3. Verify credentials.json is valid
4. Check OAuth token expiry in user_tokens table
5. Use `gmail_read.py` main() for testing fetch logic

### Improve Email Cleaning
1. Edit `clean_mails.py` to add/modify patterns
2. Test with sample emails
3. Ensure important content isn't truncated
4. Run sync to reprocess emails

### Optimize Vector Search
1. Experiment with top_k parameter
2. Adjust LLM prompt for better relevance
3. Monitor ChromaDB query times
4. Consider re-indexing all emails

---

## Troubleshooting

**"credentials.json not found"**
- Download from Google Cloud Console
- Save in project root directory
- Verify redirect URIs are correct

**"Token has been expired"**
- Run: `python backend/utilities/reauth_user.py <user_id>`
- Or use web UI: "+ Add Gmail Account"

**"No new emails found"**
- Check Gmail account has emails in Primary inbox
- Verify user email is correct
- Check sync timestamp logic

**Calendar events not syncing**
- Verify user has calendar scope in OAuth
- Check Calendar API is enabled in Google Cloud
- Try re-authenticating user

**AI search returning poor results**
- Check vector database has emails (look in `vector_database/`)
- Verify Ollama is running and accessible
- Try more specific queries
- Increase top_k parameter for more results

**Frontend not connecting to backend**
- Verify FastAPI is running on port 8000
- Check browser console for CORS errors
- Verify API endpoints are accessible (http://localhost:8000/api/users)

---

## Contact & Maintenance

This project is currently being developed and maintained by Alimurat Keceli.

Last updated: November 23, 2025
