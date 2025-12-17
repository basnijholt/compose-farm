"""FastAPI application setup."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

if TYPE_CHECKING:
    from compose_farm.config import Config

# Paths
WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


@lru_cache
def get_config() -> Config:
    """Load config once per process (cached)."""
    from compose_farm.config import load_config

    return load_config()


def get_templates() -> Jinja2Templates:
    """Get Jinja2 templates instance."""
    return Jinja2Templates(directory=str(TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    # Startup: pre-load config
    get_config()
    yield
    # Shutdown: nothing to clean up


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Compose Farm",
        description="Web UI for managing Docker Compose services across multiple hosts",
        lifespan=lifespan,
    )

    # Mount static files
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Import and include routers
    from compose_farm.web.routes import actions, api, pages
    from compose_farm.web.ws import router as ws_router

    app.include_router(pages.router)
    app.include_router(api.router, prefix="/api")
    app.include_router(actions.router, prefix="/api")
    app.include_router(ws_router)

    return app
