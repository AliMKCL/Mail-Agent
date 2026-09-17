"""
Accounts service FastAPI application (:8010).

Public routes reach it through the Gateway; /internal/* routes must not (R6).
Run with: uv run python -m backend.services.accounts.app
"""

from fastapi import FastAPI

from backend.services.accounts.config import HOST, PORT, SERVICE_NAME
from backend.services.accounts.routers import (
    email_accounts,
    internal,
    oauth,
    registration,
)

app = FastAPI(title="Mail Agent Accounts Service")

app.include_router(registration.router)
app.include_router(oauth.router)
app.include_router(email_accounts.router)
app.include_router(internal.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
