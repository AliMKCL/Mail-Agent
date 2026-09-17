"""
Vector DB service FastAPI application (:8040).

Internal only — never routed through the Gateway (R6).
Run with: uv run python -m backend.services.vector_db.app
"""

from fastapi import FastAPI

from backend.services.vector_db.config import HOST, PORT, SERVICE_NAME
from backend.services.vector_db.routers import vectors

app = FastAPI(title="Mail Agent Vector DB Service")

app.include_router(vectors.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
