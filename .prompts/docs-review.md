Review documentation for accuracy, completeness, and consistency. Focus on things that require judgment—automated checks handle the rest.

## What's Already Automated

Don't waste time on these—CI and pre-commit hooks handle them:

- **README help output**: `markdown-code-runner` regenerates `cf --help` blocks in CI
- **README command table**: Pre-commit hook verifies commands are listed
- **Linting/formatting**: Handled by pre-commit

## What This Review Is For

Focus on things that require judgment:

1. **Accuracy**: Does the documentation match what the code actually does?
2. **Completeness**: Are there undocumented features, options, or behaviors?
3. **Clarity**: Would a new user understand this? Are examples realistic?
4. **Consistency**: Do different docs contradict each other?
5. **Freshness**: Has the code changed in ways the docs don't reflect?

## Review Process

### 1. Check Recent Changes

```bash
# What changed recently that might need doc updates?
git log --oneline -20 | grep -iE "feat|fix|add|remove|change|option"

# What code files changed?
git diff --name-only HEAD~20 | grep "\.py$"
```

Look for new features, changed defaults, renamed options, or removed functionality.

### 2. Verify docs/commands.md Options Tables

The README auto-updates help output, but `docs/commands.md` has **manually maintained options tables**. These can drift.

For each command's options table, compare against `cf <command> --help`:
- Are all options listed?
- Are short flags correct?
- Are defaults accurate?
- Are descriptions accurate?

**Pay special attention to subcommands** (`cf config *`, `cf ssh *`)—these have their own options that are easy to miss.

### 3. Verify docs/configuration.md

Compare against Pydantic models in the source:

```bash
# Find the config models
grep -r "class.*BaseModel" src/ --include="*.py" -A 15
```

Check:
- All config keys documented
- Types and defaults match code
- Config file search order is accurate
- Example YAML would actually work

### 4. Verify docs/architecture.md

```bash
# What source files actually exist?
git ls-files "src/**/*.py"
```

Check:
- Listed files exist
- No files are missing from the list
- Descriptions match what the code does

### 5. Check Examples

For examples in any doc:
- Would the YAML/commands actually work?
- Are service names, paths, and options realistic?
- Do examples use current syntax (not deprecated options)?

### 6. Cross-Reference Consistency

The same info appears in multiple places. Check for conflicts:
- README.md vs docs/index.md
- docs/commands.md vs CLAUDE.md command tables
- Config examples across different docs

### 7. Self-Check This Prompt

This prompt can become outdated too. If you notice:
- New automated checks that should be listed above
- New doc files that need review guidelines
- Patterns that caused issues

Include prompt updates in your fixes.

## Output Format

Categorize findings:

1. **Critical**: Wrong info that would break user workflows
2. **Inaccuracy**: Technical errors (wrong defaults, paths, types)
3. **Missing**: Undocumented features or options
4. **Outdated**: Was true, no longer is
5. **Inconsistency**: Docs contradict each other
6. **Minor**: Typos, unclear wording

For each issue, provide a ready-to-apply fix:

```
### Issue: [Brief description]

- **File**: docs/commands.md:652
- **Problem**: `cf ssh setup` has `--config` option but it's not documented
- **Fix**: Add `--config, -c PATH` to the options table
- **Verify**: `cf ssh setup --help`
```
