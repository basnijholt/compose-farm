# Documentation Review Prompt

Use this prompt with an AI assistant to review the Compose Farm documentation for accuracy.

---

## Prompt

```
Review all documentation in this repository for accuracy, completeness, and consistency. Cross-reference documentation against the actual codebase to identify issues.

## Scope

Review all documentation files:
- docs/*.md (primary documentation)
- README.md (repository landing page)
- CLAUDE.md (development guidelines)
- examples/README.md (example configurations)

## Review Checklist

### 1. Command Documentation (docs/commands.md)

For each documented command, verify against the CLI source code (src/compose_farm/cli/*.py):

- [ ] Command exists in codebase
- [ ] All options are documented with correct names, types, and defaults
- [ ] Short options (-x) match long options (--xxx)
- [ ] Examples would work as written
- [ ] Check for undocumented commands or options

Run `cf --help` and `cf <command> --help` for each command to verify.

### 2. Configuration Documentation (docs/configuration.md)

Verify against src/compose_farm/config.py (Pydantic models):

- [ ] All config keys are documented
- [ ] Types match Pydantic field types
- [ ] Required vs optional fields are correct
- [ ] Default values are accurate
- [ ] Config file search order matches code (check paths.py)
- [ ] Example YAML is valid and uses current schema

### 3. Architecture Documentation (docs/architecture.md, CLAUDE.md)

Verify against actual directory structure:

- [ ] File paths are correct (src/compose_farm/ not compose_farm/)
- [ ] All modules listed actually exist
- [ ] No modules are missing from the list
- [ ] Component descriptions match code functionality
- [ ] CLI module list includes all command files

### 4. State and Data Files

Verify against src/compose_farm/state.py, logs.py, paths.py:

- [ ] State file name and location are correct
- [ ] State file format matches actual structure
- [ ] Log file name and location are correct
- [ ] What triggers state/log updates is accurate

### 5. Installation Documentation (docs/getting-started.md)

Verify against pyproject.toml:

- [ ] Python version requirement matches requires-python
- [ ] Package name is correct
- [ ] Optional dependencies ([web]) are documented
- [ ] CLI entry points (cf, compose-farm) are mentioned
- [ ] Installation methods work as documented

### 6. Feature Claims

For each claimed feature, verify it exists and works as described:

- [ ] Auto-migration logic exists in operations.py
- [ ] Parallel execution uses asyncio.gather
- [ ] SSH execution logic in executor.py
- [ ] Local host detection skips SSH
- [ ] Traefik integration in traefik.py
- [ ] Preflight checks exist

### 7. Cross-Reference Consistency

Check for conflicts between documentation files:

- [ ] README.md vs docs/index.md (should be consistent)
- [ ] CLAUDE.md vs actual code structure
- [ ] Command tables match across files
- [ ] Config examples are consistent

## Output Format

Provide findings in these categories:

1. **Critical Issues**: Incorrect information that would cause user problems
2. **Inaccuracies**: Technical errors, wrong defaults, incorrect paths
3. **Missing Documentation**: Features/commands that exist but aren't documented
4. **Outdated Content**: Information that was once true but no longer is
5. **Inconsistencies**: Conflicts between different documentation files
6. **Minor Issues**: Typos, formatting, unclear wording
7. **Verified Accurate**: Sections confirmed to be correct

For each issue, include:
- File path and line number (if applicable)
- What the documentation says
- What the code actually does
- Suggested fix
```

---

## How to Use

1. Open a new conversation with an AI assistant that has access to the codebase
2. Copy the prompt above
3. The assistant will read documentation files, source code, and run CLI help commands
4. Review the findings and apply fixes as needed

## Recommended Frequency

- After major feature additions
- Before releases
- Quarterly for maintenance
