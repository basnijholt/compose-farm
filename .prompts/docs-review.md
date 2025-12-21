Review all documentation in this repository for accuracy, completeness, and consistency. Cross-reference documentation against the actual codebase to identify issues.

## Scope

Review all documentation files:
- docs/*.md (primary documentation)
- README.md (repository landing page)
- CLAUDE.md (development guidelines)
- examples/README.md (example configurations)

## Quick Reference: Code ↔ Docs Mapping

| Documentation | Verify Against |
|---------------|----------------|
| docs/commands.md | `cf <cmd> --help` for each command |
| docs/configuration.md | `src/compose_farm/config.py` (Pydantic models, `COMPOSE_FILENAMES`) |
| docs/architecture.md | `ls src/compose_farm/` and `ls src/compose_farm/cli/` |
| docs/web-ui.md | Actual web UI behavior and `src/compose_farm/web/` |
| README.md (help blocks) | `uv run markdown-code-runner README.md` |
| Installation claims | `pyproject.toml` (`requires-python`, `[project.optional-dependencies]`) |
| State/log file info | `src/compose_farm/state.py`, `src/compose_farm/logs.py` |

## Review Checklist

### 0. Quick Checks First

Run these commands before the deep review to catch obvious issues:

```bash
# Recent changes that might need doc updates
git log --oneline -20

# Compare command list to docs
cf --help

# Check for new/changed options on all commands
for cmd in up down stop pull restart update apply compose logs ps stats check refresh init-network traefik-file web; do
  echo "=== cf $cmd ===" && cf $cmd --help
done

# Check subcommands
cf config --help && cf ssh --help
for sub in init show path validate edit symlink; do cf config $sub --help; done
for sub in setup status keygen; do cf ssh $sub --help; done
```

### 1. Command Documentation

For each documented command in `docs/commands.md`, verify against CLI help:

- Command exists and description matches
- **All options are documented** with correct names, types, and defaults
- Short options (-x) match long options (--xxx)
- Examples would work as written
- Check for undocumented commands or options

**Common gotchas:**
- Subcommands (`cf config`, `cf ssh`) have their own options - check each subcommand's `--help`
- Same short flag can mean different things (`-n` is `--dry-run` on some commands, `--tail` on `logs`)
- Some commands have `--host` option, others don't

### 2. Configuration Documentation

Verify `docs/configuration.md` against `src/compose_farm/config.py`:

| Check | Code Location |
|-------|---------------|
| Config keys | `Config` class fields |
| Host options | `Host` class fields (`address`, `user`, `port`) |
| Compose filenames | `COMPOSE_FILENAMES` tuple |
| Default values | Field defaults in Pydantic models |
| Search order | `load_config()` docstring and `config_search_paths()` |

Also verify:
- Example YAML is valid and uses current schema
- Required vs optional fields are correct

### 3. Architecture Documentation

Verify `docs/architecture.md` against actual directory structure:

```bash
# Main modules
ls src/compose_farm/*.py

# CLI modules
ls src/compose_farm/cli/*.py

# Web modules (if documented)
ls src/compose_farm/web/
```

Check:
- File paths match actual source code location
- All modules listed actually exist
- No modules are missing from the list
- Component descriptions match code functionality

### 4. State and Data Files

Verify against `src/compose_farm/state.py` and `src/compose_farm/logs.py`:

| Documentation Claim | Verify In |
|---------------------|-----------|
| State file name | `config.get_state_path()` |
| State file location | Same method - "alongside config file" |
| State file format | `load_state()` and `save_state()` |
| Log file name | `logs.py:DEFAULT_LOG_PATH` |
| What triggers updates | Search for calls to `save_state()` and `write_toml()` |

### 5. Installation Documentation

Verify against `pyproject.toml`:

| Claim | Check Against |
|-------|---------------|
| Python version | `requires-python` field |
| Package name | `[project] name` |
| CLI commands | `[project.scripts]` |
| Optional deps | `[project.optional-dependencies]` |

Ensure:
- The `[web]` extra is mentioned where `cf web` is documented
- All installation methods actually work

### 6. Web UI Documentation

Verify `docs/web-ui.md` against actual web UI:

- Keyboard shortcuts work as documented
- All mentioned pages exist (`/`, `/stack/{name}`, `/console`)
- Command palette commands are accurate
- Requirements section mentions `compose-farm[web]`

### 7. Feature Claims

For each claimed feature, verify it exists and works as described.

### 8. Cross-Reference Consistency

Check for conflicts between documentation files:

- README.md vs docs/index.md (should be consistent)
- CLAUDE.md vs actual code structure
- Command tables match across files
- Config examples are consistent

### 9. Recent Changes Check

Before starting the review:

```bash
git log --oneline -20
```

- Look for commits with `feat:`, `fix:`, or that mention new options/commands
- Cross-reference these against the documentation to catch undocumented features
- Pay special attention to commits touching `cli/*.py` files

### 10. Auto-Generated Content

For README.md or docs with `<!-- CODE:BASH:START -->` blocks:

```bash
uv run markdown-code-runner README.md
git diff README.md
```

- If diff shows only whitespace/box-drawing changes, that's OK (terminal width)
- If diff shows actual content changes, the docs are stale
- Check for missing `<!-- OUTPUT:START -->` markers (blocks that never ran)

### 11. CLI Options Completeness

For each command, run `cf <command> --help` and verify:

- Every option shown in help is documented
- Short flags (-x) are listed alongside long flags (--xxx)
- Default values in help match documented defaults
- Argument descriptions match

### 12. Review This Prompt

This prompt itself can become outdated. Verify it's still accurate:

- Check the Quick Reference table lists all current doc files
- Verify source file paths still exist (`ls` the paths mentioned)
- Confirm the bash commands in "Quick Checks First" still work
- Check if new commands/subcommands were added that aren't in the loops
- Verify the Common Gotchas are still relevant
- Add any new gotchas discovered during this review

If this prompt needs updates, include them in your fixes.

## Common Gotchas

- **Subcommand options**: `cf config` and `cf ssh` have subcommands with their own options. Check each subcommand's `--help`, not just the parent.
- **Short flags**: Verify `-x` maps to `--xxx` correctly (e.g., `-n` could be `--dry-run` or `--tail` depending on command)
- **Default values**: Check both "default:" in help output AND behavior when option is omitted
- **Auto-generated content**: README.md has `<!-- CODE:BASH:START -->` blocks that may have stale output if terminal width differs
- **Optional arguments**: Some commands have optional positional args (e.g., `cf config symlink [TARGET]`)

## Output Format

Provide findings in these categories:

1. **Critical Issues**: Incorrect information that would cause user problems
2. **Inaccuracies**: Technical errors, wrong defaults, incorrect paths
3. **Missing Documentation**: Features/commands that exist but aren't documented
4. **Outdated Content**: Information that was once true but no longer is
5. **Inconsistencies**: Conflicts between different documentation files
6. **Minor Issues**: Typos, formatting, unclear wording
7. **Verified Accurate**: Sections confirmed to be correct

For each issue, provide a **ready-to-apply fix**:

```
### Issue: [Brief description]

- **File**: docs/commands.md:652
- **Category**: Missing Documentation
- **Current text**:
  > `**Options for cf ssh setup and cf ssh keygen:**`
- **Problem**: `cf ssh setup` has `--config` option, `cf ssh keygen` does not
- **Fix**: Separate into individual tables for each subcommand
- **Verify**: `cf ssh setup --help` vs `cf ssh keygen --help`
```

## After Fixing Issues

After making fixes, verify:

```bash
# Regenerate auto-generated content
uv run markdown-code-runner README.md

# Check for unintended changes
git diff

# Run the review again to confirm fixes
```
