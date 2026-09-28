# syntax=docker/dockerfile:1

# Build stage - install with uv
FROM ghcr.io/astral-sh/uv:python3.14-alpine AS builder

ARG VERSION
RUN uv tool install --compile-bytecode "compose-farm[web]${VERSION:+==$VERSION}"

# Runtime stage - minimal image without uv
FROM python:3.14-alpine

# Install only runtime requirements. nss_wrapper provides an identity for
# arbitrary runtime UIDs without modifying the image's system account files.
RUN apk add --no-cache nss_wrapper openssh-client

# Copy installed tool virtualenv and bin symlinks from builder
COPY --from=builder /root/.local/share/uv/tools/compose-farm /root/.local/share/uv/tools/compose-farm
COPY --from=builder /usr/local/bin/cf /usr/local/bin/compose-farm /usr/local/bin/

# Allow non-root users to access the installed tool
# (required when running with user: "${CF_UID:-0}:${CF_GID:-0}")
RUN chmod 755 /root

# Entrypoint supplies an NSS identity for arbitrary non-root UIDs.
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["--help"]
