---
icon: lucide/puzzle
---

# Plugins

Compose Farm assumes stack directories are shared over NFS and secrets live in `.env` files. Plugins let you replace those assumptions: copy compose files with rsync, give containers secrets decrypted by the host (agenix, sops-nix), or move per-stack ZFS datasets along with a migrating stack.

## Enabling plugins

Plugins are listed under `plugins:` in the config. Each key is a plugin name, each value its options (or nothing). Plugins run in the order they are listed.

```yaml
plugins:
  sync:
    excludes: [".git"]
  commands:
    compose_args: ["--env-file", "/run/agenix/{stack}.env"]
```

An unknown plugin name or invalid options make config loading fail, so `cf config validate` and `cf check` catch mistakes early. `cf check` also lists the enabled plugins.

## Installing plugins

List the packages that provide your plugins under `plugin_packages`. The next `cf` command that finds an enabled plugin missing installs them:

```yaml
plugin_packages:
  - github:basnijholt/compose-farm/examples/plugins/pin
plugins:
  pin:
    gitea: nas
```

Each entry is a pip requirement (a PyPI name, a `git+https://...` URL, or a local path) or the GitHub shorthand `github:OWNER/REPO/SUBDIR@REF`, where `/SUBDIR` and `@REF` (a branch, tag, or commit) are optional. Local paths are relative to the config file. They are installed into the Python environment that runs `cf`, with `uv pip install` when uv is on `PATH` and pip otherwise.

- **Automatic (default):** when an enabled plugin is missing, `cf` installs `plugin_packages` once and continues. The installer's output goes to stderr, so scripted output such as `cf list --simple` stays clean. This also restores the plugins after `uv tool upgrade compose-farm`, which recreates the tool's environment without them. If the install fails, `cf` stops with the plugin error and doesn't retry in the same process.
- **Explicit:** `cf plugins install` installs them now and shows the installer's output. Set `plugin_auto_install: false` to install only this way.

The config decides what gets installed and run, so treat `plugin_packages` like the `commands` plugin: only list sources you trust. With automatic installs, any command that loads the config can install packages, including read-only ones like `cf ps` and a `compose-farm.yaml` found in the current directory. Set `plugin_auto_install: false` if you run `cf` next to configs you don't trust.

The web UI installs missing plugins only when it starts, since an install during a request would block it. After adding a plugin, restart it or run `cf plugins install`.

Plugins run wherever `cf` runs, including the web UI (which runs `cf` for its actions). The Docker image includes the builtin plugins, the [example plugins](#example-plugins), and uv (so `local:` steps can run `uv run --script` scripts that work with the image's Python). Automatic installs of other plugins work in the container only when it runs as root and for sources that don't need git (e.g. PyPI names, local paths), and they are repeated whenever the container is recreated; for other plugins, build your own image that installs them next to compose-farm (`uv tool install "compose-farm[web]" --with <plugin>`).

## Builtin plugins

### commands

Runs shell commands from the config at lifecycle hooks and adds extra `docker compose` arguments. No Python needed.

```yaml
plugins:
  commands:
    before_up:
      - run: "mkdir -p /srv/data/{stack}"     # on the hook's host, over compose-farm's SSH
      - local: "./scripts/notify.sh {stack}"  # on the machine running cf
    after_source_stopped: []
    after_up: []
    after_stack_removed: []
    preflight:
      - run: "test -r /run/agenix/{stack}.env"  # non-zero exit = preflight problem
    compose_args: ["--env-file", "/run/agenix/{stack}.env"]
    after_changes:                              # once per cf command, on the machine running cf
      - local: "cd {compose_dir} && ./scripts/kuma-sync.py sync --apply"
```

- Each step is `run:` (runs on the host the hook is for) or `local:` (runs where `cf` runs). `after_changes` steps are always `local:` because that hook is not tied to a host.
- A failing step fails the hook (see [Hooks](#hooks) for what that means per hook). Preflight steps never fail the hook; each failing check is reported as a problem.
- Placeholders: `{stack}`, `{host}`, `{source_host}` (empty unless migrating), `{compose_dir}`, `{stack_dir}`. In `run`/`local` commands the values are inserted already shell-quoted, so don't wrap placeholders in quotes yourself (`echo {stack}`, not `echo '{stack}'`). `compose_args` items are substituted as-is and each item is quoted when the compose command is built. `after_changes` steps have `{stacks}` (the changed stacks, each quoted) and `{compose_dir}` instead. Unknown placeholders, conversions (`{stack!r}`), and format specs (`{stack:>9}`) are config errors.

### sync

Copies `compose_dir/<stack>/` from the machine running `cf` to the same path on the target host before every start, using rsync over compose-farm's SSH settings (key, `known_hosts`, port). This replaces NFS for compose files.

```yaml
plugins:
  sync:
    excludes: [".git", "*.tmp"]  # rsync --exclude patterns
    delete: false                # rsync --delete (default: false)
```

- `compose_dir` must exist locally at the same path it has on the hosts.
- Hosts that are the local machine are skipped.
- `rsync` must be installed on the machine running `cf` (the Docker image includes it) and on the hosts.
- **The local copy is the source of truth.** Every file that exists locally replaces the host's version whenever they differ, even if the host's file is newer. Keep runtime data (`./data`, `./config` bind mounts) and host-specific files (a per-host `.env`) out of the stack directory, or list them in `excludes`.
- **Careful with `delete: true`**: it also removes every file on the host that is not in the local copy, including such data and host-only files. Only enable it together with `excludes` for those paths.

## Hooks

Hooks run in config order. The first failure stops the chain for that call, except for `preflight` (problems from every plugin are collected), `after_up` and `after_changes` (every plugin still runs; failures are warnings).

| Hook | Called | On failure |
|------|--------|------------|
| `before_up` | Before preflight on the target host, on every start (`up`, `update`, `apply`, including `--service` and `--host`). During a migration the source is still running. | This stack is not started; the source is untouched |
| `preflight` | During preflight (`up`) and `cf check`. Must not change anything. | Returned problems are reported like missing paths |
| `after_source_stopped` | Migration only: the source is stopped, the target not started yet | Rollback: the stack is restarted on the source if it was running there |
| `after_up` | The stack started on the host (after the state update) | Warning only |
| `after_stack_removed` | An orphaned stack (removed from config) was stopped via `cf down --orphaned` or `cf apply`. Not called for strays or a plain `down` | Later plugins are skipped, and the stack stays in the state file, so the next `cf down --orphaned`/`cf apply` retries |
| `after_changes` | Once per `cf up`/`update`, `down` (including `--orphaned`), or `apply` that started, moved, or stopped stacks, after all of them. Gets a `ChangesContext` with the changed `stacks`. For plugins that act on the whole deployment (DNS records, monitors) | Warning only |
| `compose_args` | Every time a compose command is built for a stack on a host (`up`, `down`, `ps`, `logs`, `pull`, `restart`, `compose`, ...) | On start, the stack fails before anything is stopped or started; other commands abort with the error |

Migrating a stack runs:

1. `before_up` on the target (`source_host` set, source still running)
2. Preflight on the target
3. Pull and build on the target
4. `docker compose down` on the source
5. `after_source_stopped`
6. `docker compose up -d` on the target
7. `after_up` on the target (`source_host` set)

Multi-host stacks run `before_up` and preflight on every host before starting any host, then `after_up` for each host that started. They never migrate, so `source_host` is always empty.

`stop`, `restart`, plain `down`, and `cf compose` do not run lifecycle hooks, but they do get `compose_args`.

## Writing a plugin

A plugin is a Python class registered under the `compose_farm.plugins` entry-point group. Override any subset of the hooks.

```python
import shlex

from compose_farm.plugins import HookContext, Plugin, PluginError


class DataDirPlugin(Plugin):
    """Create /srv/data/<stack> on the target host before each start."""

    def __init__(self, options):
        super().__init__(options)
        self.root = options.get("root", "/srv/data")
        if not self.root.startswith("/"):
            raise PluginError("root must be an absolute path")

    async def preflight(self, ctx: HookContext) -> list[str]:
        result = await ctx.run(f"test -w {shlex.quote(self.root)}", stream=False, check=False)
        return [] if result.success else [f"{self.root} is not writable"]

    async def before_up(self, ctx: HookContext) -> None:
        await ctx.run(f"mkdir -p {shlex.quote(f'{self.root}/{ctx.stack}')}", stream=False)
```

```toml
# pyproject.toml of your package
[project.entry-points."compose_farm.plugins"]
datadir = "my_package:DataDirPlugin"
```

List the package under [`plugin_packages`](#installing-plugins) and enable it with `plugins: {datadir: {root: /srv/data}}`; `cf` installs it on its next run. See [Example plugins](#example-plugins) for complete ones.

`HookContext` has:

- `cfg`: the loaded `Config`
- `stack`, `host`: the stack and the host this call is for (the target for up hooks)
- `source_host`: the previous host during a migration, otherwise `None`. It is set even if that host is no longer in the config.
- `await ctx.run(command, host=None, stream=True, check=True)`: run a shell command on `host` (default `ctx.host`) through compose-farm's SSH. With `check=True` a non-zero exit raises `PluginError`.
- `await ctx.run_local(command, stream=True, check=True)`: the same on the machine running `cf`.

`after_changes` gets a `ChangesContext` instead, with `cfg`, `stacks` (the stacks the command started, moved, or stopped), `await ctx.run(command, host=..., ...)` (the host is required), and `await ctx.run_local(...)`.

Raise `PluginError` (or any exception) to fail a hook; the message is shown to the user.

Rules for plugins:

- **Idempotent**: a hook can run again for the same stack and host after a failure or retry.
- **Keep the source intact** in `before_up` and `after_source_stopped`. Irreversible cleanup of the source belongs in `after_up`.
- **Refuse when the source is unreachable**: if your plugin needs the source (for example to copy data) and `ctx.source_host not in ctx.cfg.hosts`, raise in `before_up` instead of starting from scratch.
- **Don't treat a missing `source_host` as proof of a first deploy**: `cf down` removes the stack from the state file, so a later `cf up` on another host has no `source_host`. A data-moving plugin should check whether the data already exists elsewhere before creating it empty.
- **`after_stack_removed` needs the stack directory**: it runs after `docker compose down` succeeds in that directory, and a retry runs `down` again. If your plugin removes or renames the directory, list it last, so a failure in another plugin does not leave the stack stuck in the state file.
- **No blocking calls**: hooks for different stacks run concurrently. Use `ctx.run`/`ctx.run_local` or asyncio subprocesses.
- **`compose_args` stays cheap**: it is called for every compose command, so no remote commands or slow work there.

## Example plugins

Complete, installable plugins live in [`examples/plugins/`](https://github.com/basnijholt/compose-farm/tree/main/examples/plugins). Use them as-is or as a starting point:

| Plugin | What it does |
|--------|--------------|
| [agenix](https://github.com/basnijholt/compose-farm/tree/main/examples/plugins/agenix) | Passes host-decrypted secret env files (`/run/agenix/...`) to compose as `--env-file` for the stacks you list, and checks during preflight that every secret exists on the target host |
| [zfs](https://github.com/basnijholt/compose-farm/tree/main/examples/plugins/zfs) | A ZFS dataset per stack: created on first deploy, moved with `zfs send`/`recv` on migration (live send, then a short final incremental), retired on removal. A `storage_host` mode keeps all datasets on one NAS instead |
| [pin](https://github.com/basnijholt/compose-farm/tree/main/examples/plugins/pin) | Keeps stacks on the host they must run on (static IPs, USB devices, GPUs): starting one elsewhere fails with the reason, and `cf check` flags a pin that no longer matches `stacks:` |
| [traefik-dns](https://github.com/basnijholt/compose-farm/tree/main/examples/plugins/traefik-dns) | After every change, writes one DNS record per Traefik `Host()` name under a domain into a managed block (Headscale `extra_records` or hosts lines) and restarts the reading stack if the records changed |
| [traefik-policy](https://github.com/basnijholt/compose-farm/tree/main/examples/plugins/traefik-policy) | Checks Traefik router labels in preflight, e.g. that a router on a public entrypoint is also on `websecure` |

To use one, list it under `plugin_packages` and enable it under `plugins:`:

```yaml
plugin_packages:
  - github:basnijholt/compose-farm/examples/plugins/zfs
```

Without installing anything, the `commands` plugin covers simple cases. For example, a secret env file for every stack:

```yaml
plugins:
  commands:
    preflight:
      - run: "test -r /run/agenix/{stack}.env"
    compose_args: ["--env-file", ".env", "--env-file", "/run/agenix/{stack}.env"]
```

`compose_args` from `commands` apply to **every** stack, so every stack then needs both `.env` and `/run/agenix/<stack>.env` (compose fails if a listed file is missing). The agenix example plugin avoids that by only touching the stacks you list.

More `commands` recipes:

```yaml
plugins:
  commands:
    before_up:
      # Remount NFS after the NAS rebooted (needs passwordless sudo for mount)
      - run: "mountpoint -q /mnt/data || sudo mount /mnt/data"
      # Create the shared Docker network if this host doesn't have it yet
      - run: "docker network inspect mynetwork >/dev/null 2>&1 || docker network create --subnet 172.20.0.0/16 --gateway 172.20.0.1 mynetwork"
    after_changes:
      # Sync Uptime Kuma monitors once per command, not once per stack
      - local: "cd {compose_dir} && ./scripts/kuma-sync.py sync --apply"
```

## Security

`compose_args` must contain file paths and flags, **never secret values**. Compose commands are printed to the terminal, stored in the web UI's task logs, and visible in `ps` on the host.

The `commands` plugin runs shell commands from the config file. The config already controls SSH access to every host, so this adds no new trust boundary.

## Limitations

- Compose Farm parses each stack's compose file and `.env` locally for preflight paths, ports, and Traefik labels. That parsing does not see `compose_args`: variables that only exist in an extra env file are not visible there, and services, volumes, or labels added through extra `-f` files are not reflected in preflight or Traefik output.
- Avoid `-f` and `-p` in `compose_args`: a single `-f` replaces compose's file discovery (list the stack's own compose file too), and `-p` changes the project name, which `cf refresh` and stray detection rely on (they expect the directory name).
- `cf check` runs preflight without running `before_up` first. Paths that a plugin creates on start are therefore reported as missing on hosts that never ran the stack: `compose_dir` with `sync`, and volume paths with per-stack datasets. `cf up` creates them.
- Plugins cannot add CLI commands yet.
