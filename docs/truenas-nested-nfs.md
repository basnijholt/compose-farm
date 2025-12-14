# TrueNAS NFS: Accessing Child ZFS Datasets

When NFS-exporting a parent ZFS dataset on TrueNAS, child datasets appear as **empty directories** to NFS clients. This document explains the problem and provides a workaround.

## The Problem

TrueNAS structures storage as ZFS datasets. A common pattern is:

```
tank/data              <- parent dataset (NFS exported)
tank/data/app1         <- child dataset
tank/data/app2         <- child dataset
```

When you create an NFS share for `tank/data`, clients mount it and see the `app1/` and `app2/` directories—but they're empty. This happens because each ZFS dataset is a separate filesystem, and NFS doesn't traverse into child filesystems by default.

## The Solution: `crossmnt`

The NFS `crossmnt` export option tells the server to allow clients to traverse into child filesystems. However, TrueNAS doesn't expose this option in the UI.

### Workaround Script

This Python script injects `crossmnt` into `/etc/exports`:

```python
#!/usr/bin/env python3
"""
Add crossmnt to TrueNAS NFS exports for child dataset visibility.

Usage: fix-nfs-crossmnt.py /mnt/pool/dataset

Setup:
  1. scp fix-nfs-crossmnt.py root@truenas.local:/root/
  2. chmod +x /root/fix-nfs-crossmnt.py
  3. Test: /root/fix-nfs-crossmnt.py /mnt/pool/dataset
  4. Add cron job: TrueNAS UI > System > Advanced > Cron Jobs
     Command: /root/fix-nfs-crossmnt.py /mnt/pool/dataset
     Schedule: */5 * * * *
"""

import re
import subprocess
import sys
from pathlib import Path

EXPORTS_FILE = Path("/etc/exports")


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} /mnt/pool/dataset", file=sys.stderr)
        return 1

    export_path = sys.argv[1]
    content = EXPORTS_FILE.read_text()

    if f'"{export_path}"' not in content:
        print(f"ERROR: {export_path} not found in {EXPORTS_FILE}", file=sys.stderr)
        return 1

    lines = content.splitlines()
    result = []
    in_block = False
    modified = False

    for line in lines:
        if f'"{export_path}"' in line:
            in_block = True
        elif line.startswith('"'):
            in_block = False

        if in_block and line[:1] in (" ", "\t") and "crossmnt" not in line:
            line = re.sub(r"\)(\\\s*)?$", r",crossmnt)\1", line)
            modified = True

        result.append(line)

    if not modified:
        return 0  # Already applied

    EXPORTS_FILE.write_text("\n".join(result) + "\n")
    subprocess.run(["exportfs", "-ra"], check=True)
    print(f"Added crossmnt to {export_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

## Setup Instructions

### 1. Copy the script to TrueNAS

```bash
scp fix-nfs-crossmnt.py root@truenas.local:/root/
ssh root@truenas.local chmod +x /root/fix-nfs-crossmnt.py
```

### 2. Test manually

```bash
ssh root@truenas.local

# Run the script
/root/fix-nfs-crossmnt.py /mnt/tank/data

# Verify crossmnt was added
cat /etc/exports
```

You should see `,crossmnt` added to the client options:

```
"/mnt/tank/data"\
    192.168.1.10(sec=sys,rw,no_subtree_check,crossmnt)\
    192.168.1.11(sec=sys,rw,no_subtree_check,crossmnt)
```

### 3. Verify on NFS client

```bash
# Before: empty directory
ls /mnt/data/app1/
# (nothing)

# After: actual contents visible
ls /mnt/data/app1/
# config.yaml  data/  logs/
```

### 4. Make it persistent

TrueNAS regenerates `/etc/exports` when you modify NFS shares in the UI. To survive this, set up a cron job:

1. Go to **TrueNAS UI → System → Advanced → Cron Jobs → Add**
2. Configure:
   - **Description:** Fix NFS crossmnt
   - **Command:** `/root/fix-nfs-crossmnt.py /mnt/tank/data`
   - **Run As User:** root
   - **Schedule:** `*/5 * * * *` (every 5 minutes)
   - **Enabled:** checked
3. Save

The script is idempotent—it only modifies the file if `crossmnt` is missing, and skips the write entirely if already applied.

## How It Works

1. Parses `/etc/exports` to find the specified export block
2. Adds `,crossmnt` before the closing `)` on each client line
3. Writes the file only if changes were made
4. Runs `exportfs -ra` to reload the NFS configuration

## Why Not Use SMB Instead?

SMB handles child datasets seamlessly, but:

- NFS is simpler for Linux-to-Linux with matching UIDs
- SMB requires more complex permission mapping for Docker volumes
- Many existing setups already use NFS

## Related Links

- [TrueNAS Forum: Add crossmnt option to NFS exports](https://forums.truenas.com/t/add-crossmnt-option-to-nfs-exports/10573)
- [exports(5) man page](https://man7.org/linux/man-pages/man5/exports.5.html) - see `crossmnt` option

## Tested On

- TrueNAS SCALE 24.10
