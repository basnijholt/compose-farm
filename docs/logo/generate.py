"""Generate the Compose Farm logo, icon, and social preview.

The artwork is composed by hand in isometric scene coordinates (x, y on the
ground, z up); this script projects it to 2D and writes:

- docs/assets/logo.svg            scene + wordmark (animated)
- docs/assets/logo-scene.svg      scene only (animated, used in the README)
- docs/assets/icon.svg            square barn icon (favicon, docs header)
- src/compose_farm/web/static/icon.svg   same icon for the web UI
- docs/assets/social-preview.svg  1280x640 card for GitHub's social preview

Run with `just logo`. Render the social preview to PNG (GitHub needs a
bitmap) with:

    inkscape docs/assets/social-preview.svg -o docs/assets/social-preview.png
"""

from __future__ import annotations

import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "docs" / "assets"
STATIC = ROOT / "src" / "compose_farm" / "web" / "static"

U = 20.0  # px per scene unit
C = U * math.cos(math.pi / 6)
S = U / 2
OX, OY = 400.0, 200.0  # screen position of scene origin

INK = "#1e2a38"
LIGHT = "#e6edf3"
BLUE_TOP, BLUE_L, BLUE_R = "#6cb4f5", "#3d92e3", "#2a74c4"
GREEN, GREEN_DARK, GREEN_SIDE = "#5cb85c", "#3f8f43", "#3a7d3e"
AMBER = "#fbbf24"
TERMINAL = "#4ade80"

Vec = tuple[float, float, float]

_bbox: list[tuple[float, float]] = []


def p(x: float, y: float, z: float = 0.0) -> tuple[float, float]:
    """Project a scene point to screen coordinates (and track the bounding box)."""
    pt = (OX + (x - y) * C, OY + (x + y) * S - z * U)
    _bbox.append(pt)
    return pt


def fmt(pt: tuple[float, float]) -> str:
    """Format a screen point."""
    return f"{pt[0]:.1f},{pt[1]:.1f}"


def poly(corners: list[Vec], fill: str, cls: str = "o") -> str:
    """Filled, outlined polygon through scene points."""
    points = " ".join(fmt(p(*q)) for q in corners)
    return f'<polygon class="{cls}" points="{points}" fill="{fill}"/>'


def line(a: Vec, b: Vec, cls: str) -> str:
    """Straight line between two scene points."""
    (x1, y1), (x2, y2) = p(*a), p(*b)
    return f'<line class="{cls}" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>'


def box(lo: Vec, hi: Vec, *, ribs: bool = True, cls: str = "o") -> list[str]:
    """Shipping container: top, left-front (y=max) and right-front (x=max) faces."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    out = [
        poly([(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)], BLUE_L, cls),
        poly([(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)], BLUE_R, cls),
        poly([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], BLUE_TOP, cls),
    ]
    if ribs:
        step = 0.4
        out += [
            line((x0 + i * step, y1, z0 + 0.15), (x0 + i * step, y1, z1 - 0.15), "rib")
            for i in range(1, round((x1 - x0) / step))
        ]
        out += [
            line((x1, y0 + i * step, z0 + 0.15), (x1, y0 + i * step, z1 - 0.15), "rib")
            for i in range(1, round((y1 - y0) / step))
        ]
    return out


def plot(x0: float, x1: float, y0: float, y1: float) -> list[str]:
    """Green ground slab."""
    t = 0.35
    return [
        poly([(x0, y1, 0), (x1, y1, 0), (x1, y1, -t), (x0, y1, -t)], GREEN_SIDE),
        poly([(x1, y0, 0), (x1, y1, 0), (x1, y1, -t), (x1, y0, -t)], GREEN_DARK),
        poly([(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)], GREEN),
    ]


def link(i: int, *route: tuple[float, float]) -> list[str]:
    """Dashed network cable with an outgoing and a returning pulse."""
    fwd = " L".join(fmt(p(*q)) for q in route)
    back = " L".join(fmt(p(*q)) for q in reversed(route))
    return [
        f'<path class="dash" d="M{fwd}"/>',
        f'<path class="pulse out" pathLength="100" style="animation-delay:-{i * 0.7:.1f}s" '
        f'd="M{fwd}"/>',
        f'<path class="pulse back" pathLength="100" '
        f'style="animation-delay:-{i * 0.7 + 1.4:.1f}s" d="M{back}"/>',
    ]


# --- barn -----------------------------------------------------------------
X0, X1, Y0, Y1, WALL, RIDGE = -2.2, 2.2, -3.0, 3.0, 3.0, 5.0


def barn(*, ribs: bool = True) -> list[str]:
    """Container barn with a terminal in the loft window."""
    out = [
        poly([(X0, Y0, WALL), (0, Y0, RIDGE), (0, Y1, RIDGE), (X0, Y1, WALL)], BLUE_L),
        poly([(X1, Y0, 0), (X1, Y1, 0), (X1, Y1, WALL), (X1, Y0, WALL)], BLUE_R),
    ]
    if ribs:
        out += [line((X1, y / 2, 0.15), (X1, y / 2, WALL - 0.15), "rib") for y in range(-5, 6)]
    out.append(
        poly(
            [(X0, Y1, 0), (X1, Y1, 0), (X1, Y1, WALL), (0, Y1, RIDGE), (X0, Y1, WALL)],
            BLUE_L,
        )
    )
    if ribs:
        for i in range(1, 10):
            x = X0 + i * 0.44
            top = WALL + (RIDGE - WALL) * (1 - abs(x) / X1)
            out.append(line((x, Y1, 0.15), (x, Y1, top - 0.15), "rib"))
    eave = (X1 + 0.35, WALL - 0.25)
    out += [
        poly(
            [(0, Y0, RIDGE), (eave[0], Y0, eave[1]), (eave[0], Y1, eave[1]), (0, Y1, RIDGE)],
            BLUE_TOP,
        ),
        poly([(-0.9, Y1, 0), (0.9, Y1, 0), (0.9, Y1, 2.1), (-0.9, Y1, 2.1)], INK),
        poly([(-0.62, Y1, 3.1), (0.62, Y1, 3.1), (0.62, Y1, 4.05), (-0.62, Y1, 4.05)], INK),
    ]
    # `>_` drawn in the plane of the gable wall: u runs along x, v along z
    ex, ey = OX - Y1 * C, OY + Y1 * S
    out += [
        f'<g transform="matrix({C:.2f} {S:.2f} 0 {-U:.2f} {ex:.1f} {ey:.1f})">',
        '<path class="term" d="M-0.42,3.83 L-0.2,3.575 L-0.42,3.32"/>',
        '<path class="term cursor" d="M-0.02,3.32 H0.36"/>',
        "</g>",
    ]
    return out


def vane(*, arrow: bool = True) -> list[str]:
    """Weathervane on the ridge: navy casing, amber core, arrow turns in the ground plane."""
    bx, by = p(0, 0.8, RIDGE)
    _bbox.extend([(bx - 18, by - 45), (bx + 18, by - 45)])
    k = 0.7
    iso = f"matrix({C * k:.2f} {S * k:.2f} {-C * k:.2f} {S * k:.2f} 0 0)"
    pole = f"M{bx:.1f},{by:.1f} V{by - 39:.1f}"
    shaft = "M-0.7,0 H0.6"
    ball = f'<circle class="vfill" cx="{bx:.1f}" cy="{by - 40:.1f}" r="3"/>'
    if not arrow:
        return [f'<path class="vcase" d="{pole}"/><path class="vcore" d="{pole}"/>', ball]
    return [
        f'<path class="vcase" d="{pole}"/><path class="vcore" d="{pole}"/>',
        f'<g transform="translate({bx:.1f} {by - 26:.1f}) {iso}"><g class="spin">',
        f'<path class="vcase" d="{shaft}"/>',
        '<polygon class="vfill" points="1.15,0 0.5,0.42 0.5,-0.42"/>',
        '<polygon class="vfill" points="-0.35,0 -0.8,0.4 -1.15,0.4 -0.85,0 -1.15,-0.4 -0.8,-0.4"/>',
        f'<path class="vcore" d="{shaft}"/>',
        "</g></g>",
        ball,
    ]


def field() -> list[str]:
    """Field with furrows and rows of container crops."""
    fx0, fx1, fy0, fy1 = 2.8, 8.8, 3.2, 9.2
    rows = [fy0 + 1.2 + 1.8 * j for j in range(3)]
    cols = [fx0 + 0.9 + 1.4 * i for i in range(4)]
    out = plot(fx0, fx1, fy0, fy1)
    out += [line((fx0 + 0.3, y, 0), (fx1 - 0.3, y, 0), "furrow") for y in rows]
    crops = [(i, j) for i in range(len(cols)) for j in range(len(rows))]
    crops.sort(key=lambda ij: ij[0] + ij[1])  # back to front, so nearer crops paint on top
    for i, j in crops:
        x, y = cols[i], rows[j]
        out.append(f'<g class="crop" style="animation-delay:{(i + j) * 0.12:.2f}s">')
        out += box((x - 0.3, y - 0.22, 0), (x + 0.3, y + 0.22, 0.38), ribs=False, cls="o thin")
        out.append("</g>")
    return out


# Container that migrates between the two left hosts
MIGRANT_FROM: Vec = (-9.8, 0.6, 1.0)
MIGRANT_TO: Vec = (-4.8, 7.0, 1.0)
MIGRANT_SIZE: Vec = (1.6, 1.4, 1.0)


def _hop() -> tuple[float, float, float]:
    """Screen offset from host A to host B, and the (negative) height of the arc's peak."""
    (sx0, sy0), (sx1, sy1) = p(*MIGRANT_FROM), p(*MIGRANT_TO)
    dy = sy1 - sy0
    return sx1 - sx0, dy, min(0.0, dy) - 30  # peak clears both stacks


def migrant(*, animated: bool) -> list[str]:
    """The container that hops from one host to another (see `migrate` keyframes)."""
    x, y, z = MIGRANT_FROM
    dx, dy, dz = MIGRANT_SIZE
    container = box((x, y, z), (x + dx, y + dy, z + dz))
    if not animated:
        return container
    top_x, top_y = p(x, y, z + dz)
    _bbox.append((top_x + _hop()[0] / 2, top_y + _hop()[2]))
    return ['<g class="migrant">', *container, "</g>"]


def scene(*, animated: bool) -> list[str]:
    """Full farm in painter's order (back to front)."""
    out = ["<!-- hosts (back) -->"]
    out += plot(-10.5, -7.5, -0.5, 2.5) + plot(-0.5, 2.5, -10.5, -7.5)
    out += box((-9.8, 0.0, 0), (-8.2, 2.0, 1)) + box((0.0, -9.8, 0), (2.0, -8.2, 1))
    out.append("<!-- links -->")
    out += link(0, (-2.2, 1.0), (-7.5, 1.0))
    out += link(1, (1.0, -3), (1.0, -7.5))
    out += link(2, (-1.2, 3), (-1.2, 7.5), (-3.5, 7.5))
    out += link(3, (2, -1.2), (7.5, -1.2), (7.5, -3.5))
    out.append("<!-- barn -->")
    out += barn()
    out.append("<!-- field -->")
    out += field()
    out.append("<!-- hosts (front) -->")
    out += plot(-5.5, -2.5, 6, 9) + plot(6, 9, -5.5, -2.5)
    out += box((-5.0, 6.6, 0), (-3.0, 8.4, 1))
    out += box((6.5, -5.0, 0), (8.5, -3.0, 1)) + box((6.5, -4.6, 1), (8.5, -3.0, 2))
    out.append("<!-- migrating container -->")
    out += migrant(animated=animated)
    out.append("<!-- weathervane -->")
    out += vane()
    return out


# --- wordmark -------------------------------------------------------------
# Monoline letters on centerlines (stroke 13), cap height 56 including stroke.
T, B, MID, SW = 6.5, 49.5, 28.0, 13.0


def _c(x: float, w: float) -> str:
    lx, rx, k = x + 6.5, x + 6.5 + w, 13
    return f"M{rx},{T} H{lx + k} A{k},{k} 0 0 0 {lx},{T + k} V{B - k} A{k},{k} 0 0 0 {lx + k},{B} H{rx}"


def _o(x: float, w: float) -> str:
    lx, rx, k = x + 6.5, x + 6.5 + w, 14
    return (
        f"M{lx + k},{T} H{rx - k} A{k},{k} 0 0 1 {rx},{T + k} V{B - k} A{k},{k} 0 0 1 {rx - k},{B} "
        f"H{lx + k} A{k},{k} 0 0 1 {lx},{B - k} V{T + k} A{k},{k} 0 0 1 {lx + k},{T} Z"
    )


def _p(x: float, w: float) -> str:
    lx, rx, k, y = x + 6.5, x + 6.5 + w, 11, 30
    return f"M{lx},{B} V{T} H{rx - k} A{k},{k} 0 0 1 {rx},{T + k} V{y - k} A{k},{k} 0 0 1 {rx - k},{y} H{lx}"


def _r(x: float, w: float) -> str:
    rx = x + 6.5 + w
    return _p(x, w) + f" M{rx - 13},30 L{rx},{B}"


def _s(x: float, w: float) -> str:
    lx, rx, k = x + 6.5, x + 6.5 + w, 10.75
    return (
        f"M{rx},{T} H{lx + k} A{k},{k} 0 0 0 {lx + k},{MID} H{rx - k} "
        f"A{k},{k} 0 0 1 {rx - k},{B} H{lx}"
    )


def _e(x: float, w: float) -> str:
    lx, rx = x + 6.5, x + 6.5 + w
    return f"M{rx},{T} H{lx} V{B} H{rx} M{lx},{MID} H{rx - 6}"


def _f(x: float, w: float) -> str:
    lx, rx = x + 6.5, x + 6.5 + w
    return f"M{rx},{T} H{lx} V{B} M{lx},{MID} H{rx - 6}"


def _a(x: float, w: float) -> str:
    lx, rx, k = x + 6.5, x + 6.5 + w, 14
    return (
        f"M{lx},{B} V{T + k} A{k},{k} 0 0 1 {lx + k},{T} H{rx - k} A{k},{k} 0 0 1 {rx},{T + k} "
        f"V{B} M{lx},{MID + 3} H{rx}"
    )


def _m(x: float, w: float) -> str:
    """Filled outline, so the pointed joins don't overshoot the cap height."""
    x0, x1 = x, x + w + SW
    xm = (x0 + x1) / 2
    pts = [
        (x0, 56),
        (x0, 0),
        (x0 + SW + 2, 0),
        (xm, 23),
        (x1 - SW - 2, 0),
        (x1, 0),
        (x1, 56),
        (x1 - SW, 56),
        (x1 - SW, 20),
        (xm, 43),
        (x0 + SW, 20),
        (x0 + SW, 56),
    ]
    return "F" + " ".join(f"{a:g},{b:g}" for a, b in pts)


LETTERS = [(_c, 34), (_o, 36), (_m, 42), (_p, 33), (_o, 36), (_s, 34), (_e, 30), None,
           (_f, 30), (_a, 36), (_r, 33), (_m, 42)]  # fmt: skip


def wordmark() -> tuple[list[str], float]:
    """Paths for "COMPOSE FARM" and the total width (height is 56)."""
    gap, space, x, out = 9.0, 28.0, 0.0, []
    for item in LETTERS:
        if item is None:
            x += space - gap
            continue
        fn, w = item
        d = fn(x, w)
        if d.startswith("F"):
            out.append(f'<polygon class="wfill" points="{d[1:]}"/>')
        else:
            out.append(f'<path d="{d}"/>')
        x += w + SW + gap
    return out, x - gap


# --- styles ---------------------------------------------------------------
def _spin_keyframes() -> str:
    """Gusty full turn: advances 90 degrees, overshoots a little, settles."""
    frames = []
    for i in range(21):
        t = i / 20
        angle = 360 * t + 30 * math.sin(4 * math.pi * t)
        frames.append(f"{t * 100:.0f}%{{transform:rotate({angle:.1f}deg)}}")
    return "".join(frames)


def _migrate_keyframes() -> str:
    """Sit on host A, arc over to host B, sit, arc back."""
    dx, dy, peak = _hop()
    lift = 14

    def at(x: float, y: float) -> str:
        return f"{{transform:translate({x:.1f}px,{y:.1f}px)}}"

    return (
        f"0%,36%{at(0, 0)}39%{at(0, -lift)}44%{at(dx / 2, peak)}"
        f"51%{at(dx, dy - lift)}54%,86%{at(dx, dy)}89%{at(dx, dy - lift)}"
        f"94%{at(dx / 2, peak)}98%{at(0, -lift)}100%{at(0, 0)}"
    )


def styles(*, animated: bool, dark: str) -> str:
    """Stylesheet. `dark` is "media" (follow the viewer), "force", or "none"."""
    css = [
        f".o{{stroke:{INK};stroke-width:2.2;stroke-linejoin:round}}",
        ".o.thin{stroke-width:1.4}",
        f".rib{{stroke:{INK};stroke-opacity:.35;stroke-width:1.2}}",
        f".furrow{{stroke:{GREEN_DARK};stroke-width:2;stroke-linecap:round}}",
        f".dash{{fill:none;stroke:{INK};stroke-width:2.2;stroke-dasharray:6 5;"
        "stroke-linecap:round;stroke-linejoin:round}",
        f".term{{fill:none;stroke:{TERMINAL};stroke-width:1.8;stroke-linecap:round;"
        "stroke-linejoin:round;vector-effect:non-scaling-stroke}",
        f".vcase{{fill:none;stroke:{INK};stroke-width:5;stroke-linecap:round;"
        "stroke-linejoin:round;vector-effect:non-scaling-stroke}",
        f".vcore{{fill:none;stroke:{AMBER};stroke-width:2;stroke-linecap:round;"
        "stroke-linejoin:round;vector-effect:non-scaling-stroke}",
        f".vfill{{fill:{AMBER};stroke:{INK};stroke-width:1.5;stroke-linejoin:round;"
        "vector-effect:non-scaling-stroke}",
        f".word{{fill:none;stroke:{INK};stroke-width:13;stroke-linejoin:miter}}",
        f".wfill{{fill:{INK};stroke:none}}",
        ".pulse{display:none}",
    ]
    if animated:
        css += [
            ".spin{transform-origin:0 0;animation:spin 10s linear infinite}",
            f"@keyframes spin{{{_spin_keyframes()}}}",
            ".pulse{display:inline;fill:none;stroke-width:4.5;stroke-linecap:round;"
            "stroke-dasharray:12 200;stroke-dashoffset:12;animation:pulse 2.8s linear infinite}",
            ".pulse.out{stroke:#facc15;filter:drop-shadow(0 0 3px #f59e0b)}",
            ".pulse.back{stroke:#4ade80;filter:drop-shadow(0 0 3px #16a34a)}",
            "@keyframes pulse{0%{stroke-dashoffset:12}65%,100%{stroke-dashoffset:-100}}",
            ".crop{transform-box:fill-box;transform-origin:50% 100%;"
            "animation:grow 12s ease-out infinite both}",
            "@keyframes grow{0%,78%{transform:scale(1)}84%,88%{transform:scale(0)}"
            "94%{transform:scale(1.15)}97%,100%{transform:scale(1)}}",
            ".migrant{animation:migrate 12s ease-in-out infinite}",
            f"@keyframes migrate{{{_migrate_keyframes()}}}",
            ".cursor{animation:blink 1.2s steps(1) infinite}",
            "@keyframes blink{50%{opacity:0}}",
            "@media (prefers-reduced-motion:reduce){.spin,.crop,.migrant,.cursor{animation:none}"
            ".pulse{display:none}}",
        ]
    on_dark = f".dash,.word{{stroke:{LIGHT}}}.wfill{{fill:{LIGHT}}}"
    if dark == "media":
        css.append(f"@media (prefers-color-scheme:dark){{{on_dark}}}")
    elif dark == "force":
        css.append(on_dark)
    return "\n".join(css)


# --- documents ------------------------------------------------------------
def svg(body: list[str], view: tuple[float, float, float, float], css: str, **attrs: str) -> str:
    """Wrap elements in an <svg> document."""
    x, y, w, h = view
    extra = "".join(f' {k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x:.0f} {y:.0f} {w:.0f} {h:.0f}" '
        f'width="{w:.0f}" height="{h:.0f}" role="img" aria-label="Compose Farm"{extra}>\n'
        f"<title>Compose Farm</title>\n<style>\n{css}\n</style>\n" + "\n".join(body) + "\n</svg>\n"
    )


def bounds(pad: float) -> tuple[float, float, float, float]:
    """Padded bounding box of everything projected since the last reset."""
    xs, ys = [q[0] for q in _bbox], [q[1] for q in _bbox]
    return min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad


def logo(*, with_wordmark: bool) -> str:
    """Animated farm, optionally with the wordmark underneath."""
    _bbox.clear()
    body = scene(animated=True)
    x, y, w, h = bounds(pad=10)
    if with_wordmark:
        letters, width = wordmark()
        scale = (w - 20) / width
        top = y + h + 6
        body += [
            f'<g class="word" transform="translate({x + 10:.1f} {top:.1f}) scale({scale:.3f})">',
            *letters,
            "</g>",
        ]
        h += 6 + 56 * scale + 10
    return svg(body, (x, y, w, h), styles(animated=True, dark="media"))


def icon() -> str:
    """Square, static barn on a plot: reads at favicon sizes."""
    _bbox.clear()
    body = plot(-3.0, 3.0, -3.8, 3.8) + barn(ribs=False) + vane(arrow=False)
    x, y, w, h = bounds(pad=6)
    side = max(w, h)
    view = (x - (side - w) / 2, y - (side - h) / 2, side, side)
    css = styles(animated=False, dark="none")
    # strokes scale with the image so they stay proportional at favicon sizes
    css += "\n.vcase,.vcore,.vfill,.term{vector-effect:none}.term{stroke-width:.1}"
    return svg(body, view, css)


def social_preview() -> str:
    """1280x640 card with the farm, wordmark, and tagline on a dark background."""
    _bbox.clear()
    farm = scene(animated=False)
    x, y, w, h = bounds(pad=0)
    letters, width = wordmark()
    scale = 0.62 * w / width
    farm_scale = 400 / h
    tx = 640 - (x + w / 2) * farm_scale
    ty = 60 - y * farm_scale
    word_x = 640 - width * scale * farm_scale / 2
    word_y = 60 + h * farm_scale + 34
    body = [
        '<rect width="1280" height="640" fill="#0d1117"/>',
        f'<g transform="translate({tx:.1f} {ty:.1f}) scale({farm_scale:.3f})">',
        *farm,
        "</g>",
        f'<g class="word" transform="translate({word_x:.1f} {word_y:.1f}) '
        f'scale({scale * farm_scale:.3f})">',
        *letters,
        "</g>",
        f'<text x="640" y="{word_y + 56 * scale * farm_scale + 46:.0f}" text-anchor="middle" '
        f'fill="#9aa7b4" font-family="Inter, \'DejaVu Sans\', sans-serif" font-size="26">'
        "Agentless multi-host Docker Compose over SSH</text>",
    ]
    return svg(body, (0, 0, 1280, 640), styles(animated=False, dark="force"))


def main() -> None:
    """Write all variants."""
    outputs = {
        ASSETS / "logo.svg": logo(with_wordmark=True),
        ASSETS / "logo-scene.svg": logo(with_wordmark=False),
        ASSETS / "icon.svg": icon(),
        STATIC / "icon.svg": icon(),
        ASSETS / "social-preview.svg": social_preview(),
    }
    for path, content in outputs.items():
        path.write_text(content)
        print(f"wrote {path.relative_to(ROOT)} ({len(content.encode()) / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
