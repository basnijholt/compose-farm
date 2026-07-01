"""Web server command."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from compose_farm.cli.app import app
from compose_farm.console import console

if TYPE_CHECKING:
    from rich.text import Text


def _compose_farm_banner() -> Text:
    """Build the colored web startup banner."""
    from rich.text import Text  # noqa: PLC0415

    banner = Text()
    structure = "bright_cyan"
    windows = "yellow"

    banner.append("           .-^-.\n", style=structure)
    banner.append("        .-'  _  '-.\n", style=structure)
    banner.append("       /    ", style=structure)
    banner.append("|_|", style=windows)
    banner.append("    \\\n", style=structure)
    banner.append("      /-------------\\\n", style=structure)
    banner.append("     /  ", style=structure)
    banner.append("[]", style=windows)
    banner.append("  ", style=structure)
    banner.append("[]", style=windows)
    banner.append("  ", style=structure)
    banner.append("[]", style=windows)
    banner.append("   \\\n", style=structure)
    banner.append("    /_________________\\\n", style=structure)
    banner.append("    |  ", style=structure)
    banner.append("COMPOSE FARM", style="bold green")
    banner.append("   |\n", style=structure)
    banner.append("    |_________________|", style=structure)
    return banner


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

    console.print(_compose_farm_banner())
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
