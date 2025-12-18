"""CLI interface using Typer."""

from __future__ import annotations

# Import command modules to trigger registration via @app.command() decorators
from compose_farm.cli import (
    config,  # noqa: F401
    lifecycle,  # noqa: F401
    management,  # noqa: F401
    monitoring,  # noqa: F401
    ssh,  # noqa: F401
    web,  # noqa: F401
)

# Import the shared app instance
from compose_farm.cli.app import app

__all__ = ["app"]

if __name__ == "__main__":
    app()
