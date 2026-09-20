# Graph Report - Mail-Agent  (2026-09-20)

## Corpus Check
- 146 files · ~98,893 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 8 file(s) not represented in the graph (top: (none) 3, .css 2, .ini 1)

## Summary
- 2073 nodes · 3792 edges · 181 communities (126 shown, 55 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 262 edges (avg confidence: 0.92)
- Token cost: 166,434 input · 0 output

## Community Hubs (Navigation)
- MCP Contract Tests
- Vector DB Service
- User_data Service Core
- Endpoint Integration Tests
- Gateway App & Proxy
- User_data Internal Contract Tests
- Database Service Unit Tests
- Accounts Credential Ladder
- Accounts Service App
- Golden Test Capture Mocks
- Database Manager Methods
- MCP Server & Resources
- User_data Contract Tests
- Rate Limiter Go Tests
- MCP Calendar Test Fixtures
- Vector DB Contracts & Routes
- Database Emails Routes
- MCP Gmail Test Fakes
- Sync HTTP Client
- Accounts Email Account Routes
- Database Stats & Models
- Golden Parity Test Harness
- User_data Internal Emails
- Credential Refresh Wording Tests
- Database Accounts Routes
- Accounts Add-User CLI
- Accounts CLI Scripts
- MCP Tools & Clients
- User_data Sync & Cleaning
- Rate Limiter Python Client
- Golden Vector Store Fakes
- Accounts Credential Ladder Tests
- MCP HTTP Contract Tests
- User_data Internal Fixtures
- Go Sync Server Core
- MCP Account Resources
- User_data Calendar Routes
- User_data Sync Fallback Tests
- Database App & Email Tokens
- User_data Gmail Client
- Internal Gmail Search Tests
- User_data Internal Emails Routes
- Database Email Account Routes
- MCP Client Resources
- Rate Limiter Core (Go)
- Token Bucket Algorithm (Go)
- Rate Limiter Docs & Concepts
- User_data Public Emails Routes
- User_data Service Fixtures
- Go Server Auth & DB Helpers
- Data Recorder & Ollama Client
- User_data Calendar Event Routes
- MCP Calendar Regression Tests
- Frontend Main Dashboard JS
- Auth Endpoint Integration Tests
- MCP LLM Integration
- Landing Page Auth JS
- Gateway Test Fixtures
- Accounts Registration Tests
- Accounts Email Account Tests
- MCP Tool Stack Fixtures
- User_data Internal Vector Fakes
- Rate Limiter HTTP Handlers (Go)
- README Data Model & Defects
- Test User Fixtures
- MCP Email Summarization Tests
- User_data Service Vector Fakes
- User_data Calendar Service Fakes
- Rate Limit Header Tests
- Test Suite Fixtures (conftest)
- MCP HTTP App
- Rate Limiter Client Decorators
- run_all.sh Orchestration
- Vector DB Test Mocks
- MCP Error Envelope Tests
- User_data Calendar List Fakes
- Rate Limiter Feature Tests
- Shared Config Settings
- Landing Page Auth Flow
- Rate Limiter Go Models
- Vector DB Embedding Fakes
- Internal Semantic Search Tests
- Moodle Calendar Tests
- User Endpoint Tests
- Email Token Model
- Rate Limiter Logger (Go)
- Rate Limiter Global Scope Tests
- Accounts Credential Write Tests
- Accounts Internal Identity Tests
- Accounts Registration Routes
- Frontend LLM Query UI
- Rate Limiter Main Entry (Go)
- Accounts OAuth Flow Fakes
- User_data Email Lookup Tests
- Endpoint Error Handling Tests
- Server-Total Bucket Tests
- Rate Limiter Integration Tests
- User_data Gmail Search
- README Service Ports
- Calendar Mock Service (conftest)
- Accounts Test Fixtures
- OAuth Web Flow Fake
- Accounts Calendar Auth Tests
- Accounts OAuth Route Tests
- MCP LLM Fixture
- MCP OpenAI Fake
- Golden Calendar Mock
- Gateway Client Factory
- MCP Email Tools
- README Query & Vector Search
- Primary Email Account Tests
- User_data Internal Accounts Stub
- User_data Sync Error Tests
- Static File Endpoint Tests
- Rate Limiter Sync Endpoint Tests
- Rate Limiter Calendar Endpoint Tests
- Rate Limiter Edge Case Tests
- Rate Limiter Account Scope Tests
- Rate Limiter Email Account Scope Tests
- Go Server Gmail Auth
- Accounts User Creation Route
- User_data Calendar Event DTOs
- MCP HTTP Limiter Fixture
- MCP User_data Stub
- MCP LLM Query Keyword Tests
- User_data Error Envelope Tests
- Frontend Calendar Rendering
- MCP OpenAI Fixture (conftest)
- Accounts Offline Flow Fixture
- Accounts Reauth-All Tests
- Accounts Credential Guard Tests
- MCP Tool Registry Tests
- User_data Calendar Service Test
- User_data Accounts Stub
- Golden OpenAI Fake
- Frontend Event Detail JS
- README Calendar Endpoints
- Gateway Client Fixture (conftest)
- Accounts Interactive Flow Fixture
- Accounts Credentials Fixture
- MCP Tool Registry Coroutine Test
- MCP Ollama Mode Test
- User_data OAuth Timeout Test
- Gateway Package Init
- HTTP Client Internals
- Common Libs Init
- Contracts Package Init
- Libs Package Init
- Accounts CLI Init
- Accounts Service Init
- Accounts Routers Init
- Database Service Init
- Database Routers Init
- Services Package Init
- MCP Service Init
- MCP Tools Init
- User_data CLI Init
- User_data Service Init
- User_data Routers Init
- Vector DB Service Init
- Vector DB Routers Init
- Frontend Title Search JS
- Contract Tests Init
- Accounts Unrefreshable Creds Test
- MCP Orchestrator Test
- MCP Query Validation Test
- MCP Shared Bucket Test
- MCP Tool Manifest Test
- MCP Route Inventory Test
- MCP Orchestrator Error Test
- E2E Tests Init
- Unit Tests Init
- Frontend Email Selection JS
- Project Package Metadata
- Go Server Module
- Rate Limiter Go Module
- Mail Agent Project Root
- Single-Writer Ownership Pattern
- Dead Code Defect (X7)

## God Nodes (most connected - your core abstractions)
1. `AsyncServiceClient` - 99 edges
2. `DatabaseManager` - 60 edges
3. `UpstreamError` - 42 edges
4. `TestCredentialLadder` - 28 edges
5. `EmailAccountDTO` - 25 edges
6. `RateLimiterClient` - 25 edges
7. `_make_account()` - 25 edges
8. `get_calendar_service()` - 24 edges
9. `reset_bucket()` - 22 edges
10. `get_user_data_client()` - 21 edges

## Surprising Connections (you probably didn't know these)
- `test_get_account_info_is_key_for_key()` --calls--> `get_account_info()`  [INFERRED]
  tests/contract/test_mcp_tools.py → backend/services/mcp/tools/accounts.py
- `Rate limiter fails open when unavailable` --semantically_similar_to--> `Fail-Open Design`  [INFERRED] [semantically similar]
  README.md → ratelimiter/README.md
- `Feature: Intelligent Search` --semantically_similar_to--> `GET /api/query`  [INFERRED] [semantically similar]
  frontend/landing.html → README.md
- `Feature: Calendar Integration` --semantically_similar_to--> `/api/calendar/events (GET/POST/PUT/DELETE)`  [INFERRED] [semantically similar]
  frontend/landing.html → README.md
- `database_client()` --uses--> `AsyncServiceClient`  [INFERRED]
  tests/contract/test_accounts_service.py → backend/libs/common/http.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Preserved defects X1-X10 (behavior-preserving refactor)** — readme_x1_signup_account_takeover, readme_x2_users_endpoint_leak, readme_x3_message_id_no_unique_constraint, readme_x4_calendar_hardwired_account, readme_x5_thread_id_null, readme_x6_invalid_slm_model_string, readme_x7_duplicate_data_recorder, readme_x8_slm_response_commented_out, readme_x9_last_sync_time_process_local, readme_x10_blocking_calls_in_async_handlers [EXTRACTED 1.00]
- **Six-service single-writer ownership architecture** — readme_gateway_service, readme_accounts_service, readme_user_data_service, readme_database_service, readme_vector_db_service, readme_mcp_service [EXTRACTED 1.00]
- **Rate limiter flexible scoping (scope/identifier/endpoint recipes)** — ratelimiter_readme_scope_types, ratelimiter_usage_guide_per_user_limiting, ratelimiter_usage_guide_global_limiting, ratelimiter_usage_guide_server_wide_limiting, ratelimiter_usage_guide_custom_config [INFERRED 0.85]

## Communities (181 total, 55 thin omitted)

### Community 0 - "MCP Contract Tests"
Cohesion: 0.03
Nodes (74): backend_services_mcp_tools, inspect, sys, Contract tests for the MCP service's 14 tools and 9 resources (agent M1, Spec…, Test creating a calendar event., Test creating an all-day calendar event., Test complete CRUD cycle: Create -> Read -> Update -> Delete This ensures no…, Test updating an existing calendar event. (+66 more)

### Community 1 - "Vector DB Service"
Cohesion: 0.05
Nodes (40): backend_services_vector_db, health(), get, Vector DB service FastAPI application (:8040). Internal only — never routed…, Vector DB service configuration (:8040). Binds loopback (R6). Sole owner of…, backend_services_vector_db_routers, embed_and_store(), query_vector_db() (+32 more)

### Community 2 - "User_data Service Core"
Cohesion: 0.06
Nodes (40): backend_services_user_data, health(), get, User_data service FastAPI application (:8020). Public routes reach it through…, delete_events_on_date(), _fetch_email_accounts(), get_primary_email_account_id(), main() (+32 more)

### Community 3 - "Endpoint Integration Tests"
Cohesion: 0.05
Nodes (26): patch, Test email-related endpoints, Test GET /api/emails requires email_account_id parameter, Test GET /api/emails returns email list structure, Test GET /api/emails respects limit parameter, Test GET /api/sync successfully syncs emails (mocked Gmail API, real HTTP…, Test GET /api/sync requires email_account_id parameter, Test calendar event endpoints (+18 more)

### Community 4 - "Gateway App & Proxy"
Cohesion: 0.07
Nodes (40): health(), lifespan(), FastAPI, get, FastAPI gateway for Gmail agent. The single public entry point (:8000). It…, Liveness probe. ``scripts/run_all.sh`` health-gates the gateway on this., Serve the landing HTML page using an absolute path file response, read_root() (+32 more)

### Community 5 - "User_data Internal Contract Tests"
Cohesion: 0.05
Nodes (22): calendar, Contract tests for the User_data service's ``/internal/*`` surface (agent U2).…, The helper's swallow-everything ``except`` is load-bearing., ``description is not None`` rather than truthiness: ``""`` clears it., With no ``date`` the existing event's date is sliced out of whichever of…, Google's ``timeMax`` is exclusive, so an explicit ``end_date`` gets +1 day —…, No dates supplied -> first of this month 00:00:00 through the last day at…, A Moodle failure is logged and skipped — it never fails the request.… (+14 more)

### Community 6 - "Database Service Unit Tests"
Cohesion: 0.12
Nodes (37): client(), _email_payload(), _make_account(), _make_email_account(), manager(), fixture, Unit tests for the Database service (:8030) — Spec 3.2, step 2.4. The service…, X1: an existing email + any password still yields the existing id. The stored… (+29 more)

### Community 7 - "Accounts Credential Ladder"
Cohesion: 0.09
Nodes (32): credentials_to_dto(), Flatten a Google ``Credentials`` object into a wire DTO. Mirrors…, backend_services_accounts, authentication_failed_error(), The credential ladder — Spec section **3.3.1**, step 4.2. This is the highest-…, Resolve usable credentials for one email account. Returns ``(credentials,…, ``gmail_read.py:89`` verbatim — the *Gmail* row's wording. The trailing period…, ``gmail_read.py:80`` verbatim — the *Gmail* row's failed-refresh wording.… (+24 more)

### Community 8 - "Accounts Service App"
Cohesion: 0.10
Nodes (23): Upstream error transport for service-to-service calls. Rule R7: every public…, Thin HTTP clients for service-to-service calls. ``AsyncServiceClient`` is the…, health(), get, Accounts service FastAPI application (:8010). Public routes reach it through…, Downstream clients for the Accounts service. These are **provider functions**,…, Accounts service configuration (:8010). Binds loopback (R6). Sole credential…, authenticate_google_calendar() (+15 more)

### Community 9 - "Golden Test Capture Mocks"
Cohesion: 0.08
Nodes (26): base64, importlib, re, shutil, subprocess, tempfile, _asgi_client(), build_gmail_service_mock() (+18 more)

### Community 10 - "Database Manager Methods"
Cohesion: 0.09
Nodes (18): DatabaseManager, datetime, Get all accounts from the database, Get all email accounts from the database, Get existing email account or create new one, Get all email accounts for a specific account, Get email account by ID, Get email account's stored OAuth credentials (+10 more)

### Community 11 - "MCP Server & Resources"
Cohesion: 0.09
Nodes (26): MCP service configuration (:8050 + stdio). Binds loopback (R6). Sole caller of…, MCP Server for Gmail Calendar Agent - Updated for new database schema Exposes…, get_accounts_resource(), get_calendar_event_resource(), get_calendar_events_resource(), get_email_account_resource(), get_email_resource(), get_inbox_resource() (+18 more)

### Community 12 - "User_data Contract Tests"
Cohesion: 0.08
Nodes (13): Contract tests for the User_data service (:8020) — Spec 3.4 public half / Phase…, P2 directly: the keyword MCP calls with must exist.…, ``or "No Subject"`` / ``or "Unknown"`` / ``or ""`` are preserved verbatim., A cached email makes the query ``in:inbox category:primary newer_than:Nd``., The auth-URL request carries the ORIGINAL id, not the primary one.…, An Accounts failure falls back to the id the caller supplied., _seed_email(), test_get_calendar_events_auth_required_uses_original_id() (+5 more)

### Community 13 - "Rate Limiter Go Tests"
Cohesion: 0.21
Nodes (26): go_pkg_testing, testing.T, StatsResponse, makeCheckRequest(), resetBucket(), TestBucketPersistence(), TestCustomTokensConsumption(), TestGlobalEndpointRateLimiting_CustomParams() (+18 more)

### Community 14 - "MCP Calendar Test Fixtures"
Cohesion: 0.07
Nodes (18): Calendar, manager(), mock_calendar_service(), no_network(), fixture, Everything a test needs about the faked Google Calendar., A ``DatabaseManager`` on a throwaway database, never the real one. This one…, Nothing in this file may reach Ollama, OpenAI or the Go server. (+10 more)

### Community 15 - "Vector DB Contracts & Routes"
Cohesion: 0.15
Nodes (24): EmbedAndStoreRequest, BaseModel, QueryRequest, QueryResponse, Vector DB service request/response DTOs (Spec 3.5). ``/embed-and-store``…, One retrieved document, matching the current ``/api/query`` shape., Body for ``POST /embed-and-store``: texts plus parallel metadata/ids., Body for ``POST /store``: embeddings already computed upstream. (+16 more)

### Community 16 - "Database Emails Routes"
Cohesion: 0.16
Nodes (24): EmailDTO, An ``emails`` row as returned by the Database service., Email, Email table - stores Gmail/Outlook message data What it stores: - message_id:…, count_emails(), email_by_message_id(), _email_dto(), emails_by_message_ids() (+16 more)

### Community 17 - "MCP Gmail Test Fakes"
Cohesion: 0.10
Nodes (18): _b64(), _Executable, FakeGmailService, _gmail_message(), _md5(), Any, Path, ``sync_emails`` is the only writer of ``context["last_sync_time"]``. (+10 more)

### Community 18 - "Sync HTTP Client"
Cohesion: 0.20
Nodes (10): _check(), _parse_body(), Any, Response, Synchronous HTTP client bound to one downstream service's base URL. This class…, Issue one request and return the response, raising on non-2xx., Parsed JSON when possible, otherwise the raw text., Issue one request and return the response, raising on non-2xx. (+2 more)

### Community 19 - "Accounts Email Account Routes"
Cohesion: 0.10
Nodes (24): EmailAccountDTO, An ``email_accounts`` row., get_email_account_info(), get_users(), get, Get email accounts for a specific account (or all if no account_id provided), Get specific email account information, account_detail() (+16 more)

### Community 20 - "Database Stats & Models"
Cohesion: 0.13
Nodes (20): AccountStatsDTO, EmailStatsDTO, BaseModel, Stats DTOs for ``/stats/accounts`` and ``/stats/emails`` (Spec 3.2) and the…, Response of ``GET /stats/accounts`` and ``GET /internal/stats``., Response of ``GET /stats/emails``., ``DatabaseManager`` — the SQLAlchemy access layer for the Gmail agent (Spec…, Account (+12 more)

### Community 21 - "Golden Parity Test Harness"
Cohesion: 0.13
Nodes (23): TestClient, golden_path(), load_app(), main(), normalize(), perform(), Path, Resolve a ``"module:attribute"`` string into an ASGI app object. (+15 more)

### Community 22 - "User_data Internal Emails"
Cohesion: 0.12
Nodes (22): Any, raise_http_from_upstream(), A downstream service returned a non-2xx response. Attributes: status: the HTTP…, Re-raise an :class:`UpstreamError` as a ``fastapi.HTTPException``. The upstream…, UpstreamError, count_emails(), email_by_message_id(), email_stats() (+14 more)

### Community 23 - "Credential Refresh Wording Tests"
Cohesion: 0.12
Nodes (9): _expired_creds(), ``gmail_read`` prints emoji-free sentences, and stdout is the operator…, ``setup_calendar`` prints the emoji variants of the same events. Companion to…, Both values asserted side by side, so neither can be "tidied up". Same branch,…, ⚠️ Half of the asymmetry: Calendar **does** open a browser here.…, ``gmail_read.py:80`` — the *failed-refresh* wording, not ``:89``'s.…, Calendar's wording differs from Gmail's — both are load-bearing., Expired with **no** refresh token: no refresh is possible.… (+1 more)

### Community 24 - "Database Accounts Routes"
Cohesion: 0.16
Nodes (20): AccountDTO, AccountWithSecretDTO, Account, email-account and OAuth-credential DTOs. Field names and types mirror…, An ``accounts`` row, without its secret., An ``accounts`` row including ``password_hash``. Only ever produced by the…, _account_dto(), _account_with_secret_dto(), AccountGetOrCreateRequest (+12 more)

### Community 25 - "Accounts Add-User CLI"
Cohesion: 0.15
Nodes (18): AsyncBaseTransport, AsyncServiceClient, Async HTTP client bound to one downstream service's base URL. This is the…, CredentialsDTO, dto_to_credentials(), BaseModel, An ``email_tokens`` row expressed as Google OAuth2 credential fields., Rebuild a Google ``Credentials`` object from a wire DTO. Mirrors… (+10 more)

### Community 26 - "Accounts CLI Scripts"
Cohesion: 0.15
Nodes (18): asyncio, _email_account_emails(), main(), Script to list all Gmail email accounts in the database. Ported from…, Replacement for ``db_manager.get_email_account_emails(id, limit=...)``. The…, main(), Command-line re-authentication for email accounts with expired or invalid OAuth…, Command-line interface for re-authenticating email accounts. Usage: python -m… (+10 more)

### Community 27 - "MCP Tools & Clients"
Cohesion: 0.17
Nodes (15): backend_services_mcp, llm_response(), Downstream clients for the MCP service. These are **provider functions**, not…, MCP tools backed by the Accounts service: list_accounts, list_email_accounts,…, extract_dates_from_emails(), MCP AI tools: llm_response, extract_dates_from_emails, summarize_emails. Spec…, Extract deadlines and important dates from recent emails using LLM. Optionally…, create_calendar_event() (+7 more)

### Community 28 - "User_data Sync & Cleaning"
Cohesion: 0.15
Nodes (17): clean_email(), collapse_blank_lines(), has_important_content(), html_to_text(), Check if text contains important information that shouldn't be truncated, truncate_at_markers(), _embed_and_store_request(), _metadata() (+9 more)

### Community 29 - "Rate Limiter Python Client"
Cohesion: 0.16
Nodes (11): Any, RateLimiterClient, Get current status of a rate limit bucket Args: scope: Scope type ("account",…, Reset a rate limit bucket to full capacity (admin operation) Args: scope: Scope…, Check service health and get statistics Returns: Dictionary with health status…, Check rate limit for a specific Account (logged-in user) Args: account_id: The…, Check rate limit for a specific EmailAccount (Gmail/Outlook account) Args:…, Client for interacting with the Rate Limiter microservice Updated to work with… (+3 more)

### Community 30 - "Golden Vector Store Fakes"
Cohesion: 0.11
Nodes (11): SimpleNamespace, build_vector_store_fakes(), GoldenCollection, GoldenEmbeddings, GoldenRawCollection, ``tests/conftest.py::mock_vector_db``'s keyword branching, unchanged., Stands in for ``store.embeddings`` (``OllamaEmbeddings``). No network.…, Stands in for ``collection._collection`` — the Go-sync ``/store`` path. (+3 more)

### Community 31 - "Accounts Credential Ladder Tests"
Cohesion: 0.15
Nodes (7): ``scopes=None`` is stored as SQL NULL and read back as ``[]``., No ``require_scope`` means no scope check, even with empty scopes., All six branches of Spec 4.8, for each of the three parity rows. Every test…, ⚠️ The other half of the asymmetry: Gmail **does** open a browser., ⚠️ **The single most important assertion in this file.**…, TestCredentialLadder, _valid_creds()

### Community 32 - "MCP HTTP Contract Tests"
Cohesion: 0.13
Nodes (9): _document(), Any, Contract tests for the MCP service's HTTP surface (agent M2). Covers…, test_query_builds_sources_and_answer(), test_query_defaults_top_k_to_three(), test_query_llm_failure_falls_back(), test_query_stringifies_a_list_response(), test_query_unwraps_a_dict_response() (+1 more)

### Community 33 - "User_data Internal Fixtures"
Cohesion: 0.11
Nodes (16): calendar_service(), client(), database(), FakeCalendarService, manager(), merged_calendar_service(), no_go_server(), fixture (+8 more)

### Community 34 - "Go Sync Server Core"
Cohesion: 0.16
Nodes (15): embedMails(), getOllamaEmbeddings(), extractBody(), fetchSingleEmail(), fetchWorker(), gmail.Service, gmail.MessagePart, go_pkg_bytes (+7 more)

### Community 35 - "MCP Account Resources"
Cohesion: 0.11
Nodes (18): get_accounts_client(), Client for the Accounts service (:8010)., get_account_resource(), get_email_accounts_resource(), Resource: Get list of all email accounts in the system. URI: mailbox://list…, Resource: Get detailed information about a specific account. URI:…, get_account_info(), get_email_account_info() (+10 more)

### Community 36 - "User_data Calendar Routes"
Cohesion: 0.14
Nodes (18): get_calendar_service(), Get Google Calendar service for the email account, create_calendar_event(), delete_calendar_event(), get_calendar_event(), get_calendar_events(), delete, get (+10 more)

### Community 37 - "User_data Sync Fallback Tests"
Cohesion: 0.12
Nodes (10): _b64(), FakeGmailService, _gmail_message(), Any, A googleapiclient-shaped fake for the handful of calls we make.…, Go server answers 200 -> the mails it returns go to /store as-is., S1 regression test. The Go server is unreachable, so the Python fallback runs:…, test_sync_fallback_embeds_cleaned_bodies() (+2 more)

### Community 38 - "Database App & Email Tokens"
Cohesion: 0.12
Nodes (15): health(), get, Database service FastAPI application (:8030). Internal only — never routed…, get_db_manager(), Provider for the process-wide ``DatabaseManager``. Built on first use rather…, backend_services_database_routers, get_email_token(), get (+7 more)

### Community 39 - "User_data Gmail Client"
Cohesion: 0.16
Nodes (16): get_accounts_sync_client(), Blocking client for the Accounts service, for the two sync service getters., _find_part(), get_message_body(), get_message_metadata(), get_service(), parse_email_date(), prepare_email_data() (+8 more)

### Community 40 - "Internal Gmail Search Tests"
Cohesion: 0.15
Nodes (13): _b64(), FakeGmailService, _gmail_message(), A googleapiclient-shaped fake for the handful of calls we make.…, The MCP sync flavour, end to end. It differs from the public ``/api/sync`` in…, ``new_emails`` is ``len(save_emails(...))``, i.e. the Database service's…, Gmail knows the message but we never synced it, so no row comes back., test_internal_gmail_search_ignores_uncached_ids() (+5 more)

### Community 41 - "User_data Internal Emails Routes"
Cohesion: 0.16
Nodes (14): EmailInputDTO, BaseModel, Email DTOs. Field names and types mirror the ``emails`` table columns exactly.…, One element of the list ``DatabaseManager.save_emails`` consumes.…, _embed_and_store_request(), _filter_by_account(), post, User_data service — /internal/emails/* routes consumed by MCP. Spec section 3.4… (+6 more)

### Community 42 - "Database Email Account Routes"
Cohesion: 0.17
Nodes (15): EmailAccount, EmailAccount table - stores Gmail/Outlook accounts What it stores: - id:…, _email_account_dto(), EmailAccountGetOrCreateRequest, get_email_account(), get_or_create_email_account(), list_email_accounts(), BaseModel (+7 more)

### Community 43 - "MCP Client Resources"
Cohesion: 0.13
Nodes (16): get_user_data_client(), Client for the User_data service (:8020)., get_system_status_resource(), Resource: Get system status and statistics. URI: system://status Returns JSON…, delete_calendar_event(), Delete a calendar event. Always uses the main calendar email account (email…, Update an existing calendar event. Always uses the main calendar email account…, update_calendar_event() (+8 more)

### Community 44 - "Rate Limiter Core (Go)"
Cohesion: 0.20
Nodes (10): sync.Map, time.Duration, Config, DefaultConfig(), GenerateKey(), StatsResponse, NewRateLimiter(), NewRateLimiterWithConfig() (+2 more)

### Community 45 - "Token Bucket Algorithm (Go)"
Cohesion: 0.19
Nodes (6): sync.Mutex, time.Time, minFloat(), NewTokenBucket(), Stats, TokenBucket

### Community 46 - "Rate Limiter Docs & Concepts"
Cohesion: 0.14
Nodes (16): POST /check, Hourly inactive-bucket cleanup, Concurrency model (sync.Map + per-bucket mutex), Fail-Open Design, GET /health, RateLimiterClient (Python client), Flexible scope types (user/global/endpoint/custom), GET /status (+8 more)

### Community 47 - "User_data Public Emails Routes"
Cohesion: 0.17
Nodes (13): get_database_client(), get_limiter(), get_vector_db_client(), Downstream clients for the User_data service. These are **provider functions**,…, Client for the Database service (:8030)., Client for the Vector DB service (:8040)., Rate limiter client. ``limiter.check(...)`` blocks move here verbatim (R8)., get_emails() (+5 more)

### Community 48 - "User_data Service Fixtures"
Cohesion: 0.13
Nodes (13): accounts(), calendar_service(), client(), database(), FakeLimiter, limiter(), manager(), fixture (+5 more)

### Community 49 - "Go Server Auth & DB Helpers"
Cohesion: 0.19
Nodes (11): addMailToDB(), cleanDateString(), parseEmailDate(), go_pkg_context, go_pkg_database_sql, go_pkg_fmt, go_pkg_golang_org_x_oauth2, go_pkg_google_golang_org_api_gmail_v1 (+3 more)

### Community 50 - "Data Recorder & Ollama Client"
Cohesion: 0.15
Nodes (12): Any, data_recorder.py - Simple SLM Response Recorder This script records only the…, Record SLM response to data.json file Args: slm_response: The JSON response…, Dummy function for compatibility with existing app.py calls, record_email_processing(), record_slm_response(), Ollama chat + OpenAI completions for the MCP service (Spec 6.1). Lifted…, dotenv (+4 more)

### Community 51 - "User_data Calendar Event Routes"
Cohesion: 0.15
Nodes (14): CalendarEventData, create_calendar_event(), CreateCalendarEventRequest, delete_calendar_event(), get_primary_email_account_id(), BaseModel, delete, post (+6 more)

### Community 52 - "MCP Calendar Regression Tests"
Cohesion: 0.19
Nodes (7): FakeCalendarService, _EventsResource, _Executable, Regression guard for approved fix **P2**. ``mcp_server.get_calendar_events``…, Spec 3.4's boxed warning, asserted. The internal and public calendar routes…, test_internal_and_public_calendar_shapes_differ(), test_internal_calendar_events_merge_moodle()

### Community 53 - "Frontend Main Dashboard JS"
Cohesion: 0.15
Nodes (11): addGmailAccount(), loadEmails(), loadUsers(), renderAccountDropdown(), selectUser(userId), syncEmails(), GET /api/auth/google, GET /api/emails (+3 more)

### Community 54 - "Auth Endpoint Integration Tests"
Cohesion: 0.15
Nodes (10): pytest, _hash_password(), Integration tests for FastAPI endpoints IMPORTANT: These are REAL integration…, Test authentication endpoints, Test POST /api/auth/signup creates account and email account, Test POST /api/auth/signin with valid credentials, Test POST /api/auth/signin with invalid credentials, Helper function to hash passwords for test accounts. (+2 more)

### Community 55 - "MCP LLM Integration"
Cohesion: 0.24
Nodes (12): slm_response(), execute_tool(), process_llm_query(), process_with_ollama(), process_with_openai(), Any, LLM Integration for MCP Tools - Updated for new database schema Provides tool…, Execute an MCP tool by name with given arguments Args: tool_name: Name of the… (+4 more)

### Community 56 - "Landing Page Auth JS"
Cohesion: 0.15
Nodes (12): authForm, getStartedBtn, googleBtn, mainContainer, microsoftBtn, observer, observerOptions, passwordInput (+4 more)

### Community 57 - "Gateway Test Fixtures"
Cohesion: 0.15
Nodes (11): _asgi_client(), client(), _empty_inbox_gmail_service(), _FailOpenLimiter, _no_go_server(), _overridden(), Install ``overrides`` into ``app.dependency_overrides`` and take exactly those…, ``RateLimiterClient`` stand-in returning the fail-open dict verbatim, so the… (+3 more)

### Community 58 - "Accounts Registration Tests"
Cohesion: 0.19
Nodes (4): _hash(), 🔴 **KNOWN BUG X1, PRESERVED ON PURPOSE — DO NOT "FIX" THIS.**…, The scan returns the ``is_primary`` mailbox, not merely the first., TestRegistration

### Community 59 - "Accounts Email Account Tests"
Cohesion: 0.15
Nodes (3): Expired-with-refresh-token still reports ``false``. This is the point of…, X2, preserved: no ``account_id`` leaks every mailbox., TestEmailAccountRoutes

### Community 60 - "MCP Tool Stack Fixtures"
Cohesion: 0.15
Nodes (8): collection(), FakeCollection, FakeEmbeddings, Stands in for the module-scope Chroma collection in ``store``., Stands in for ``OllamaEmbeddings``. Never hits the network., The wired-up in-process service stack handed to every test., MCP's tool layer wired to the real Accounts, User_data, Database and Vector DB…, Stack

### Community 61 - "User_data Internal Vector Fakes"
Cohesion: 0.15
Nodes (7): collection(), FakeCollection, FakeEmbeddings, FakeRawCollection, Stands in for the module-scope LangChain ``Chroma`` collection., Stands in for ``OllamaEmbeddings``. Never hits the network., Stands in for ``collection._collection`` (the private Chroma handle).

### Community 62 - "Rate Limiter HTTP Handlers (Go)"
Cohesion: 0.47
Nodes (6): go_pkg_strconv, net/http.Request, net/http.ResponseWriter, respondError(), respondJSON(), Server

### Community 63 - "README Data Model & Defects"
Cohesion: 0.20
Nodes (12): Account (DB model), GET /api/sync, ChromaDB 'mails' collection, Email (DB model), EmailAccount (DB model), EmailToken (DB model), Go Email Sync Service (:8001), Go sync server writes straight to SQLite (documented exception) (+4 more)

### Community 64 - "Test User Fixtures"
Cohesion: 0.17
Nodes (11): mock_llm(), fixture, Create a test user (returns email_account for backward compatibility)., Create a second test user (returns email_account for backward compatibility)., Create a user with sample emails., Create second user with different emails., Mock LLM response function., second_user() (+3 more)

### Community 65 - "MCP Email Summarization Tests"
Cohesion: 0.17
Nodes (9): FakeDocument, parametrize, Test basic email summarization (without actual LLM call). Revived from the old…, Three prompt shapes; anything unknown falls through to ``brief``., Duck-typed stand-in for a LangChain ``Document``. ``store.query_vector_db``…, The semantic branch drops the ``documents`` key the route carries.…, test_search_emails_semantic_branch(), test_summarize_emails_basic() (+1 more)

### Community 66 - "User_data Service Vector Fakes"
Cohesion: 0.17
Nodes (7): collection(), FakeCollection, FakeEmbeddings, FakeRawCollection, Stands in for ``collection._collection`` (the private Chroma handle)., Stands in for the module-scope LangChain ``Chroma`` collection., Stands in for ``OllamaEmbeddings``. Never hits the network.

### Community 67 - "User_data Calendar Service Fakes"
Cohesion: 0.23
Nodes (3): _Executable, FakeCalendarService, Google Calendar service fake: records bodies, replays canned results.

### Community 68 - "Rate Limit Header Tests"
Cohesion: 0.21
Nodes (8): Test that rate limit headers are present in responses., Helper function to reset a rate limit bucket using the client., Test rate limiting on GET /api/emails endpoint., Test that requests within the limit are allowed., Test that custom capacity (10) is applied., Test that requests are denied after exhausting the limit., reset_bucket(), TestEmailsEndpoint

### Community 69 - "Test Suite Fixtures (conftest)"
Cohesion: 0.22
Nodes (10): backend_gateway, backend_services_accounts_routers, fastapi_testclient, _hash_password(), Pytest configuration and shared fixtures for the Mail Agent test suite. **The…, Helper function to hash passwords for test accounts., Create a test account with primary email account., Create a second test account with primary email account. (+2 more)

### Community 70 - "MCP HTTP App"
Cohesion: 0.20
Nodes (10): get_limiter(), Rate limiter client. ``limiter.check(...)`` blocks move here verbatim (R8)., health(), llm_query_endpoint(), get, post, query_vector_database(), MCP service HTTP app (:8050). The AI-assisted half of the old monolith: ``GET… (+2 more)

### Community 71 - "Rate Limiter Client Decorators"
Cohesion: 0.18
Nodes (8): create_rate_limit_dependency(), get_identifier_from_request(), rate_limited(), decorator(), Python client library for Rate Limiter microservice - Updated for new database…, Create a FastAPI dependency for rate limiting Usage: limiter =…, Decorator for rate limiting FastAPI endpoints Usage: limiter =…, Helper to extract the correct identifier from a request based on scope Args:…

### Community 72 - "run_all.sh Orchestration"
Cohesion: 0.49
Nodes (10): die(), log(), module_exists(), service_field(), run_all.sh script, start_all(), start_service(), stop_all() (+2 more)

### Community 73 - "Vector DB Test Mocks"
Cohesion: 0.18
Nodes (9): _FakeCollection, mock_vector_db(), mock_query(), ``store.collection`` stand-in: an in-memory LangChain ``Chroma``.…, The two documents ``mock_vector_db`` has always returned. Shared with the fake…, ``mock_vector_db``'s keyword branching, as a plain function., Mock vector database query function., _vector_documents() (+1 more)

### Community 74 - "MCP Error Envelope Tests"
Cohesion: 0.18
Nodes (9): ExplodingClient, All four calendar tools share the same two-key catch shape., A downstream client that always fails, for the error-envelope tests., The catch shape is ``{"status": "error", "error": str(e)}`` — two keys., test_calendar_error_envelope(), test_list_accounts_error_envelope(), test_search_emails_error_envelope(), test_sync_emails_error_envelope() (+1 more)

### Community 75 - "User_data Calendar List Fakes"
Cohesion: 0.18
Nodes (4): _CalendarListResource, _FakeHttp, Any, ``service._http`` — the handle ``save_calendar_credentials_after_use`` reaches…

### Community 76 - "Rate Limiter Feature Tests"
Cohesion: 0.22
Nodes (8): get_bucket_status(), Any, Test various rate limiter features across different endpoints., Test that different endpoints have different rate limit configurations., Test that bucket state persists across multiple requests., Test that global scope rate limiting affects all users., Helper function to get bucket status using the client., TestRateLimiterFeatures

### Community 77 - "Shared Config Settings"
Cohesion: 0.20
Nodes (6): Single environment-driven settings object shared by every Mail Agent service.…, Immutable configuration snapshot, resolved from the environment at import., Settings, Database service configuration (:8030). Internal-only service; binds loopback…, dataclasses, pathlib

### Community 78 - "Landing Page Auth Flow"
Cohesion: 0.20
Nodes (9): authForm (sign in / sign up form), Feature: Calendar Integration, Feature: Privacy First, Feature: Smart Organization, Landing & auth page (landing.html), POST /api/auth/signin, POST /api/auth/signup, /api/calendar/events (GET/POST/PUT/DELETE) (+1 more)

### Community 79 - "Rate Limiter Go Models"
Cohesion: 0.20
Nodes (9): CheckRequest, CheckResponse, ErrorResponse, HealthResponse, StatsResponse, ResetRequest, ResetResponse, StatusRequest (+1 more)

### Community 80 - "Vector DB Embedding Fakes"
Cohesion: 0.20
Nodes (4): _FakeEmbeddings, _FakeRawCollection, ``store.embeddings`` stand-in. Records the query text so the fake collection…, ``collection._collection`` — the private Chroma handle ``store_in_vector_db``…

### Community 81 - "Internal Semantic Search Tests"
Cohesion: 0.20
Nodes (9): FakeDocument, _hit(), Duck-typed stand-in for a LangChain ``Document``. ``store.query_vector_db``…, With no ``email_account_id`` the hits pass through untouched — this is the form…, The account filter uses the Database service's batch ``by-message-ids`` route…, test_internal_semantic_search_filter_drops_everything_uncached(), test_internal_semantic_search_filters_by_email_account(), test_internal_semantic_search_truncates_long_snippets() (+1 more)

### Community 82 - "Moodle Calendar Tests"
Cohesion: 0.20
Nodes (7): MoodleCalendarService, X4: with no id supplied the endpoint falls back to email account 1., Adds the ``calendarList`` surface ``moodle.py`` needs., test_calendar_status_defaults_to_account_one(), fake(), test_moodle_events_grouped_by_date(), test_moodle_events_missing_calendar_is_500()

### Community 83 - "User Endpoint Tests"
Cohesion: 0.20
Nodes (6): Test user management endpoints, Test GET /api/users returns list of email accounts, Test GET /api/users?account_id=X filters by account, Test GET /api/email-account/{email_account_id} returns email account info, Test GET /api/email-account/{email_account_id} returns 404 for non-existent…, TestUserEndpoints

### Community 84 - "Email Token Model"
Cohesion: 0.25
Nodes (6): Save or update email account's OAuth token, EmailToken, Convert stored token data to Google Credentials object, Create EmailToken from Google Credentials object, EmailToken table - stores OAuth2 credentials for email accounts What it stores:…, Base

### Community 85 - "Rate Limiter Logger (Go)"
Cohesion: 0.39
Nodes (3): go_pkg_log, Logger, LogLevel

### Community 86 - "Rate Limiter Global Scope Tests"
Cohesion: 0.22
Nodes (7): DELETE /reset, Integration tests for rate limiter with actual FastAPI endpoints - Updated for…, Test the global scope convenience method., Test the check_global_limit convenience method., TestGlobalScopeConvenienceMethod, Note: reset rate limiter before running tests, time

### Community 87 - "Accounts Credential Write Tests"
Cohesion: 0.22
Nodes (3): Unconditional: stored valid credentials are not consulted., ``authenticate_google_calendar`` returns ``(None, str(e))``., TestInternalCredentialWrites

### Community 89 - "Accounts Registration Routes"
Cohesion: 0.29
Nodes (8): BaseModel, post, Handle user sign-up from landing page Creates new account and email account 🔴…, Handle user sign-in from landing page Validates credentials and returns…, sign_in(), sign_up(), SignInRequest, SignUpRequest

### Community 90 - "Frontend LLM Query UI"
Cohesion: 0.29
Nodes (7): formatActionsHtml(actions), onTopSearchSubmit(evt), POST /api/llm-query, MCP service (:8050 + stdio), MCP tool registry (14 tools, 9 resources), X6: invalid local SLM model string with trailing whitespace, X9: last_sync_time is process-local and stale

### Community 91 - "Rate Limiter Main Entry (Go)"
Cohesion: 0.25
Nodes (7): go_pkg_net_http, go_pkg_os, go_pkg_os_signal, go_pkg_syscall, NewServer(), NewLogger(), main()

### Community 92 - "Accounts OAuth Flow Fakes"
Cohesion: 0.25
Nodes (4): FakeInstalledAppFlow, installed_flow(), Replace ``InstalledAppFlow`` in the one namespace that resolves it., Stands in for ``InstalledAppFlow`` in ``google_oauth``'s namespace.…

### Community 93 - "User_data Email Lookup Tests"
Cohesion: 0.25
Nodes (8): **X3, preserved.** ``emails.message_id`` has no unique constraint, so a message…, _seed_email(), test_internal_email_by_message_id(), test_internal_email_by_message_id_has_no_account_filter(), test_internal_email_count(), test_internal_email_stats(), test_internal_list_emails_honours_limit(), test_internal_list_emails_passes_through()

### Community 94 - "Endpoint Error Handling Tests"
Cohesion: 0.25
Nodes (5): Test error handling across endpoints, Test accessing non-existent endpoint returns 404, Test endpoints handle invalid email_account_id types, Test POST endpoints handle malformed JSON, TestErrorHandling

### Community 95 - "Server-Total Bucket Tests"
Cohesion: 0.25
Nodes (5): Test server-wide rate limiting (shared bucket across endpoints)., Test that /api/query and /api/llm-query share the same bucket., Test that server_total endpoints consume 2 tokens per request., Test that server_total bucket can be exhausted., TestServerTotalBucket

### Community 96 - "Rate Limiter Integration Tests"
Cohesion: 0.25
Nodes (5): Test integration between FastAPI app and rate limiter service., Verify that the rate limiter service is accessible., Verify that the FastAPI application is accessible., Test that if rate limiter is down, requests are still allowed. Note: This test…, TestRateLimiterIntegration

### Community 97 - "User_data Gmail Search"
Cohesion: 0.29
Nodes (7): list_message_ids(), Return a list of message IDs using Gmail's search. Examples of 'query': -…, Any, The non-semantic branch of ``search_emails`` (``mcp_server.py:272-304``). Gmail…, The per-email dict both ``search_emails`` branches return., search_emails_gmail(), _search_result()

### Community 98 - "README Service Ports"
Cohesion: 0.43
Nodes (7): Accounts service (:8010), GET /oauth/callback, Database service (:8030), Gateway service (:8000), Golden test suite (28 captured responses), Interactive OAuth runs inside a web request, User_data service (:8020)

### Community 100 - "Accounts Test Fixtures"
Cohesion: 0.29
Nodes (7): account(), client(), fixture, ASGI client for the Accounts app, wired to the in-process Database app., Replace ``Flow`` in **both** namespaces that bind it. ``google_oauth`` uses it…, One account with one primary mailbox: ``testuser@gmail.com`` / id pair., web_flow()

### Community 102 - "Accounts Calendar Auth Tests"
Cohesion: 0.29
Nodes (3): ``authenticate_calendar`` — the operator bootstrap, not a route. It has a pre-…, The ``os.path.exists`` guard, kept from ``setup_calendar``., TestAuthenticateCalendar

### Community 104 - "MCP LLM Fixture"
Cohesion: 0.29
Nodes (5): client(), FakeLLM, llm(), fixture, Stand-in for ``ask_ollama.llm_response`` (OpenAI ``gpt-5-mini``).

### Community 105 - "MCP OpenAI Fake"
Cohesion: 0.33
Nodes (5): FakeAsyncOpenAI, openai_clients(), factory(), Stand-in for ``openai.AsyncOpenAI``. Never opens a socket. Answers every…, Patch ``openai.AsyncOpenAI``; returns the list of clients constructed.

### Community 107 - "Gateway Client Factory"
Cohesion: 0.33
Nodes (6): Install ``factory`` as the upstream-client provider; return the previous one., set_client_factory(), ClientFactory, _gateway_clients(), AsyncClient, Point the Gateway's three upstreams at in-process apps.…

### Community 108 - "MCP Email Tools"
Cohesion: 0.33
Nodes (6): Generate an AI summary of emails matching specific criteria. Args: query:…, summarize_emails(), Search emails using Gmail query syntax or semantic search across the vector…, search_emails(), No hits short-circuits before the model, with a two-key dict., test_summarize_emails_no_results()

### Community 109 - "README Query & Vector Search"
Cohesion: 0.33
Nodes (6): Feature: Intelligent Search, GET /api/query, ChromaDB (vector_database/ store), Ollama local AI server (:11434), Vector DB service (:8040), X8: /api/query local-SLM path commented out

### Community 110 - "Primary Email Account Tests"
Cohesion: 0.33
Nodes (3): ``get_primary_email_account_id`` moved verbatim — fallbacks included., Fallback 1 — never a 404, by contract., TestPrimaryEmailAccount

### Community 111 - "User_data Internal Accounts Stub"
Cohesion: 0.33
Nodes (3): accounts(), Stub for the Accounts service client. ``/internal/*`` only ever writes to…, StubAccounts

### Community 112 - "User_data Sync Error Tests"
Cohesion: 0.33
Nodes (4): test_sync_other_errors_are_500(), test_sync_reports_expired_token(), explode(), test_sync_reports_missing_credential_fields()

### Community 113 - "Static File Endpoint Tests"
Cohesion: 0.33
Nodes (4): Test static file serving and root endpoint, Test GET / returns HTML page, Test static CSS files are accessible, TestStaticEndpoints

### Community 114 - "Rate Limiter Sync Endpoint Tests"
Cohesion: 0.33
Nodes (4): Test rate limiting on GET /api/sync endpoint., Test that custom capacity (10) is applied to sync endpoint., Test that sync endpoint is rate limited after 10 requests., TestSyncEndpoint

### Community 115 - "Rate Limiter Calendar Endpoint Tests"
Cohesion: 0.33
Nodes (4): Test rate limiting on POST /api/calendar/events endpoint., Test that calendar events endpoint has high capacity (100)., Test that calendar events endpoint allows many requests due to high capacity., TestCalendarEventsEndpoint

### Community 116 - "Rate Limiter Edge Case Tests"
Cohesion: 0.33
Nodes (4): Test edge cases and error handling., Test that missing required parameters return 400, not rate limit error., Test that resetting a bucket works correctly., TestEdgeCases

### Community 117 - "Rate Limiter Account Scope Tests"
Cohesion: 0.33
Nodes (4): Test rate limiting with account scope (new database schema feature)., Test that account scope rate limiting works per account., Test that different accounts have separate rate limit buckets., TestAccountScopeRateLimiting

### Community 118 - "Rate Limiter Email Account Scope Tests"
Cohesion: 0.33
Nodes (4): Test rate limiting with email_account scope (new database schema feature)., Test that email_account scope rate limiting works per email account., Test that different email accounts have separate rate limit buckets., TestEmailAccountScopeRateLimiting

### Community 119 - "Go Server Gmail Auth"
Cohesion: 0.50
Nodes (5): createGmailService(), getCredentials(), gmail.Service, operateEmails(), Credentials

### Community 120 - "Accounts User Creation Route"
Cohesion: 0.40
Nodes (5): create_user_and_auth(), BaseModel, post, Add a new email account to the logged-in user's account, UserCreateRequest

### Community 121 - "User_data Calendar Event DTOs"
Cohesion: 0.40
Nodes (5): CreateEventRequest, BaseModel, ``create_calendar_event(title, date, time, description, category)``., ``update_calendar_event(event_id, title, date, time, description, category)``.…, UpdateEventRequest

### Community 122 - "MCP HTTP Limiter Fixture"
Cohesion: 0.40
Nodes (3): FakeLimiter, limiter(), ``RateLimiterClient`` stand-in. Records calls; allows by default.

### Community 123 - "MCP User_data Stub"
Cohesion: 0.40
Nodes (3): Stub for the User_data client (:8020). ``/api/query`` makes exactly one…, StubUserData, user_data()

### Community 124 - "MCP LLM Query Keyword Tests"
Cohesion: 0.40
Nodes (4): P3: the monolith called ``process_llm_query(query, user_id=...)`` while the…, test_llm_query_passes_email_account_id_keyword(), spy(), test_llm_query_rate_limited()

### Community 125 - "User_data Error Envelope Tests"
Cohesion: 0.40
Nodes (4): Failures keep the MCP tool's ``{"status": "error", ...}`` dict at HTTP 200., test_internal_gmail_search_error_envelope(), test_internal_sync_returns_the_tool_error_envelope(), explode()

### Community 131 - "MCP Tool Registry Tests"
Cohesion: 0.50
Nodes (4): get_tool_function(), Extract the actual function from a FunctionTool wrapper. Carried over from the…, Spec 6.4 acceptance: the stdio entrypoint exposes 14 tools + 9 resources.…, test_stdio_server_registers_14_tools_and_9_resources()

### Community 132 - "User_data Calendar Service Test"
Cohesion: 0.50
Nodes (3): On success it builds the client and PUTs the creds back…, test_get_calendar_service_builds_and_saves_credentials(), put()

### Community 136 - "README Calendar Endpoints"
Cohesion: 0.67
Nodes (3): GET /api/calendar/moodle, GET /api/calendar/status, X4: Calendar access hardwired to email account 1

### Community 137 - "Gateway Client Fixture (conftest)"
Cohesion: 0.67
Nodes (3): _gateway_clients(), AsyncClient, Point the Gateway's three upstreams at in-process apps. ``set_client_factory``…

### Community 139 - "Accounts Credentials Fixture"
Cohesion: 0.67
Nodes (3): _creds(), datetime, A ``Credentials`` object. ``expiry`` must stay naive UTC: ``google.auth``…

## Ambiguous Edges - Review These
- `POST /api/auth/signup` → `authForm (sign in / sign up form)`  [AMBIGUOUS]
  frontend/landing.html · relation: references
- `POST /api/auth/signin` → `authForm (sign in / sign up form)`  [AMBIGUOUS]
  frontend/landing.html · relation: references

## Knowledge Gaps
- **49 isolated node(s):** `main.go`, `messageIDs`, `mainContainer`, `getStartedBtn`, `signInTab` (+44 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 1001 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **55 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `POST /api/auth/signup` and `authForm (sign in / sign up form)`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **What is the exact relationship between `POST /api/auth/signin` and `authForm (sign in / sign up form)`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **Why does `AsyncServiceClient` connect `Accounts Add-User CLI` to `MCP Contract Tests`, `User_data Service Core`, `User_data Internal Contract Tests`, `Accounts Credential Ladder`, `Accounts Service App`, `Golden Test Capture Mocks`, `User_data Contract Tests`, `HTTP Client Internals`, `Sync HTTP Client`, `Accounts Email Account Routes`, `User_data Internal Emails`, `Accounts CLI Scripts`, `MCP Tools & Clients`, `User_data Sync & Cleaning`, `User_data Internal Fixtures`, `MCP Account Resources`, `User_data Calendar Routes`, `User_data Internal Emails Routes`, `MCP Client Resources`, `User_data Public Emails Routes`, `User_data Service Fixtures`, `User_data Calendar Event Routes`, `Gateway Test Fixtures`, `MCP Tool Stack Fixtures`, `Test Suite Fixtures (conftest)`, `MCP HTTP App`, `Accounts Registration Routes`, `User_data Email Lookup Tests`, `User_data Gmail Search`, `Accounts User Creation Route`?**
  _High betweenness centrality (0.142) - this node is a cross-community bridge._
- **Why does `DatabaseManager` connect `Database Manager Methods` to `MCP Contract Tests`, `User_data Internal Fixtures`, `Test Suite Fixtures (conftest)`, `Database App & Email Tokens`, `Accounts Credential Ladder`, `User_data Internal Contract Tests`, `Golden Test Capture Mocks`, `Database Email Account Routes`, `Database Service Unit Tests`, `User_data Contract Tests`, `MCP Calendar Test Fixtures`, `Database Emails Routes`, `User_data Service Fixtures`, `Database Stats & Models`, `Email Token Model`, `Database Accounts Routes`, `MCP Tool Stack Fixtures`?**
  _High betweenness centrality (0.103) - this node is a cross-community bridge._
- **Why does `Go Rate Limiter Service (:8002)` connect `Rate Limiter Docs & Concepts` to `Frontend LLM Query UI`, `README Service Ports`, `Rate Limiter Global Scope Tests`, `README Data Model & Defects`?**
  _High betweenness centrality (0.041) - this node is a cross-community bridge._
- **Are the 57 inferred relationships involving `AsyncServiceClient` (e.g. with `_find_email_account()` and `_reauth()`) actually correct?**
  _`AsyncServiceClient` has 57 INFERRED edges - model-reasoned connections that need verification._
- **Are the 30 inferred relationships involving `DatabaseManager` (e.g. with `Account` and `Email`) actually correct?**
  _`DatabaseManager` has 30 INFERRED edges - model-reasoned connections that need verification._