# compose-farm-traefik-dns

Example [compose-farm plugin](../../../docs/plugins.md) that keeps DNS records in sync with your Traefik hostnames. After every `cf` command that starts, moves, or stops stacks, it:

1. collects every `` Host(`...`) `` name under a domain from all configured stacks' Traefik labels (with `${DOMAIN}` from each stack's `.env` filled in), plus the `rule:` values in optional hand-written route files;
2. writes one record per name, all pointing at your Traefik entry, between `# BEGIN <marker>` and `# END <marker>` in a file;
3. restarts the stack that reads the file, but only when the records changed.

Use it for [Headscale](https://headscale.net/) `extra_records` (so lab names resolve over the tailnet), or a hosts-style file for Pi-hole, dnsmasq, or `/etc/hosts`.

## Install

```yaml
# compose-farm.yaml
plugin_packages:
  - github:basnijholt/compose-farm/examples/plugins/traefik-dns
```

Then run `cf plugins install`. The Docker image already includes this plugin.

## Configure

```yaml
# compose-farm.yaml
plugins:
  traefik-dns:
    file: headscale/config.yaml               # relative to compose_dir, or absolute
    domain: lab.example.com                   # names equal to or under this domain
    address: 100.64.0.28                      # where every name points (an IPv6 address writes AAAA)
    format: headscale                         # headscale (default) or hosts
    marker: MANAGED LAB DNS                   # default: compose-farm traefik-dns
    rule_files: [traefik/dynamic.d/manual.yml]  # routes that aren't in compose labels
    restart: headscale                        # optional: restart this stack when records change
```

Add the markers where the records go. The plugin owns everything between them and keeps their indentation:

```yaml
# headscale/config.yaml
dns:
  extra_records:
    # BEGIN MANAGED LAB DNS
    # END MANAGED LAB DNS
```

With `format: hosts`, each record is a line like `100.64.0.28 grafana.lab.example.com`.

## Notes

- Only stacks in `compose-farm.yaml` count, so commented-out stacks don't leave stale records behind.
- If a stack's compose file can't be read, the update stops with an error and the existing records stay as they are, instead of silently dropping that stack's names.
- The restart covers every host of the `restart` stack. If it fails anywhere, the file is put back, so the next `cf` command writes it again and retries the restart.
- The file is written on the machine running `cf`. Before restarting, the plugin runs the reader stack's `before_up` hooks on its hosts, as `cf up` would. With the `sync` plugin, that copies the new file to them; with a shared (NFS) `compose_dir`, the hosts already see it.
- Records point at the Traefik entry, not at the host that runs each service.
