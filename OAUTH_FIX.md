# OAuth URI Mismatch Fix

## Problem
Getting "Error 400: uri_mismatch" when trying to authenticate Gmail account via the dropdown menu.

## Root Cause
The redirect URI `http://localhost:8000/oauth/callback` used in the code is not registered in Google Cloud Console's OAuth client configuration.

## OAuth Flow Trace

### Current Wiring:
1. **Frontend** (`localhost:3000/templates/main.html` line 427)
   ```javascript
   const response = await fetch(`${API_BASE_URL}/api/auth/google?email_account_id=${userId}`);
   ```

2. **Backend API** (`backend/app.py` line 812-828)
   ```python
   @app.get("/api/auth/google")
   async def initiate_google_oauth(email_account_id: int):
       auth_url, state = authenticate_google_calendar(email_account_id)
       return {"auth_url": auth_url, "state": state}
   ```

3. **OAuth URL Generation** (`backend/services/setup_calendar.py` line 123-140)
   ```python
   flow = Flow.from_client_secrets_file('credentials.json', SCOPES)
   flow.redirect_uri = 'http://localhost:8000/oauth/callback'  # ← This must match Google Console
   auth_url, state = flow.authorization_url(...)
   ```

4. **User Redirected to Google**
   - Google checks if redirect_uri is in authorized list
   - ❌ If not found → Error 400: uri_mismatch

5. **OAuth Callback** (`backend/app.py` line 831-881)
   ```python
   @app.get("/oauth/callback")
   async def oauth_callback(code: str, state: Optional[str] = None):
       flow.redirect_uri = 'http://localhost:8000/oauth/callback'
       flow.fetch_token(code=code)
       # Save credentials and redirect to main.html
   ```

## Solution

### Step 1: Update Google Cloud Console
1. Go to https://console.cloud.google.com/
2. Select project: **mail-agent-472711**
3. Navigate to: **APIs & Services** → **Credentials**
4. Click on your OAuth 2.0 Client ID (the one with client_id: `629510714278-...`)
5. Under **Authorized redirect URIs**, ensure these are added:
   - `http://localhost:8080/` (for legacy gmail_read.py script)
   - `http://localhost:8000/oauth/callback` ← **REQUIRED for web app**
6. Click **Save**

### Step 2: Verify Configuration
After updating Google Cloud Console, test the OAuth flow:
1. Go to `http://localhost:3000/landing.html`
2. Sign in
3. Click on an account in the dropdown that needs authentication
4. You should be redirected to Google's consent screen
5. After approval, you'll be redirected back to `http://localhost:8000/oauth/callback`
6. Backend will save credentials and redirect to `http://localhost:3000/templates/main.html`

## Port Summary
- **Port 3000**: Frontend (Python HTTP server)
- **Port 8000**: Backend FastAPI server
- **Port 8001**: Go email fetching service
- **Port 8002**: Go rate limiter service
- **Port 8080**: Legacy OAuth redirect (for gmail_read.py script)
- **Port 11434**: Ollama LLM service

## Redirect URI Requirements
For the web application to work, Google Cloud Console must have:
- `http://localhost:8000/oauth/callback` (for web app OAuth flow)

Optional (for standalone scripts):
- `http://localhost:8080/` (for gmail_read.py and other standalone scripts)

## Files Involved
- `credentials.json` - OAuth client configuration (web application type)
- `backend/services/setup_calendar.py` - OAuth flow initiation
- `backend/app.py` - OAuth callback handler
- `frontend/templates/main.html` - Frontend OAuth trigger
