#!/bin/sh
set -eu

# Docker's `user:` may select a numeric UID/GID absent from /etc/passwd. OpenSSH
# needs an NSS identity, but the process must never be allowed to edit /etc/passwd.
if ! id -un >/dev/null 2>&1; then
    nss_dir="$(mktemp -d)"
    cp /etc/passwd "$nss_dir/passwd"
    cp /etc/group "$nss_dir/group"
    printf 'compose-farm:x:%s:%s:Compose Farm:%s:/sbin/nologin\n' \
        "$(id -u)" "$(id -g)" "${HOME:-/}" >> "$nss_dir/passwd"
    printf 'compose-farm:x:%s:\n' "$(id -g)" >> "$nss_dir/group"

    export NSS_WRAPPER_PASSWD="$nss_dir/passwd"
    export NSS_WRAPPER_GROUP="$nss_dir/group"
    export LD_PRELOAD=/usr/lib/libnss_wrapper.so
fi

exec cf "$@"
