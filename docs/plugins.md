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
    on_stack_removed: []
    preflight:
      - run: "test -r /run/agenix/{stack}.env"  # non-zero exit = preflight problem
    compose_args: ["--env-file", "/run/agenix/{stack}.env"]
```

- Each step is `run:` (runs on the host the hook is for) or `local:` (runs where `cf` runs).
- A failing step fails the hook (see [Hooks](#hooks) for what that means per hook). Preflight steps never fail the hook; each failing check is reported as a problem.
- Placeholders: `{stack}`, `{host}`, `{source_host}` (empty unless migrating), `{compose_dir}`, `{stack_dir}`. In `run`/`local` commands the values are shell-quoted. `compose_args` items are substituted as-is and each item is quoted when the compose command is built. Unknown placeholders are a config error.

### sync

Copies `compose_dir/<stack>/` from the machine running `cf` to the same path on the target host before every start, using rsync over compose-farm's SSH settings (key, `known_hosts`, port). This replaces NFS for compose files.

```yaml
plugins:
  sync:
    excludes: [".git", "*.tmp"]  # rsync --exclude patterns
    delete: true                 # rsync --delete (default)
```

- `compose_dir` must exist locally at the same path it has on the hosts.
- Hosts that are the local machine are skipped.
- `rsync` must be installed on the machine running `cf` and on the hosts.

## Hooks

| Hook | Called | On failure |
|------|--------|------------|
| `before_up` | Before preflight on the target host, on every start (`up`, `update`, `apply`, including `--service` and `--host`). During a migration the source is still running. | This stack is not started; the source is untouched |
| `preflight` | During preflight (`up`) and `cf check`. Must not change anything. | Returned problems are reported like missing paths |
| `after_source_stopped` | Migration only: the source is stopped, the target not started yet | Rollback: the stack is restarted on the source if it was running there |
| `after_up` | The stack started on the host (after the state update) | Warning only |
| `on_stack_removed` | An orphaned stack (removed from config) was stopped via `cf down --orphaned` or `cf apply`. Not called for strays or a plain `down` | The stack stays in the state file, so the next `cf down --orphaned`/`cf apply` retries |
| `compose_args` | Every time a compose command is built for a stack on a host (`up`, `down`, `ps`, `logs`, `pull`, `restart`, `compose`, ...) | The command fails |

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
from compose_farm.plugins import HookContext, Plugin, PluginError


class ZfsPlugin(Plugin):
    def __init__(self, options):
        super().__init__(options)
        self.parent = options.get("parent") or ""
        if not self.parent:
            raise PluginError("parent is required")

    async def preflight(self, ctx: HookContext) -> list[str]:
        result = await ctx.run(f"zfs list {self.parent}", stream=False, check=False)
        return [] if result.success else [f"dataset {self.parent} not found"]

    async def before_up(self, ctx: HookContext) -> None:
        await ctx.run(f"zfs create -p {self.parent}/{ctx.stack}")
```

```toml
# pyproject.toml of your package
[project.entry-points."compose_farm.plugins"]
zfs = "my_package:ZfsPlugin"
```

Install the package next to compose-farm (for example `uv tool install compose-farm --with my-package`) and enable it with `plugins: {zfs: {parent: tank/stacks}}`.

`HookContext` has:

- `cfg`: the loaded `Config`
- `stack`, `host`: the stack and the host this call is for (the target for up hooks)
- `source_host`: the previous host during a migration, otherwise `None`. It is set even if that host is no longer in the config.
- `await ctx.run(command, host=None, stream=True, check=True)`: run a shell command on `host` (default `ctx.host`) through compose-farm's SSH. With `check=True` a non-zero exit raises `PluginError`.
- `await ctx.run_local(command, stream=True, check=True)`: the same on the machine running `cf`.

Raise `PluginError` (or any exception) to fail a hook; the message is shown to the user.

Rules for plugins:

- **Idempotent**: a hook can run again for the same stack and host after a failure or retry.
- **Keep the source intact** in `before_up` and `after_source_stopped`. Irreversible cleanup of the source belongs in `after_up`.
- **Refuse when the source is unreachable**: if your plugin needs the source (for example to copy data) and `ctx.source_host not in ctx.cfg.hosts`, raise in `before_up` instead of starting from scratch.
- **No blocking calls**: hooks for different stacks run concurrently. Use `ctx.run`/`ctx.run_local` or asyncio subprocesses.
- **`compose_args` does no I/O**: it is called for every compose command.

## Recipes

### Secrets from agenix

agenix decrypts secrets on each host into `/run/agenix/`. Pass them to compose as an env file:

```yaml
plugins:
  commands:
    preflight:
      - run: "test -r /run/agenix/{stack}.env"
    compose_args: ["--env-file", ".env", "--env-file", "/run/agenix/{stack}.env"]
```

Passing any `--env-file` stops compose from reading `.env` implicitly, so list `.env` too if the stack has one (compose fails if a listed file is missing).

Alternatively, symlink the decrypted file into the stack directory in `before_up` (`ln -sfn /run/agenix/{stack}.env {stack_dir}/.env`).

### Per-stack ZFS datasets instead of NFS

A ZFS plugin can create a dataset per stack and move it when the stack migrates:

- `preflight`: check the parent dataset exists.
- `before_up`: without `source_host`, create the dataset if missing. With `source_host`, snapshot on the source and send it to the target while the source still runs (full the first time, incremental after).
- `after_source_stopped`: snapshot again and send the small incremental, so downtime is short.
- `after_up` with `source_host`: rename the source dataset out of the way (for example `<parent>/.retired/<stack>`), or destroy it if you opt in.
- `on_stack_removed`: retire the dataset the same way.

Pipe the transfer through the machine running `cf` (`ssh src zfs send ... | ssh dst zfs recv ...` via `ctx.run_local` and `compose_farm.executor.build_ssh_command`) so hosts do not need SSH access to each other. If `compose_dir/<stack>` is itself the dataset, compose files move with the data and `sync` is not needed.

If the target starts and writes data but then fails, the rollback restarts the source from its own data and the target's writes are discarded (the same as with NFS today).

## Security

`compose_args` must contain file paths and flags, **never secret values**. Compose commands are printed to the terminal, stored in the web UI's task logs, and visible in `ps` on the host.

The `commands` plugin runs shell commands from the config file. The config already controls SSH access to every host, so this adds no new trust boundary.

## Limitations

- Compose Farm parses each stack's compose file and `.env` locally for preflight paths, ports, and Traefik labels. That parsing does not see `compose_args`: variables that only exist in an extra env file are not visible there, and services, volumes, or labels added through extra `-f` files are not reflected in preflight or Traefik output.
- With per-stack datasets, volume paths do not exist on hosts that never ran the stack, so `cf check` reports them as missing there.
- Plugins cannot add CLI commands yet.
