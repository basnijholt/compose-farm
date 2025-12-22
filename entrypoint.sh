#!/bin/sh
# Create passwd entry for non-root users (required for SSH)
[ "$(id -u)" != "0" ] && ! getent passwd "$(id -u)" >/dev/null 2>&1 && \
  echo "${USER:-user}:x:$(id -u):$(id -g)::${HOME:-/tmp}:/bin/sh" >> /etc/passwd
exec cf "$@"
