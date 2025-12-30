"""FastAPI application setup."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any, cast

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from rich.logging import RichHandler

from compose_farm.web.deps import STATIC_DIR, get_config
from compose_farm.web.routes import actions, api, containers, pages
from compose_farm.web.streaming import TASK_TTL_SECONDS, cleanup_stale_tasks
from compose_farm.web.ws import router as ws_router

# Configure logging with Rich handler for compose_farm.web modules
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
)
# Set our web modules to INFO level (uvicorn handles its own logging)
logging.getLogger("compose_farm.web").setLevel(logging.INFO)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


async def _task_cleanup_loop() -> None:
    """Periodically clean up stale completed tasks."""
    while True:
        await asyncio.sleep(TASK_TTL_SECONDS // 2)  # Run every 5 minutes
        cleanup_stale_tasks()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    # Startup: pre-load config (ignore errors - handled per-request)
    with suppress(ValidationError, FileNotFoundError):
        get_config()

    # Start background cleanup task
    cleanup_task = asyncio.create_task(_task_cleanup_loop())

    yield

    # Shutdown: cancel cleanup task
    cleanup_task.cancel()
    with suppress(asyncio.CancelledError):
        await cleanup_task


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Compose Farm",
        description="Web UI for managing Docker Compose stacks across multiple hosts",
        lifespan=lifespan,
    )

    # Enable Gzip compression for faster transfers over slow networks
    app.add_middleware(cast("Any", GZipMiddleware), minimum_size=1000)

    # Mount static files
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(pages.router)
    app.include_router(containers.router)
    app.include_router(api.router, prefix="/api")
    app.include_router(actions.router, prefix="/api")

    app.include_router(ws_router)

    return app
