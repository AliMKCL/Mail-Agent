"""
FastAPI web application for Gmail agent.
Provides REST API endpoints to serve email data and static files.
"""

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import os
import pickle
import json
from ask_ollama import slm_response
from clean_mails import clean_email
from data_utils.data_recorder import record_slm_response
# Local imports
from database import DatabaseManager
from pprint import pprint
from setup_calendar import get_calendar_service, authenticate_google_calendar

# Google Calendar imports
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# Initialize FastAPI app
app = FastAPI(title="Gmail Agent", description="Web interface for Gmail email management")

# Database setup
db_manager = DatabaseManager()

# Google Calendar configuration
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/calendar'
]

USER_ID_FOR_CALENDAR = 1


# Pydantic models
class UserCreateRequest(BaseModel):
    email: str
    name: Optional[str] = None

# Use absolute paths so StaticFiles works even if the process cwd differs
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(BASE_DIR, "static")
templates_dir = os.path.join(BASE_DIR, "templates")


if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    # fallback: mount nothing if static directory missing
    pass

if os.path.isdir(templates_dir):
    app.mount("/templates", StaticFiles(directory=templates_dir), name="templates")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    """Serve the main HTML page using an absolute path file response"""

    index_path = os.path.join(templates_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h1>index.html not found</h1>", status_code=500)

@app.get("/api/emails")
async def get_emails(user_id: Optional[int] = None, limit: int = 50) -> List[Dict]:
    """
    Get emails for a specific user from database
    Returns email data formatted for frontend display
    """
    try:
        if user_id is None:
            raise HTTPException(status_code=400, detail="user_id parameter is required")
        
        # Fetch stored emails for the specified user
        stored_emails = db_manager.get_user_emails(user_id, limit=limit)
        
        # Format emails for API response
        emails = []
        for email in stored_emails:
            emails.append({
                "id": email.message_id,
                "subject": email.subject or "No Subject",
                "sender": email.sender or "Unknown",
                "recipient": email.recipient or "Unknown", 
                "date_sent": email.date_sent.isoformat() if email.date_sent else None,
                "snippet": email.snippet or "",
                "body_text": email.body_text or "",
                "body_html": email.body_html or "",
                "created_at": email.created_at.isoformat()
            })
        
        return emails
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching emails: {str(e)}")

@app.get("/api/sync")
async def sync_emails(user_id: Optional[int] = None):
    """
    Trigger email synchronization from Gmail
    This endpoint can be called to fetch new emails
    """
    try:
        from gmail_read import get_service, list_message_ids, prepare_email_data
        from datetime import datetime, timedelta
        
        if user_id is None:
            raise HTTPException(status_code=400, detail="user_id parameter is required")
        
        # Create Gmail service (this handles OAuth flow if needed)
        service = get_service(user_id)
        
        # Get the date of the most recent email we have stored
        latest_date = db_manager.get_latest_email_date(user_id)
        
        # Build Gmail search query to fetch only newer emails
        query = ""
        if latest_date:
            # Calculate days since the latest email for newer_than syntax
            days_since = (datetime.now() - latest_date).days
            if days_since == 0:
                # If it's the same day, use hours
                hours_since = (datetime.now() - latest_date).total_seconds() / 3600
                if hours_since < 1:
                    query = "newer_than:1h"  # Search last hour if very recent
                else:
                    query = f"newer_than:{int(hours_since)}h"
            else:
                query = f"newer_than:{days_since}d"
            
            print(f"DEBUG: Latest email date: {latest_date}")
            print(f"DEBUG: Days since latest: {days_since}")
            print(f"DEBUG: Gmail query: '{query}'")
        else:
            print("DEBUG: No previous emails found, fetching recent emails")
        
        # Build query to fetch Primary inbox emails (matches Gmail dashboard)
        primary_query = "in:inbox category:primary"
        if query:
            # Combine time-based query with primary inbox filter
            final_query = f"{primary_query} {query}"
        else:
            final_query = primary_query
        
        # Fetch email IDs from Gmail Primary inbox only
        print(f"DEBUG: About to call list_message_ids with query='{final_query}'")
        ids = list_message_ids(service, query=final_query, max_results=20)
        print(f"DEBUG: Found {len(ids)} email IDs")
        
        if ids:
            # Prepare and save email data
            email_data = prepare_email_data(service, ids)   # Dict of full email data
            saved_emails = db_manager.save_emails(user_id, email_data)

            dates_and_events = []
            print("Email data to be sent to SLM (cleaning emails first):")
            
            # Clean email content to reduce token count
            cleaned_emails = []
            for num, email in enumerate(email_data):
                try:
                    # Clean the email content using the clean_email function
                    cleaned_content = clean_email(
                        body_text=email.get('body_text', ''),
                        body_html=email.get('body_html')
                    )
                    cleaned_emails.append({
                        'index': num,
                        'subject': email.get('subject', ''),
                        'cleaned_body': cleaned_content
                    })
                    
                    # Show before/after size comparison
                    original_size = len(email.get('body_text', ''))
                    cleaned_size = len(cleaned_content)
                    reduction_pct = ((original_size - cleaned_size) / original_size * 100) if original_size > 0 else 0
                    print(f"Mail {num}: {original_size} -> {cleaned_size} chars ({reduction_pct:.1f}% reduction)")
                    
                except Exception as e:
                    print(f"Error cleaning email {num}: {e}")
                    # Fallback to original body_text if cleaning fails
                    cleaned_emails.append({
                        'index': num,
                        'subject': email.get('subject', ''),
                        'cleaned_body': email.get('body_text', '')
                    })
            
            # Process emails in batches based on character count (3000 char limit per batch)
            batch_char_limit = 3000
            base_prompt_parts = [
                "[INST]Task: You are a date extractor. Do not summarize or interpret. Extract only explicit or relative dates and their associated events from the text. ",
                "Rules:\n",
                "1. Output must be a strict JSON array only. No extra text.",
                "2. Each item has: 'date': formatted as dd-MM-yyyy, 'description': short phrase of the event or context tied to the date",
                "3. If a year is not given, assume the nearest year for that month",
                "4. If no dates exist, output an empty array []",
                "5. Include an event and date only if the date is in the form of a deadline, not a random or past event"
                "6. Never explain. Never add extra fields, never add extra data to fields.",
                "The email contents are as follows: [/INST]"
            ]
            
            # Group emails into batches based on character count
            batches = []
            current_batch = []
            current_batch_chars = 0
            
            for cleaned_email in cleaned_emails:
                body_snip = cleaned_email['cleaned_body'].strip()
                email_char_count = len(body_snip)
                
                print(f"Mail {cleaned_email['index']}: {email_char_count} chars after cleaning")
                
                # If single email exceeds limit, process it alone
                if email_char_count > batch_char_limit:
                    # First, add current batch if it has content
                    if current_batch:
                        batches.append(current_batch)
                        current_batch = []
                        current_batch_chars = 0
                    
                    # Add the large email as its own batch
                    batches.append([cleaned_email])
                    print(f"  -> Processing large email alone ({email_char_count} chars)")
                    
                # If adding this email would exceed limit, start new batch
                elif current_batch_chars + email_char_count > batch_char_limit:
                    batches.append(current_batch)
                    current_batch = [cleaned_email]
                    current_batch_chars = email_char_count
                    print(f"  -> Starting new batch (current: {email_char_count} chars)")
                    
                # Add to current batch
                else:
                    current_batch.append(cleaned_email)
                    current_batch_chars += email_char_count
                    print(f"  -> Added to current batch (total: {current_batch_chars} chars)")
            
            # Add final batch if it has content
            if current_batch:
                batches.append(current_batch)
            
            print(f"\nProcessing {len(cleaned_emails)} emails in {len(batches)} batches:")
            
            # Process each batch separately
            for batch_idx, batch in enumerate(batches):
                batch_chars = sum(len(email['cleaned_body'].strip()) for email in batch)
                print(f"Batch {batch_idx + 1}/{len(batches)}: {len(batch)} emails, {batch_chars} chars")
                
                # Build prompt for this batch
                prompt_parts = base_prompt_parts.copy()
                
                for cleaned_email in batch:
                    body_snip = cleaned_email['cleaned_body'].strip()
                    prompt_parts.append(f"| {body_snip} |")
                    print(body_snip)
                
                prompt = "\n\n".join(prompt_parts)
                
                try:
                    print(f"Sending batch {batch_idx + 1} to SLM (prompt length: {len(prompt)} chars)")
                    response = slm_response(prompt)
                    dates_and_events.append(response)
                    
                    # Record SLM response to data.json
                    record_slm_response(response)
                    
                    print(f"Batch {batch_idx + 1} SLM RESPONSE:")
                    pprint(response)
                    print()
                    
                except Exception as slm_error:
                    print(f"ERROR: Batch {batch_idx + 1} SLM processing failed: {slm_error}")
                    dates_and_events.append(f"BATCH_{batch_idx + 1}_ERROR: {slm_error}")
                    print("Continuing with next batch...")
                
            
            print("All batches processed.")
            print("FINAL SLM RESPONSES:")
            if dates_and_events:
                pprint(dates_and_events)
            print("\n\n")
            

            return {
                "status": "success",
                "message": f"Synced {len(saved_emails)} new emails",
                "total_fetched": len(ids),
                "new_emails": len(saved_emails)
            }
        else:
            return {
                "status": "success", 
                "message": "No new emails found",
                "total_fetched": 0,
                "new_emails": 0
            }
            
    except Exception as e:
        error_msg = str(e)
        if "credentials do not contain the necessary fields" in error_msg:
            return {
                "status": "error",
                "message": "OAuth credentials need to be refreshed. Please run the gmail_read.py script first to authenticate.",
                "error": "authentication_required"
            }
        elif "invalid_grant" in error_msg or "Token has been expired" in error_msg:
            return {
                "status": "error", 
                "message": "OAuth token has expired. Please re-authenticate by running gmail_read.py.",
                "error": "token_expired"
            }
        else:
            raise HTTPException(status_code=500, detail=f"Error syncing emails: {error_msg}")

@app.get("/api/users")
async def get_users():
    """Get all users from the database"""
    try:
        users = db_manager.get_all_users()
        return [
            {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "created_at": user.created_at.isoformat()
            }
            for user in users
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching users: {str(e)}")

@app.get("/api/user/{user_id}")
async def get_user_info(user_id: int):
    """Get specific user information"""
    try:
        from database import User
        with db_manager.get_session() as session:
            user = session.query(User).filter_by(id=user_id).first()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            
            return {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "created_at": user.created_at.isoformat()
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching user info: {str(e)}")

@app.post("/api/users")
async def create_user_and_auth(user_data: UserCreateRequest):
    """Create a new user and initiate OAuth flow"""
    try:
        from gmail_read import get_service
        
        email = user_data.email
        name = user_data.name
        
        if not email:
            raise HTTPException(status_code=400, detail="Email is required")
        
        # Create or get user
        user = db_manager.get_or_create_user(email, name)
        
        # Trigger OAuth flow for this user
        service = get_service(user.id)
        
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "created_at": user.created_at.isoformat(),
            "message": "User created and OAuth completed successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating user: {str(e)}")

@app.get("/api/calendar/events")
async def get_calendar_events(user_id: Optional[int] = None, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """
    Get Google Calendar events for a specific user within a date range
    """
    try:
        if user_id is None:
            raise HTTPException(status_code=400, detail="user_id parameter is required")
        
        # Get calendar service
        service, error = get_calendar_service(USER_ID_FOR_CALENDAR)
        if not service:
            if "Authentication required" in str(error):
                auth_url, state = authenticate_google_calendar(user_id)
                if auth_url:
                    return {
                        "status": "auth_required",
                        "auth_url": auth_url,
                        "message": "Please authenticate with Google Calendar"
                    }
            raise HTTPException(status_code=500, detail=f"Failed to get calendar service: {error}")
        
        # Set default date range if not provided (current month)
        if not start_date:
            start_date = datetime.now().replace(day=1).isoformat() + 'Z'
        if not end_date:
            # Get last day of current month
            next_month = datetime.now().replace(day=28) + timedelta(days=4)
            end_date = (next_month - timedelta(days=next_month.day)).isoformat() + 'Z'
        
        # Fetch events from Google Calendar
        events_result = service.events().list(
            calendarId='primary',
            timeMin=start_date,
            timeMax=end_date,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        # Format events for frontend
        formatted_events = {}
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            if start:
                # Parse date
                if 'T' in start:
                    event_date = datetime.fromisoformat(start.replace('Z', '+00:00'))
                else:
                    event_date = datetime.fromisoformat(start)
                date_key = event_date.strftime('%Y-%m-%d');
                
                if date_key not in formatted_events:
                    formatted_events[date_key] = []
                
                # Determine category based on event summary
                category = "Social"  # Default
                summary = event.get('summary', '').lower()
                if any(word in summary for word in ['meeting', 'work', 'job', 'interview']):
                    category = "Career"
                elif any(word in summary for word in ['class', 'assignment', 'exam', 'study']):
                    category = "Academic"
                elif any(word in summary for word in ['deadline', 'due', 'submit']):
                    category = "Deadline"
                
                # Format time
                if 'dateTime' in event['start']:
                    time_str = event_date.strftime('%I:%M %p')
                else:
                    time_str = 'All Day'
                
                formatted_events[date_key].append({
                    "id": event['id'],
                    "title": event.get('summary', 'No Title'),
                    "category": category,
                    "time": time_str,
                    "description": event.get('description', ''),
                    "start": start,
                    "end": event['end'].get('dateTime', event['end'].get('date'))
                })
        
        return {
            "status": "success",
            "events": formatted_events,
            "message": "Calendar events retrieved successfully"
        }
        
    except HttpError as e:
        raise HTTPException(status_code=500, detail=f"Google Calendar API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching calendar events: {str(e)}")

@app.post("/api/calendar/events")
async def create_calendar_event(request_data: dict):
    """
    Create a new calendar event
    """
    try:
        user_id = request_data.get('user_id')
        event_data = request_data.get('event_data', {})
        
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")
        
        # Get calendar service
        service, error = get_calendar_service(USER_ID_FOR_CALENDAR)
        if not service:
            raise HTTPException(status_code=500, detail=f"Failed to get calendar service: {error}")
        
        # Parse event data
        title = event_data.get('title', 'New Event')
        description = event_data.get('description', '')
        date = event_data.get('date')  # Format: YYYY-MM-DD
        time = event_data.get('time', '')  # Format: HH:MM AM/PM
        
        if not date:
            raise HTTPException(status_code=400, detail="Event date is required")
        
        # Create event object
        if time and time != 'All Day':
            # Parse time and create datetime
            try:
                time_obj = datetime.strptime(time, '%I:%M %p').time()
                start_datetime = datetime.combine(datetime.fromisoformat(date).date(), time_obj)
                end_datetime = start_datetime + timedelta(hours=1)  # Default 1 hour duration
                
                event = {
                    'summary': title,
                    'description': description,
                    'start': {
                        'dateTime': start_datetime.isoformat(),
                        'timeZone': 'UTC',
                    },
                    'end': {
                        'dateTime': end_datetime.isoformat(),
                        'timeZone': 'UTC',
                    },
                }
            except ValueError:
                # If time parsing fails, create all-day event
                event = {
                    'summary': title,
                    'description': description,
                    'start': {'date': date},
                    'end': {'date': date},
                }
        else:
            # All-day event
            event = {
                'summary': title,
                'description': description,
                'start': {'date': date},
                'end': {'date': date},
            }
        
        # Create event in Google Calendar
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        
        return {
            "status": "success",
            "message": "Event created successfully",
            "event_id": created_event['id'],
            "event_link": created_event.get('htmlLink', '')
        }
        
    except HttpError as e:
        raise HTTPException(status_code=500, detail=f"Google Calendar API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating calendar event: {str(e)}")

@app.put("/api/calendar/events/{event_id}")
async def update_calendar_event(event_id: str, request_data: dict):
    """
    Update an existing calendar event
    """
    try:
        user_id = request_data.get('user_id')
        event_data = request_data.get('event_data', {})
        
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")
        
        # Get calendar service
        service, error = get_calendar_service(USER_ID_FOR_CALENDAR)
        if not service:
            raise HTTPException(status_code=500, detail=f"Failed to get calendar service: {error}")
        
        # Get existing event
        event = service.events().get(calendarId='primary', eventId=event_id).execute()
        
        # Update event fields
        if 'title' in event_data:
            event['summary'] = event_data['title']
        if 'description' in event_data:
            event['description'] = event_data['description']
        
        # Handle time updates
        if 'time' in event_data and 'date' in event_data:
            date = event_data['date']
            time = event_data['time']
            
            if time and time != 'All Day':
                try:
                    time_obj = datetime.strptime(time, '%I:%M %p').time()
                    start_datetime = datetime.combine(datetime.fromisoformat(date).date(), time_obj)
                    end_datetime = start_datetime + timedelta(hours=1)
                    
                    event['start'] = {
                        'dateTime': start_datetime.isoformat(),
                        'timeZone': 'UTC',
                    }
                    event['end'] = {
                        'dateTime': end_datetime.isoformat(),
                        'timeZone': 'UTC',
                    }
                except ValueError:
                    event['start'] = {'date': date}
                    event['end'] = {'date': date}
            else:
                event['start'] = {'date': date}
                event['end'] = {'date': date}
        
        # Update event in Google Calendar
        updated_event = service.events().update(calendarId='primary', eventId=event_id, body=event).execute()
        
        return {
            "status": "success",
            "message": "Event updated successfully",
            "event_link": updated_event.get('htmlLink', '')
        }
        
    except HttpError as e:
        if e.resp.status == 404:
            raise HTTPException(status_code=404, detail="Event not found")
        raise HTTPException(status_code=500, detail=f"Google Calendar API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating calendar event: {str(e)}")

@app.delete("/api/calendar/events/{event_id}")
async def delete_calendar_event(event_id: str, user_id: int):
    """
    Delete a calendar event
    """
    try:
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")
        
        # Get calendar service
        service, error = get_calendar_service(USER_ID_FOR_CALENDAR)
        if not service:
            raise HTTPException(status_code=500, detail=f"Failed to get calendar service: {error}")
        
        # Delete event from Google Calendar
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        
        return {
            "status": "success",
            "message": "Event deleted successfully"
        }
        
    except HttpError as e:
        if e.resp.status == 404:
            raise HTTPException(status_code=404, detail="Event not found")
        raise HTTPException(status_code=500, detail=f"Google Calendar API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting calendar event: {str(e)}")

@app.get("/oauth/callback")
async def oauth_callback(code: str, state: Optional[str] = None):
    """Handle OAuth callback for Google Calendar"""
    try:
        flow = Flow.from_client_secrets_file('credentials.json', SCOPES)
        flow.redirect_uri = 'http://localhost:8000/oauth/callback'
        
        # Exchange authorization code for credentials
        flow.fetch_token(code=code)
        creds = flow.credentials
        
        # Save credentials
        user_id = state if state and state != 'default' else 'default'
        token_file = f'token_{user_id}.pickle'
        with open(token_file, 'wb') as token:
            pickle.dump(creds, token)
        
        return HTMLResponse("""
        <html>
            <head><title>Authorization Complete</title></head>
            <body>
                <h2>Authorization successful!</h2>
                <p>You can now close this window and return to the application.</p>
                <script>
                    setTimeout(function() {
                        window.close();
                    }, 3000);
                </script>
            </body>
        </html>
        """)
        
    except Exception as e:
        return HTMLResponse(f"""
        <html>
            <head><title>Authorization Error</title></head>
            <body>
                <h2>Authorization failed</h2>
                <p>Error: {str(e)}</p>
                <p>Please try again.</p>
            </body>
        </html>
        """, status_code=500)

# Run the FastAPI application
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)