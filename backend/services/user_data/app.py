"""
User_data service FastAPI application (:8020).

Public routes reach it through the Gateway; /internal/* routes must not (R6).
Run with: uv run python -m backend.services.user_data.app
"""

from fastapi import FastAPI

from backend.services.user_data.config import HOST, PORT, SERVICE_NAME
from backend.services.user_data.routers import (
    calendar,
    emails,
    internal_calendar,
    internal_emails,
)

app = FastAPI(title="Mail Agent User_data Service")

app.include_router(emails.router)
app.include_router(calendar.router)
app.include_router(internal_emails.router)
app.include_router(internal_calendar.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
