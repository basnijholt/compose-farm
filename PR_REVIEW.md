# PR Review: feat(web): add Glances integration for host resource stats

**Branch:** `glances-integration`
**PR #124**

---

## Summary

This PR adds Glances integration for host resource monitoring and container stats in the web UI. It includes 3 new Python modules, templates, tests, and documentation.

**New files:**
| File | Lines | Description |
|------|-------|-------------|
| `src/compose_farm/glances.py` | 274 | Glances API client (host + container stats) |
| `src/compose_farm/registry.py` | 375 | Container registry API for update checking |
| `src/compose_farm/web/routes/containers.py` | 366 | Containers page routes |
| `src/compose_farm/web/templates/containers.html` | 196 | Interactive container table |
| `src/compose_farm/web/templates/partials/glances.html` | 47 | Host stats partial |
| `tests/test_glances.py` | 365 | Glances module tests |
| `tests/test_containers.py` | 497 | Containers page tests |
| `tests/test_registry.py` | 212 | Registry module tests |

---

## Review Checklist

### Code Cleanliness
- [x] Well-structured with clear separation of concerns
- [x] Proper use of dataclasses (`HostStats`, `ContainerStats`, `ImageRef`, `TagCheckResult`)
- [x] Async patterns used correctly with `asyncio.gather` for parallel fetching
- [x] Lazy imports (`# noqa: PLC0415`) used appropriately for CLI performance

### DRY Principle
- [x] Common patterns centralized (`_format_bytes`, `_parse_image`, `_progress_class`)
- [x] Registry clients share abstract base class
- [ ] **Issue:** Container enrichment logic duplicated (see below)

### Code Reuse
- [x] Uses existing `run_command` from executor for SSH commands
- [x] Uses existing template/component patterns (`collapse`, `page_header`)
- [x] Follows established icon pattern in `icons.html`

### Organization
- [x] New files placed correctly in appropriate directories
- [x] Tests organized by module

### Consistency
- [x] Follows existing HTMX patterns
- [x] Uses same DaisyUI component classes
- [x] Config field `glances_stack` follows pattern of `traefik_stack`

### User Experience
- [x] Live tested with 111 containers across 4 hosts - works smoothly
- [x] 3-second auto-refresh with pause indicator
- [x] Dropdown actions pause refresh to prevent UI disruption
- [x] Progress bars with color-coded thresholds
- [x] Graceful error display for unreachable hosts

### Tests
- [x] 67 tests pass for new modules
- [x] Unit tests for parsing functions, API endpoints, error cases
- [x] Browser tests for dropdown pause behavior

### CLAUDE.md Compliance
- [x] Icons added as macros following documented pattern
- [x] HTMX uses custom events (not `hx-swap-oob`)
- [x] Lazy imports where appropriate

---

## Issues Found

### 1. Unused Code: `registry.py` (375 lines)

The registry module provides infrastructure for checking container image updates:
- Abstract `RegistryClient` base class
- Three concrete clients (DockerHub, GHCR, Generic OCI)
- Image reference parsing and version comparison

The `/api/containers/check-updates` endpoint exists but **no UI element calls it**. This is 375 lines + 212 test lines for an unfinished feature.

**Recommendation:** Remove or defer until UI is implemented.

### 2. Dead Code: `/api/containers/stream` endpoint (~60 lines)

`containers.py:232-295` contains an SSE streaming endpoint. However, the template uses simple HTMX polling:

```html
<tbody id="container-rows"
       hx-get="/api/containers/rows"
       hx-trigger="load">
```

Commit history shows streaming was replaced with HTMX polling but code wasn't removed.

**Recommendation:** Remove dead code.

### 3. Unused Endpoint: `/api/containers/list`

Returns JSON but UI only uses `/api/containers/rows` (HTML).

**Recommendation:** Remove unless needed for future API consumers.

### 4. DRY Violation: Container Enrichment Logic

The same enrichment pattern appears in two places:

**`glances.py:232-264`:**
```python
enriched.append(ContainerStats(
    name=c.name, host=c.host, status=c.status, ...
    stack=stack, service=service,
))
```

**`containers.py:260-280`:**
```python
enriched = ContainerStats(
    name=c.name, host=c.host, status=c.status, ...
    stack=stack, service=service,
)
```

**Recommendation:** Extract to a helper function if keeping the streaming endpoint.

---

## JavaScript Analysis (~90 lines)

| Feature | Lines | Verdict |
|---------|-------|---------|
| Filtering | ~10 | Necessary |
| Client-side sorting | ~35 | Justified for instant UX |
| HTMX after-swap handler | ~8 | Necessary |
| Dropdown pause mechanism | ~20 | Necessary (simpler approaches tried, failed) |
| Timer countdown | ~15 | Nice-to-have, could be static text |

The JavaScript is mostly justified. The dropdown pause mechanism solves a real UX problem (dropdown closing unexpectedly during refresh). Simpler CSS-based approaches were tried per commit history but were unreliable.

---

## Verdict

**Approve with suggestions**

The core feature (Glances integration for host stats and container monitoring) is well-implemented, tested, and works correctly. The main concern is unused code that should be cleaned up:

1. Remove `registry.py` and `/api/containers/check-updates` (unused feature)
2. Remove `/api/containers/stream` (dead code)
3. Remove `/api/containers/list` (unused endpoint)

This would reduce the PR by ~700 lines while keeping all functional features intact.
