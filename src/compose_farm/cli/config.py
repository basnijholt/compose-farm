"""Configuration management commands for compose-farm."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from compose_farm.cli.app import app
from compose_farm.console import MSG_CONFIG_NOT_FOUND, console, print_error, print_success
from compose_farm.paths import config_search_paths, default_config_path, find_config_path

if TYPE_CHECKING:
    from compose_farm.config import Config

config_app = typer.Typer(
    name="config",
    help="Manage compose-farm configuration files.",
    no_args_is_help=True,
)


# --- CLI Options (same pattern as cli.py) ---
_PathOption = Annotated[
    Path | None,
    typer.Option("--path", "-p", help="Path to config file. Uses auto-detection if not specified."),
]
_ForceOption = Annotated[
    bool,
    typer.Option("--force", "-f", help="Overwrite existing config without confirmation."),
]
_RawOption = Annotated[
    bool,
    typer.Option("--raw", "-r", help="Output raw file contents (for copy-paste)."),
]


def _get_editor() -> str:
    """Get the user's preferred editor ($EDITOR > $VISUAL > platform default)."""
    if editor := os.environ.get("EDITOR") or os.environ.get("VISUAL"):
        return editor
    return next((e for e in ("nano", "vim", "vi") if shutil.which(e)), "vi")


def _generate_template() -> str:
    """Generate a config template with documented schema."""
    try:
        template_file = resources.files("compose_farm") / "example-config.yaml"
        return template_file.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        print_error("Example config template is missing from the package")
        console.print("Reinstall compose-farm or report this issue.")
        raise typer.Exit(1) from e


def _get_config_file(path: Path | None) -> Path | None:
    """Resolve config path, or auto-detect from standard locations."""
    if path:
        return path.expanduser().resolve()

    config_path = find_config_path()
    return config_path.resolve() if config_path else None


def _load_config_with_path(path: Path | None) -> tuple[Path, Config]:
    """Load config and return both the resolved path and Config object.

    Exits with error if config not found or invalid.
    """
    from compose_farm.cli.common import load_config_or_exit  # noqa: PLC0415

    config_file = _get_config_file(path)
    if config_file is None:
        print_error(MSG_CONFIG_NOT_FOUND)
        raise typer.Exit(1)

    cfg = load_config_or_exit(config_file)
    return config_file, cfg


def _report_missing_config(explicit_path: Path | None = None) -> None:
    """Report that a config file was not found."""
    console.print("[yellow]Config file not found.[/yellow]")
    if explicit_path:
        console.print(f"\nProvided path does not exist: [cyan]{explicit_path}[/cyan]")
    else:
        console.print("\nSearched locations:")
        for p in config_search_paths():
            status = "[green]exists[/green]" if p.exists() else "[dim]not found[/dim]"
            console.print(f"  - {p} ({status})")
    console.print("\nRun [bold cyan]cf config init[/bold cyan] to create one.")


@config_app.command("init")
def config_init(
    path: _PathOption = None,
    force: _ForceOption = False,
) -> None:
    """Create a new config file with documented example.

    The generated config file serves as a template showing all available
    options with explanatory comments.
    """
    target_path = (path.expanduser().resolve() if path else None) or default_config_path()

    if target_path.exists() and not force:
        console.print(
            f"[bold yellow]Config file already exists at:[/bold yellow] [cyan]{target_path}[/cyan]",
        )
        if not typer.confirm("Overwrite existing config file?"):
            console.print("[dim]Aborted.[/dim]")
            raise typer.Exit(0)

    # Create parent directories
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Generate and write template
    template_content = _generate_template()
    target_path.write_text(template_content, encoding="utf-8")

    print_success(f"Config file created at: {target_path}")
    console.print("\n[dim]Edit the file to customize your settings:[/dim]")
    console.print("  [cyan]cf config edit[/cyan]")


@config_app.command("edit")
def config_edit(
    path: _PathOption = None,
) -> None:
    """Open the config file in your default editor.

    The editor is determined by: $EDITOR > $VISUAL > platform default.
    """
    config_file = _get_config_file(path)

    if config_file is None:
        _report_missing_config()
        raise typer.Exit(1)

    if not config_file.exists():
        _report_missing_config(config_file)
        raise typer.Exit(1)

    editor = _get_editor()
    console.print(f"[dim]Opening {config_file} with {editor}...[/dim]")

    try:
        editor_cmd = shlex.split(editor)
    except ValueError as e:
        print_error("Invalid editor command. Check [bold]$EDITOR[/]/[bold]$VISUAL[/]")
        raise typer.Exit(1) from e

    if not editor_cmd:
        print_error("Editor command is empty")
        raise typer.Exit(1)

    try:
        subprocess.run([*editor_cmd, str(config_file)], check=True)
    except FileNotFoundError:
        print_error(f"Editor [cyan]{editor_cmd[0]}[/] not found")
        console.print("Set [bold]$EDITOR[/] environment variable to your preferred editor.")
        raise typer.Exit(1) from None
    except subprocess.CalledProcessError as e:
        print_error(f"Editor exited with error code {e.returncode}")
        raise typer.Exit(e.returncode) from None


@config_app.command("show")
def config_show(
    path: _PathOption = None,
    raw: _RawOption = False,
) -> None:
    """Display the config file location and contents."""
    config_file = _get_config_file(path)

    if config_file is None:
        _report_missing_config()
        raise typer.Exit(0)

    if not config_file.exists():
        _report_missing_config(config_file)
        raise typer.Exit(1)

    content = config_file.read_text(encoding="utf-8")

    if raw:
        print(content, end="")
        return

    from rich.syntax import Syntax  # noqa: PLC0415

    console.print(f"[bold green]Config file:[/bold green] [cyan]{config_file}[/cyan]")
    console.print()
    syntax = Syntax(content, "yaml", theme="monokai", line_numbers=True, word_wrap=True)
    console.print(syntax)
    console.print()
    console.print("[dim]Tip: Use -r for copy-paste friendly output[/dim]")


@config_app.command("path")
def config_path(
    path: _PathOption = None,
) -> None:
    """Print the config file path (useful for scripting)."""
    config_file = _get_config_file(path)

    if config_file is None:
        _report_missing_config()
        raise typer.Exit(1)

    # Just print the path for easy piping
    print(config_file)


@config_app.command("validate")
def config_validate(
    path: _PathOption = None,
) -> None:
    """Validate the config file syntax and schema."""
    config_file, cfg = _load_config_with_path(path)

    print_success(f"Valid config: {config_file}")
    console.print(f"  Hosts: {len(cfg.hosts)}")
    console.print(f"  Stacks: {len(cfg.stacks)}")


@config_app.command("symlink")
def config_symlink(
    target: Annotated[
        Path | None,
        typer.Argument(help="Config file to link to. Defaults to ./compose-farm.yaml"),
    ] = None,
    force: _ForceOption = False,
) -> None:
    """Create a symlink from the default config location to a config file.

    This makes a local config file discoverable globally without copying.
    Always uses absolute paths to avoid broken symlinks.

    Examples:
        cf config symlink                    # Link to ./compose-farm.yaml
        cf config symlink /opt/compose/config.yaml  # Link to specific file

    """
    # Default to compose-farm.yaml in current directory
    target_path = (target or Path("compose-farm.yaml")).expanduser().resolve()

    if not target_path.exists():
        print_error(f"Target config file not found: {target_path}")
        raise typer.Exit(1)

    if not target_path.is_file():
        print_error(f"Target is not a file: {target_path}")
        raise typer.Exit(1)

    symlink_path = default_config_path()

    # Check if symlink location already exists
    if symlink_path.exists() or symlink_path.is_symlink():
        if symlink_path.is_symlink():
            current_target = symlink_path.resolve() if symlink_path.exists() else None
            if current_target == target_path:
                print_success(f"Symlink already points to: {target_path}")
                return
            # Update existing symlink
            if not force:
                existing = symlink_path.readlink()
                console.print(f"[yellow]Symlink exists:[/] {symlink_path} -> {existing}")
                if not typer.confirm(f"Update to point to {target_path}?"):
                    console.print("[dim]Aborted.[/dim]")
                    raise typer.Exit(0)
            symlink_path.unlink()
        else:
            # Regular file exists
            print_error(f"A regular file exists at: {symlink_path}")
            console.print("    Back it up or remove it first, then retry.")
            raise typer.Exit(1)

    # Create parent directories
    symlink_path.parent.mkdir(parents=True, exist_ok=True)

    # Create symlink with absolute path
    symlink_path.symlink_to(target_path)

    print_success("Created symlink:")
    console.print(f"    {symlink_path}")
    console.print(f"    -> {target_path}")


def _detect_domain(cfg: Config) -> str | None:
    """Try to detect DOMAIN from traefik Host() rules in existing stacks.

    Uses extract_website_urls from traefik module to get interpolated
    URLs, then extracts the domain from the first valid URL.
    Skips local domains (.local, localhost, etc.).
    """
    from urllib.parse import urlparse  # noqa: PLC0415

    from compose_farm.traefik import extract_website_urls  # noqa: PLC0415

    max_stacks_to_check = 10
    min_domain_parts = 2
    subdomain_parts = 4
    skip_tlds = {"local", "localhost", "internal", "lan", "home"}

    for stack_name in list(cfg.stacks.keys())[:max_stacks_to_check]:
        urls = extract_website_urls(cfg, stack_name)
        for url in urls:
            host = urlparse(url).netloc
            parts = host.split(".")
            # Skip local/internal domains
            if parts[-1].lower() in skip_tlds:
                continue
            if len(parts) >= subdomain_parts:
                # e.g., "app.lab.nijho.lt" -> "lab.nijho.lt"
                return ".".join(parts[-3:])
            if len(parts) >= min_domain_parts:
                # e.g., "app.example.com" -> "example.com"
                return ".".join(parts[-2:])
    return None


def _detect_local_host(cfg: Config) -> str | None:
    """Find which config host matches local machine's IPs."""
    from compose_farm.executor import is_local  # noqa: PLC0415

    for name, host in cfg.hosts.items():
        if is_local(host):
            return name
    return None


@config_app.command("init-env")
def config_init_env(
    path: _PathOption = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", "-o", help="Output .env file path. Defaults to .env in config directory."
        ),
    ] = None,
    force: _ForceOption = False,
) -> None:
    """Generate a .env file for Docker deployment.

    Reads the compose-farm.yaml config and auto-detects settings:
    - CF_COMPOSE_DIR from compose_dir
    - CF_LOCAL_HOST by detecting which config host matches local IPs
    - CF_UID/GID/HOME/USER from current user
    - DOMAIN from traefik labels in stacks (if found)

    Example::

        cf config init-env           # Create .env next to config
        cf config init-env -o .env   # Create .env in current directory

    """
    config_file, cfg = _load_config_with_path(path)

    # Determine output path
    env_path = output.expanduser().resolve() if output else config_file.parent / ".env"

    if env_path.exists() and not force:
        console.print(f"[yellow].env file already exists:[/] {env_path}")
        if not typer.confirm("Overwrite?"):
            console.print("[dim]Aborted.[/dim]")
            raise typer.Exit(0)

    # Auto-detect values
    uid = os.getuid()
    gid = os.getgid()
    home = os.environ.get("HOME", "/root")
    user = os.environ.get("USER", "root")
    compose_dir = str(cfg.compose_dir)
    local_host = _detect_local_host(cfg)
    domain = _detect_domain(cfg)

    # Generate .env content
    lines = [
        "# Generated by: cf config init-env",
        f"# From config: {config_file}",
        "",
        "# Domain for Traefik labels",
        f"DOMAIN={domain or 'example.com'}",
        "",
        "# Compose files location",
        f"CF_COMPOSE_DIR={compose_dir}",
        "",
        "# Run as current user (recommended for NFS)",
        f"CF_UID={uid}",
        f"CF_GID={gid}",
        f"CF_HOME={home}",
        f"CF_USER={user}",
        "",
        "# Local hostname for Glances integration",
        f"CF_LOCAL_HOST={local_host or '# auto-detect failed - set manually'}",
        "",
    ]

    env_path.write_text("\n".join(lines), encoding="utf-8")

    print_success(f"Created .env file: {env_path}")
    console.print()
    console.print("[dim]Detected settings:[/dim]")
    console.print(f"  DOMAIN: {domain or '[yellow]example.com[/] (edit this)'}")
    console.print(f"  CF_COMPOSE_DIR: {compose_dir}")
    console.print(f"  CF_UID/GID: {uid}:{gid}")
    console.print(f"  CF_LOCAL_HOST: {local_host or '[yellow]not detected[/] (set manually)'}")
    console.print()
    console.print("[dim]Review and edit as needed:[/dim]")
    console.print(f"  [cyan]$EDITOR {env_path}[/cyan]")


# Register config subcommand on the shared app
app.add_typer(config_app, name="config", rich_help_panel="Configuration")
