"""
FastAPI gateway for Gmail agent.

The single public entry point (:8000). It serves the frontend locally and reverse
proxies every public API route to the service that owns it (Spec 3.1 / 3.7).

The CORS config, the static/template mounts and the ``"/"`` handler below are
lifted verbatim from the monolith's ``backend/app.py`` so that the twelve
hardcoded ``http://localhost:8000`` references in the frontend and the
``http://localhost:8000/oauth/callback`` redirect URI registered with Google
Cloud keep working unchanged.

This is the only process that binds ``0.0.0.0`` (R6); the five backend services
bind ``127.0.0.1``. It holds no business logic, exposes nothing under
``/internal/*`` (R6) and performs no ``limiter.check`` of its own -- rate
limiting stays in the services that own the endpoints and their 429s, headers
included, pass straight through (R8).
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.gateway.proxy import close_clients, register_proxy_routes


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await close_clients()


# Initialize FastAPI app
app = FastAPI(
    title="Gmail Agent",
    description="Web interface for Gmail email management",
    lifespan=lifespan,
)

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

# Register the proxy routes: explicit paths per Spec 3.1, with the Spec 3.7
# per-route timeout table. Never a catch-all, so an unmapped path 404s here
# instead of silently reaching a service.
register_proxy_routes(app)

# Use absolute paths so StaticFiles works even if the process cwd differs
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
static_dir = os.path.join(BASE_DIR, "../../frontend/static")
templates_dir = os.path.join(BASE_DIR, "../../frontend/templates")


if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    # fallback: mount nothing if static directory missing
    pass

if os.path.isdir(templates_dir):
    app.mount("/templates", StaticFiles(directory=templates_dir), name="templates")


@app.get("/health")
async def health():
    """Liveness probe. ``scripts/run_all.sh`` health-gates the gateway on this."""
    return {"status": "ok", "service": "gateway"}


# This endpoint is called when the main page is loaded.
@app.get("/", response_class=HTMLResponse)
async def read_root():
    """Serve the landing HTML page using an absolute path file response"""
    # This endpoint serves the landing page.

    frontend_dir = os.path.join(BASE_DIR, "../../frontend")
    landing_path = os.path.join(frontend_dir, "landing.html")
    if os.path.exists(landing_path):
        return FileResponse(landing_path)
    return HTMLResponse("<h1>landing.html not found</h1>", status_code=500)


# Run the FastAPI application
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
