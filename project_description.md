# Mail Agent - Intelligent Gmail & Calendar Manager

## Project Vision
An AI-powered personal email assistant that automatically organizes emails, extracts important dates/events, and provides intelligent search across multiple Gmail accounts. The system combines local caching, vector embeddings, and LLM processing to help users manage their inbox and calendar efficiently.

## Current Implementation Status

### ✅ Completed Features
- **Multi-user Gmail Integration**
  - OAuth2 authentication flow with token refresh handling
  - Per-user credential storage in SQLite database
  - Primary inbox filtering and syncing
  - Automatic duplicate detection via message IDs
  
- **Email Management UI**
  - List view with sender, subject, date, and snippet
  - Full email detail view with proper HTML/plain-text rendering
  - Automatic spacing compression for cleaner display
  - Search functionality (by subject)
  - Back navigation between list and detail views

- **Interactive Calendar System**
  - Monthly calendar grid with navigation
  - Full CRUD operations (Create, Read, Update, Delete events)
  - Google Calendar API integration for persistence
  - Event categorization (Academic, Career, Social, Personal, Other)
  - Color-coded event indicators (max 4 per day, showing unique categories)
  - Event detail panel with date selection
  - Today highlighting and visual feedback

- **Backend Architecture**
  - FastAPI REST API with endpoints for:
    - `/api/emails` - Fetch user emails
    - `/api/sync` - Trigger Gmail sync
    - `/api/users` - User management
    - `/api/calendar/events` - Calendar CRUD operations
  - SQLAlchemy ORM with SQLite database
  - Database models: User, Email, UserToken, CalendarEvent
  - Email body cleaning pipeline (HTML stripping, content extraction)
  - Vector database integration (ChromaDB) for embeddings

- **Data Processing Pipeline**
  - Email cleaning using BeautifulSoup (removes HTML, extracts text)
  - Batch processing for large email volumes (3000 char limit per batch)
  - Email embedding and storage in ChromaDB vector database
  - Incremental sync (only fetches newer emails since last sync)

- **Utilities & Services**
  - `gmail_read.py` - Gmail API client with OAuth handling
  - `setup_calendar.py` - Google Calendar API integration
  - `clean_mails.py` - HTML email content cleaning
  - `reauth_user.py` - Token refresh and re-authentication
  - `ask_ollama.py` - LLM integration helpers (SLM/LLM response functions)

### 🚧 In Progress / Planned Features
- **AI-Powered Date Extraction**
  - LLM scanning of incoming emails for dates and events
  - Automatic calendar event creation from extracted information
  - Currently: Infrastructure in place, needs activation/testing
    
- **Reply & Forward Functionality**
  - UI buttons exist (placeholders currently)
  - Needs Gmail send API integration

### 📋 Future Enhancements
- **Security & Privacy**
  - Encryption of email bodies and sender information at rest
  - Secure credential storage improvements
  
- **Academic Integration**
  - Moodle deadline tracking and reminder system
  - Integration with university systems
  
- **Advanced Features**
  - Email threading/conversation view
  - Attachment handling and preview
  - Email labeling and filtering
  - Smart notifications
  - Mobile-responsive design improvements

## Tech Stack

### Backend
- **Framework**: FastAPI (async Python web framework)
- **Database**: SQLAlchemy ORM + SQLite (relational data)
- **Vector Store**: ChromaDB (email embeddings for semantic search)
- **AI/LLM**: Ollama integration (local LLM/SLM processing)
- **APIs**: 
  - Google Gmail API (OAuth2, read access)
  - Google Calendar API (read/write access)

### Frontend
- **Core**: Vanilla HTML5, CSS3, JavaScript (ES6+)
- **Architecture**: Single-page application (SPA) pattern
- **API Communication**: Fetch API for REST endpoints
- **UI Components**: Custom calendar grid, email list/detail views, modals

### Data Processing
- **HTML Parsing**: BeautifulSoup4 + lxml
- **Authentication**: google-auth-oauthlib, google-auth-httplib2
- **Date Parsing**: Python datetime + email.utils