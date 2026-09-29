"""Plugin package commands for compose-farm."""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from importlib.util import find_spec

import typer

from compose_farm.cli.app import app
from compose_farm.cli.common import ConfigOption, load_config_or_exit
from compose_farm.console import print_error, print_success, print_warning

plugins_app = typer.Typer(
    name="plugins",
    help="Manage plugin packages.",
    no_args_is_help=True,
)


def _requirement(package: str) -> str:
    """Expand ``github:OWNER/REPO[/SUBDIR][@REF]`` into a pip git URL; pass others through."""
    if not package.startswith("github:"):
        return package
    path, _, ref = package.removeprefix("github:").partition("@")
    owner, _, rest = path.partition("/")
    repo, _, subdir = rest.partition("/")
    if not owner or not repo:
        msg = f"Invalid plugin package {package!r}: use github:OWNER/REPO/SUBDIR@REF"
        print_error(f"{msg} (SUBDIR and REF are optional)")
        raise typer.Exit(1)
    url = f"git+https://github.com/{owner}/{repo}" + (f"@{ref}" if ref else "")
    return f"{url}#subdirectory={subdir}" if subdir else url


def _install_command(packages: list[str]) -> list[str]:
    """Install packages into the Python environment running compose-farm."""
    if uv := shutil.which("uv"):
        return [uv, "pip", "install", "--python", sys.executable, "--", *packages]
    if find_spec("pip") is None:  # uv tool environments (and the Docker image) have no pip
        print_error("Installing plugin_packages needs uv on PATH or pip in compose-farm's Python")
        raise typer.Exit(1)
    return [sys.executable, "-m", "pip", "install", "--", *packages]


@plugins_app.command("install")
def plugins_install(config: ConfigOption = None) -> None:
    """Install the config's plugin_packages next to compose-farm.

    Entries are pip requirements or github:OWNER/REPO[/SUBDIR][@REF].
    Uses uv when available, else pip. Run it again after
    `uv tool upgrade compose-farm`, which resets the tool's environment.
    """
    cfg = load_config_or_exit(config, check_plugins=False)
    if cfg.plugin_packages:
        command = _install_command([_requirement(package) for package in cfg.plugin_packages])
        # Run next to the config so relative local paths resolve against it
        if subprocess.run(command, check=False, cwd=cfg.config_path.parent).returncode != 0:
            print_error("Installing plugin_packages failed")
            raise typer.Exit(1)
        importlib.invalidate_caches()  # Let the entry-point scan see the new packages
    else:
        print_warning("No plugin_packages in config")
    cfg = load_config_or_exit(config)  # Exits if an enabled plugin is still missing
    print_success(f"Plugins: {', '.join(cfg.plugins) or 'none enabled'}")


# Register plugins subcommand on the shared app
app.add_typer(plugins_app, name="plugins", rich_help_panel="Configuration")
