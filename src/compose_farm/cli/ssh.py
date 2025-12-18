"""SSH key management commands for compose-farm."""

from __future__ import annotations

import asyncio
import subprocess
from typing import TYPE_CHECKING, Annotated

import typer

from compose_farm.cli.app import app
from compose_farm.cli.common import ConfigOption, load_config_or_exit, run_parallel_with_progress
from compose_farm.console import console, err_console
from compose_farm.executor import run_command

if TYPE_CHECKING:
    from compose_farm.config import Host

from compose_farm.ssh_keys import (
    SSH_KEY_PATH,
    SSH_PUBKEY_PATH,
    get_pubkey_content,
    get_ssh_env,
    key_exists,
)

_DEFAULT_SSH_PORT = 22
_PUBKEY_DISPLAY_THRESHOLD = 60

ssh_app = typer.Typer(
    name="ssh",
    help="Manage SSH keys for passwordless authentication.",
    no_args_is_help=True,
)

_ForceOption = Annotated[
    bool,
    typer.Option("--force", "-f", help="Regenerate key even if it exists."),
]


def _generate_key(*, force: bool = False) -> bool:
    """Generate an ED25519 SSH key with no passphrase.

    Returns True if key was generated, False if skipped.
    """
    if key_exists() and not force:
        console.print(f"[yellow]![/] SSH key already exists: {SSH_KEY_PATH}")
        console.print("[dim]Use --force to regenerate[/]")
        return False

    # Create .ssh directory if it doesn't exist
    SSH_KEY_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    # Remove existing key if forcing regeneration
    if force:
        SSH_KEY_PATH.unlink(missing_ok=True)
        SSH_PUBKEY_PATH.unlink(missing_ok=True)

    console.print(f"[dim]Generating SSH key at {SSH_KEY_PATH}...[/]")

    try:
        subprocess.run(
            [  # noqa: S607
                "ssh-keygen",
                "-t",
                "ed25519",
                "-N",
                "",  # No passphrase
                "-f",
                str(SSH_KEY_PATH),
                "-C",
                "compose-farm",
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        err_console.print(f"[red]Failed to generate SSH key:[/] {e.stderr.decode()}")
        return False
    except FileNotFoundError:
        err_console.print("[red]ssh-keygen not found. Is OpenSSH installed?[/]")
        return False

    # Set correct permissions
    SSH_KEY_PATH.chmod(0o600)
    SSH_PUBKEY_PATH.chmod(0o644)

    console.print(f"[green]Generated SSH key:[/] {SSH_KEY_PATH}")
    return True


def _copy_key_to_host(host_name: str, address: str, user: str, port: int) -> bool:
    """Copy public key to a host's authorized_keys.

    Uses ssh-copy-id which handles agent vs password fallback automatically.
    Returns True on success, False on failure.
    """
    target = f"{user}@{address}"
    console.print(f"[dim]Copying key to {host_name} ({target})...[/]")

    cmd = ["ssh-copy-id"]

    # Disable strict host key checking (consistent with executor.py)
    cmd.extend(["-o", "StrictHostKeyChecking=no"])
    cmd.extend(["-o", "UserKnownHostsFile=/dev/null"])

    if port != _DEFAULT_SSH_PORT:
        cmd.extend(["-p", str(port)])

    cmd.extend(["-i", str(SSH_PUBKEY_PATH), target])

    try:
        # Don't capture output so user can see password prompt
        result = subprocess.run(cmd, check=False, env=get_ssh_env())
        if result.returncode == 0:
            console.print(f"[green]Key copied to {host_name}[/]")
            return True
        err_console.print(f"[red]Failed to copy key to {host_name}[/]")
        return False
    except FileNotFoundError:
        err_console.print("[red]ssh-copy-id not found. Is OpenSSH installed?[/]")
        return False


@ssh_app.command("keygen")
def ssh_keygen(
    force: _ForceOption = False,
) -> None:
    """Generate SSH key (does not distribute to hosts).

    Creates an ED25519 key at ~/.ssh/compose-farm/id_ed25519 with no passphrase.
    Use 'cf ssh setup' to also distribute the key to all configured hosts.
    """
    success = _generate_key(force=force)
    if not success and not key_exists():
        raise typer.Exit(1)


@ssh_app.command("setup")
def ssh_setup(
    config: ConfigOption = None,
    force: _ForceOption = False,
) -> None:
    """Generate SSH key and distribute to all configured hosts.

    Creates an ED25519 key at ~/.ssh/compose-farm/id_ed25519 (no passphrase)
    and copies the public key to authorized_keys on each host.

    For each host, tries SSH agent first. If agent is unavailable,
    prompts for password.
    """
    cfg = load_config_or_exit(config)

    # Skip localhost hosts
    remote_hosts = {
        name: host
        for name, host in cfg.hosts.items()
        if host.address.lower() not in ("localhost", "127.0.0.1")
    }

    if not remote_hosts:
        console.print("[yellow]No remote hosts configured.[/]")
        raise typer.Exit(0)

    # Generate key if needed
    if not key_exists() or force:
        if not _generate_key(force=force):
            raise typer.Exit(1)
    else:
        console.print(f"[dim]Using existing key: {SSH_KEY_PATH}[/]")

    console.print()
    console.print(f"[bold]Distributing key to {len(remote_hosts)} host(s)...[/]")
    console.print()

    # Copy key to each host
    succeeded = 0
    failed = 0

    for host_name, host in remote_hosts.items():
        if _copy_key_to_host(host_name, host.address, host.user, host.port):
            succeeded += 1
        else:
            failed += 1

    console.print()
    if failed == 0:
        console.print(
            f"[green]Setup complete.[/] {succeeded}/{len(remote_hosts)} hosts configured."
        )
    else:
        console.print(
            f"[yellow]Setup partially complete.[/] {succeeded}/{len(remote_hosts)} hosts configured, "
            f"[red]{failed} failed[/]."
        )
        raise typer.Exit(1)


@ssh_app.command("status")
def ssh_status(
    config: ConfigOption = None,
) -> None:
    """Show SSH key status and host connectivity."""
    from rich.table import Table  # noqa: PLC0415

    cfg = load_config_or_exit(config)

    # Key status
    console.print("[bold]SSH Key Status[/]")
    console.print()

    if key_exists():
        console.print(f"  [green]Key exists:[/] {SSH_KEY_PATH}")
        pubkey = get_pubkey_content()
        if pubkey:
            # Show truncated public key
            if len(pubkey) > _PUBKEY_DISPLAY_THRESHOLD:
                console.print(f"  [dim]Public key:[/] {pubkey[:30]}...{pubkey[-20:]}")
            else:
                console.print(f"  [dim]Public key:[/] {pubkey}")
    else:
        console.print(f"  [yellow]No key found:[/] {SSH_KEY_PATH}")
        console.print("  [dim]Run 'cf ssh setup' to generate and distribute a key[/]")

    console.print()
    console.print("[bold]Host Connectivity[/]")
    console.print()

    # Skip localhost hosts
    remote_hosts = {
        name: host
        for name, host in cfg.hosts.items()
        if host.address.lower() not in ("localhost", "127.0.0.1")
    }

    if not remote_hosts:
        console.print("  [dim]No remote hosts configured[/]")
        return

    async def check_host(item: tuple[str, Host]) -> tuple[str, str, str]:
        """Check connectivity to a single host."""
        host_name, host = item
        target = f"{host.user}@{host.address}"
        if host.port != _DEFAULT_SSH_PORT:
            target += f":{host.port}"

        try:
            result = await asyncio.wait_for(
                run_command(host, "echo ok", host_name, stream=False),
                timeout=5.0,
            )
            status = "[green]OK[/]" if result.success else "[red]Auth failed[/]"
        except TimeoutError:
            status = "[red]Timeout (5s)[/]"
        except Exception as e:
            status = f"[red]Error: {e}[/]"

        return host_name, target, status

    # Check connectivity in parallel with progress bar
    results = run_parallel_with_progress(
        "Checking hosts",
        list(remote_hosts.items()),
        check_host,
    )

    # Build table from results
    table = Table(show_header=True, header_style="bold")
    table.add_column("Host")
    table.add_column("Address")
    table.add_column("Status")

    # Sort by host name for consistent order
    for host_name, target, status in sorted(results, key=lambda r: r[0]):
        table.add_row(host_name, target, status)

    console.print(table)


# Register ssh subcommand on the shared app
app.add_typer(ssh_app, name="ssh", rich_help_panel="Configuration")
