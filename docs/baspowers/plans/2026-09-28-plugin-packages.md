# Plugin Packages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use baspowers:subagent-driven-development (recommended) or baspowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the config list plugin packages and `cf plugins install` install them into the Python environment running cf.

**Architecture:** A new `plugin_packages: list[str]` config field; `load_config(..., check_plugins=False)` reads the config before the plugins exist; a `cf plugins install` Typer sub-app runs `uv pip install --python sys.executable` (pip fallback) and then reloads the config to verify the plugins load.

**Tech Stack:** Python 3.11+, pydantic, Typer, pytest, uv.

**Spec:** `docs/baspowers/specs/2026-09-28-plugin-packages-design.md`

## Global Constraints

- Imports at top level; `subprocess`, `shutil`, `shlex`, `importlib`, `sys` are already loaded at cf startup, so top-level imports cost nothing. Never import pydantic or `compose_farm.config`/`compose_farm.plugins` at module level in CLI modules (use the existing lazy `load_config_or_exit`).
- Hint text, verbatim: `List their packages under plugin_packages and run \`cf plugins install\`.`
- No auto-install on load. No uv receipt edits. Docker image unchanged.
- The spec and plan docs are deleted before the PR.

---

### Task 1: Config field, loader hint, `check_plugins`

**Files:**
- Modify: `src/compose_farm/config.py` (field after `plugins`, validator, `load_config`)
- Modify: `src/compose_farm/plugins/__init__.py` (`load_plugins` unknown-plugin message)
- Modify: `src/compose_farm/cli/common.py` (`load_config_or_exit`)
- Test: `tests/test_plugins.py` (class `TestLoader`)

**Interfaces:**
- Produces: `Config.plugin_packages: list[str]`; `load_config(path: Path | None = None, *, check_plugins: bool = True) -> Config`; `load_config_or_exit(config_path: Path | None, *, check_plugins: bool = True) -> Config`.

- [ ] **Step 1: Write the failing tests** in `TestLoader`:

```python
    def test_unknown_plugin_hints_at_plugin_packages(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"web": "h1"})
        cfg.plugins = {"nope": None}
        with (
            _entry_points(),
            pytest.raises(PluginError, match=r"run `cf plugins install`"),
        ):
            load_plugins(cfg)

    def test_plugin_packages_parse_and_allow_null(self, tmp_path: Path) -> None:
        path = tmp_path / "compose-farm.yaml"
        base = "compose_dir: /opt/compose\nhosts: {h1: localhost}\nstacks: {web: h1}\n"
        path.write_text(base + "plugin_packages: [compose-farm-pin, ./local]\n")
        assert load_config(path).plugin_packages == ["compose-farm-pin", "./local"]
        path.write_text(base + "plugin_packages:\n")
        assert load_config(path).plugin_packages == []

    def test_load_config_can_skip_plugins(self, tmp_path: Path) -> None:
        path = tmp_path / "compose-farm.yaml"
        path.write_text(
            "compose_dir: /opt/compose\n"
            "hosts: {h1: localhost}\n"
            "stacks: {web: h1}\n"
            "plugins: {nope: null}\n"
        )
        with patch("importlib.metadata.entry_points", side_effect=AssertionError):
            assert load_config(path, check_plugins=False).plugins == {"nope": None}
```

- [ ] **Step 2: Run** `uv run pytest tests/test_plugins.py -k "hints or plugin_packages or skip_plugins" -q` — expect 3 failures.

- [ ] **Step 3: Implement.**

`config.py`, after the `plugins` field:

```python
    # Pip requirements providing plugins; `cf plugins install` installs them next to cf
    plugin_packages: list[str] = Field(default_factory=list)
```

Validator next to `empty_plugins`:

```python
    @field_validator("plugin_packages", mode="before")
    @classmethod
    def empty_plugin_packages(cls, value: Any) -> Any:
        """Treat an empty ``plugin_packages:`` section (YAML null) as none."""
        return [] if value is None else value
```

`load_config(path: Path | None = None, *, check_plugins: bool = True)`; docstring line "Pass check_plugins=False to read a config whose plugins aren't installed yet."; end:

```python
    config = Config(**raw)
    if check_plugins:
        config.get_plugins()  # Fail early on unknown plugins or invalid plugin options
    return config
```

`plugins/__init__.py`, unknown-plugin message:

```python
        msg = (
            f"Unknown plugin(s): {', '.join(missing)} (available: {names}). "
            "List their packages under plugin_packages and run `cf plugins install`."
        )
```

`cli/common.py`:

```python
def load_config_or_exit(config_path: Path | None, *, check_plugins: bool = True) -> Config:
    ...
        return load_config(config_path, check_plugins=check_plugins)
```

- [ ] **Step 4: Run** `uv run pytest tests/test_plugins.py tests/web/test_plugin_config_error.py -q` — all pass (existing `match=r"Unknown plugin\(s\): nope \(available: known\)"` still matches).

- [ ] **Step 5: Commit** `feat(config): add plugin_packages and hint at cf plugins install`.

### Task 2: `cf plugins install`

**Files:**
- Create: `src/compose_farm/cli/plugins.py`
- Modify: `src/compose_farm/cli/__init__.py` (import `plugins`)
- Test: `tests/test_cli_plugins.py`

**Interfaces:**
- Consumes: `load_config_or_exit(config_path, *, check_plugins=...)`, `Config.plugin_packages`.
- Produces: `plugins_app` registered as `cf plugins`, command `install`; helper `_install_command(packages: list[str]) -> list[str]`.

- [ ] **Step 1: Write the failing tests** (`tests/test_cli_plugins.py`):

```python
"""Tests for cf plugins install."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from compose_farm.cli.app import app

runner = CliRunner()


def _config(tmp_path: Path, extra: str) -> Path:
    path = tmp_path / "compose-farm.yaml"
    path.write_text("compose_dir: /opt/compose\nhosts: {h1: localhost}\nstacks: {web: h1}\n" + extra)
    return path


def _install(path: Path, *, uv: str | None, returncode: int = 0) -> tuple[object, list[list[str]]]:
    calls: list[list[str]] = []

    def fake_run(command: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        assert check is False
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode)

    with (
        patch("compose_farm.cli.plugins.shutil.which", return_value=uv),
        patch("compose_farm.cli.plugins.subprocess.run", side_effect=fake_run),
    ):
        result = runner.invoke(app, ["plugins", "install", "-c", str(path)])
    return result, calls


def test_installs_with_uv_into_this_python(tmp_path: Path) -> None:
    path = _config(tmp_path, "plugin_packages: [pkg-a, ./b]\nplugins: {commands: null}\n")
    result, calls = _install(path, uv="/usr/bin/uv")
    assert result.exit_code == 0, result.output
    assert calls == [["/usr/bin/uv", "pip", "install", "--python", sys.executable, "pkg-a", "./b"]]
    assert "Plugins: commands" in result.output


def test_falls_back_to_pip(tmp_path: Path) -> None:
    path = _config(tmp_path, "plugin_packages: [pkg-a]\n")
    result, calls = _install(path, uv=None)
    assert result.exit_code == 0, result.output
    assert calls == [[sys.executable, "-m", "pip", "install", "pkg-a"]]


def test_reads_config_whose_plugins_are_missing(tmp_path: Path) -> None:
    path = _config(tmp_path, "plugin_packages: [pkg-a]\nplugins: {nope: null}\n")
    result, calls = _install(path, uv="/usr/bin/uv")
    assert len(calls) == 1
    assert result.exit_code == 1
    assert "Unknown plugin(s): nope" in result.output


def test_failing_installer_exits_nonzero(tmp_path: Path) -> None:
    path = _config(tmp_path, "plugin_packages: [pkg-a]\n")
    result, _ = _install(path, uv="/usr/bin/uv", returncode=2)
    assert result.exit_code == 1


def test_no_packages_warns(tmp_path: Path) -> None:
    result, calls = _install(_config(tmp_path, ""), uv="/usr/bin/uv")
    assert result.exit_code == 0
    assert calls == []
    assert "No plugin_packages" in result.output
```

- [ ] **Step 2: Run** `uv run pytest tests/test_cli_plugins.py -q` — expect failures (no `plugins` command).

- [ ] **Step 3: Implement** `src/compose_farm/cli/plugins.py`:

```python
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
    console.print(f"[dim]$ {shlex.join(command)}[/]")
    if subprocess.run(command, check=False).returncode != 0:
        print_error("Installing plugin_packages failed")
        raise typer.Exit(1)
    importlib.invalidate_caches()  # Let the entry-point scan see the new packages
    cfg = load_config_or_exit(config)  # Exits if an enabled plugin is still missing
    print_success(f"Plugins: {', '.join(cfg.plugins) or 'none enabled'}")


app.add_typer(plugins_app, name="plugins", rich_help_panel="Configuration")
```

Add `plugins,  # noqa: F401` to the import list in `cli/__init__.py` (alphabetical, after `monitoring`).

- [ ] **Step 4: Run** `uv run pytest tests/test_cli_plugins.py tests/test_plugins.py -q` — pass.

- [ ] **Step 5: Commit** `feat(cli): add cf plugins install`.

### Task 3: Docs

**Files:**
- Modify: `docs/plugins.md` (install paragraphs near lines 23, 129, 165-170)
- Modify: `docs/configuration.md` (new `### plugin_packages` after `### plugins`)
- Modify: `src/compose_farm/example-config.yaml` (commented `plugin_packages` example next to plugins, if a plugins block exists; otherwise after `glances_stack`)
- Modify: `examples/plugins/*/README.md` (install snippets), `examples/README.md` if it mentions install
- Modify: `README.md` (plugins paragraph; regenerate help output blocks), `docs/commands.md` (add `plugins install`), `AGENTS.md` (architecture tree `cli/plugins.py`, commands table row `plugins`)

- [ ] **Step 1:** Replace `uv tool install compose-farm --with ...` install instructions with:

```yaml
plugin_packages:
  - git+https://github.com/basnijholt/compose-farm#subdirectory=examples/plugins/zfs
```

then `cf plugins install`. State once in `docs/plugins.md`: uses uv when available (else pip), installs into the environment running cf, re-run after `uv tool upgrade compose-farm`; the Docker image already includes the example plugins and other plugins need a custom image.

- [ ] **Step 2:** Regenerate README help blocks with the repo's markdown-code-runner hook (`uv run markdown-code-runner README.md` or the prek hook), and check `docs/commands.md` structure to add `cf plugins install`.

- [ ] **Step 3: Verify** `uv run prek run --all-files` (or `just lint`) and `uv run pytest -m "not browser" -n auto -q`.

- [ ] **Step 4: Commit** `docs: install plugins with plugin_packages and cf plugins install`.

### Task 4: Manual end-to-end check and PR

- [ ] **Step 1:** Throwaway uv tool env: `UV_TOOL_DIR=$T/tools UV_TOOL_BIN_DIR=$T/bin uv tool install $PWD`; config with `plugin_packages: [$PWD/examples/plugins/pin]` and `plugins: {pin: {}}`; run `$T/bin/cf plugins install -c cfg.yaml` then `$T/bin/cf config validate -p cfg.yaml`; confirm `Plugins: pin`. Then `uv tool upgrade` wipes it and cf prints the hint.
- [ ] **Step 2:** Delete `docs/baspowers/`, commit, push, open PR (no unchecked checklists), link it via `link_pull_request`.
