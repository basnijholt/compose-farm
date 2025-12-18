"""FastAPI application setup."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from compose_farm.web.deps import STATIC_DIR, get_config
from compose_farm.web.routes import actions, api, pages
from compose_farm.web.ws import router as ws_router

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


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

    app.include_router(pages.router)
    app.include_router(api.router, prefix="/api")
    app.include_router(actions.router, prefix="/api")
    app.include_router(ws_router)

    return app
