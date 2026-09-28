# Compose Farm Development Commands
# Run `just` to see available commands

# Default: list available commands
default:
    @just --list

# Install development dependencies
install:
    uv sync --all-extras --dev

# Run all tests (parallel, with bounded browser workers)
test:
    uv run pytest -m "not browser" -n auto
    uv run pytest -m browser -n 4

# Run CLI tests only (parallel, with coverage)
test-cli:
    uv run pytest -m "not browser" -n auto

# Run web UI tests only (bounded parallelism for browser server fixtures)
test-web:
    uv run pytest -m browser -n 4

# Lint, format, and type check
lint:
    uv run ruff check --fix .
    uv run ruff format .
    uv run mypy src
    uv run ty check src

# Audit the exact locked production dependencies, including web extras
audit:
    #!/usr/bin/env bash
    set -euo pipefail
    audit_requirements="$(mktemp)"
    trap 'rm -f "$audit_requirements"' EXIT
    uv export --frozen --all-extras --no-dev --no-emit-project --no-hashes --output-file "$audit_requirements"
    uvx pip-audit==2.10.1 --requirement "$audit_requirements"

# Start web UI in development mode with auto-reload
web:
    uv run cf web --reload --port 9001

# Kill the web server
kill-web:
    lsof -ti :9001 | xargs kill -9 2>/dev/null || true

# Regenerate logo, icon, and social preview (PNG needs inkscape)
logo:
    python docs/logo/generate.py
    inkscape docs/assets/social-preview.svg -o docs/assets/social-preview.png

# Build docs and serve locally
doc:
    uvx zensical build
    python -m http.server -d site 9002

# Kill the docs server
kill-doc:
    lsof -ti :9002 | xargs kill -9 2>/dev/null || true

# Record CLI demos (all or specific: just record-cli quickstart)
record-cli *demos:
    python docs/demos/cli/record.py {{demos}}

# Record web UI demos (all or specific: just record-web navigation)
record-web *demos:
    python docs/demos/web/record.py {{demos}}

# Clean up build artifacts and caches
clean:
    rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
