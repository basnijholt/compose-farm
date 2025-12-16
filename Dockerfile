# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.14-alpine

# Install SSH client (required for remote host connections)
RUN apk add --no-cache openssh-client

# Install compose-farm from PyPI
ARG VERSION
RUN uv tool install compose-farm${VERSION:+==$VERSION}

# Add uv tool bin to PATH
ENV PATH="/root/.local/bin:$PATH"

# Default entrypoint
ENTRYPOINT ["cf"]
CMD ["--help"]
