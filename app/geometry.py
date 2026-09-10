from __future__ import annotations

import re

try:
    from svgpathtools import parse_path
    from svgpathtools.path import Arc, CubicBezier, Line, QuadraticBezier
except ImportError:  # pragma: no cover
    parse_path = None  # type: ignore
    Arc = CubicBezier = Line = QuadraticBezier = object  # type: ignore


def apply_matrix(pt: complex, mat: tuple[float, float, float, float, float, float] | None) -> complex:
    if mat is None:
        return pt
    a, b, c, d, e, f = mat
    return complex(a * pt.real + c * pt.imag + e, b * pt.real + d * pt.imag + f)


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


def color_to_aci(color_str: str) -> int:
    if not color_str or color_str.lower() == "none":
        return 7
    s = color_str.strip().lower()
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) == 6:
            try:
                r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
                if (r < 90 and g < 90 and b < 90 and abs(r - g) < 12 and abs(g - b) < 12) or (r == g == b == 0):
                    return 250
                return 7
            except ValueError:
                return 7
    return 7
