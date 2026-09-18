"""
MCP AI tools: llm_response, extract_dates_from_emails, summarize_emails.

Spec section 3.6. Filled by agent M1.

``llm_response`` is re-exported from ``ask_ollama`` — MCP is the sole caller of
``openai`` and of Ollama chat (R5), so the model call stays local to this
service and is never proxied.

Both prompt strings below are **byte-identical** to the monolith's, whitespace
and ``chr(10).join(...)`` included. They encode the year-inference rules the
model relies on; reformatting them would change model output.

``extract_dates_from_emails`` reads its emails from User_data
``/internal/emails`` and, with ``auto_create_events``, calls this service's own
``create_calendar_event`` tool. ``summarize_emails`` calls this service's own
``search_emails`` tool. Both stay in-process, exactly as before.
"""

import json
import logging
from datetime import datetime
from typing import Optional

from backend.services.mcp import clients
from backend.services.mcp.ask_ollama import llm_response
from backend.services.mcp.tools.calendar import create_calendar_event
from backend.services.mcp.tools.emails import search_emails

logger = logging.getLogger(__name__)


async def extract_dates_from_emails(email_account_id: int, limit: int = 20, auto_create_events: bool = False) -> dict:
    """
    Extract deadlines and important dates from recent emails using LLM.
    Optionally create calendar events automatically.

    Args:
        email_account_id: The ID of the email account whose emails to analyze
        limit: Number of recent emails to analyze (default: 20)
        auto_create_events: If True, automatically creates calendar events for found dates (default: False)

    Returns:
        Extracted dates and optionally created event IDs
    """
    try:
        logger.info(f"Extracting dates from {limit} emails for email account {email_account_id}")

        # Get recent emails
        user_data_client = clients.get_user_data_client()
        emails = (
            await user_data_client.get(
                "/internal/emails",
                params={"email_account_id": email_account_id, "limit": limit},
            )
        ).json()

        if not emails:
            return {"status": "success", "extracted_dates": [], "message": "No emails found"}

        # Build email text for LLM
        email_texts = []
        for email in emails:
            # ``date_sent`` arrives as an ISO string over the wire but used to be
            # a datetime interpolated with str(); rebuilding it keeps the prompt
            # bytes identical ("2026-09-15 10:30:00", not "...T10:30:00").
            date_sent = email["date_sent"]
            if date_sent is not None:
                date_sent = datetime.fromisoformat(date_sent)
            email_text = f"Subject: {email['subject']}\nFrom: {email['sender']}\nDate: {date_sent}\n"
            if email["body_text"]:
                email_text += f"Body: {email['body_text'][:500]}\n"
            email_texts.append(email_text)

        # Get current date context
        now = datetime.now()
        current_date = now.strftime("%B %d, %Y")
        current_year = now.year
        is_end_of_year = now.month >= 11

        # Create prompt for LLM with date context
        prompt = f"""TODAY'S DATE: {current_date}

IMPORTANT: When you see dates without years in the emails:
1. If the month hasn't passed yet this year, assume it's {current_year}
2. If the month has already passed this year, assume it's {current_year + 1}
{"3. Since it's near end of year, months like January, February, March likely refer to " + str(current_year + 1) if is_end_of_year else ""}
4. For relative dates like "next week", "this Friday", calculate from today: {current_date}

Extract all dates, deadlines, and time-sensitive information from these emails.
For each date found, provide:
- date: in YYYY-MM-DD format (MUST include the year based on rules above)
- description: what the deadline/event is about
- email_subject: the subject of the email it came from

Format your response as a JSON array like this:
[{{"date": "2025-03-15", "description": "CS101 Assignment due", "email_subject": "Assignment 3"}}]

Only include actual deadlines and important dates. Skip general references to dates.

Emails:
{chr(10).join(email_texts)}

Respond with ONLY the JSON array, no other text.
"""

        # Call LLM
        response = llm_response(prompt)

        # Try to parse JSON response
        try:
            # Clean up response - remove markdown code blocks if present
            clean_response = response.strip()
            if clean_response.startswith("```"):
                clean_response = clean_response.split("```")[1]
                if clean_response.startswith("json"):
                    clean_response = clean_response[4:]
            clean_response = clean_response.strip()

            dates = json.loads(clean_response)

            # Optionally create calendar events
            created_events = []
            if auto_create_events and dates:
                logger.info(f"Auto-creating {len(dates)} calendar events")
                for item in dates:
                    result = await create_calendar_event(
                        title=item.get('description', 'Deadline'),
                        date=item['date'],
                        description=f"From email: {item.get('email_subject', 'Unknown')}",
                        category="Deadline"
                    )
                    if result.get('status') == 'success':
                        created_events.append(result['event_id'])

            return {
                "status": "success",
                "extracted_dates": dates,
                "count": len(dates),
                "created_events": created_events if auto_create_events else None
            }
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            return {
                "status": "error",
                "error": "Failed to parse dates from LLM response",
                "raw_response": response
            }
    except Exception as e:
        logger.error(f"Error extracting dates: {e}")
        return {"status": "error", "error": str(e)}


async def summarize_emails(query: str = "unread", email_account_id: Optional[int] = None, summary_type: str = "brief") -> dict:
    """
    Generate an AI summary of emails matching specific criteria.

    Args:
        query: Filter criteria - Gmail query syntax (default: "unread")
        email_account_id: Optional email account ID to filter emails (if not provided, uses semantic search across all)
        summary_type: Type of summary - "brief", "detailed", or "bullet_points" (default: "brief")

    Returns:
        AI-generated summary of matching emails
    """
    try:
        logger.info(f"Summarizing emails with query: {query}, type: {summary_type}")

        # Search for emails
        search_result = await search_emails(query=query, email_account_id=email_account_id, use_semantic=True, limit=20)

        if search_result.get('status') != 'success' or not search_result.get('results'):
            return {"status": "success", "summary": "No emails found matching the criteria."}

        emails = search_result['results']

        # Get current date context
        now = datetime.now()
        current_date = now.strftime("%B %d, %Y")
        current_year = now.year

        # Build context for LLM
        email_context = []
        for email in emails:
            email_context.append(
                f"From: {email['sender']}\n"
                f"Subject: {email['subject']}\n"
                f"Date: {email['date']}\n"
                f"Content: {email['snippet']}\n"
            )

        # Create prompt based on summary type
        date_context = f"TODAY'S DATE: {current_date}\nWhen mentioning dates or deadlines in your summary, interpret relative dates based on today's date.\n\n"

        if summary_type == "bullet_points":
            prompt = date_context + f"""Summarize these emails as a bullet-point list.
Group by category (work, academic, personal, etc.) if applicable.
If any deadlines are mentioned, include them with full dates (YYYY-MM-DD).

Emails:
{chr(10).join(email_context)}

Provide a concise bullet-point summary:
"""
        elif summary_type == "detailed":
            prompt = date_context + f"""Provide a detailed summary of these emails.
Include key information, action items, and any deadlines mentioned.
For deadlines without years, infer the year based on today's date ({current_date}).

Emails:
{chr(10).join(email_context)}

Detailed summary:
"""
        else:  # brief
            prompt = date_context + f"""Provide a brief summary of these emails in 2-3 sentences.
Focus on the most important information, especially any upcoming deadlines.

Emails:
{chr(10).join(email_context)}

Brief summary:
"""

        # Generate summary
        summary = llm_response(prompt)

        return {
            "status": "success",
            "summary": summary,
            "email_count": len(emails),
            "summary_type": summary_type
        }
    except Exception as e:
        logger.error(f"Error summarizing emails: {e}")
        return {"status": "error", "error": str(e)}


__all__ = ["llm_response", "extract_dates_from_emails", "summarize_emails"]
