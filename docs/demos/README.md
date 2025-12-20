# Terminal Demos

[VHS](https://github.com/charmbracelet/vhs) tape files for recording terminal demos.

## Demos

| File | Shows |
|------|-------|
| `install.tape` | Installing with `uv tool install` |
| `quickstart.tape` | `cf ps`, `cf up`, `cf logs` |
| `logs.tape` | Viewing logs |
| `update.tape` | `cf update` |
| `migration.tape` | Service migration |
| `apply.tape` | `cf apply` |

## Recording

```bash
# Record all demos (outputs to docs/assets/)
./docs/demos/record.sh

# Single demo
cd /opt/stacks && vhs /path/to/docs/demos/quickstart.tape
```

Output files (GIF + WebM) are tracked with Git LFS.
