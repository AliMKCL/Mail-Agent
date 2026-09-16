"""
FastAPI web application for Gmail agent.
Provides REST API endpoints to serve email data and static files.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.controllers import calendar, emails, llm, oauth, registration, users

# Initialize FastAPI app
app = FastAPI(title="Gmail Agent", description="Web interface for Gmail email management")

# Add CORS middleware to allow requests from frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # Frontend HTTP server
        "http://localhost:8080", 
        "http://localhost:8000", 
        "http://127.0.0.1:8000",
        "http://127.0.0.1:3000"   # Frontend HTTP server (alternative)
    ],
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)

# Register domain routers
app.include_router(registration.router)
app.include_router(oauth.router)
app.include_router(users.router)
app.include_router(emails.router)
app.include_router(calendar.router)
app.include_router(llm.router)

# Use absolute paths so StaticFiles works even if the process cwd differs
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(BASE_DIR, "../frontend/static")
templates_dir = os.path.join(BASE_DIR, "../frontend/templates")


if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    # fallback: mount nothing if static directory missing
    pass

if os.path.isdir(templates_dir):
    app.mount("/templates", StaticFiles(directory=templates_dir), name="templates")

# This endpoint is called when the main page is loaded.
@app.get("/", response_class=HTMLResponse)
async def read_root():
    """Serve the landing HTML page using an absolute path file response"""
    # This endpoint serves the landing page.

    frontend_dir = os.path.join(BASE_DIR, "../frontend")
    landing_path = os.path.join(frontend_dir, "landing.html")
    if os.path.exists(landing_path):
        return FileResponse(landing_path)
    return HTMLResponse("<h1>landing.html not found</h1>", status_code=500)


# Run the FastAPI application
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
