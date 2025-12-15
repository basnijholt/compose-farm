# TrueNAS NFS: Disabling Root Squash

When running Docker containers on NFS-mounted storage, containers that run as root will fail to write files unless root squash is disabled. This document explains the problem and solution.

## The Problem

By default, NFS uses "root squash" which maps the root user (UID 0) on clients to `nobody` on the server. This is a security feature to prevent remote root users from having root access to the NFS server's files.

However, many Docker containers run as root internally. When these containers try to write to NFS-mounted volumes, the writes fail with "Permission denied" because the NFS server sees them as `nobody`, not `root`.

Example error in container logs:
```
System.UnauthorizedAccessException: Access to the path '/data' is denied.
Error: EACCES: permission denied, mkdir '/app/data'
```

## The Solution

In TrueNAS, configure the NFS share to map remote root to local root:

### TrueNAS SCALE UI

1. Go to **Shares → NFS**
2. Edit your share
3. Under **Advanced Options**:
   - **Maproot User**: `root`
   - **Maproot Group**: `wheel`
4. Save

### Result in /etc/exports

```
"/mnt/pool/data"\
    192.168.1.25(sec=sys,rw,no_root_squash,no_subtree_check)\
    192.168.1.26(sec=sys,rw,no_root_squash,no_subtree_check)
```

The `no_root_squash` option means remote root is treated as root on the server.

## Why `wheel`?

On FreeBSD/TrueNAS, the root user's primary group is `wheel` (GID 0), not `root` like on Linux. So `root:wheel` = `0:0`.

## Security Considerations

Disabling root squash means any machine that can mount the NFS share has full root access to those files. This is acceptable when:

- The NFS clients are on a trusted private network
- Only known hosts (by IP) are allowed to mount the share
- The data isn't security-critical

For home lab setups with Docker containers, this is typically fine.

## Alternative: Run Containers as Non-Root

If you prefer to keep root squash enabled, you can run containers as a non-root user:

1. **LinuxServer.io images**: Set `PUID=1000` and `PGID=1000` environment variables
2. **Other images**: Add `user: "1000:1000"` to the compose service

However, not all containers support running as non-root (they may need to bind to privileged ports, create system directories, etc.).

## Tested On

- TrueNAS SCALE 24.10
