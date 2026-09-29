# Plugin packages: install plugins from the config

## Problem

Installing plugins means remembering a long `uv tool install "compose-farm[web]==X" --with "... @ git+...@vX#subdirectory=..."` command, repeated on every upgrade. The config already says which plugins to enable, but not where they come from.

## Design

**Config.** New top-level `plugin_packages: list[str]` of pip requirement strings (PyPI names, `git+https://...#subdirectory=...`, local paths). An empty section (YAML null) means none. It is separate from `plugins:` because plugin options may be keyed by user names (`pin` keys by stack), so a reserved key inside them could collide.

```yaml
plugin_packages:
  - git+https://github.com/basnijholt/compose-farm#subdirectory=examples/plugins/pin
plugins:
  pin: {...}
```

**Command.** `cf plugins install [--config PATH]`:

1. Loads the config without loading plugins (`load_config(path, check_plugins=False)`), because the plugins are missing until installed.
2. No packages: warns and exits 0.
3. Runs `uv pip install --python <sys.executable> <packages>` when `uv` is on PATH, else `<sys.executable> -m pip install <packages>`. The command is echoed and streams output; a non-zero exit exits 1.
4. Invalidates import caches, loads the enabled plugins, and prints them (or the plugin error, exit 1).

Targeting `sys.executable` installs into the same environment that runs cf: the uv tool venv, a pipx venv, or a regular venv.

**Missing-plugin hint.** `load_plugins`' unknown-plugin error appends: "List their packages under plugin_packages and run `cf plugins install`." The CLI and the web UI config-error banner show it.

## Decisions

- **Additive install, not a uv receipt rewrite.** `uv tool install --with` replaces the with-list, so a persistent install would have to rebuild compose-farm's own requirement from uv's internal receipt format (registry, git, editable, directory variants). `uv tool upgrade compose-farm` resets the tool environment and drops the added packages; the next command then fails with the hint above, and `cf plugins install` restores them. Documented.
- **Explicit command, no auto-install.** Installing on load would put network installs inside any command, retry on every web UI request when it can't install (the Docker image has no uv or pip in the tool venv), and race between concurrent cf processes.
- **Docker unchanged.** The image already bundles the example plugins; for others, users build an image.

## Testing

- Config: `plugin_packages` parses; null becomes `[]`; `check_plugins=False` skips plugin loading.
- Loader: unknown-plugin error includes the hint.
- CLI (subprocess and `shutil.which` patched): uv command, pip fallback, failing installer exits 1, no packages warns, success prints the plugins.
- Manual: a throwaway uv tool install of this branch, config with a local example plugin path, `cf plugins install`, then `cf config validate`.
