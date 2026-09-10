from __future__ import annotations

import math
import re
from functools import lru_cache

try:
    from svgpathtools import parse_path
    from svgpathtools.path import Arc, CubicBezier, Line, QuadraticBezier
except ImportError:  # pragma: no cover
    parse_path = None  # type: ignore
    Arc = CubicBezier = Line = QuadraticBezier = object  # type: ignore

# An SVG affine transform as (a, b, c, d, e, f):
#     | a c e |
#     | b d f |
#     | 0 0 1 |
Matrix = tuple[float, float, float, float, float, float]

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def apply_matrix(pt: complex, mat: Matrix | None) -> complex:
    if mat is None:
        return pt
    a, b, c, d, e, f = mat
    return complex(a * pt.real + c * pt.imag + e, b * pt.real + d * pt.imag + f)


def compose_matrix(outer: Matrix | None, inner: Matrix | None) -> Matrix | None:
    """Return ``outer * inner`` — the matrix that applies ``inner`` first, then ``outer``.

    This is the composition SVG uses both for a transform *list*
    (``transform="translate(..) scale(..)"`` scales first) and for nesting
    (an ancestor ``<g transform>`` applies after the child's own transform).
    """
    if outer is None:
        return inner
    if inner is None:
        return outer
    a1, b1, c1, d1, e1, f1 = outer
    a2, b2, c2, d2, e2, f2 = inner
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def translation_matrix(tx: float, ty: float) -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, tx, ty)


def scale_matrix(sx: float, sy: float) -> Matrix:
    return (sx, 0.0, 0.0, sy, 0.0, 0.0)


def rotation_matrix(degrees: float, cx: float = 0.0, cy: float = 0.0) -> Matrix:
    rad = math.radians(degrees)
    cos, sin = math.cos(rad), math.sin(rad)
    rot: Matrix = (cos, sin, -sin, cos, 0.0, 0.0)
    if cx or cy:
        return compose_matrix(compose_matrix(translation_matrix(cx, cy), rot), translation_matrix(-cx, -cy))  # type: ignore[return-value]
    return rot


def skew_matrix(degrees: float, axis: str) -> Matrix:
    t = math.tan(math.radians(degrees))
    return (1.0, 0.0, t, 1.0, 0.0, 0.0) if axis == "x" else (1.0, t, 0.0, 1.0, 0.0, 0.0)


def split_subpaths(path_data: str) -> list[str]:
    matches = list(re.finditer(r"(?<![eE])[Mm]", path_data))
    if not matches:
        return [path_data]
    return [path_data[matches[i].start(): matches[i + 1].start() if i + 1 < len(matches) else None].strip() for i in range(len(matches))]


def cubic_from_line(p0: complex, p1: complex) -> tuple[complex, complex, complex, complex]:
    v = p1 - p0
    return (p0, p0 + v / 3, p0 + 2 * v / 3, p1)


def cubic_from_quadratic(p0: complex, q1: complex, p1: complex) -> tuple[complex, complex, complex, complex]:
    return (p0, p0 + 2 / 3 * (q1 - p0), p1 + 2 / 3 * (q1 - p1), p1)


def cubics_from_arc(seg) -> list[tuple[complex, complex, complex, complex]]:
    cubics: list[tuple[complex, complex, complex, complex]] = []
    for i in range(4):
        t0, t1 = i / 4, (i + 1) / 4
        p0, p1 = seg.point(t0), seg.point(t1)
        eps = 1e-4
        try:
            d0 = (seg.point(t0 + eps) - seg.point(t0 - eps)) / (2 * eps) if 0 < t0 < 1 else (seg.point(t0 + eps) - p0) / eps
            d1 = (seg.point(t1 + eps) - seg.point(t1 - eps)) / (2 * eps) if 0 < t1 < 1 else (p1 - seg.point(t1 - eps)) / eps
        except Exception:
            d0 = d1 = p1 - p0
        scale = (1 / 4) / 3
        cubics.append((complex(p0), complex(p0 + d0 * scale), complex(p1 - d1 * scale), complex(p1)))
    return cubics


def subpath_to_cubics(subpath: str, mat: tuple[float, float, float, float, float, float] | None) -> tuple[list[tuple[complex, complex, complex, complex]], bool]:
    if parse_path is None:
        raise ValueError("svgpathtools is required for path parsing")
    try:
        parsed = parse_path(subpath)
    except Exception as exc:
        raise ValueError("Invalid SVG path data") from exc
    cubics: list[tuple[complex, complex, complex, complex]] = []
    is_closed = bool(re.search(r"[Zz]\s*$", subpath.strip()))
    for seg in parsed:
        if isinstance(seg, Line):
            cubics.append(cubic_from_line(apply_matrix(seg.start, mat), apply_matrix(seg.end, mat)))
        elif isinstance(seg, CubicBezier):
            cubics.append((apply_matrix(seg.start, mat), apply_matrix(seg.control1, mat), apply_matrix(seg.control2, mat), apply_matrix(seg.end, mat)))
        elif isinstance(seg, QuadraticBezier):
            cubics.append(cubic_from_quadratic(apply_matrix(seg.start, mat), apply_matrix(seg.control, mat), apply_matrix(seg.end, mat)))
        elif isinstance(seg, Arc):
            for a0, a1, a2, a3 in cubics_from_arc(seg):
                cubics.append((apply_matrix(a0, mat), apply_matrix(a1, mat), apply_matrix(a2, mat), apply_matrix(a3, mat)))
        else:
            try:
                cubics.append(cubic_from_line(apply_matrix(seg.start, mat), apply_matrix(seg.end, mat)))
            except Exception:
                continue
    if is_closed and cubics:
        if abs(cubics[-1][3] - cubics[0][0]) > 1e-6:
            cubics.append(cubic_from_line(cubics[-1][3], cubics[0][0]))
    if not is_closed and cubics and len(cubics) > 2 and abs(cubics[-1][3] - cubics[0][0]) < 1e-6:
        is_closed = True
    return cubics, is_closed


def cubics_to_control_and_knots(cubics: list[tuple[complex, complex, complex, complex]]) -> tuple[list[tuple[float, float]], list[float]]:
    if not cubics:
        return [], []
    ctrl: list[tuple[float, float]] = []
    p0, c1, c2, p3 = cubics[0]
    ctrl.extend([(float(p0.real), float(p0.imag)), (float(c1.real), float(c1.imag)), (float(c2.real), float(c2.imag)), (float(p3.real), float(p3.imag))])
    for _, c1, c2, p3 in cubics[1:]:
        ctrl.extend([(float(c1.real), float(c1.imag)), (float(c2.real), float(c2.imag)), (float(p3.real), float(p3.imag))])
    n = len(cubics)
    knots: list[float] = [0.0, 0.0, 0.0, 0.0]
    for i in range(1, n):
        v = i / n
        knots.extend([v, v, v])
    knots.extend([1.0, 1.0, 1.0, 1.0])
    assert len(knots) == len(ctrl) + 4, f"knot/ctrl mismatch {len(knots)} vs {len(ctrl)}"
    return ctrl, knots


_NAMED_COLORS = {
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0), "lime": (0, 255, 0),
    "blue": (0, 0, 255), "yellow": (255, 255, 0), "cyan": (0, 255, 255), "aqua": (0, 255, 255),
    "magenta": (255, 0, 255), "fuchsia": (255, 0, 255), "silver": (192, 192, 192),
    "gray": (128, 128, 128), "grey": (128, 128, 128), "maroon": (128, 0, 0),
    "olive": (128, 128, 0), "green": (0, 128, 0), "purple": (128, 0, 128),
    "teal": (0, 128, 128), "navy": (0, 0, 128), "orange": (255, 165, 0),
}


@lru_cache(maxsize=512)
def parse_color(color_str: str) -> tuple[int, int, int] | None:
    """Parse ``#rgb``/``#rrggbb``/``rgb(r,g,b)``/named CSS colors. ``None`` when unpaintable."""
    if not color_str:
        return None
    s = color_str.strip().lower()
    if not s or s in {"none", "transparent", "currentcolor"} or s.startswith("url("):
        return None
    if s.startswith("#"):
        h = s[1:]
        if len(h) in {3, 4}:
            h = "".join(c * 2 for c in h[:3])
        if len(h) in {6, 8}:
            try:
                return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            except ValueError:
                return None
        return None
    m = re.match(r"rgba?\s*\(([^)]*)\)", s)
    if m:
        parts = [p.strip() for p in m.group(1).replace("/", ",").split(",") if p.strip()]
        if len(parts) >= 3:
            try:
                vals = []
                for p in parts[:3]:
                    vals.append(int(round(float(p[:-1]) * 2.55)) if p.endswith("%") else int(round(float(p))))
                return tuple(max(0, min(255, v)) for v in vals)  # type: ignore[return-value]
            except ValueError:
                return None
    return _NAMED_COLORS.get(s)


@lru_cache(maxsize=512)
def color_to_true_color(color_str: str) -> int | None:
    """24-bit 0xRRGGBB for ezdxf ``true_color``; ``None`` when the color is unpaintable."""
    rgb = parse_color(color_str)
    if rgb is None:
        return None
    r, g, b = rgb
    return (r << 16) | (g << 8) | b


try:
    from ezdxf.colors import DXF_DEFAULT_COLORS as _ACI_PALETTE
except ImportError:  # pragma: no cover
    _ACI_PALETTE = ()  # type: ignore[assignment]

# ACI 7 is AutoCAD's "foreground" index — it renders black on white paper and
# white on a dark background, so near-black and near-white artwork both belong
# there rather than on a nearest-neighbour grey.
ACI_FOREGROUND = 7


def _perceptual_distance(r1: int, g1: int, b1: int, r2: int, g2: int, b2: int) -> float:
    """Redmean weighted RGB distance — a cheap stand-in for a real Lab metric.

    Plain RGB distance sends pastels to grey; weighting by the mean red level
    keeps light tints on the right hue.
    """
    rmean = (r1 + r2) / 2
    dr, dg, db = r1 - r2, g1 - g2, b1 - b2
    return (2 + rmean / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rmean) / 256) * db * db


@lru_cache(maxsize=512)
def color_to_aci(color_str: str) -> int:
    """Nearest AutoCAD Color Index over the full 256-entry palette.

    The export carries exact RGB in ``true_color`` as well, but readers that
    ignore group code 420 fall back to this index, so an accurate match is what
    keeps a color drawing readable in every CAM tool. Near-black and near-white
    both resolve to the foreground index.
    """
    rgb = parse_color(color_str)
    if rgb is None:
        return ACI_FOREGROUND
    r, g, b = rgb
    if max(r, g, b) < 40:
        return ACI_FOREGROUND
    if min(r, g, b) > 215 and (max(r, g, b) - min(r, g, b)) < 24:
        return ACI_FOREGROUND
    if not _ACI_PALETTE:
        return ACI_FOREGROUND
    best, best_dist = ACI_FOREGROUND, None
    for index in range(1, min(256, len(_ACI_PALETTE))):
        if index == ACI_FOREGROUND:
            continue
        packed = _ACI_PALETTE[index]
        pr, pg, pb = (packed >> 16) & 0xFF, (packed >> 8) & 0xFF, packed & 0xFF
        dist = _perceptual_distance(r, g, b, pr, pg, pb)
        if best_dist is None or dist < best_dist:
            best, best_dist = index, dist
    return best


@lru_cache(maxsize=512)
def color_layer_name(color_str: str) -> str:
    """Deterministic per-color layer name so CAM can map one operation per color."""
    rgb = parse_color(color_str)
    if rgb is None:
        return "VECTOR_OUTLINES"
    return "COLOR_{:02X}{:02X}{:02X}".format(*rgb)
