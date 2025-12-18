"""FastAPI application setup."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from compose_farm.web.deps import STATIC_DIR, get_config
from compose_farm.web.routes import actions, api, pages

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    # Startup: pre-load config (ignore errors - handled per-request)
    with suppress(ValidationError, FileNotFoundError):
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

    # WebSocket routes use Unix-only modules (fcntl, pty)
    if sys.platform != "win32":
        from compose_farm.web.ws import router as ws_router  # noqa: PLC0415

        app.include_router(ws_router)

    return app
