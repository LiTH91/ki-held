from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from datetime import datetime
import uvicorn
import json

# Setup logging (minimal for debug)
import logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(f"[STARTUP] Debug app lifespan startup.")
    yield
    log.info("[SHUTDOWN] Debug app lifespan shutting down.")

app = FastAPI(
    title="Web Scraping Debug App",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420", "tauri://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    log.info("[REQUEST] Debug health check endpoint called")
    return {"status": "healthy", "timestamp": str(datetime.now())}

@app.get("/debug-routes")
async def debug_routes():
    log.info("[REQUEST] Debug routes endpoint called")
    routes = [{"path": route.path, "name": route.name, "methods": list(route.methods)}
              for route in app.routes if hasattr(route, "path") and hasattr(route, "methods")]
    return {"routes": routes}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

