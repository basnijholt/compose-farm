Update demo recordings to match the current compose-farm.yaml configuration.

## Critical: Initial State

**Before recording any demos**, ensure stacks are in their expected state:

```bash
# Verify stacks are on correct hosts
cf ps audiobookshelf grocy immich

# If audiobookshelf is on anton (from previous migration demo), reset it:
cf down audiobookshelf  # stops on wrong host
cf up audiobookshelf    # starts on nas (per config)
```

The migration demo moves `audiobookshelf` from `nas` → `anton`. After recording:
- `record.py` reverts the config change via sed
- Then runs `cf apply` to reset running state

**Important for AI agents:**

Before suggesting ANY reset commands, you MUST:
1. Run `git -C /opt/stacks diff compose-farm.yaml` to check for uncommitted changes
2. If there are changes, TELL the user what changes exist and ASK if they want to proceed
3. NEVER run `git checkout` without explicit user confirmation
4. Prefer targeted `sed` commands that only revert demo-specific changes over `git checkout`

## Stack Selection: Prefer `nas`

Demos are recorded from `nas`, so prefer stacks hosted there (no SSH latency):

| Host | Latency | Use for demos? |
|------|---------|----------------|
| `nas` | None (local) | ✅ Preferred |
| `nuc`, `hp`, `anton` | SSH overhead | ⚠️ Avoid if possible |

Current `nas` stacks (good choices): `grocy`, `immich`, `audiobookshelf`, `dozzle`, `glances`, `jellyfin`, `paperless-ngx`

## Quick Reference: Stacks Used in Demos

| Stack | CLI Demos | Web Demos | Host | OK? |
|-------|-----------|-----------|------|-----|
| `audiobookshelf` | quickstart, migration | - | nas | ✅ |
| `grocy` | update | navigation, stack, workflow, console, shell | nas | ✅ |
| `immich` | logs | navigation | nas | ✅ |
| `dozzle` | - | workflow | nas | ✅ |

## CLI Demos (docs/demos/cli/)

### 1. Analyze Current Config

Read `/opt/stacks/compose-farm.yaml` and identify:

- Line range for `hosts:` section (from `compose_dir:` to line before `stacks:`)
- Line range for `stacks:` section (first ~15-20 stacks for readability)
- Verify stacks used in demos exist and are on expected hosts

### 2. Update quickstart.tape

Update `bat -r` line ranges:

```tape
# Line ~24: Show hosts section (compose_dir + hosts)
Type "bat -r 1:16 compose-farm.yaml"

# Line ~34: Show stacks section
Type "bat -r 17:35 compose-farm.yaml"
```

Verify sed command (~line 73) uses a stack on `nas`:
```tape
Type "sed -i 's/audiobookshelf: nas/audiobookshelf: anton/' compose-farm.yaml"
```

### 3. Update migration.tape

Verify:
- Stack name exists and is assigned to `nas`
- Target host `anton` is available
- nvim keystrokes still work (search, change word)

### 4. Verify Other CLI Tapes

| Tape | Stack | Check |
|------|-------|-------|
| `logs.tape` | immich | Exists, running |
| `update.tape` | grocy | Exists |
| `apply.tape` | (none) | - |
| `install.tape` | (none) | - |

### 5. Prerequisites

Before recording CLI demos, verify:

1. **Check for uncommitted changes** (warn user, don't block):
   ```bash
   git -C /opt/stacks diff compose-farm.yaml
   ```
   If changes exist, inform the user and confirm before proceeding.

2. **Required tools installed**: `vhs`, `bat`, `nvim`

3. **Stacks running**: `cf ps audiobookshelf immich grocy`

## Web Demos (docs/demos/web/)

### 1. Verify Stack Names

Search for hardcoded stacks in demo files:
```bash
grep -r "grocy\|immich\|dozzle" docs/demos/web/
```

Update if stacks are renamed or removed.

### 2. Verify UI Selectors

Check these selectors still exist in templates:

| Selector | Used In | Purpose |
|----------|---------|---------|
| `#sidebar-stacks` | conftest.py | Wait for sidebar |
| `#cmd-palette` | conftest.py | Command palette |
| `#cmd-input` | All demos | Palette input |
| `#console-terminal` | console, workflow | Terminal input |
| `#compose-editor` | stack, workflow | Monaco editor |
| `#terminal-output` | stack, workflow | Action output |
| `#theme-btn` | workflow, themes | Theme picker |
| `[data-tip="Open shell"]` | shell | Container shell button |

**Note**: Container action buttons use icons only, not text. Select by `data-tip` attribute:
```python
# Wrong - buttons have no text
page.locator("#containers-list button", has_text="Shell")

# Correct - use tooltip attribute
page.locator('#containers-list [data-tip="Open shell"]')
```

### 3. Prerequisites

Before recording web demos:
```bash
# Required tools
which ffmpeg
playwright install chromium  # or use system chromium

# Stacks should be running (for realistic output)
cf ps grocy immich dozzle
```

## Testing

```bash
# Test single CLI demo
python docs/demos/cli/record.py quickstart

# Test single web demo
python docs/demos/web/record.py navigation
```

## Output

List all changes made with file:line and before/after values:

```
quickstart.tape:24  bat -r 1:11  ->  bat -r 1:16
quickstart.tape:34  bat -r 13:30 ->  bat -r 17:35
demo_workflow.py:132  mealie  ->  dozzle
```
