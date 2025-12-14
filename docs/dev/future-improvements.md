# Future Improvements

Low-priority improvements identified during code review. These are not currently causing issues but could be addressed if they become pain points.

## 1. State Module Efficiency (LOW)

**Current:** Every state operation reads and writes the entire file.

```python
def set_service_host(config, service, host):
    state = load_state(config)   # Read file
    state[service] = host
    save_state(config, state)    # Write file
```

**Impact:** With 87 services, this is fine. With 1000+, it would be slow.

**Potential fix:** Add batch operations:
```python
def update_state(config, updates: dict[str, str | None]) -> None:
    """Batch update: set services to hosts, None means remove."""
    state = load_state(config)
    for service, host in updates.items():
        if host is None:
            state.pop(service, None)
        else:
            state[service] = host
    save_state(config, state)
```

**When to do:** Only if state operations become noticeably slow.

---

## 2. Remote-Aware Compose Path Resolution (LOW)

**Current:** `config.get_compose_path()` checks if files exist on the local filesystem:

```python
def get_compose_path(self, service: str) -> Path:
    for filename in ("compose.yaml", "compose.yml", ...):
        candidate = service_dir / filename
        if candidate.exists():  # Local check!
            return candidate
```

**Why this works:** NFS/shared storage means local = remote.

**Why it could break:** If running compose-farm from a machine without the NFS mount, it returns `compose.yaml` (the default) even if `docker-compose.yml` exists on the remote host.

**Potential fix:** Query the remote host for file existence, or accept this limitation and document it.

**When to do:** Only if users need to run compose-farm from non-NFS machines.

---

## 3. Add Integration Tests for CLI Commands (MEDIUM)

**Current:** No integration tests for the actual CLI commands. Tests cover the underlying functions but not the Typer commands themselves.

**Potential fix:** Add integration tests using `CliRunner` from Typer:

```python
from typer.testing import CliRunner
from compose_farm.cli import app

runner = CliRunner()

def test_check_command_validates_config():
    result = runner.invoke(app, ["check", "--local"])
    assert result.exit_code == 0
```

**When to do:** When CLI behavior becomes complex enough to warrant dedicated testing.

---

## 4. Add Tests for operations.py (MEDIUM)

**Current:** Operations module has 30% coverage. Most logic is tested indirectly through test_sync.py.

**Potential fix:** Add dedicated tests for:
- `up_services()` with migration scenarios
- `preflight_check()`
- `check_host_compatibility()`

**When to do:** When adding new operations or modifying migration logic.

---

## 5. Consider Structured Logging (LOW)

**Current:** Operations print directly to console using Rich. This couples the operations module to the Rich library.

**Potential fix:** Use Python's logging module with a custom Rich handler:

```python
import logging

logger = logging.getLogger(__name__)

# In operations:
logger.info("Migrating %s from %s to %s", service, old_host, new_host)

# In cli.py - configure Rich handler:
from rich.logging import RichHandler
logging.basicConfig(handlers=[RichHandler()])
```

**Benefits:**
- Operations become testable without capturing stdout
- Logs can be redirected to files
- Log levels provide filtering

**When to do:** Only if console output coupling becomes a problem for testing or extensibility.

---

## Design Decisions to Keep

These patterns are working well and should be preserved:

1. **asyncio + asyncssh** - Solid async foundation
2. **Pydantic models** - Clean validation
3. **Rich for output** - Good UX
4. **Test structure** - Good coverage
5. **Module separation** - cli/operations/executor/compose pattern
6. **KISS principle** - Don't over-engineer
