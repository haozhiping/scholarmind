import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from sqlalchemy import text
from common.config import settings
from common.logging import logger

from app.routers.auth import router as auth_router
from app.routers.papers import router as papers_router, folders_router
from app.routers.ingest import router as ingest_router
from app.routers.chat import router as chat_router
from app.routers.advanced import router as advanced_router
from app.routers.observability import router as observability_router

app = FastAPI(
    title="ScholarMind API",
    description="ScholarMind (文渊) - 跨语言学术文献智能调研系统后端 API",
    version="1.0.0"
)

# Set up CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    """Best-effort access logging into MySQL access_logs (never blocks the response on error)."""
    start = time.monotonic()
    response = await call_next(request)
    try:
        path = request.url.path
        if path.startswith("/api") and request.method != "OPTIONS":
            from common.db.mysql import get_db_session
            latency = int((time.monotonic() - start) * 1000)
            client_ip = request.client.host if request.client else None
            async with get_db_session() as db:
                await db.execute(
                    text("""
                        INSERT INTO access_logs (user_id, method, path, status_code, ip, latency_ms)
                        VALUES (NULL, :m, :p, :s, :ip, :lat)
                    """),
                    {"m": request.method, "p": path[:255], "s": response.status_code, "ip": client_ip, "lat": latency},
                )
    except Exception:  # noqa: BLE001
        pass
    return response

# Register routers under /api prefix
app.include_router(auth_router, prefix="/api")
app.include_router(papers_router, prefix="/api")
app.include_router(folders_router, prefix="/api")
app.include_router(ingest_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(advanced_router, prefix="/api")
app.include_router(observability_router, prefix="/api")

@app.on_event("startup")
async def ensure_schema():
    """
    Idempotent self-healing migration for DB volumes initialized before later schema additions
    (e.g. doc_blocks.content_zh). Safe to run on every startup.
    """
    migrations = [
        ("doc_blocks", "content_zh", "ALTER TABLE doc_blocks ADD COLUMN content_zh TEXT NULL AFTER content"),
    ]
    try:
        from common.db.mysql import get_db_session
        async with get_db_session() as db:
            for table, column, ddl in migrations:
                exists = await db.execute(
                    text("""
                        SELECT COUNT(*) FROM information_schema.columns
                        WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c
                    """),
                    {"t": table, "c": column},
                )
                if (exists.scalar() or 0) == 0:
                    await db.execute(text(ddl))
                    logger.info(f"[migrate] added {table}.{column}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[migrate] schema check failed: {e}")


@app.get("/health")
async def health_check():
    logger.info("Health check endpoint hit")
    return {
        "status": "healthy",
        "service": "scholarmind",
        "env": settings.APP_ENV
    }

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.APP_PORT, reload=True)
