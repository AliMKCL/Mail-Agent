"""
FastAPI web application for Gmail agent.
Provides REST API endpoints to serve email data and static files.
"""

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime
import os
from ask_ollama import slm_response
from clean_mails import clean_email
# Local imports
from database import DatabaseManager
from pprint import pprint

# Initialize FastAPI app
app = FastAPI(title="Gmail Agent", description="Web interface for Gmail email management")

# Database setup
db_manager = DatabaseManager()

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
        ids = list_message_ids(service, query=final_query, max_results=50)
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
            
            # Build prompt with cleaned content
            prompt_parts = [
                "Task: Extract all event or deadline dates from the email content. ",
                "Rules:\n",
                "1. Output only in JSON.",
                "2. If no relevant dates, output [].",
                "3. Each JSON object must be: { 'date': 'dd-mm-yyyy', 'description': 'short sentence including what the date is for and any context (location, participants, requirements)' }",
                "4. If multiple dates are present, include all in one JSON array.",
                "5. Convert vague phrases ('tomorrow', 'next week') using reference date 25-09-2025.",
                "6. Do NOT infer dates unless they are stated explicitly or via vague phrases (like tomorrow, next week, etc.)",
                "7. Always use dd-mm-yyyy format for dates.",
                "The email contents are as follows: "
            ]

            # Append each cleaned email body as a compact segment; join later to form the final prompt
            for cleaned_email in cleaned_emails:
                # keep the snippet compact by stripping excessive whitespace
                body_snip = cleaned_email['cleaned_body'].strip()
                prompt_parts.append(f"| {body_snip} ")
                print(f"Mail body: {body_snip}\n\n")

            # Build the final prompt with two-line separators to keep readability while minimizing intermediate concatenations
            prompt = "\n\n".join(prompt_parts)
            
            try:
                response = slm_response(prompt)
                dates_and_events.append(response)
                print("SLM RESPONSE:")
                if (dates_and_events):
                    pprint(dates_and_events)
                print("\n\n")
                
            except Exception as slm_error:
                print(f"ERROR: SLM processing failed: {slm_error}")
                print(f"ERROR: SLM error type: {type(slm_error)}")
                # Continue without SLM results rather than failing the entire sync
                dates_and_events.append("SLM_ERROR")
                print("SLM processing failed, continuing without date extraction")
            

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
