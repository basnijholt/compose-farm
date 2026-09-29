"""Plugin package commands for compose-farm."""

from __future__ import annotations

import typer

from compose_farm.cli.app import app
from compose_farm.cli.common import ConfigOption, load_config_or_exit
from compose_farm.console import print_error, print_success, print_warning
from compose_farm.plugins import PluginError, install_packages

plugins_app = typer.Typer(
    name="plugins",
    help="Manage plugin packages.",
    no_args_is_help=True,
)


@plugins_app.command("install")
def plugins_install(config: ConfigOption = None) -> None:
    """Install the config's plugin_packages next to compose-farm.

    Entries are pip requirements or github:OWNER/REPO[/SUBDIR][@REF].
    Uses uv when available, else pip. With plugin_auto_install (the default),
    cf also does this by itself when an enabled plugin is missing.
    """
    cfg = load_config_or_exit(config, check_plugins=False)
    if cfg.plugin_packages:
        try:
            install_packages(cfg.plugin_packages, cfg.config_path.parent)
        except PluginError as e:
            print_error(f"Installing plugin_packages failed: {e}")
            raise typer.Exit(1) from e
    else:
        print_warning("No plugin_packages in config")
    cfg.plugin_auto_install = False  # Just installed; check what loads without installing again
    try:
        cfg.get_plugins()
    except PluginError as e:
        print_error(f"Invalid config: {e}")
        raise typer.Exit(1) from e
    print_success(f"Plugins: {', '.join(cfg.plugins) or 'none enabled'}")


# Register plugins subcommand on the shared app
app.add_typer(plugins_app, name="plugins", rich_help_panel="Configuration")
