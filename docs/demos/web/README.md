# Web UI Demo Recordings

Playwright-based demo recording for Compose Farm web UI.

## Requirements

- Chromium: `playwright install chromium`
- ffmpeg: `apt install ffmpeg` or `brew install ffmpeg`

## Usage

```bash
# Record all demos
python docs/demos/web/record.py

# Record specific demo
python docs/demos/web/record.py navigation
```

## Demos

| Demo | Description |
|------|-------------|
| `navigation` | Command palette fuzzy search and navigation |
| `stack` | Stack restart/logs via command palette |
| `themes` | Theme switching with arrow key preview |
| `workflow` | Full workflow: filter, navigate, logs, themes |
| `console` | Console terminal running cf commands |
| `shell` | Container shell exec with top |

## Output

WebM and GIF files saved to `docs/assets/web-{demo}.{webm,gif}`.

## Files

- `record.py` - Orchestration script
- `conftest.py` - Playwright fixtures, helper functions
- `demo_*.py` - Individual demo scripts

## Notes

- Uses real config at `/opt/stacks/compose-farm.yaml`
- Adjust `pause(page, ms)` calls to control timing
- Viewport: 1280x720
