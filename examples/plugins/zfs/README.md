# compose-farm-zfs

Example [compose-farm plugin](../../../docs/plugins.md) that gives every stack its own ZFS dataset, `<dataset>/<stack>`, created on first deploy and retired when the stack is removed from the config. It has two modes:

| Mode | Data lives | On migration |
|------|------------|--------------|
| **Per host** (default) | On the host running the stack, no NFS for data | The dataset moves: a live `zfs send` while the old host still runs, a short final incremental after it stops, then the old copy is retired |
| **`storage_host`** | On one host (a NAS) that shares `/mnt/data` with the others over NFS | Nothing moves; every host sees the same data |

## Install

```bash
uv tool install compose-farm \
  --with "compose-farm-zfs @ git+https://github.com/basnijholt/compose-farm#subdirectory=examples/plugins/zfs"
```

## Configure

```yaml
# compose-farm.yaml
plugins:
  zfs:
    dataset: tank/data    # parent dataset; stack "mealie" gets tank/data/mealie
    zfs: sudo zfs         # command prefix (default: zfs)
    stacks: [mealie, forgejo]  # optional: only these stacks (default: all)
    retire: rename        # rename to <stack>.retired-<time> (default), or destroy
    # storage_host: nas   # all datasets on this host instead (NAS mode)
```

Give the parent a mountpoint and bind-mount the stack's directory in `compose.yaml`:

```bash
zfs create -o mountpoint=/mnt/data tank/data   # on every host (per-host mode) or the NAS
```

```yaml
# /opt/stacks/mealie/compose.yaml
services:
  mealie:
    image: ghcr.io/mealie-recipes/mealie
    volumes:
      - /mnt/data/mealie:/app/data/
```

## What happens

**Per-host mode**:

1. **First deploy**: `before_up` creates `tank/data/mealie` on the target. `cf down` forgets where a stack ran, so whenever the state doesn't say the stack runs on the target, every other host is checked first. If one holds a copy, or can't be reached, the start is refused instead of starting empty or next to a newer copy. Multi-host stacks (`all`) get one dataset per host.
2. **Migration** (`cf up` after changing the stack's host):
   1. `before_up`: snapshot `cf-<time>` on the old host and send it to the new one while the stack still runs. The first send is full; later sends are incremental from the newest snapshot both copies share (matched by guid, not name).
   2. compose-farm stops the stack on the old host.
   3. `after_source_stopped`: second snapshot and a small incremental send.
   4. compose-farm starts the stack on the new host.
   5. `after_up`: the old copy is renamed to `tank/data/mealie.retired-<time>` (or destroyed), and older `cf-` snapshots on the new host are pruned.
3. **Removed from config** (`cf down --orphaned` or `cf apply`): the dataset is retired. If that fails, the stack stays in the state file and the next run retries.

Transfers are relayed through the machine running `cf` (`ssh old zfs send | ssh new zfs recv`), so hosts don't need SSH access to each other. That machine's network link carries the data.

**`storage_host` mode**: `before_up` creates the dataset on the storage host and `on_stack_removed` retires it there, once no host runs the stack anymore (a removed multi-host stack stops host by host). Migrations move nothing. Export the parent so child datasets are visible over NFS, e.g. on a NixOS NAS:

```nix
services.nfs.server = {
  enable = true;
  exports = "/mnt/data 192.168.1.0/24(rw,crossmnt,no_subtree_check)";
};
```

## Permissions

The SSH user needs to run `zfs create`, `snapshot`, `send`, `recv`, `rename`, and `destroy`, and mount the results. On Linux only root can mount, so `zfs allow` delegation is not enough; use `zfs: sudo zfs` with a rule like:

```nix
security.sudo.extraRules = [{
  users = [ "bas" ];
  commands = [{ command = "/run/current-system/sw/bin/zfs"; options = [ "NOPASSWD" ]; }];
}];
```

> ⚠️ Passwordless `zfs` lets that user destroy any dataset on the host. Only grant it to the account compose-farm uses, and keep `retire: rename` (the default) so retired copies are never destroyed.

## Notes

- Anything that can't be checked stops the operation before data changes: an old host that is no longer in the config or can't be reached, a `zfs` error, or another host that is down during a first deploy. Fix the host (or move the dataset by hand), then `cf up` again.
- Auto-snapshot tools (sanoid, zfs-auto-snapshot) are fine. Snapshots they take on the idle new copy between the two sends hold no data (`written` is 0) and are dropped before the final receive. If a target snapshot does hold data, the migration stops instead.
- Datasets with child datasets are not moved (the migration is refused); this example sends single datasets only.
- Retired copies stay mounted next to the live ones (`/mnt/data/mealie.retired-...`). Destroy them once you're happy.
- A failed start after the final send restarts the stack on the old host from its own data. Anything the new host wrote is discarded.
