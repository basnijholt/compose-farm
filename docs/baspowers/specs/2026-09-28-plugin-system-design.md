# Plugin System Design

**Date:** 2026-09-28
**Status:** Proposed
**Input:** Joe Weston's fork, branch `jbweston/feat/lifecycle-plugins` (12 commits, +2623 lines)

## Goal

Let users replace compose-farm's built-in assumptions (NFS-shared stack
directories, plaintext `.env` secrets) with their own infrastructure without
forking. Motivating cases from Joe's setup:

1. **Secrets via agenix**: secrets are age-encrypted in the repo, decrypted by
   the host OS into `/run/agenix/...`, and handed to containers instead of a
   plaintext `.env`.
2. **ZFS datasets instead of NFS**: each stack gets its own ZFS dataset,
   created on first deploy, moved with `zfs send | zfs recv` when the stack
   migrates, and retired when the stack is removed.
3. **rsync instead of NFS for compose files** (`sync` plugin in the fork).
4. **Arbitrary shell hooks** (`command-hooks` plugin in the fork).

## What the fork contains

The agenix and ZFS plugins are **not** in the fork (no other branches, repos,
gists or PRs). The branch contains only the framework plus two builtins. The
framework does expose seams that the builtins never use
(`compose_farm.plugin_config_models` entry points, CLI command registration),
which suggests the agenix/ZFS plugins live in a private package.

| Piece | What it does |
|-------|--------------|
| `plugins/types.py` | 16 `HookEvent`s (`pre_up`, `pre_stop_source`, `rollback_started`, ...), `HookContext`, `HookResult`, `blocking`/`warn` policy |
| `plugins/manager.py` | Entry-point discovery (`compose_farm.plugins`), dispatch, policy overrides, CLI registrar with name-collision check |
| `builtin/command_hooks.py` | Runs `/bin/sh -lc` commands **locally** per event with `{stack}`/`{source_host}`/`{target_host}` placeholders |
| `builtin/sync.py` | rsyncs a local stack tree to each host's `compose_dir/<stack>` |
| `operations.py` | ~30 `_dispatch_hook(...)` call sites across up/migrate/rollback/stop |
| `config.py` | `plugins: list[str]`, `plugin_config: dict`, hardcoded pydantic schemas for both builtins, entry-point scan inside a field validator |
| `cli/management.py` | `cf plugins` diagnostics command |

The branch merges cleanly into current `main`.

## Assessment

### Keep (ideas, and some code)

- **Entry-point discovery + explicit opt-in in config.** Installed is not
  enabled; config order is dispatch order.
- **Migration checkpoint placement.** Joe correctly found the one moment ZFS
  needs that the core cannot provide: *after the source is stopped, before the
  target starts* (final incremental send).
- **Hook failure after source shutdown triggers rollback**, and the fix that
  threads `was_running` into `_migrate_stack` (commit `0725fbd`).
- **Diagnostics** (`cf plugins`): fold into `cf check`.
- **Test scenarios** in `tests/test_operations.py` for migration and rollback
  ordering.

### Rewrite or drop

| Problem | Evidence | Consequence |
|---------|----------|-------------|
| Event list is checkpoints, not intent | 16 events; `post_stop_source` and `pre_start_target` fire back-to-back; `rollback_*` has no consumer | Large public API to freeze; plugin authors must know internals |
| Coverage is inconsistent | `pre_down`/`post_down` fire only in `_stop_stacks_on_hosts` (orphans/strays); plain `cf down` goes through `run_on_stacks` and fires nothing | Plugins silently miss events |
| No way to influence the compose invocation | Hooks only produce side effects | Secrets must be handled by mutating the stack dir (symlinks) instead of `--env-file`/`-f` |
| Blocking I/O in async code | `subprocess.run` inside hooks called from `asyncio.gather` | Parallel `cf up` serializes on every hook |
| Plugins reimplement SSH | `sync.py` shells out to `ssh user@host` | Ignores compose-farm's key, `known_hosts`, local-host detection |
| Core knows about specific plugins | `Config.get_compose_path` special-cases `sync.source_dir`; builtin schemas and the event `Literal` are duplicated in `config.py` | Plugin boundary leaks into core |
| Manager rebuilt per event | `HookManager.from_config(cfg)` inside every `_dispatch_hook` call | Entry points scanned and plugins re-instantiated dozens of times per `cf up` |
| Startup cost | `register_cli_commands(app)` at `compose_farm.cli` import loads every installed plugin | ~24 ms entry-point scan (+ plugin imports) on every `cf` call, including `--help`; violates the lazy-import rule |
| Two config keys | `plugins:` list + `plugin_config:` map | Redundant; typo in one silently ignores the other |
| Policy overrides | `plugin_config.<p>.policies` | Complexity with no concrete use case; core should own failure semantics |

Conclusion: salvage the ideas and tests, not the code. The framework needs a
smaller, intent-level API plus one capability the fork lacks (compose args).

## Approaches considered

1. **Merge the fork, then fix.** Fastest start, but fixing the problems above
   touches every file it adds; the 16-event API would become public before it
   is corrected.
2. **Intent-level hooks with a small custom dispatcher (chosen).** Six hooks
   derived from the four use cases. Plugins are classes with optional async
   methods receiving one context object. Everything is async-native.
3. **pluggy.** Same hooks as hookspecs. Gains signature validation and
   entry-point loading, but pluggy is sync-first (async impls return
   coroutines the caller must await and close), calls impls in LIFO order, and
   adds decorator ceremony and a dependency. Not worth it for six hooks.

## Design

### Configuration

One key. Mapping order is dispatch order. Values are the plugin's own options
(`null` allowed).

```yaml
plugins:
  sync:
    excludes: [".git"]
  zfs:
    parent: tank/stacks
  commands:
    preflight:
      - run: "test -r /run/agenix/{stack}.env"
    compose_args: ["--env-file", "/run/agenix/{stack}.env"]
```

`Config.plugins: dict[str, dict[str, Any] | None]`. `Config` does no
validation of plugin options and no entry-point scanning; each plugin
validates its own options when constructed.

### Plugin API

New package `src/compose_farm/plugins/`: `__init__.py` holds the API, loader
and dispatch; builtins live next to it as `commands.py` and `sync.py`.

```python
class Plugin:
    """Base class. Override any subset of hooks."""

    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options  # subclasses validate here, raise PluginError

    async def preflight(self, ctx: HookContext) -> list[str]:
        return []

    async def before_up(self, ctx: HookContext) -> None: ...
    async def after_source_stopped(self, ctx: HookContext) -> None: ...
    async def after_up(self, ctx: HookContext) -> None: ...
    async def on_stack_removed(self, ctx: HookContext) -> None: ...

    def compose_args(self, ctx: HookContext) -> list[str]:
        return []


@dataclass(frozen=True)
class HookContext:
    cfg: Config
    stack: str
    host: str                        # host this call concerns (target for up hooks)
    source_host: str | None = None   # set only during a migration

    async def run(self, command: str, *, host: str | None = None,
                  stream: bool = True) -> CommandResult:
        """Run a shell command on `host` (default: self.host) through
        compose-farm's executor (SSH keys, known_hosts, local detection,
        [stack@host] output prefix)."""


class PluginError(Exception):
    """Raised by plugins to fail a hook with a user-facing message."""
```

Plugins signal failure by raising. No result objects.

Forward compatibility: new hooks are added as base-class methods with no-op
defaults; new context data is added as fields on `HookContext`. Neither breaks
existing plugins.

### Hooks

| Hook | Called | On failure | Used by |
|------|--------|------------|---------|
| `before_up` | Per target host, **before** preflight, on every `up`/`update`/`apply` start. `source_host` is set when migrating (source still running). | Abort this stack (failed `CommandResult`); source untouched | ZFS create or initial send; sync rsync; agenix symlink approach |
| `preflight` | Inside `check_stack_requirements`, so during `up` and `cf check`. Must not mutate. | Returned strings reported like missing paths | ZFS pool exists; secret file readable |
| `after_source_stopped` | Migration only: source `down` succeeded, target not started | Roll back (restart source if it was running) | ZFS final incremental send |
| `after_up` | Target `up` succeeded. `source_host` set if this was a migration | Warning only | ZFS retire source dataset |
| `on_stack_removed` | After a successful `down` of an **orphaned** stack (removed from config) via `cf down --orphaned` / `cf apply`. Not fired for strays or plain `down` | Warning only | ZFS retire/destroy dataset |
| `compose_args` | Synchronously, whenever a compose command is built for (stack, host). No I/O. | Error aborts the command | agenix `--env-file`, extra `-f`, `--profile` |

Hooks run sequentially in config order within one stack. Different stacks
still run concurrently, so hooks must be async and use `ctx.run` (or
`asyncio` subprocesses), never blocking calls.

### Lifecycle flow

Single-host `up` with migration (`_up_single_stack`), new steps in bold:

1. **`before_up(host=target, source_host=current)`**
2. Preflight on target (core checks + **plugin `preflight`**)
3. Pull/build on target
4. `down` on source
5. **`after_source_stopped`**; on failure run existing
   `_cleanup_and_rollback` with the correct `was_running`
6. `up` on target (compose command includes **`compose_args`**)
7. On success: state update, then **`after_up(source_host=current)`**;
   on failure: existing rollback

Without migration (`_up_stack_simple`, `_up_multi_host_stack`): steps 1, 2,
6, 7 per host with `source_host=None`.

`stop_orphaned_stacks` passes a flag to `_stop_stacks_on_hosts` so it fires
`on_stack_removed` after each successful `down`; `stop_stray_stacks` does not.

`compose_args` is applied at the single choke point
`executor._build_compose_command` (gains `extra_args`), so every compose
invocation (`up`, `down`, `ps`, `logs`, `pull`, `restart`, `compose`
passthrough, `check_stack_running`) sees the same project. Callers pass
`(cfg, stack, host)` so the args can be computed. `_print_compose_command`
shows them.

The web UI runs `cf` as a subprocess, so it gets plugin behavior for free.

Not hooked (by design): `stop`, `restart`, plain `down`, `cf compose ... up`
passthrough. They still get `compose_args`.

### Loading

```python
def get_plugins(cfg: Config) -> tuple[Plugin, ...]:
    """Instantiate enabled plugins once per Config (cached on the instance)."""
```

- `cfg.plugins` empty: return `()` without importing `importlib.metadata`.
  Zero cost for users without plugins.
- Otherwise scan entry-point group `compose_farm.plugins` once, error if a
  configured name is missing (listing available names), instantiate in config
  order with its options. Import is lazy (`# noqa: PLC0415`, only needed when
  plugins are configured).
- Builtins register through the same entry-point group in `pyproject.toml`.
- `cf check` and `cf config validate` load plugins, so bad options surface
  early. `cf check` also lists loaded plugins and runs `preflight`.

### Error handling

- Blocking hooks (`before_up`, `after_source_stopped`, `preflight`,
  `compose_args`): exception becomes a failed `CommandResult` for that stack
  with message `plugin <name>.<hook>: <error>`; other stacks continue.
  `after_source_stopped` failure triggers rollback.
- Warning hooks (`after_up`, `on_stack_removed`): exception printed with
  `print_warning`, operation still succeeds.
- `KeyboardInterrupt`/`OperationInterruptedError` propagate unchanged.

### Security

- `compose_args` must contain **paths, never secret values**. Command lines
  are printed by `_print_compose_command`, stored in web task logs, and
  visible in `ps` on the remote host. Documented in the plugin guide.
- The `commands` plugin runs shell from the config file. The config is
  already trusted (it drives SSH to every host), so no new trust boundary.
- Destructive ZFS behavior (destroy on removal/migration) must be opt-in; the
  default retires datasets by renaming them.

### Builtin plugins

**`commands`** (replaces `command-hooks`): no-Python escape hatch.

```yaml
plugins:
  commands:
    before_up:
      - run: "mkdir -p /srv/data/{stack}"     # on ctx.host via compose-farm SSH
      - local: "./scripts/notify.sh {stack}"  # on the machine running cf
    after_source_stopped: [...]
    after_up: [...]
    on_stack_removed: [...]
    preflight:
      - run: "test -r /run/agenix/{stack}.env"  # non-zero exit = preflight error
    compose_args: ["--env-file", "/run/agenix/{stack}.env"]
```

Placeholders: `{stack}`, `{host}`, `{source_host}`, `{compose_dir}`,
`{stack_dir}`. In `run`/`local` commands, values are shell-quoted when
substituted. `compose_args` items are substituted verbatim and each finished
item is quoted by the core when the compose command is built. Unknown
placeholders are a config error at load time.

**`sync`** (rewrite of the fork's plugin): `before_up` rsyncs
`compose_dir/<stack>/` from the machine running `cf` to the same path on the
target host, using compose-farm's SSH options (key, `known_hosts`, port) via
`rsync -e`. Skipped for local hosts. Options: `excludes`, `delete`.
Keeps the "same path everywhere" invariant without NFS, so the fork's
`source_dir` option and the `get_compose_path` special case are dropped.

### How the motivating plugins map (for Joe, not shipped in phase 1)

**agenix** — two options, both supported:

- Config only: `commands.compose_args: ["--env-file", ".env", "--env-file",
  "/run/agenix/{stack}.env"]` plus a `preflight` readability check. Note that
  any `--env-file` disables compose's implicit `.env`, so list it explicitly
  if the stack also has one.
- Joe's symlink approach: `before_up` runs `ln -sfn /run/agenix/... ...` via
  `ctx.run` on the target host.

Caveat: compose-farm interpolates `.env` locally for Traefik labels and port
parsing; variables that live only in the agenix file are not visible there.
Fine for secrets, not for domains/ports.

**zfs**:

- `preflight`: `zfs list <parent>` on the host.
- `before_up`: no `source_host` and dataset absent: `zfs create -p`. With
  `source_host`: snapshot on source, full or incremental send to target while
  the source still runs.
- `after_source_stopped`: second snapshot, incremental send (short downtime).
- `after_up` with `source_host`: rename source dataset to
  `<parent>/.retired/<stack>` (or destroy if opted in); prune snapshots.
- `on_stack_removed`: same retirement.
- Host-to-host transfer can be relayed through the `cf` machine
  (`ssh src zfs send | ssh dst zfs recv`) using
  `executor.build_ssh_command`, so hosts need no mutual SSH trust.
- If `compose_dir/<stack>` is itself the dataset, compose files and data move
  together and `sync` is unnecessary.

Known limitation: if the state's previous host has been removed from config,
`source_host` is `None`, so a plugin cannot distinguish that from a first
deploy. The core already warns in this case.

## Testing

- Loader: missing plugin lists available names; options reach the plugin;
  config order preserved; no `importlib.metadata` import when `plugins` is
  empty.
- Dispatch: blocking hook failure yields failed `CommandResult` and other
  stacks proceed; warning hook failure does not fail the operation.
- Migration ordering with a recording fake plugin: exact hook sequence;
  `after_source_stopped` failure restarts the source only if it was running
  (port the fork's rollback tests).
- `on_stack_removed` fires for orphans, not strays.
- `compose_args` appear, quoted, in commands from every executor entry point.
- `commands`: placeholder rendering and quoting, `run` vs `local`, preflight
  exit codes, unknown placeholder rejected.
- `sync`: generated rsync argv includes compose-farm SSH options; local host
  skipped.

## Out of scope (deferred until a concrete need)

- **Plugin CLI commands** (`cf zfs status`). Registering them requires loading
  plugins at CLI import time on every invocation. Plugins can ship their own
  console script meanwhile. Revisit with a lazy Click group.
- **Plugin-managed paths in `cf check`.** With ZFS, volume paths legitimately
  do not exist on hosts that have never run the stack; `cf check` reports them
  as missing. A `managed_paths` hook could suppress this later.
- Apply-level hooks (`pre_apply`/`post_apply`), hooks for `stop`/`restart`/
  plain `down`, per-hook policy overrides, plugin-private state storage.
- Shipping `zfs` and `agenix` plugins: wait for Joe's private implementations,
  then decide builtin vs separate package.

## Phases

1. Core: `Config.plugins`, loader, `HookContext`, dispatch, the six hooks
   wired into `operations.py`/`executor.py`, `cf check` integration, docs.
2. Builtins: `commands`, `sync`.
3. `zfs` and `agenix` plugins, informed by Joe's code.
