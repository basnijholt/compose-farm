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
# "COMPOSE FARM" set in Inter ExtraBold (SIL Open Font License) with its own
# kerning plus 4% tracking, converted to outlines so it renders identically
# without the font installed. Cap height is 56.
WORDMARK_WIDTH = 668.4
WORDMARK_HEIGHT = 56.0
WORDMARK = (
    "M29.6 56.8Q22 56.8 16 53.4Q10 50.1 6.6 43.6Q3.1 37.2 3.1 28Q3.1 18.8 6.6 12.4Q10.1 5.9 "
    "16.1 2.6Q22.1 -0.8 29.6 -0.8Q34.6 -0.8 38.8 0.6Q43.1 2 46.3 4.6Q49.6 7.3 51.7 11.1Q53.7 "
    "14.9 54.3 19.8H40.9Q40.6 17.7 39.7 16.1Q38.7 14.4 37.3 13.3Q35.9 12.1 34.1 11.5Q32.2 "
    "10.9 29.9 10.9Q25.8 10.9 22.8 12.9Q19.8 15 18.2 18.8Q16.6 22.6 16.6 28Q16.6 33.6 18.3 "
    "37.5Q19.9 41.3 22.9 43.2Q25.8 45.1 29.8 45.1Q32.1 45.1 34 44.5Q35.9 43.9 37.3 42.7Q38.7 "
    "41.6 39.7 39.9Q40.6 38.3 40.9 36.2H54.3Q54 40 52.2 43.6Q50.4 47.2 47.3 50.2Q44.2 53.2 "
    "39.7 55Q35.3 56.8 29.6 56.8Z M90.1 56.8Q82.5 56.8 76.5 53.4Q70.5 50.1 66.9 43.6Q63.4 "
    "37.2 63.4 28Q63.4 18.8 66.9 12.4Q70.5 5.9 76.5 2.6Q82.5 -0.8 90.1 -0.8Q97.6 -0.8 103.6 "
    "2.6Q109.7 5.9 113.2 12.4Q116.7 18.8 116.7 28Q116.7 37.2 113.2 43.7Q109.7 50.1 103.6 "
    "53.4Q97.6 56.8 90.1 56.8ZM90.1 45.1Q94.1 45.1 97.1 43.1Q100 41.2 101.6 37.4Q103.2 33.6 "
    "103.2 28Q103.2 22.5 101.6 18.6Q100 14.8 97.1 12.9Q94.1 10.9 90.1 10.9Q86 10.9 83.1 "
    "12.9Q80.1 14.8 78.5 18.7Q77 22.5 77 28Q77 33.6 78.5 37.4Q80.1 41.2 83.1 43.1Q86 45.1 "
    "90.1 45.1Z M127.3 56V0H147.9L155.1 21.8Q155.7 23.8 156.4 27.1Q157.2 30.4 158 34.1Q158.8 "
    "37.9 159.4 41.4Q160.1 44.9 160.5 47.4H158Q158.4 44.9 159.1 41.4Q159.7 37.9 160.5 "
    "34.1Q161.3 30.4 162 27.1Q162.8 23.8 163.4 21.8L170.4 0H191.1V56H177.8V31.9Q177.8 30 "
    "177.9 27Q178 24.1 178 20.6Q178.1 17.2 178.2 13.7Q178.3 10.1 178.3 7.1H179.1Q178.4 10.4 "
    "177.6 14Q176.7 17.6 175.8 21Q174.9 24.4 174.1 27.2Q173.3 30 172.7 31.9L164.8 "
    "56H153.7L145.6 31.9Q145 30 144.2 27.2Q143.4 24.4 142.5 21Q141.6 17.6 140.7 14.1Q139.8 "
    "10.5 139 7.1H140Q140 10.1 140.1 13.6Q140.2 17.1 140.3 20.6Q140.4 24.1 140.5 27Q140.6 30 "
    "140.6 31.9V56Z M202.9 56V0H225.9Q232.2 0 236.8 2.4Q241.4 4.9 243.8 9.2Q246.3 13.6 246.3 "
    "19.4Q246.3 25.1 243.8 29.4Q241.2 33.7 236.6 36.1Q231.9 38.5 225.5 "
    "38.5H211.3V28H223.1Q226.2 28 228.3 26.9Q230.4 25.9 231.5 23.9Q232.5 21.9 232.5 "
    "19.4Q232.5 16.7 231.5 14.8Q230.4 12.9 228.3 11.8Q226.2 10.7 223.1 10.7H216.2V56Z M281.6 "
    "56.8Q274 56.8 268 53.4Q262 50.1 258.5 43.6Q254.9 37.2 254.9 28Q254.9 18.8 258.5 12.4Q262 "
    "5.9 268 2.6Q274 -0.8 281.6 -0.8Q289.1 -0.8 295.2 2.6Q301.2 5.9 304.7 12.4Q308.2 18.8 "
    "308.2 28Q308.2 37.2 304.7 43.7Q301.2 50.1 295.2 53.4Q289.1 56.8 281.6 56.8ZM281.6 "
    "45.1Q285.7 45.1 288.6 43.1Q291.5 41.2 293.1 37.4Q294.7 33.6 294.7 28Q294.7 22.5 293.1 "
    "18.6Q291.5 14.8 288.6 12.9Q285.7 10.9 281.6 10.9Q277.5 10.9 274.6 12.9Q271.6 14.8 270.1 "
    "18.7Q268.5 22.5 268.5 28Q268.5 33.6 270.1 37.4Q271.6 41.2 274.6 43.1Q277.5 45.1 281.6 "
    "45.1Z M340.4 56.8Q333.4 56.8 328.2 54.6Q323 52.5 320.1 48.3Q317.2 44 317.1 "
    "37.5H329.9Q330 40.2 331.3 42.1Q332.6 43.9 334.9 44.8Q337.2 45.7 340.2 45.7Q343 45.7 345 "
    "45Q347 44.2 348 42.9Q349.1 41.5 349.1 39.7Q349.1 38.1 348.1 37Q347.1 35.9 345.1 35Q343.1 "
    "34.1 340 33.4L334.1 32.1Q326.9 30.4 322.7 26.6Q318.6 22.9 318.6 16.6Q318.6 11.4 321.4 "
    "7.5Q324.2 3.6 329.1 1.4Q334 -0.8 340.4 -0.8Q346.9 -0.8 351.7 1.4Q356.4 3.6 359.1 "
    "7.6Q361.7 11.5 361.8 16.7H349Q348.8 13.6 346.5 12Q344.3 10.3 340.3 10.3Q337.7 10.3 335.9 "
    "11Q334.1 11.7 333.2 12.9Q332.3 14.1 332.3 15.7Q332.3 17.4 333.3 18.6Q334.3 19.8 336.2 "
    "20.6Q338.1 21.3 340.6 21.9L345.4 23Q349.5 23.9 352.7 25.4Q355.8 26.9 358 28.9Q360.2 31 "
    "361.4 33.7Q362.5 36.4 362.5 39.8Q362.5 45.1 359.9 48.9Q357.2 52.7 352.3 54.7Q347.3 56.8 "
    "340.4 56.8Z M372.7 56V0H411.5V10.8H385.9V22.4H409.5V33H385.9V45.2H411.5V56Z M442.6 "
    "56V0H480.7V10.8H455.9V25H478.2V35.6H455.9V56Z M481.2 56 499.8 0H517.6L536.9 56H522L514.2 "
    "31.5Q512.3 25.4 510.6 18.5Q508.8 11.6 507.1 4.2H510Q508.3 11.7 506.8 18.5Q505.2 25.4 "
    "503.4 31.5L495.9 56ZM493.9 44.1V33.9H524.2V44.1Z M546.1 56V0H569Q575.3 0 579.9 2.3Q584.5 "
    "4.5 586.9 8.7Q589.4 12.9 589.4 18.6Q589.4 24.4 586.9 28.4Q584.4 32.5 579.7 34.6Q575 36.7 "
    "568.6 36.7H554V26.2H566.2Q569.3 26.2 571.4 25.4Q573.5 24.6 574.6 22.9Q575.6 21.2 575.6 "
    "18.6Q575.6 16 574.6 14.2Q573.5 12.5 571.4 11.6Q569.3 10.7 566.2 10.7H559.3V56ZM576.8 56 "
    "563.2 30.4H577.4L591.4 56Z M600.1 56V0H620.8L627.9 21.8Q628.5 23.8 629.3 27.1Q630.1 30.4 "
    "630.9 34.1Q631.6 37.9 632.3 41.4Q633 44.9 633.4 47.4H630.9Q631.3 44.9 631.9 41.4Q632.6 "
    "37.9 633.3 34.1Q634.1 30.4 634.9 27.1Q635.7 23.8 636.3 21.8L643.3 "
    "0H664V56H650.7V31.9Q650.7 30 650.7 27Q650.8 24.1 650.9 20.6Q651 17.2 651.1 13.7Q651.2 "
    "10.1 651.2 7.1H652Q651.3 10.4 650.4 14Q649.5 17.6 648.6 21Q647.8 24.4 647 27.2Q646.1 30 "
    "645.6 31.9L637.6 56H626.5L618.4 31.9Q617.9 30 617.1 27.2Q616.2 24.4 615.3 21Q614.4 17.6 "
    "613.5 14.1Q612.6 10.5 611.9 7.1H612.8Q612.9 10.1 613 13.6Q613 17.1 613.1 20.6Q613.2 24.1 "
    "613.3 27Q613.4 30 613.4 31.9V56Z"
)


def wordmark(x: float, y: float, width: float) -> str:
    """The wordmark scaled to `width`, with its top-left corner at (x, y)."""
    scale = width / WORDMARK_WIDTH
    return (
        f'<path class="wfill" transform="translate({x:.1f} {y:.1f}) scale({scale:.4f})" '
        f'd="{WORDMARK}"/>'
    )


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
    on_dark = f".dash{{stroke:{LIGHT}}}.wfill{{fill:{LIGHT}}}"
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
        width = w - 40
        body.append(wordmark(x + 20, y + h + 4, width))
        h += 4 + WORDMARK_HEIGHT * width / WORDMARK_WIDTH + 12
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
    """1280x640 card with the farm and wordmark on a dark background."""
    _bbox.clear()
    farm = scene(animated=False)
    x, y, w, h = bounds(pad=0)
    farm_scale = 420 / h
    word_w = 0.62 * w * farm_scale
    word_h = WORDMARK_HEIGHT * word_w / WORDMARK_WIDTH
    gap = 40
    top = (640 - h * farm_scale - gap - word_h) / 2
    body = [
        '<rect width="1280" height="640" fill="#0d1117"/>',
        f'<g transform="translate({640 - (x + w / 2) * farm_scale:.1f} {top - y * farm_scale:.1f}) '
        f'scale({farm_scale:.3f})">',
        *farm,
        "</g>",
        wordmark(640 - word_w / 2, top + h * farm_scale + gap, word_w),
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
