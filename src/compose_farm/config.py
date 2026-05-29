"""Configuration loading and Pydantic models."""

from __future__ import annotations

import getpass
import logging
import os
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .paths import config_search_paths, find_config_path

# Supported compose filenames, in priority order
COMPOSE_FILENAMES = ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")
PLUGIN_CONFIG_MODEL_ENTRYPOINT_GROUP = "compose_farm.plugin_config_models"


class Host(BaseModel, extra="forbid"):
    """SSH host configuration."""

    address: str
    user: str = Field(default_factory=getpass.getuser)
    port: int = 22


class _PluginConfigBase(BaseModel, extra="forbid"):
    """Shared settings accepted by built-in plugins."""

    policies: dict[str, Literal["blocking", "warn"]] = Field(default_factory=dict)


class _SyncPluginConfig(_PluginConfigBase):
    """Validation schema for plugin_config.sync."""

    source_dir: str | None = None
    events: (
        list[
            Literal[
                "pre_apply",
                "post_apply",
                "pre_up",
                "post_up",
                "up_failed",
                "pre_down",
                "post_down",
                "down_failed",
                "pre_migrate",
                "pre_stop_source",
                "post_stop_source",
                "pre_start_target",
                "post_start_target",
                "migrate_failed",
                "rollback_started",
                "rollback_completed",
            ]
        ]
        | None
    ) = None
    excludes: list[str] = Field(default_factory=list)
    delete: bool = True
    rsync_flags: list[str] = Field(default_factory=lambda: ["-az"])


class _CommandHooksPluginConfig(_PluginConfigBase):
    """Validation schema for plugin_config.command-hooks."""

    hooks: dict[
        Literal[
            "pre_apply",
            "post_apply",
            "pre_up",
            "post_up",
            "up_failed",
            "pre_down",
            "post_down",
            "down_failed",
            "pre_migrate",
            "pre_stop_source",
            "post_stop_source",
            "pre_start_target",
            "post_start_target",
            "migrate_failed",
            "rollback_started",
            "rollback_completed",
        ],
        list[str],
    ] = Field(default_factory=dict)


_PLUGIN_CONFIG_MODELS: dict[str, type[_PluginConfigBase]] = {
    "sync": _SyncPluginConfig,
    "command-hooks": _CommandHooksPluginConfig,
}

_LOG = logging.getLogger(__name__)


def _is_pydantic_model(value: object) -> bool:
    """Return True when value is a Pydantic model class."""
    return isinstance(value, type) and issubclass(value, BaseModel)


def _discover_registered_plugin_config_models() -> dict[str, type[BaseModel]]:
    """Discover plugin config models registered via dedicated entry points."""
    discovered: dict[str, type[BaseModel]] = {}
    for ep in entry_points(group=PLUGIN_CONFIG_MODEL_ENTRYPOINT_GROUP):
        try:
            loaded = ep.load()
        except Exception as exc:
            _LOG.debug("Failed to load plugin config model entry point %s: %s", ep.name, exc)
            continue

        if _is_pydantic_model(loaded):
            discovered[ep.name] = loaded

    return discovered


class Config(BaseModel, extra="forbid"):
    """Main configuration."""

    compose_dir: Path = Path("/opt/compose")
    hosts: dict[str, Host]
    stacks: dict[str, str | list[str]]  # stack_name -> host_name or list of hosts
    traefik_file: Path | None = None  # Auto-regenerate traefik config after up/down
    traefik_stack: str | None = None  # Stack name for Traefik (skip its host in file-provider)
    glances_stack: str | None = (
        None  # Stack name for Glances (enables host resource stats in web UI)
    )
    plugins: list[str] = Field(default_factory=list)  # Enabled lifecycle hook plugins
    plugin_config: dict[str, dict[str, Any]] = Field(default_factory=dict)
    config_path: Path = Path()  # Set by load_config()

    @field_validator("plugin_config")
    @classmethod
    def validate_plugin_config(
        cls, plugin_config: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Validate known plugin configs and normalize them to plain dicts."""
        validated_plugin_config: dict[str, dict[str, Any]] = {}
        schemas: dict[str, type[BaseModel]] = {
            **_PLUGIN_CONFIG_MODELS,
            **_discover_registered_plugin_config_models(),
        }

        for plugin_name, raw_plugin_cfg in plugin_config.items():
            if not isinstance(raw_plugin_cfg, dict):
                msg = f"plugin_config.{plugin_name} must be a mapping"
                raise TypeError(msg)

            schema = schemas.get(plugin_name)
            if schema is None:
                validated_plugin_config[plugin_name] = raw_plugin_cfg
                continue

            try:
                parsed = schema.model_validate(raw_plugin_cfg)
            except ValidationError as exc:
                msg = f"Invalid plugin_config.{plugin_name}: {exc}"
                raise ValueError(msg) from exc

            validated_plugin_config[plugin_name] = parsed.model_dump(mode="json", exclude_none=True)

        return validated_plugin_config

    def get_state_path(self) -> Path:
        """Get the state file path (stored alongside config)."""
        return self.config_path.parent / "compose-farm-state.yaml"

    @model_validator(mode="after")
    def validate_hosts_and_stacks(self) -> Config:
        """Validate host names and stack configurations."""
        # "all" is reserved keyword, cannot be used as host name
        if "all" in self.hosts:
            msg = "'all' is a reserved keyword and cannot be used as a host name"
            raise ValueError(msg)

        for stack, host_value in self.stacks.items():
            # Validate list configurations
            if isinstance(host_value, list):
                if not host_value:
                    msg = f"Stack '{stack}' has empty host list"
                    raise ValueError(msg)
                if len(host_value) != len(set(host_value)):
                    msg = f"Stack '{stack}' has duplicate hosts in list"
                    raise ValueError(msg)

            # Validate all referenced hosts exist
            host_names = self.get_hosts(stack)
            for host_name in host_names:
                if host_name not in self.hosts:
                    msg = f"Stack '{stack}' references unknown host '{host_name}'"
                    raise ValueError(msg)

        return self

    def get_hosts(self, stack: str) -> list[str]:
        """Get list of host names for a stack.

        Supports:
        - Single host: "truenas-debian" -> ["truenas-debian"]
        - All hosts: "all" -> list of all configured hosts
        - Explicit list: ["host1", "host2"] -> ["host1", "host2"]
        """
        if stack not in self.stacks:
            msg = f"Unknown stack: {stack}"
            raise ValueError(msg)
        host_value = self.stacks[stack]
        if isinstance(host_value, list):
            return host_value
        if host_value == "all":
            return list(self.hosts.keys())
        return [host_value]

    def is_multi_host(self, stack: str) -> bool:
        """Check if a stack runs on multiple hosts."""
        return len(self.get_hosts(stack)) > 1

    def get_host(self, stack: str) -> Host:
        """Get host config for a stack (first host if multi-host)."""
        if stack not in self.stacks:
            msg = f"Unknown stack: {stack}"
            raise ValueError(msg)
        host_names = self.get_hosts(stack)
        return self.hosts[host_names[0]]

    def get_stack_dir(self, stack: str) -> Path:
        """Get stack directory path."""
        return self.compose_dir / stack

    def is_plugin_enabled(self, plugin_name: str) -> bool:
        """Return True when a plugin is explicitly enabled."""
        return plugin_name in self.plugins

    def get_compose_path(self, stack: str) -> Path:
        """Get compose file path for a stack (tries compose.yaml first).

        Note: This checks local filesystem. For remote execution, use
        get_stack_dir() and let docker compose find the file.
        """
        search_dirs: list[Path] = []

        if self.is_plugin_enabled("sync"):
            sync_cfg = self.get_plugin_config("sync")
            source_dir_raw = sync_cfg.get("source_dir")
            if isinstance(source_dir_raw, str) and source_dir_raw:
                source_root = Path(source_dir_raw).expanduser()
                if not source_root.is_absolute() and self.config_path:
                    source_root = (self.config_path.parent / source_root).resolve()
                search_dirs.append(source_root / stack)

        stack_dir = self.get_stack_dir(stack)
        search_dirs.append(stack_dir)

        for base_dir in search_dirs:
            for filename in COMPOSE_FILENAMES:
                candidate = base_dir / filename
                if candidate.exists():
                    return candidate

        # Default to compose.yaml if none exist (will error later)
        return search_dirs[0] / "compose.yaml"

    def discover_compose_dirs(self) -> set[str]:
        """Find all directories in compose_dir that contain a compose file."""
        found: set[str] = set()
        if not self.compose_dir.exists():
            return found
        for subdir in self.compose_dir.iterdir():
            if subdir.is_dir() and any((subdir / f).exists() for f in COMPOSE_FILENAMES):
                found.add(subdir.name)
        return found

    def get_web_stack(self) -> str:
        """Get web stack name from CF_WEB_STACK environment variable."""
        return os.environ.get("CF_WEB_STACK", "")

    def get_plugin_config(self, plugin_name: str) -> dict[str, Any]:
        """Return config dict for a plugin name."""
        return self.plugin_config.get(plugin_name, {})

    def get_local_host_from_web_stack(self) -> str | None:
        """Resolve the local host from the web stack configuration (container only).

        When running in the web UI container (CF_WEB_STACK is set), this returns
        the host that the web stack runs on. This is used for:
        - Glances connectivity (use container name instead of IP)
        - Container exec (local docker exec vs SSH)
        - File read/write (local filesystem vs SSH)

        Returns None if not in container mode or web stack is not configured.
        """
        if os.environ.get("CF_WEB_STACK") is None:
            return None
        web_stack = self.get_web_stack()
        if not web_stack or web_stack not in self.stacks:
            return None
        host_names = self.get_hosts(web_stack)
        if len(host_names) != 1:
            return None
        return host_names[0]


def _parse_hosts(raw_hosts: dict[str, Any]) -> dict[str, Host]:
    """Parse hosts from config, handling both simple and full forms."""
    hosts = {}
    for name, value in raw_hosts.items():
        if isinstance(value, str):
            # Simple form: hostname: address
            hosts[name] = Host(address=value)
        else:
            # Full form: hostname: {address: ..., user: ..., port: ...}
            hosts[name] = Host(**value)
    return hosts


def load_config(path: Path | None = None) -> Config:
    """Load configuration from YAML file.

    Search order:
    1. Explicit path if provided via --config
    2. CF_CONFIG environment variable
    3. ./compose-farm.yaml
    4. $XDG_CONFIG_HOME/compose-farm/compose-farm.yaml (defaults to ~/.config)
    """
    config_path = path or find_config_path()

    if config_path is None or not config_path.exists():
        msg = f"Config file not found. Searched: {', '.join(str(p) for p in config_search_paths())}"
        raise FileNotFoundError(msg)

    if config_path.is_dir():
        msg = (
            f"Config path is a directory, not a file: {config_path}\n"
            "This often happens when Docker creates an empty directory for a missing mount."
        )
        raise FileNotFoundError(msg)

    with config_path.open() as f:
        raw = yaml.safe_load(f)

    # Parse hosts with flexible format support
    raw["hosts"] = _parse_hosts(raw.get("hosts", {}))
    raw["config_path"] = config_path.resolve()

    return Config(**raw)
