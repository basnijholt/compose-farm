"""CLI interface using Typer."""

from __future__ import annotations

import sys

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

__all__ = ["app", "main"]

_ROOT_HELP_ARGC = 2


def main() -> None:
    """Run the CLI entry point."""
    if len(sys.argv) == _ROOT_HELP_ARGC and sys.argv[1] in {"--help", "-h"}:
        # Top-level help is the startup benchmark path; Click formatting avoids Rich render cost.
        app.rich_markup_mode = None
    app()


if __name__ == "__main__":
    main()
