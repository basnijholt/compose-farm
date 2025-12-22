Update demo recordings to match the current compose-farm.yaml configuration.

## Key Gotchas

1. **Never `git checkout` without asking** - check for uncommitted changes first
2. **Prefer `nas` stacks** - demos run locally on nas, SSH adds latency
3. **Terminal captures keyboard** - use `blur()` to release focus before command palette
4. **Clicking sidebar navigates away** - clicking h1 scrolls to top
5. **Buttons have icons, not text** - use `[data-tip="..."]` selectors
6. **`record.py` auto-restores config** - no manual cleanup needed after CLI demos

## Stacks Used in Demos

| Stack | CLI Demos | Web Demos | Notes |
|-------|-----------|-----------|-------|
| `audiobookshelf` | quickstart, migration, apply | - | Migrates nas→anton |
| `grocy` | update | navigation, stack, workflow, console | - |
| `immich` | logs, compose | shell | Multiple containers |
| `dozzle` | - | workflow | - |

## CLI Demos

**Files:** `docs/demos/cli/*.tape`

Check:
- `quickstart.tape`: `bat -r` line ranges match current config structure
- `migration.tape`: nvim keystrokes work, stack exists on nas
- `compose.tape`: exec commands produce meaningful output

Run: `python docs/demos/cli/record.py [demo]`

## Web Demos

**Files:** `docs/demos/web/demo_*.py`

Check:
- Stack names in demos still exist in config
- Selectors match current templates (grep for IDs in `templates/`)
- Shell demo uses command palette for ALL navigation

Run: `python docs/demos/web/record.py [demo]`

## Before Recording

```bash
# Check for uncommitted config changes
git -C /opt/stacks diff compose-farm.yaml

# Verify stacks are running
cf ps audiobookshelf grocy immich dozzle
```
