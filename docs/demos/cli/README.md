# CLI Demo Recordings

VHS-based terminal demo recordings for Compose Farm CLI.

## Requirements

- [VHS](https://github.com/charmbracelet/vhs): `go install github.com/charmbracelet/vhs@latest`

## Usage

```bash
# Record all demos
python docs/demos/cli/record.py

# Record specific demos
python docs/demos/cli/record.py quickstart migration
```

## Demos

| Tape | Description |
|------|-------------|
| `install.tape` | Installing with `uv tool install` |
| `quickstart.tape` | `cf ps`, `cf up`, `cf logs` |
| `logs.tape` | Viewing logs |
| `compose.tape` | `cf compose` passthrough (--help, images, exec) |
| `update.tape` | `cf update` |
| `migration.tape` | Service migration |
| `apply.tape` | `cf apply` |

## Output

GIF and WebM files saved to `docs/assets/`.
