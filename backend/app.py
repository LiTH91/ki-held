from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from datetime import datetime

from backend.config import settings
from backend.logger import setup_logging
from backend.routers.login import router as login_router
from backend.routers.scan import router as scan_router

log = setup_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(f"[STARTUP] Settings loaded: HOST={settings.HOST}, PORT={settings.PORT}, API_KEY={'***' if settings.API_KEY else None}")
    yield
    log.info("[SHUTDOWN] Application shutting down.")

def create_app() -> FastAPI:
    app = FastAPI(
        title="Web Scraping Desktop App",
        description="Backend API for web scraping desktop application",
        version="0.1.0",
        lifespan=lifespan
    )
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        log.info(f"[REQUEST] {request.method} {request.url.path} origin={request.headers.get('origin')}")
        response = await call_next(request)
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Allow all origins for development
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(login_router, prefix="/api", tags=["login"])
    app.include_router(scan_router, prefix="/api", tags=["scan"])

    @app.get("/health")
    async def health_check():
        log.info("[REQUEST] Health check endpoint called")
        return {"status": "healthy", "timestamp": str(datetime.now())}

    return app
