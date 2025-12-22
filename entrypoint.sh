#!/bin/sh
# Entrypoint script for compose-farm container
# Creates a passwd entry for non-root users to enable SSH functionality

set -e

# If running as non-root and no passwd entry exists for our UID, create one
if [ "$(id -u)" != "0" ]; then
    if ! getent passwd "$(id -u)" > /dev/null 2>&1; then
        # Create a minimal passwd entry
        # Use USER env var for username, fallback to "user"
        USERNAME="${USER:-user}"
        # Use HOME env var for home dir, fallback to /home/$USERNAME
        HOMEDIR="${HOME:-/home/$USERNAME}"
        echo "${USERNAME}:x:$(id -u):$(id -g):${USERNAME}:${HOMEDIR}:/bin/sh" >> /etc/passwd
    fi
fi

exec cf "$@"
