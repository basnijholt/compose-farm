# compose-farm-traefik-policy

Example [compose-farm plugin](../../../docs/plugins.md) that enforces Traefik routing conventions during preflight. A router that breaks them fails at `cf up` and shows up in `cf check`, instead of going live.

It shows the pattern for any label policy: parse the stack's Traefik labels with compose-farm's own parser (`compose_farm.traefik.generate_traefik_config`, with `${VAR}` values from the stack's `.env` already filled in), then return the problems from `preflight`.

## Install

```yaml
# compose-farm.yaml
plugin_packages:
  - github:basnijholt/compose-farm/examples/plugins/traefik-policy
```

Then run `cf plugins install`. The Docker image already includes this plugin.

## Configure

```yaml
# compose-farm.yaml
plugins:
  traefik-policy:
    entrypoints_require:
      wan: [websecure]   # a router on the public "wan" entrypoint must also be on "websecure"
```

```console
$ cf up mealie
✗ [mealie] Cannot start on nas:
✗   plugin traefik-policy: router mealie-pub is on entrypoint wan but not on websecure
```

A rule like this is useful when entrypoints carry meaning. For example, `websecure` holds an allowlist middleware, and tools such as compose-farm and uptime-kuma sync only pick `https` URLs from routers that list it.
