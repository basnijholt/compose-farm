# Live Stats Scroll Performance Report (CDP Trace)

Date: 2025-12-26

This report measures where time is spent while scrolling the Live Stats table.
The goal is to identify which row features cause the largest layout/paint cost
and explain the perceived "blank while scrolling" issue on mobile.

## Environment

- Host: NixOS (local dev)
- Browser: Chromium (headless) via Playwright + Chrome DevTools Protocol (CDP)
- Data source: `CF_CONFIG=/opt/stacks/compose-farm.yaml`
- Dataset size (current config):
  - anton: 2 rows
  - hp: 10 rows
  - nuc: 12 rows
  - nas: 87 rows
  - Total: 111 rows

## Methodology

We created a minimal Live Stats page and gradually re-added features to isolate
which elements cause layout/paint cost while scrolling. Each step was profiled
using a CDP trace captured during automatic scroll.

### How the traces were captured

1) Start server:

```
CF_CONFIG=/opt/stacks/compose-farm.yaml just web
```

2) Run the profiler (one example):

```
nix-shell --run "python scripts/profile_live_stats.py --url http://localhost:9001/live-stats/min?step=0 --duration-ms 12000 --scroll-steps 8 --scroll-delay-ms 700 --out .code/trace-step0.json --summary .code/trace-step0-summary.json --chromium $(command -v chromium)"
```

3) Repeat for steps 1 through 5 (see table below).

### Step definitions

We used `/live-stats/min?step=N` which switches features on progressively:

- Step 0: Plain text rows only (no badges, no actions, no update checks, no bars)
- Step 1: Add stack link + host/status badges
- Step 2: Add action button
- Step 3: Add Update column (HTMX per-row update check)
- Step 4: Add CPU/Mem bars
- Step 5: Add sticky header + zebra striping

Notes:
- Step 5 is close to the full page, but not identical.
- The image cell is still plain text (no `<code>` pill styling).

## Results

All numbers are total time (ms) spent during the 12s trace while scrolling.

```
Step  Features added                               UpdateLayoutTree  Paint   Layout
----- -------------------------------------------- ----------------  ------  ------
0     Plain text only                              3088              434     235
1     + stack link + host/status badges            3577              556     238
2     + action button                              3830              546     268
3     + Update column (HTMX)                       5555              2388    310
4     + CPU/Mem bars                               6262              3063    345
5     + sticky header + zebra                      5606              3251    304
```

## Interpretation

Measured, not guessed:

- The **Update column** is the first major cliff.
  - Paint jumps from ~550ms to ~2400ms at Step 3.
- The **CPU/Mem bars** are the second major cliff.
  - Layout and paint both rise significantly at Step 4.
- Badges and action button add only minor overhead.
- Sticky header + zebra do **not** dominate layout cost; they primarily add paint.

The scroll blanking correlates with large spikes in `UpdateLayoutTree` and
`Paint`. The data shows those spikes come primarily from:

1) Update column (HTMX per-row work)
2) CPU/Mem bar DOM (nested flex + bars)

## Conclusions

- The problem is not the number of rows alone. It is the **complexity per row**
  and the **per-row dynamic UI**.
- **Update checks** and **bar charts** are the main measured contributors.
- Sticky header and zebra are not the primary cause of the scroll blanking.

## Recommended next actions (in order)

These are based on measured data, not assumptions:

1) **Defer Update column rendering**
   - Render update cells on demand (click or after idle)
   - Or suspend update checks while scrolling

2) **Replace CPU/Mem bars with text on mobile or while scrolling**
   - Bars are expensive; plain text is cheap

3) **If still slow, consider table layout reductions**
   - `table-layout: fixed` with explicit widths + ellipsis
   - Or switch to a CSS grid for mobile

## Appendix: Files added for profiling

The following diagnostic pieces were introduced to support this analysis:

- `scripts/profile_live_stats.py`
  - Captures CDP traces and writes summary JSON
- `shell.nix`
  - Provides Playwright + Chromium on NixOS
- `src/compose_farm/web/templates/containers_minimal.html`
  - Minimal view with step-based feature toggles
- `/live-stats/min` + `/api/containers/rows-min/{host}` routes

These can be retained for ongoing profiling or removed later once we finalize
the performance fixes.
