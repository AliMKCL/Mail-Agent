"""
LLM Integration for MCP Tools - Updated for new database schema

Provides tool manifest and execution logic for connecting LLM to MCP server tools

Updated for new database schema:
- Account: The logged-in user (account_id from accounts table)
- EmailAccount: A connected Gmail/Outlook account (email_account_id from email_accounts table)
- Email: Email messages linked to EmailAccounts
"""

import json
import logging
from typing import List, Dict, Any, Optional
import os
from dotenv import load_dotenv

# Import MCP tools from updated server
from backend.mcp_server import (
    # Account management
    list_accounts,
    list_email_accounts,
    get_account_info,
    get_email_account_info,
    # Email tools
    search_emails,
    sync_emails,
    get_email_details,
    get_email_account_emails,
    # Calendar tools
    create_calendar_event,
    update_calendar_event,
    delete_calendar_event,
    get_calendar_events,
    # AI-enhanced tools
    extract_dates_from_emails,
    summarize_emails,
)

#load_dotenv()
logger = logging.getLogger(__name__)

# Tool registry - maps tool names to actual functions
TOOL_REGISTRY = {
    "list_accounts": list_accounts,
    "list_email_accounts": list_email_accounts,
    "get_account_info": get_account_info,
    "get_email_account_info": get_email_account_info,
    "search_emails": search_emails,
    "sync_emails": sync_emails,
    "get_email_details": get_email_details,
    "get_email_account_emails": get_email_account_emails,
    "create_calendar_event": create_calendar_event,
    "update_calendar_event": update_calendar_event,
    "delete_calendar_event": delete_calendar_event,
    "get_calendar_events": get_calendar_events,
    "extract_dates_from_emails": extract_dates_from_emails,
    "summarize_emails": summarize_emails,
}

# Tool manifest for OpenAI function calling
TOOLS_MANIFEST = [
    {
        "type": "function",
        "function": {
            "name": "list_accounts",
            "description": "List all registered accounts (logged-in users) in the system",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_email_accounts",
            "description": "List all email accounts (Gmail/Outlook accounts) in the system. Can optionally filter by account_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {"type": "integer", "description": "Optional account ID to filter email accounts"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_account_info",
            "description": "Get detailed information about a specific account (logged-in user) by their ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {"type": "integer", "description": "The ID of the account to retrieve"}
                },
                "required": ["account_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_account_info",
            "description": "Get detailed information about a specific email account (Gmail/Outlook) by its ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_account_id": {"type": "integer", "description": "The ID of the email account to retrieve"}
                },
                "required": ["email_account_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_emails",
            "description": "Search emails using Gmail query syntax or semantic search. Use semantic=True for natural language queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (Gmail syntax or natural language)"},
                    "email_account_id": {"type": "integer", "description": "Optional email account ID to filter emails"},
                    "use_semantic": {"type": "boolean", "description": "Use vector database semantic search", "default": False},
                    "limit": {"type": "integer", "description": "Maximum results to return", "default": 10}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sync_emails",
            "description": "Fetch new emails from Gmail for a specific email account and store them",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_account_id": {"type": "integer", "description": "The ID of the email account whose emails to sync"},
                    "max_results": {"type": "integer", "description": "Maximum emails to fetch", "default": 50}
                },
                "required": ["email_account_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_details",
            "description": "Get full details of a specific email by its message ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "The Gmail message ID"}
                },
                "required": ["message_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_account_emails",
            "description": "Get cached emails for a specific email account from the database",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_account_id": {"type": "integer", "description": "The email account ID"},
                    "limit": {"type": "integer", "description": "Maximum emails to return", "default": 50}
                },
                "required": ["email_account_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Create a new calendar event. Always uses the main calendar (email account ID 1). IMPORTANT: Always provide the date in YYYY-MM-DD format with the full year. Use the current date context provided in the system message to infer the correct year.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Event title"},
                    "date": {"type": "string", "description": "Event date in YYYY-MM-DD format. MUST include the full year (e.g., 2025-12-05, not 12-05). Use date context to infer the year if not specified by user."},
                    "time": {"type": "string", "description": "Event time in HH:MM AM/PM format or 'All Day'", "default": "All Day"},
                    "description": {"type": "string", "description": "Event description", "default": ""},
                    "category": {"type": "string", "enum": ["Academic", "Career", "Social", "Deadline"], "description": "Event category"}
                },
                "required": ["title", "date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_calendar_event",
            "description": "Update an existing calendar event. When updating the date, always use YYYY-MM-DD format with the full year.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "Calendar event ID"},
                    "title": {"type": "string", "description": "New title"},
                    "date": {"type": "string", "description": "New date in YYYY-MM-DD format. MUST include the full year (e.g., 2025-12-05)."},
                    "time": {"type": "string", "description": "New time"},
                    "description": {"type": "string", "description": "New description"},
                    "category": {"type": "string", "description": "New category"}
                },
                "required": ["event_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_calendar_event",
            "description": "Delete a calendar event",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "Calendar event ID to delete"}
                },
                "required": ["event_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_calendar_events",
            "description": "Get calendar events for a date range from all calendars (primary + Moodle). If no dates provided, returns current month.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                    "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "extract_dates_from_emails",
            "description": "Extract deadlines and important dates from recent emails using AI. Can optionally auto-create calendar events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_account_id": {"type": "integer", "description": "Email account ID whose emails to analyze"},
                    "limit": {"type": "integer", "description": "Number of recent emails to analyze", "default": 20},
                    "auto_create_events": {"type": "boolean", "description": "Automatically create calendar events", "default": False}
                },
                "required": ["email_account_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_emails",
            "description": "Generate AI summary of emails matching criteria",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Filter criteria", "default": "unread"},
                    "email_account_id": {"type": "integer", "description": "Optional email account ID to filter emails"},
                    "summary_type": {"type": "string", "enum": ["brief", "detailed", "bullet_points"], "description": "Type of summary", "default": "brief"}
                },
                "required": []
            }
        }
    }
]


async def execute_tool(tool_name: str, arguments: Dict[str, Any], context_email_account_id: int = None) -> Dict[str, Any]:
    """
    Execute an MCP tool by name with given arguments

    Args:
        tool_name: Name of the tool to execute
        arguments: Dictionary of arguments to pass to the tool
        context_email_account_id: The email account ID from the query context (for credential access)

    Returns:
        Result from the tool execution
    """
    if tool_name not in TOOL_REGISTRY:
        return {"status": "error", "error": f"Unknown tool: {tool_name}"}

    try:
        tool_obj = TOOL_REGISTRY[tool_name]
        # Extract the actual function from FunctionTool wrapper
        tool_func = tool_obj.fn if hasattr(tool_obj, 'fn') else tool_obj

        # If the tool doesn't have an email_account_id argument but we have a context email_account_id,
        # and it's a calendar tool, inject it
        calendar_tools = ['create_calendar_event', 'update_calendar_event', 'delete_calendar_event', 'get_calendar_events']
        if tool_name in calendar_tools and context_email_account_id is not None and 'email_account_id' not in arguments:
            # For calendar tools, they use a hardcoded CALENDAR_EMAIL_ACCOUNT_ID
            # We'll ensure the database has credentials for this email account
            logger.info(f"Calendar tool {tool_name} will use CALENDAR_EMAIL_ACCOUNT_ID from mcp_server2 (email_account_id={context_email_account_id} provided in context)")

        logger.info(f"Executing tool: {tool_name} with args: {arguments}")
        result = await tool_func(**arguments)
        logger.info(f"Tool {tool_name} completed successfully")
        return result
    except Exception as e:
        logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


async def process_llm_query(query: str, email_account_id: Optional[int] = None, use_openai: bool = True) -> Dict[str, Any]:
    """
    Process a user query through the LLM with access to MCP tools

    Args:
        query: User's natural language query
        email_account_id: Optional email account ID for context
        use_openai: Whether to use OpenAI (True) or Ollama (False)

    Returns:
        Dictionary with answer and actions taken
    """

    if use_openai:
        return await process_with_openai(query, email_account_id)
    else:
        return await process_with_ollama(query, email_account_id)


async def process_with_openai(query: str, email_account_id: Optional[int] = None) -> Dict[str, Any]:
    """Process query using OpenAI with function calling"""
    try:
        from openai import AsyncOpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return {"status": "error", "error": "OpenAI API key not configured"}

        client = AsyncOpenAI(api_key=api_key)

        # Get current date information for context
        from datetime import datetime
        now = datetime.now()
        current_date = now.strftime("%B %d, %Y")  # e.g., "November 28, 2025"
        current_month = now.strftime("%B")
        current_year = now.year
        is_end_of_year = now.month >= 11  # November or December

        # Build conversation messages
        messages = [
            {
                "role": "system",
                "content": f"""You are an AI assistant helping manage emails and calendar events.
You have access to tools for searching emails, creating calendar events, and more.

TODAY'S DATE: {current_date}
Current month: {current_month}
Current year: {current_year}
{"Note: It's near the end of the year. If the user mentions early months (Jan-March) without a year, they likely mean next year (" + str(current_year + 1) + ")." if is_end_of_year else ""}

IMPORTANT DATE HANDLING RULES:
1. When the user mentions a date without a year (e.g., "tomorrow", "next Friday", "December 5"), assume the current year ({current_year})
2. If it's currently {current_month} and they mention a past month (e.g., "January", "February"), assume they mean next year ({current_year + 1})
3. For events like "tomorrow", "next week", calculate from today's date: {current_date}
4. Always use YYYY-MM-DD format when calling calendar tools
5. When inferring dates, be explicit: "I interpreted 'tomorrow' as {(now + __import__('datetime').timedelta(days=1)).strftime('%Y-%m-%d')}"

DATABASE SCHEMA:
- Account: A logged-in user (account_id)
- EmailAccount: A Gmail/Outlook account connected to an Account (email_account_id)
- Email: Email messages belonging to an EmailAccount

{"Current email account ID: " + str(email_account_id) if email_account_id else "No email account context provided."}

When the user asks you to perform actions:
1. Use the appropriate tools
2. Provide clear feedback about what you did
3. Be concise but informative
4. When creating calendar events, always specify the full date in YYYY-MM-DD format
"""
            },
            {"role": "user", "content": query}
        ]

        # Track actions taken
        actions_taken = []

        # Iterative tool calling loop - continue until LLM stops requesting tools
        max_iterations = 5  # Safety limit to prevent infinite loops
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            logger.info(f"LLM iteration {iteration}/{max_iterations}")

            # Call LLM with tools available
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                tools=TOOLS_MANIFEST,
                tool_choice="auto"
            )

            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls

            # Add assistant's response to conversation
            messages.append(response_message)

            # If no tool calls, we're done - LLM has final answer
            if not tool_calls:
                answer = response_message.content
                break

            # Execute each tool call
            for tool_call in tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)

                logger.info(f"LLM requested tool: {function_name} with args: {function_args}")

                # Execute the tool with email account context
                result = await execute_tool(function_name, function_args, context_email_account_id=email_account_id)

                # Add to actions log
                actions_taken.append({
                    "tool": function_name,
                    "arguments": function_args,
                    "result": result
                })

                # Add tool result to conversation
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": function_name,
                    "content": json.dumps(result)
                })

            # Continue loop - LLM will see tool results and decide next action
        else:
            # Hit max iterations
            logger.warning(f"Hit max iterations ({max_iterations}) in tool calling loop")
            answer = "I've completed multiple steps but reached the iteration limit. The actions taken are listed below."

        return {
            "status": "success",
            "answer": answer,
            "actions": actions_taken
        }

    except Exception as e:
        logger.error(f"Error processing with OpenAI: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


async def process_with_ollama(query: str, email_account_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Process query using Ollama (local LLM)
    Note: This is a simplified version without function calling
    """
    from backend.utilities.ask_ollama import slm_response

    try:
        # Build context about available tools
        tools_desc = "\n".join([
            f"- {tool['function']['name']}: {tool['function']['description']}"
            for tool in TOOLS_MANIFEST
        ])

        prompt = f"""You are an AI assistant with access to these tools:
{tools_desc}

User query: {query}

Respond naturally and indicate which tools you would use."""

        answer = slm_response(prompt)

        return {
            "status": "success",
            "answer": answer,
            "actions": [],
            "note": "Ollama mode - tools not auto-executed. Use OpenAI for full functionality."
        }

    except Exception as e:
        logger.error(f"Error processing with Ollama: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}

