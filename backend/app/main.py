from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from common.config import settings
from common.logging import logger
from common.exceptions import AppException, exception_handler, generic_exception_handler
from common.db.mysql_client import mysql

from app.routers.auth import router as auth_router
from app.routers.papers import router as papers_router, folders_router
from app.routers.ingest import router as ingest_router
from app.routers.chat import router as chat_router
from app.routers.advanced import router as advanced_router
from app.routers.observability import router as observability_router
from app.routers.settings import router as settings_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pools connect lazily on first query; eagerly warm MySQL so startup
    # surfaces config/connectivity errors early.
    try:
        await mysql.connect()
        logger.info("MySQL pool initialized")
    except Exception as e:  # noqa: BLE001 - log and continue;健康检查仍可用
        logger.error(f"MySQL pool init failed: {e}")
    yield
    await mysql.disconnect()
    logger.info("MySQL pool closed")


app = FastAPI(
    title="ScholarMind API",
    description="ScholarMind (文渊) - 跨语言学术文献智能调研系统后端 API",
    version="1.0.0",
    lifespan=lifespan,
)

# Set up CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8008"],  # Vite dev + production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Unified error envelope for app exceptions (401/404/400/...) + 500 fallback.
app.add_exception_handler(AppException, exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

# Register routers under /api prefix
app.include_router(auth_router, prefix="/api")
app.include_router(papers_router, prefix="/api")
app.include_router(folders_router, prefix="/api")
app.include_router(ingest_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(advanced_router, prefix="/api")
app.include_router(observability_router, prefix="/api")
app.include_router(settings_router, prefix="/api")

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
