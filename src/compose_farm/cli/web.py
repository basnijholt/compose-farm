"""Web server command."""

from __future__ import annotations

from typing import Annotated

import typer

from compose_farm.cli.app import app
from compose_farm.console import console


@app.command(rich_help_panel="Server")
def web(
    host: Annotated[
        str,
        typer.Option("--host", "-H", help="Host to bind to"),
    ] = "0.0.0.0",  # noqa: S104
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="Port to listen on"),
    ] = 8000,
    reload: Annotated[
        bool,
        typer.Option("--reload", "-r", help="Enable auto-reload for development"),
    ] = False,
) -> None:
    """Start the web UI server."""
    try:
        import uvicorn  # noqa: PLC0415
    except ImportError:
        console.print(
            "[red]Error:[/] Web dependencies not installed. "
            "Install with: [cyan]pip install compose-farm[web][/]"
        )
        raise typer.Exit(1) from None

    console.print(f"[green]Starting Compose Farm Web UI[/] at http://{host}:{port}")
    console.print("[dim]Press Ctrl+C to stop[/]")

    uvicorn.run(
        "compose_farm.web:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
