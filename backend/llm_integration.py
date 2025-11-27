"""
LLM Integration for MCP Tools
Provides tool manifest and execution logic for connecting LLM to MCP server tools
"""

import json
import logging
from typing import List, Dict, Any, Optional
import os
from dotenv import load_dotenv

# Import MCP tools
from backend.mcp_server import (
    # User management
    list_users,
    get_user_info,
    # Email tools
    search_emails,
    sync_emails,
    get_email_details,
    get_user_emails,
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
    "list_users": list_users,
    "get_user_info": get_user_info,
    "search_emails": search_emails,
    "sync_emails": sync_emails,
    "get_email_details": get_email_details,
    "get_user_emails": get_user_emails,
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
            "name": "list_users",
            "description": "List all registered Gmail users in the system",
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
            "name": "get_user_info",
            "description": "Get detailed information about a specific user by their ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer", "description": "The ID of the user to retrieve"}
                },
                "required": ["user_id"]
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
                    "user_id": {"type": "integer", "description": "Optional user ID to filter emails"},
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
            "description": "Fetch new emails from Gmail for a specific user and store them",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer", "description": "The ID of the user whose emails to sync"},
                    "max_results": {"type": "integer", "description": "Maximum emails to fetch", "default": 50}
                },
                "required": ["user_id"]
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
            "name": "get_user_emails",
            "description": "Get cached emails for a specific user from the database",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer", "description": "The user ID"},
                    "limit": {"type": "integer", "description": "Maximum emails to return", "default": 50}
                },
                "required": ["user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Create a new calendar event. Always uses the main calendar (user ID 1).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Event title"},
                    "date": {"type": "string", "description": "Event date in YYYY-MM-DD format"},
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
            "description": "Update an existing calendar event",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "Calendar event ID"},
                    "title": {"type": "string", "description": "New title"},
                    "date": {"type": "string", "description": "New date in YYYY-MM-DD format"},
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
                    "user_id": {"type": "integer", "description": "User ID whose emails to analyze"},
                    "limit": {"type": "integer", "description": "Number of recent emails to analyze", "default": 20},
                    "auto_create_events": {"type": "boolean", "description": "Automatically create calendar events", "default": False}
                },
                "required": ["user_id"]
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
                    "user_id": {"type": "integer", "description": "Optional user ID to filter emails"},
                    "summary_type": {"type": "string", "enum": ["brief", "detailed", "bullet_points"], "description": "Type of summary", "default": "brief"}
                },
                "required": []
            }
        }
    }
]


async def execute_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute an MCP tool by name with given arguments

    Args:
        tool_name: Name of the tool to execute
        arguments: Dictionary of arguments to pass to the tool

    Returns:
        Result from the tool execution
    """
    if tool_name not in TOOL_REGISTRY:
        return {"status": "error", "error": f"Unknown tool: {tool_name}"}

    try:
        tool_obj = TOOL_REGISTRY[tool_name]
        # Extract the actual function from FunctionTool wrapper
        tool_func = tool_obj.fn if hasattr(tool_obj, 'fn') else tool_obj
        
        logger.info(f"Executing tool: {tool_name} with args: {arguments}")
        result = await tool_func(**arguments)
        logger.info(f"Tool {tool_name} completed successfully")
        return result
    except Exception as e:
        logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


async def process_llm_query(query: str, user_id: Optional[int] = None, use_openai: bool = True) -> Dict[str, Any]:
    """
    Process a user query through the LLM with access to MCP tools

    Args:
        query: User's natural language query
        user_id: Optional user ID for context
        use_openai: Whether to use OpenAI (True) or Ollama (False)

    Returns:
        Dictionary with answer and actions taken
    """

    if use_openai:
        return await process_with_openai(query, user_id)
    else:
        return await process_with_ollama(query, user_id)


async def process_with_openai(query: str, user_id: Optional[int] = None) -> Dict[str, Any]:
    """Process query using OpenAI with function calling"""
    try:
        from openai import AsyncOpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return {"status": "error", "error": "OpenAI API key not configured"}

        client = AsyncOpenAI(api_key=api_key)

        # Build conversation messages
        messages = [
            {
                "role": "system",
                "content": f"""You are an AI assistant helping manage emails and calendar events.
You have access to tools for searching emails, creating calendar events, and more.
{"Current user ID: " + str(user_id) if user_id else "No user context provided."}

When the user asks you to perform actions:
1. Use the appropriate tools
2. Provide clear feedback about what you did
3. Be concise but informative
"""
            },
            {"role": "user", "content": query}
        ]

        # First LLM call - decide which tools to use
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=TOOLS_MANIFEST,
            tool_choice="auto"
        )

        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls

        # Track actions taken
        actions_taken = []

        # If LLM wants to use tools, execute them
        if tool_calls:
            messages.append(response_message)

            # Execute each tool call
            for tool_call in tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)

                logger.info(f"LLM requested tool: {function_name} with args: {function_args}")

                # Execute the tool
                result = await execute_tool(function_name, function_args)

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

            # Second LLM call - generate final answer with tool results
            final_response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages
            )

            answer = final_response.choices[0].message.content
        else:
            # No tools needed, just use the response
            answer = response_message.content

        return {
            "status": "success",
            "answer": answer,
            "actions": actions_taken
        }

    except Exception as e:
        logger.error(f"Error processing with OpenAI: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


async def process_with_ollama(query: str, user_id: Optional[int] = None) -> Dict[str, Any]:
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
