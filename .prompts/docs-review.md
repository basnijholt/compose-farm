Review all documentation in this repository for accuracy, completeness, and consistency. Cross-reference documentation against the actual codebase to identify issues.

## Scope

Review all documentation files:
- docs/*.md (primary documentation)
- README.md (repository landing page)
- CLAUDE.md (development guidelines)
- examples/README.md (example configurations)

## Review Checklist

### 1. Command Documentation

For each documented command, verify against the CLI source code:

- Command exists in codebase
- All options are documented with correct names, types, and defaults
- Short options (-x) match long options (--xxx)
- Examples would work as written
- Check for undocumented commands or options

Run `--help` for each command to verify.

### 2. Configuration Documentation

Verify against Pydantic models in the config module:

- All config keys are documented
- Types match Pydantic field types
- Required vs optional fields are correct
- Default values are accurate
- Config file search order matches code
- Example YAML is valid and uses current schema

### 3. Architecture Documentation

Verify against actual directory structure:

- File paths match actual source code location
- All modules listed actually exist
- No modules are missing from the list
- Component descriptions match code functionality
- CLI module list includes all command files

### 4. State and Data Files

Verify against state and path modules:

- State file name and location are correct
- State file format matches actual structure
- Log file name and location are correct
- What triggers state/log updates is accurate

### 5. Installation Documentation

Verify against pyproject.toml:

- Python version requirement matches requires-python
- Package name is correct
- Optional dependencies are documented
- CLI entry points are mentioned
- Installation methods work as documented

### 6. Feature Claims

For each claimed feature, verify it exists and works as described.

### 7. Cross-Reference Consistency

Check for conflicts between documentation files:

- README vs docs/index.md (should be consistent)
- CLAUDE.md vs actual code structure
- Command tables match across files
- Config examples are consistent

### 8. Recent Changes Check

Before starting the review:

- Run `git log --oneline -20` to see recent commits
- Look for commits with `feat:`, `fix:`, or that mention new options/commands
- Cross-reference these against the documentation to catch undocumented features

### 9. Auto-Generated Content

For README.md or docs with `<!-- CODE:BASH:START -->` blocks:

- Run `uv run markdown-code-runner <file>` to regenerate outputs
- Check for missing `<!-- OUTPUT:START -->` markers (blocks that never ran)
- Verify help output matches current CLI behavior

### 10. CLI Options Completeness

For each command, run `cf <command> --help` and verify:

- Every option shown in help is documented
- Short flags (-x) are listed alongside long flags (--xxx)
- Default values in help match documented defaults

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
