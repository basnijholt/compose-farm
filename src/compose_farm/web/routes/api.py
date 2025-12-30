"""JSON API routes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from collections.abc import Callable

import asyncssh
import yaml
from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import HTMLResponse

from compose_farm.compose import extract_services, get_container_name, load_compose_data_for_stack
from compose_farm.executor import is_local, run_compose_on_host, ssh_connect_kwargs
from compose_farm.glances import fetch_all_host_stats
from compose_farm.paths import backup_dir, find_config_path
from compose_farm.state import load_state
from compose_farm.web.deps import get_config, get_templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["api"])


def _validate_yaml(content: str) -> None:
    """Validate YAML content, raise HTTPException on error."""
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}") from e


def _backup_file(file_path: Path) -> Path | None:
    """Create a timestamped backup of a file if it exists and content differs.

    Backups are stored in XDG config dir under compose-farm/backups/.
    The original file's absolute path is mirrored in the backup directory.
    Returns the backup path if created, None if no backup was needed.
    """
    if not file_path.exists():
        return None

    # Create backup directory mirroring original path structure
    # e.g., /opt/stacks/plex/compose.yaml -> ~/.config/compose-farm/backups/opt/stacks/plex/
    resolved = file_path.resolve()
    file_backup_dir = backup_dir() / resolved.parent.relative_to(resolved.anchor)
    file_backup_dir.mkdir(parents=True, exist_ok=True)

    # Generate timestamped backup filename
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    backup_name = f"{file_path.name}.{timestamp}"
    backup_path = file_backup_dir / backup_name

    # Copy current content to backup
    backup_path.write_text(file_path.read_text())

    # Clean up old backups (keep last 200)
    backups = sorted(file_backup_dir.glob(f"{file_path.name}.*"), reverse=True)
    for old_backup in backups[200:]:
        old_backup.unlink()

    return backup_path


def _save_with_backup(file_path: Path, content: str) -> bool:
    """Save content to file, creating a backup first if content changed.

    Returns True if file was saved, False if content was unchanged.
    """
    # Check if content actually changed
    if file_path.exists():
        current_content = file_path.read_text()
        if current_content == content:
            return False  # No change, skip save
        _backup_file(file_path)

    file_path.write_text(content)
    return True


def _get_stack_compose_path(name: str) -> Path:
    """Get compose path for stack, raising HTTPException if not found."""
    config = get_config()

    if name not in config.stacks:
        raise HTTPException(status_code=404, detail=f"Stack '{name}' not found")

    compose_path = config.get_compose_path(name)
    if not compose_path:
        raise HTTPException(status_code=404, detail="Compose file not found")

    return compose_path


def _get_compose_services(config: Any, stack: str, hosts: list[str]) -> list[dict[str, Any]]:
    """Get container info from compose file (fast, local read).

    Returns one entry per container per host for multi-host stacks.
    """
    compose_path, compose_data = load_compose_data_for_stack(config, stack)
    if not compose_path.exists():
        return []
    raw_services = extract_services(compose_data)
    if not raw_services:
        return []

    # Project name is the directory name (docker compose default)
    project_name = compose_path.parent.name

    containers = []
    for host in hosts:
        for svc_name, svc_def in raw_services.items():
            containers.append(
                {
                    "Name": get_container_name(svc_name, svc_def, project_name),
                    "Service": svc_name,
                    "Host": host,
                    "State": "unknown",  # Status requires Docker query
                }
            )
    return containers


async def _get_container_states(
    config: Any, stack: str, containers: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Query Docker for actual container states on a single host."""
    if not containers:
        return containers

    # All containers should be on the same host
    host_name = containers[0]["Host"]

    # Use -a to include stopped/exited containers
    result = await run_compose_on_host(
        config, stack, host_name, "ps -a --format json", stream=False
    )
    if not result.success:
        logger.warning(
            "Failed to get container states for %s on %s: %s",
            stack,
            host_name,
            result.stderr or result.stdout,
        )
        return containers

    # Build state map: name -> (state, exit_code)
    state_map: dict[str, tuple[str, int]] = {}
    for line in result.stdout.strip().split("\n"):
        if line.strip():
            with contextlib.suppress(json.JSONDecodeError):
                data = json.loads(line)
                name = data.get("Name", "")
                state = data.get("State", "unknown")
                exit_code = data.get("ExitCode", 0)
                state_map[name] = (state, exit_code)

    # Update container states
    for c in containers:
        if c["Name"] in state_map:
            state, exit_code = state_map[c["Name"]]
            c["State"] = state
            c["ExitCode"] = exit_code
        else:
            # Container not in ps output means it was never started
            c["State"] = "created"
            c["ExitCode"] = None

    return containers


def _render_containers(
    stack: str, host: str, containers: list[dict[str, Any]], *, show_header: bool = False
) -> str:
    """Render containers HTML using Jinja template."""
    templates = get_templates()
    template = templates.env.get_template("partials/containers.html")
    module = template.make_module()
    # TemplateModule exports macros as attributes; getattr keeps type checkers happy
    host_containers: Callable[..., str] = getattr(module, "host_containers")  # noqa: B009
    return host_containers(stack, host, containers, show_header=show_header)


@router.get("/stack/{name}/containers", response_class=HTMLResponse)
async def get_containers(name: str, host: str | None = None) -> HTMLResponse:
    """Get containers for a stack as HTML buttons.

    If host is specified, queries Docker for that host's status.
    Otherwise returns all hosts with loading spinners that auto-fetch.
    """
    config = get_config()

    if name not in config.stacks:
        raise HTTPException(status_code=404, detail=f"Stack '{name}' not found")

    # Get hosts where stack is running from state
    state = load_state(config)
    current_hosts = state.get(name)
    if not current_hosts:
        return HTMLResponse('<span class="text-base-content/60">Stack not running</span>')

    all_hosts = current_hosts if isinstance(current_hosts, list) else [current_hosts]

    # If host specified, return just that host's containers with status
    if host:
        if host not in all_hosts:
            return HTMLResponse(f'<span class="text-error">Host {host} not found</span>')

        containers = _get_compose_services(config, name, [host])
        containers = await _get_container_states(config, name, containers)
        return HTMLResponse(_render_containers(name, host, containers))

    # Initial load: return all hosts with loading spinners, each fetches its own status
    html_parts = []
    is_multi_host = len(all_hosts) > 1

    for h in all_hosts:
        host_id = f"containers-{name}-{h}".replace(".", "-")
        containers = _get_compose_services(config, name, [h])

        if is_multi_host:
            html_parts.append(f'<div class="font-semibold text-sm mt-3 mb-1">{h}</div>')

        # Container for this host that auto-fetches its own status
        html_parts.append(f"""
            <div id="{host_id}"
                 hx-get="/api/stack/{name}/containers?host={h}"
                 hx-trigger="load"
                 hx-target="this"
                 hx-select="unset"
                 hx-swap="innerHTML">
                {_render_containers(name, h, containers)}
            </div>
        """)

    return HTMLResponse("".join(html_parts))


@router.put("/stack/{name}/compose")
async def save_compose(
    name: str, content: Annotated[str, Body(media_type="text/plain")]
) -> dict[str, Any]:
    """Save compose file content."""
    compose_path = _get_stack_compose_path(name)
    _validate_yaml(content)
    saved = _save_with_backup(compose_path, content)
    msg = "Compose file saved" if saved else "No changes to save"
    return {"success": True, "message": msg}


@router.put("/stack/{name}/env")
async def save_env(
    name: str, content: Annotated[str, Body(media_type="text/plain")]
) -> dict[str, Any]:
    """Save .env file content."""
    env_path = _get_stack_compose_path(name).parent / ".env"
    saved = _save_with_backup(env_path, content)
    msg = ".env file saved" if saved else "No changes to save"
    return {"success": True, "message": msg}


@router.put("/config")
async def save_config(
    content: Annotated[str, Body(media_type="text/plain")],
) -> dict[str, Any]:
    """Save compose-farm.yaml config file."""
    config_path = find_config_path()
    if not config_path:
        raise HTTPException(status_code=404, detail="Config file not found")

    _validate_yaml(content)
    saved = _save_with_backup(config_path, content)
    msg = "Config saved" if saved else "No changes to save"
    return {"success": True, "message": msg}


async def _read_file_local(path: str) -> str:
    """Read a file from the local filesystem."""
    expanded = Path(path).expanduser()
    return await asyncio.to_thread(expanded.read_text, encoding="utf-8")


async def _write_file_local(path: str, content: str) -> bool:
    """Write content to a file on the local filesystem with backup.

    Returns True if file was saved, False if content was unchanged.
    """
    expanded = Path(path).expanduser()
    return await asyncio.to_thread(_save_with_backup, expanded, content)


async def _read_file_remote(host: Any, path: str) -> str:
    """Read a file from a remote host via SSH."""
    # Expand ~ on remote by using shell
    cmd = f"cat {shlex.quote(path)}"
    if path.startswith("~/"):
        cmd = f"cat ~/{shlex.quote(path[2:])}"

    async with asyncssh.connect(**ssh_connect_kwargs(host)) as conn:
        result = await conn.run(cmd, check=True)
        stdout = result.stdout or ""
        return stdout.decode() if isinstance(stdout, bytes) else stdout


async def _write_file_remote(host: Any, path: str, content: str) -> None:
    """Write content to a file on a remote host via SSH."""
    # Expand ~ on remote: keep ~ unquoted for shell expansion, quote the rest
    target = f"~/{shlex.quote(path[2:])}" if path.startswith("~/") else shlex.quote(path)
    cmd = f"cat > {target}"

    async with asyncssh.connect(**ssh_connect_kwargs(host)) as conn:
        result = await conn.run(cmd, input=content, check=True)
        if result.returncode != 0:
            stderr = result.stderr.decode() if isinstance(result.stderr, bytes) else result.stderr
            msg = f"Failed to write file: {stderr}"
            raise RuntimeError(msg)


def _get_console_host(host: str, path: str) -> Any:
    """Validate and return host config for console file operations."""
    config = get_config()
    host_config = config.hosts.get(host)

    if not host_config:
        raise HTTPException(status_code=404, detail=f"Host '{host}' not found")
    if not path:
        raise HTTPException(status_code=400, detail="Path is required")

    return host_config


@router.get("/console/file")
async def read_console_file(
    host: Annotated[str, Query(description="Host name")],
    path: Annotated[str, Query(description="File path")],
) -> dict[str, Any]:
    """Read a file from a host for the console editor."""
    host_config = _get_console_host(host, path)

    try:
        if is_local(host_config):
            content = await _read_file_local(path)
        else:
            content = await _read_file_remote(host_config, path)
        return {"success": True, "content": content}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}") from None
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}") from None
    except Exception as e:
        logger.exception("Failed to read file %s from host %s", path, host)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/console/file")
async def write_console_file(
    host: Annotated[str, Query(description="Host name")],
    path: Annotated[str, Query(description="File path")],
    content: Annotated[str, Body(media_type="text/plain")],
) -> dict[str, Any]:
    """Write a file to a host from the console editor."""
    host_config = _get_console_host(host, path)

    try:
        if is_local(host_config):
            saved = await _write_file_local(path, content)
            msg = f"Saved: {path}" if saved else "No changes to save"
        else:
            await _write_file_remote(host_config, path, content)
            msg = f"Saved: {path}"  # Remote doesn't track changes
        return {"success": True, "message": msg}
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}") from None
    except Exception as e:
        logger.exception("Failed to write file %s to host %s", path, host)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/glances", response_class=HTMLResponse)
async def get_glances_stats() -> HTMLResponse:
    """Get resource stats from Glances for all hosts."""
    config = get_config()

    if not config.glances_stack:
        return HTMLResponse("")  # Glances not configured

    stats = await fetch_all_host_stats(config)

    templates = get_templates()
    template = templates.env.get_template("partials/glances.html")
    html = template.render(stats=stats)
    return HTMLResponse(html)
