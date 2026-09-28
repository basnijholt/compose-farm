# End-to-end tests on disposable VMs

Real multi-host tests of the plugin system: compose-farm runs on this machine and manages three
Incus VMs over SSH, like a control machine managing a homelab. Nothing is mocked. Everything lives
in the Incus project `cf-e2e` and under `/tmp/cf-e2e`, so teardown removes it all.

```bash
e2e/provision.sh                       # 3 Ubuntu 24.04 VMs: docker, ZFS pool "tank", NFS, age (~5 min)
uv run pytest e2e -v --no-cov          # scenarios below (~4 min)
e2e/teardown.sh                        # delete VMs, project, and /tmp/cf-e2e
```

Requires Incus with VM support, `jq`, and `uv`. Tests are skipped when the VMs are not provisioned,
and `pytest` (without the `e2e` path) never collects them.

| Test | What it proves |
|------|----------------|
| `test_sync_and_commands` | `sync` rsyncs compose files to each host (no NFS), `commands` runs `run:` steps on the host and `local:` steps here, migration moves the stack |
| `test_zfs_per_host` | The ZFS example creates a dataset on first deploy and migrates it with no lost writes (with `cf up` and `cf apply`, the latter not stopping the source as a stray). It survives a sanoid-style snapshot on the target between sends, refuses to start empty on vm3 after `cf down`, and retires the dataset when the stack is removed |
| `test_zfs_storage_host` | `storage_host` mode: datasets created on vm1 and used by vm2/vm3 over NFS; a migration moves nothing; removal retires the dataset |
| `test_agenix` | Secrets encrypted with `age` for vm1 and vm2's SSH host keys (as agenix does) reach the containers as env vars and a compose `secrets:` file; a migration to vm3, which lacks them, fails preflight while the stack keeps running on vm1; secret values never appear in cf output |
| `test_agenix_symlink` | Symlink mode: `.env` on the host becomes a symlink to `/run/agenix/...`, and a service with `env_file: .env` gets the secret with no compose changes |
| `test_after_changes_and_pin` | `after_changes` runs once per command with the changed stacks (a `commands` step logs them). A pinned stack refuses to move to another host and keeps running where it was |
| `test_traefik_policy` | A router on `wan` without `websecure` is refused in preflight; after fixing the labels it starts |
| `test_traefik_dns` | Records follow the Traefik hostnames across `cf up` and `cf apply`. The reader stack is restarted when the records change and left alone when they don't
