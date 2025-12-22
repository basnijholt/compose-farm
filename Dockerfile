# syntax=docker/dockerfile:1

# Build stage - install with uv
FROM ghcr.io/astral-sh/uv:python3.14-alpine AS builder

ARG VERSION
RUN uv tool install --compile-bytecode "compose-farm[web]${VERSION:+==$VERSION}"

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

# Allow non-root users to add passwd entries (required for SSH)
RUN chmod 666 /etc/passwd

# Entrypoint creates /etc/passwd entry for non-root UIDs (required for SSH)
ENTRYPOINT ["sh", "-c", "[ $(id -u) != 0 ] && echo ${USER:-u}:x:$(id -u):$(id -g)::${HOME:-/}:/bin/sh >> /etc/passwd; exec cf \"$@\"", "--"]
CMD ["--help"]
