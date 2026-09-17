"""
Database service FastAPI application (:8030).

Internal only — never routed through the Gateway (R6).
Run with: uv run python -m backend.services.database.app
"""

from fastapi import FastAPI

from backend.services.database.config import HOST, PORT, SERVICE_NAME
from backend.services.database.routers import (
    accounts,
    email_accounts,
    email_tokens,
    emails,
    stats,
)

app = FastAPI(title="Mail Agent Database Service")

app.include_router(accounts.router)
app.include_router(email_accounts.router)
app.include_router(email_tokens.router)
app.include_router(emails.router)
app.include_router(stats.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
