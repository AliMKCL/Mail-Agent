"""
MCP Server for Gmail Calendar Agent - Updated for new database schema

Exposes email and calendar functions as MCP tools for LLM integration

Updated for new database schema:
- Account: The logged-in user (account_id from accounts table)
- EmailAccount: A connected Gmail/Outlook account (email_account_id from email_accounts table)
- Email: Email messages linked to EmailAccounts

This is the stdio entrypoint of the MCP service (Spec 3.6 / 6.4). The tool and
resource bodies live in ``tools/`` and ``resources.py``; this module only builds
the ``FastMCP`` app and registers them, so ``http_app.py`` can share the very
same functions in-process.

Run it with: uv run python -m backend.services.mcp.mcp_server
(R9: no ``sys.path`` manipulation — everything runs from the repo root as a
module.)
"""

# Run the testing UI env: npx @modelcontextprotocol/inspector uv run python -m backend.services.mcp.mcp_server

import logging

from fastmcp import FastMCP

from backend.services.mcp.config import settings
from backend.services.mcp.resources import (
    get_account_resource,
    get_accounts_resource,
    get_calendar_event_resource,
    get_calendar_events_resource,
    get_email_account_resource,
    get_email_accounts_resource,
    get_email_resource,
    get_inbox_resource,
    get_system_status_resource,
)
from backend.services.mcp.tools.accounts import (
    get_account_info,
    get_email_account_info,
    list_accounts,
    list_email_accounts,
)
from backend.services.mcp.tools.ai import (
    extract_dates_from_emails,
    summarize_emails,
)
from backend.services.mcp.tools.calendar import (
    create_calendar_event,
    delete_calendar_event,
    get_calendar_events,
    update_calendar_event,
)
from backend.services.mcp.tools.emails import (
    context,
    get_email_account_emails,
    get_email_details,
    search_emails,
    sync_emails,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastMCP server
mcp = FastMCP("Mail Agent")

# Constants - Updated for new schema
CALENDAR_EMAIL_ACCOUNT_ID = settings.CALENDAR_EMAIL_ACCOUNT_ID  # Calendar operations use email_account ID 1 (main email account)

# Global context - only for tracking, not enforcing user restrictions
# Email operations can work across all email accounts (LLM can specify email_account_id in queries)
# Calendar operations always use CALENDAR_EMAIL_ACCOUNT_ID
#
# X9 (preserved): ``context`` is imported above and is module-level here too.
# It is MCP-process-local state, reset on restart, written only by the
# sync_emails tool and read only by system://status. ``tools/emails.py`` owns
# the one dict object — beside its only writer — so this entrypoint, the tool
# and the resource cannot drift apart into separate copies.


# ============================================================================
# MCP TOOLS - Functions the LLM can execute
# ============================================================================

# --- Account Management Tools ---
mcp.tool()(list_accounts)
mcp.tool()(list_email_accounts)
mcp.tool()(get_account_info)
mcp.tool()(get_email_account_info)

# --- Email Tools ---
mcp.tool()(search_emails)
mcp.tool()(sync_emails)
mcp.tool()(get_email_details)
mcp.tool()(get_email_account_emails)

# --- Calendar Tools ---
mcp.tool()(create_calendar_event)
mcp.tool()(update_calendar_event)
mcp.tool()(delete_calendar_event)
mcp.tool()(get_calendar_events)

# --- AI-Enhanced Tools ---
mcp.tool()(extract_dates_from_emails)
mcp.tool()(summarize_emails)


# ============================================================================
# MCP RESOURCES - Read-only data sources
# ============================================================================

mcp.resource("mail://inbox/{email_account_id}")(get_inbox_resource)
mcp.resource("mail://email/{message_id}")(get_email_resource)
mcp.resource("calendar://events")(get_calendar_events_resource)
mcp.resource("calendar://event/{event_id}")(get_calendar_event_resource)
mcp.resource("account://list")(get_accounts_resource)
mcp.resource("mailbox://list")(get_email_accounts_resource)
mcp.resource("account://info/{account_id}")(get_account_resource)
mcp.resource("mailbox://info/{email_account_id}")(get_email_account_resource)
mcp.resource("system://status")(get_system_status_resource)


# ============================================================================
# SERVER ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    logger.info("Starting Gmail Calendar Agent MCP Server (Updated Schema)...")
    mcp.run()
