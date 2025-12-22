# syntax=docker/dockerfile:1

# Build stage - install with uv
FROM ghcr.io/astral-sh/uv:python3.14-alpine AS builder

# Copy local source for development builds
COPY pyproject.toml README.md hatch_build.py ./
COPY src ./src

ARG VERSION
# Install from local source if present, otherwise from PyPI
# SETUPTOOLS_SCM_PRETEND_VERSION is needed when building from source without .git
RUN if [ -d "src/compose_farm" ]; then \
      SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION:-0.0.0.dev0} \
      uv tool install --compile-bytecode ".[web]"; \
    else \
      uv tool install --compile-bytecode "compose-farm[web]${VERSION:+==$VERSION}"; \
    fi

# Runtime stage - minimal image without uv
FROM python:3.14-alpine

# Install only runtime requirements
RUN apk add --no-cache openssh-client

# Copy installed tool virtualenv and bin symlinks from builder
COPY --from=builder /root/.local/share/uv/tools/compose-farm /root/.local/share/uv/tools/compose-farm
COPY --from=builder /usr/local/bin/cf /usr/local/bin/compose-farm /usr/local/bin/

# Allow non-root users to access the installed tool
# (required when running with user: "${CF_UID:-0}:${CF_GID:-0}")
RUN chmod 755 /root

ENTRYPOINT ["cf"]
CMD ["--help"]
