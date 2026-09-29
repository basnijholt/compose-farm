"""Plugin package commands for compose-farm."""

from __future__ import annotations

import importlib
import shlex
import shutil
import subprocess
import sys

import typer

from compose_farm.cli.app import app
from compose_farm.cli.common import ConfigOption, load_config_or_exit
from compose_farm.console import console, print_error, print_success, print_warning

plugins_app = typer.Typer(
    name="plugins",
    help="Manage plugin packages.",
    no_args_is_help=True,
)


def _install_command(packages: list[str]) -> list[str]:
    """Install packages into the Python environment running compose-farm."""
    if uv := shutil.which("uv"):
        return [uv, "pip", "install", "--python", sys.executable, *packages]
    return [sys.executable, "-m", "pip", "install", *packages]


@plugins_app.command("install")
def plugins_install(config: ConfigOption = None) -> None:
    """Install the config's plugin_packages next to compose-farm.

    Uses uv when available, else pip. Run it again after
    `uv tool upgrade compose-farm`, which resets the tool's environment.
    """
    cfg = load_config_or_exit(config, check_plugins=False)
    if not cfg.plugin_packages:
        print_warning("No plugin_packages in config")
        return
    command = _install_command(cfg.plugin_packages)
    console.print(f"$ {shlex.join(command)}", style="dim", markup=False, soft_wrap=True)
    if subprocess.run(command, check=False).returncode != 0:
        print_error("Installing plugin_packages failed")
        raise typer.Exit(1)
    importlib.invalidate_caches()  # Let the entry-point scan see the new packages
    cfg = load_config_or_exit(config)  # Exits if an enabled plugin is still missing
    print_success(f"Plugins: {', '.join(cfg.plugins) or 'none enabled'}")


# Register plugins subcommand on the shared app
app.add_typer(plugins_app, name="plugins", rich_help_panel="Configuration")
