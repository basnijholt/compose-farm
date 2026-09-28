# compose-farm-pin

Example [compose-farm plugin](../../../docs/plugins.md) that keeps stacks on the host they must run on.

Some stacks can't move. Their host's IP is configured elsewhere (router port forwards, Home Assistant, TV apps), or they need a USB device or GPU that only one host has. Today that knowledge lives in comments like `# gitea stays on nas`. With this plugin, an accidental edit of `stacks:` fails with the reason instead of migrating the stack.

## Install

```bash
uv tool install compose-farm \
  --with "compose-farm-pin @ git+https://github.com/basnijholt/compose-farm#subdirectory=examples/plugins/pin"
```

## Configure

```yaml
# compose-farm.yaml
plugins:
  pin:                     # list it first, so it runs before plugins that move data
    gitea: nas             # host only
    frigate:
      host: nas
      reason: static IP used in Home Assistant
    ollama:
      host: [pc, hp]       # any of these hosts
```

```console
$ cf up gitea    # after changing gitea's host to nuc
✗ [gitea] plugin pin.before_up: gitea is pinned to nas (router forwards ports to it); not starting it on nuc
```

- `before_up` refuses the start, so nothing is stopped or copied.
- `preflight` reports the same problem, so `cf check` flags a pin that no longer matches `stacks:`.
- To really move a pinned stack, change the pin and the `stacks:` entry together.
