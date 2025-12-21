Review all documentation in this repository for accuracy, completeness, and consistency. Cross-reference documentation against the actual codebase to identify issues.

## Approach

Use **discovery** rather than hardcoded checklists. The codebase structure may change, so:
1. Find what exists (docs, commands, source files)
2. Compare against what's documented
3. Flag discrepancies in either direction

## What This Prompt Is For

This is for **manual deep review** of documentation accuracy. Some checks can be automated as pre-commit hooks (and some already are), but this prompt covers things that require judgment:

- Does the description actually match what the code does?
- Are the examples realistic and would they work?
- Is the documentation clear and complete?
- Are there undocumented features or options?

**Already automated (pre-commit):** README command table check, linting, formatting

## Scope

Discover all documentation files:

```bash
# Find all tracked markdown docs
git ls-files "*.md"
```

Typical locations:
- `docs/*.md` - Primary documentation
- `README.md` - Repository landing page
- `CLAUDE.md` - Development guidelines
- `examples/` - Example configurations

## Review Checklist

### 0. Discovery Phase

Before reviewing, discover what exists:

```bash
# Recent changes that might need doc updates
git log --oneline -20

# Discover all CLI commands (don't assume a list)
cf --help

# Get help for each command shown
cf --help 2>&1 | grep -E "^│\s+\w+" | awk '{print $2}' | while read cmd; do
  echo "=== cf $cmd ===" && cf $cmd --help 2>/dev/null
done

# Find subcommands (commands that have their own subcommands)
for cmd in config ssh; do
  echo "=== cf $cmd subcommands ==="
  cf $cmd --help 2>&1 | grep -E "^│\s+\w+" | awk '{print $2}' | while read sub; do
    echo "--- cf $cmd $sub ---" && cf $cmd $sub --help 2>/dev/null
  done
done

# Discover source structure
ls src/*/
ls src/*/*/ 2>/dev/null

# Find Pydantic models (config definitions)
grep -r "class.*BaseModel" src/ --include="*.py" -l

# Find CLI entry points
grep -A5 "\[project.scripts\]" pyproject.toml
```

### 1. Command Documentation

Compare CLI reality against command documentation:

```bash
# What commands actually exist?
cf --help

# What does the doc say exists?
grep -E "^\| \`" docs/commands.md | head -20
```

For each command, verify:
- Command exists and description matches
- **All options are documented** with correct names, types, and defaults
- Short options (-x) match long options (--xxx)
- Examples would work as written
- Check for undocumented commands or options

**Watch for:**
- Subcommands that have their own options (check each `--help`)
- Same short flag meaning different things on different commands
- Options that exist on some commands but not others

### 2. Configuration Documentation

Find and verify config models:

```bash
# Find config-related code
grep -r "class.*BaseModel" src/ --include="*.py" -A 10

# Find config file search logic
grep -r "config.*path\|search.*path\|find.*config" src/ --include="*.py" -l
```

Verify documentation covers:
- All config keys match model fields
- Types and defaults are accurate
- Config file search order matches code
- Example YAML uses current schema

### 3. Architecture Documentation

Compare documented structure against reality:

```bash
# What actually exists?
find src/ -name "*.py" -type f | head -30

# What does architecture doc claim?
grep -E "^├|^│|^└|\.py" docs/architecture.md
```

Check:
- File paths match actual locations
- All modules listed actually exist
- No modules are missing
- Descriptions match functionality

### 4. State and Data Files

Find state/data handling code:

```bash
# Find state-related code
grep -r "state\|\.yaml\|\.toml" src/ --include="*.py" -l

# Find file path definitions
grep -r "Path\|\.yaml\|\.toml" src/ --include="*.py" | grep -i "state\|log\|config"
```

Verify documentation accurately describes:
- File names and locations
- File formats
- What triggers updates

### 5. Installation Documentation

Verify against pyproject.toml:

```bash
cat pyproject.toml | grep -A20 "^\[project\]"
cat pyproject.toml | grep -A10 "optional-dependencies"
cat pyproject.toml | grep -A5 "scripts"
```

Check:
- Python version requirement
- Package name
- Optional dependencies (like `[web]`)
- CLI entry points

### 6. Feature Claims

For each claimed feature in any doc, verify it actually exists and works as described.

### 7. Cross-Reference Consistency

Look for the same information in multiple places:

```bash
# Find command tables/lists across docs
grep -r "cf \w\+" docs/ README.md --include="*.md" | grep -v "```" | head -20

# Find config examples
grep -r "compose_dir\|stacks:" docs/ README.md --include="*.md" -A 3
```

Check for conflicts between files.

### 8. Recent Changes

```bash
# Commits that might need doc updates
git log --oneline -20 | grep -iE "feat|fix|add|new|option|command"

# Files changed recently
git diff --name-only HEAD~20 | grep -E "\.py$|\.md$"
```

Cross-reference against documentation.

### 9. Auto-Generated Content

For files with auto-generated blocks:

```bash
# Find auto-generated markers
grep -r "CODE:BASH:START\|OUTPUT:START" . --include="*.md"

# Regenerate and check for drift
uv run markdown-code-runner README.md
git diff README.md
```

Whitespace-only diffs (box drawing) are OK; content changes mean stale docs.

### 10. Self-Check

This prompt can become outdated. Verify:
- Discovery commands still work
- No hardcoded assumptions that are now wrong
- Add any new gotchas found during review

If this prompt needs updates, include them in your fixes.

## Common Gotchas

These are patterns that frequently cause doc/code drift:

- **Subcommand options**: Parent command help doesn't show subcommand options
- **Short flag collisions**: `-n` might mean `--dry-run` or `--tail` depending on command
- **Default value drift**: Code defaults change but docs don't
- **Terminal width**: Auto-generated help output varies by terminal width
- **Optional positional args**: Easy to miss documenting `[OPTIONAL_ARG]`
- **New commands**: Added to CLI but not to command tables in docs

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
