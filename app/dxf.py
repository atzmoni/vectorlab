from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET

import ezdxf

try:
    from svgpathtools import parse_path
    from svgpathtools.path import Arc, CubicBezier, Line, QuadraticBezier
except ImportError:  # pragma: no cover
    parse_path = None  # type: ignore
    Arc = CubicBezier = Line = QuadraticBezier = object  # type: ignore

MAX_SVG_CHARS = 20_000_000


def _root(svg: str) -> ET.Element:
    if not isinstance(svg, str) or not svg.strip() or len(svg) > MAX_SVG_CHARS:
        raise ValueError("SVG document is empty or exceeds the size limit")
    try:
        return ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError("Invalid SVG document") from exc


def _parse_viewbox(svg: str) -> tuple[float, float]:
    root = _root(svg)
    viewbox = root.attrib.get("viewBox", "").replace(",", " ").split()
    if len(viewbox) == 4:
        try:
            width, height = float(viewbox[2]), float(viewbox[3])
            if width > 0 and height > 0:
                return width, height
        except ValueError:
            pass

    def numeric(value: str) -> float:
        match = re.search(r"[-+]?(?:\d+\.?\d*|\.\d+)", value)
        return float(match.group(0)) if match else 0.0

    return max(1.0, numeric(root.attrib.get("width", ""))), max(1.0, numeric(root.attrib.get("height", "")))


def _parse_style_classes(svg: str) -> dict[str, dict[str, str]]:
    classes: dict[str, dict[str, str]] = {}
    for m in re.finditer(r"<style[^>]*>(.*?)</style>", svg, flags=re.DOTALL | re.IGNORECASE):
        text = re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))
        for cm in re.finditer(r"\.([A-Za-z0-9_-]+)\s*\{([^}]+)\}", text):
            props: dict[str, str] = {}
            for part in cm.group(2).split(";"):
                if ":" in part:
                    k, v = part.split(":", 1)
                    props[k.strip().lower()] = v.strip()
            classes[cm.group(1)] = props
    return classes


def _collect_g_fills(svg: str) -> dict[ET.Element, str]:
    """Map each element to inherited fill from ancestor <g>."""
    try:
        root = _root(svg)
    except Exception:
        return {}
    g_fill: dict[ET.Element, str] = {}
    # Walk tree, propagate g fill
    def walk(elem: ET.Element, inherited: str | None):
        # Determine current fill from elem's own attributes (including style)
        # For g elements, capture fill
        tag = elem.tag.rsplit("}", 1)[-1].lower() if isinstance(elem.tag, str) else ""
        cur = inherited
        # Check direct fill attribute
        if "fill" in elem.attrib:
            cur = elem.attrib["fill"].strip()
        # Check style
        style = elem.attrib.get("style", "")
        if style:
            for part in style.split(";"):
                if "fill" in part.lower():
                    k, v = part.split(":", 1) if ":" in part else ("", "")
                    if k.strip().lower() == "fill":
                        cur = v.strip()
        g_fill[elem] = cur if cur is not None else ""
        for child in elem:
            walk(child, cur)
    walk(root, None)
    return g_fill


def _effective_style(elem: ET.Element, style_classes: dict[str, dict[str, str]], g_fill: dict[ET.Element, str]) -> dict[str, str]:
    style: dict[str, str] = {}
    for cls in elem.attrib.get("class", "").split():
        if cls in style_classes:
            style.update(style_classes[cls])
    inline = elem.attrib.get("style", "")
    if inline:
        for part in inline.split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                style[k.strip().lower()] = v.strip()
    for key in ("fill", "stroke", "stroke-width", "fill-opacity", "stroke-opacity"):
        if key in elem.attrib:
            style[key] = elem.attrib[key].strip()
    # Inherit fill from parent <g> if still missing
    if "fill" not in style and not elem.attrib.get("class"):
        inherited = g_fill.get(elem, "")
        if inherited and inherited.lower() != "none":
            # Also check ancestor chain - our g_fill mapping already propagated, but for path we stored its own parent fill
            # Use it if present
            if inherited:
                style["fill"] = inherited
    return style


def extract_path_data(svg: str) -> list[str]:
    paths: list[str] = []
    for element in _root(svg).iter():
        tag = element.tag.rsplit("}", 1)[-1].lower() if isinstance(element.tag, str) else ""
        if tag == "path" and element.attrib.get("d", "").strip():
            paths.append(element.attrib["d"].strip())
    return paths


def extract_path_infos(svg: str) -> list[dict]:
    infos: list[dict] = []
    style_classes = _parse_style_classes(svg)
    g_fill = _collect_g_fills(svg)
    for element in _root(svg).iter():
        tag = element.tag.rsplit("}", 1)[-1].lower() if isinstance(element.tag, str) else ""
        if tag != "path":
            continue
        d = element.attrib.get("d", "").strip()
        if not d:
            continue
        eff = _effective_style(element, style_classes, g_fill)
        fill = eff.get("fill", "")
        stroke = eff.get("stroke", "")
        fill_is_none = fill.lower() == "none" if fill else False
        stroke_is_none = stroke.lower() == "none" if stroke else True
        if not fill and not stroke and not element.attrib.get("class"):
            # Fallback: check parent g fill already handled, if still none use black fill
            parent_fill = g_fill.get(element, "")
            if parent_fill and parent_fill.lower() != "none":
                fill = parent_fill
                fill_is_none = False
            else:
                fill_is_none = False
                fill = "#000000"
        if "fill-opacity" in eff:
            try:
                if float(eff["fill-opacity"]) == 0:
                    fill_is_none = True
            except ValueError:
                pass
        transform = element.attrib.get("transform", "").strip()
        infos.append({
            "d": d,
            "fill": fill if not fill_is_none else "none",
            "stroke": stroke if not stroke_is_none else "none",
            "fill_is_none": fill_is_none,
            "stroke_is_none": stroke_is_none,
            "transform": transform,
        })
    return infos


def _parse_transform(transform: str) -> tuple[float, float, float, float, float, float] | None:
    if not transform:
        return None
    m = re.search(r"matrix\s*\(\s*([^)]+)\)", transform, flags=re.IGNORECASE)
    if m:
        nums = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", m.group(1))]
        if len(nums) >= 6:
            return (nums[0], nums[1], nums[2], nums[3], nums[4], nums[5])
    return None


def _apply_matrix(pt: complex, mat: tuple[float, float, float, float, float, float] | None) -> complex:
    if mat is None:
        return pt
    a, b, c, d, e, f = mat
    return complex(a * pt.real + c * pt.imag + e, b * pt.real + d * pt.imag + f)


def _split_subpaths(path_data: str) -> list[str]:
    matches = list(re.finditer(r"(?<![eE])[Mm]", path_data))
    if not matches:
        return [path_data]
    return [path_data[matches[index].start(): matches[index + 1].start() if index + 1 < len(matches) else None].strip() for index in range(len(matches))]


def _cubic_from_line(p0: complex, p1: complex) -> tuple[complex, complex, complex, complex]:
    v = p1 - p0
    return (p0, p0 + v / 3, p0 + 2 * v / 3, p1)


def _cubic_from_quadratic(p0: complex, q1: complex, p1: complex) -> tuple[complex, complex, complex, complex]:
    return (p0, p0 + 2 / 3 * (q1 - p0), p1 + 2 / 3 * (q1 - p1), p1)


def _cubics_from_arc(seg) -> list[tuple[complex, complex, complex, complex]]:
    cubics: list[tuple[complex, complex, complex, complex]] = []
    n = 4
    for i in range(n):
        t0 = i / n
        t1 = (i + 1) / n
        p0 = seg.point(t0)
        p1 = seg.point(t1)
        eps = 1e-4
        try:
            if 0 < t0 < 1:
                d0 = (seg.point(t0 + eps) - seg.point(t0 - eps)) / (2 * eps)
            else:
                d0 = (seg.point(t0 + eps) - p0) / eps
            if 0 < t1 < 1:
                d1 = (seg.point(t1 + eps) - seg.point(t1 - eps)) / (2 * eps)
            else:
                d1 = (p1 - seg.point(t1 - eps)) / eps
        except Exception:
            d0 = p1 - p0
            d1 = p1 - p0
        scale = (1 / n) / 3
        c1 = p0 + d0 * scale
        c2 = p1 - d1 * scale
        cubics.append((complex(p0), complex(c1), complex(c2), complex(p1)))
    return cubics


def _subpath_to_cubics(subpath: str, mat: tuple[float, float, float, float, float, float] | None) -> tuple[list[tuple[complex, complex, complex, complex]], bool]:
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
            cubics.append(_cubic_from_line(_apply_matrix(seg.start, mat), _apply_matrix(seg.end, mat)))
        elif isinstance(seg, CubicBezier):
            cubics.append((_apply_matrix(seg.start, mat), _apply_matrix(seg.control1, mat), _apply_matrix(seg.control2, mat), _apply_matrix(seg.end, mat)))
        elif isinstance(seg, QuadraticBezier):
            cubics.append(_cubic_from_quadratic(_apply_matrix(seg.start, mat), _apply_matrix(seg.control, mat), _apply_matrix(seg.end, mat)))
        elif isinstance(seg, Arc):
            for a0, a1, a2, a3 in _cubics_from_arc(seg):
                cubics.append((_apply_matrix(a0, mat), _apply_matrix(a1, mat), _apply_matrix(a2, mat), _apply_matrix(a3, mat)))
        else:
            try:
                cubics.append(_cubic_from_line(_apply_matrix(seg.start, mat), _apply_matrix(seg.end, mat)))
            except Exception:
                continue
    if is_closed and cubics:
        first_p0 = cubics[0][0]
        last_p3 = cubics[-1][3]
        if abs(last_p3 - first_p0) > 1e-6:
            cubics.append(_cubic_from_line(last_p3, first_p0))
    if not is_closed and cubics and len(cubics) > 2 and abs(cubics[-1][3] - cubics[0][0]) < 1e-6:
        is_closed = True
    return cubics, is_closed


def _cubics_to_control_and_knots(cubics: list[tuple[complex, complex, complex, complex]]) -> tuple[list[tuple[float, float]], list[float]]:
    if not cubics:
        return [], []
    ctrl: list[tuple[float, float]] = []
    p0, c1, c2, p3 = cubics[0]
    ctrl.append((float(p0.real), float(p0.imag)))
    ctrl.append((float(c1.real), float(c1.imag)))
    ctrl.append((float(c2.real), float(c2.imag)))
    ctrl.append((float(p3.real), float(p3.imag)))
    for (p0, c1, c2, p3) in cubics[1:]:
        ctrl.append((float(c1.real), float(c1.imag)))
        ctrl.append((float(c2.real), float(c2.imag)))
        ctrl.append((float(p3.real), float(p3.imag)))
    n = len(cubics)
    knots: list[float] = [0.0, 0.0, 0.0, 0.0]
    for i in range(1, n):
        v = i / n
        knots.extend([v, v, v])
    knots.extend([1.0, 1.0, 1.0, 1.0])
    assert len(knots) == len(ctrl) + 4, f"knot/ctrl mismatch {len(knots)} vs {len(ctrl)}"
    return ctrl, knots


def _color_to_aci(color_str: str) -> int:
    if not color_str or color_str.lower() == "none":
        return 7
    color_str = color_str.strip().lower()
    if color_str.startswith("#"):
        hexc = color_str[1:]
        if len(hexc) == 3:
            hexc = "".join([c * 2 for c in hexc])
        if len(hexc) == 6:
            try:
                r = int(hexc[0:2], 16)
                g = int(hexc[2:4], 16)
                b = int(hexc[4:6], 16)
                if r < 90 and g < 90 and b < 90 and abs(r - g) < 12 and abs(g - b) < 12:
                    return 250
                if r == 0 and g == 0 and b == 0:
                    return 250
                return 7
            except ValueError:
                return 7
    return 7


def svg_to_dxf(svg: str, units: str = "mm", curve_tolerance_px: float = 1.0) -> tuple[bytes, int]:
    """Convert SVG paths to native cubic SPLINE + HATCH entities preserving Bezier precision."""
    if units not in {"mm", "in"}:
        raise ValueError("units must be 'mm' or 'in'")
    _, view_height = _parse_viewbox(svg)
    scale = 25.4 / 96 if units == "mm" else 1 / 96
    document = ezdxf.new("R2000", setup=True)
    document.header["$INSUNITS"] = 4 if units == "mm" else 1
    if "VECTOR_OUTLINES" not in document.layers:
        document.layers.add("VECTOR_OUTLINES", color=7)
    if "VECTOR_HATCH" not in document.layers:
        document.layers.add("VECTOR_HATCH", color=7)
    entity_count = 0
    infos = extract_path_infos(svg)
    if not infos:
        for d in extract_path_data(svg):
            infos.append({"d": d, "fill": "none", "stroke": "#000000", "fill_is_none": False, "stroke_is_none": False, "transform": ""})

    for info in infos:
        d = info["d"]
        mat = _parse_transform(info.get("transform", ""))
        fill_is_none = info.get("fill_is_none", True)
        stroke_is_none = info.get("stroke_is_none", True)
        fill_color = info.get("fill", "none")
        stroke_color = info.get("stroke", "none")
        if fill_is_none and stroke_is_none:
            fill_is_none = False
            fill_color = "#000000"
        is_filled = not fill_is_none
        is_stroked = not stroke_is_none

        for sub in _split_subpaths(d):
            if not sub.strip():
                continue
            try:
                cubics, is_closed = _subpath_to_cubics(sub, mat)
            except ValueError:
                continue
            if not cubics:
                continue
            ctrl_svg, knots = _cubics_to_control_and_knots(cubics)
            if len(ctrl_svg) < 2:
                continue
            ctrl_dxf = [(x * scale, (view_height - y) * scale) for (x, y) in ctrl_svg]

            if is_filled:
                # HATCH for fill
                try:
                    hatch = document.modelspace().add_hatch(color=_color_to_aci(fill_color), dxfattribs={"layer": "VECTOR_HATCH"})
                    hatch.set_solid_fill(color=_color_to_aci(fill_color), style=1)
                    edge_path = hatch.paths.add_edge_path(flags=1)
                    edge_path.add_spline(
                        control_points=[(x, y) for (x, y) in ctrl_dxf],
                        knot_values=knots,
                        degree=3,
                        periodic=1 if is_closed else 0,
                    )
                    entity_count += 1
                except Exception:
                    try:
                        hatch = document.modelspace().add_hatch(color=7, dxfattribs={"layer": "VECTOR_HATCH"})
                        hatch.set_solid_fill(color=7, style=1)
                        hatch.paths.add_polyline_path(ctrl_dxf, is_closed=is_closed, flags=1)
                        entity_count += 1
                    except Exception:
                        pass
                # Always also emit SPLINE outline for cut-ready even for filled-only
                try:
                    spline = document.modelspace().add_spline(dxfattribs={"layer": "VECTOR_OUTLINES", "color": _color_to_aci(fill_color)})
                    spline.control_points = [(x, y, 0) for (x, y) in ctrl_dxf]
                    spline.knots = knots
                    spline.weights = [1.0] * len(ctrl_dxf)
                    spline.dxf.degree = 3
                    spline.closed = bool(is_closed)
                    if is_closed:
                        try:
                            spline.set_flag_state(spline.PERIODIC, True)
                        except Exception:
                            pass
                    entity_count += 1
                except Exception:
                    pass
                continue

            if is_stroked:
                try:
                    spline = document.modelspace().add_spline(dxfattribs={"layer": "VECTOR_OUTLINES", "color": _color_to_aci(stroke_color)})
                    spline.control_points = [(x, y, 0) for (x, y) in ctrl_dxf]
                    spline.knots = knots
                    spline.weights = [1.0] * len(ctrl_dxf)
                    spline.dxf.degree = 3
                    spline.closed = bool(is_closed)
                    if is_closed:
                        try:
                            spline.set_flag_state(spline.PERIODIC, True)
                        except Exception:
                            pass
                    entity_count += 1
                except Exception:
                    try:
                        document.modelspace().add_lwpolyline(ctrl_dxf, close=is_closed, dxfattribs={"layer": "VECTOR_OUTLINES"})
                        entity_count += 1
                    except Exception:
                        pass

    if entity_count == 0:
        for info in infos:
            d = info["d"]
            mat = _parse_transform(info.get("transform", ""))
            for sub in _split_subpaths(d):
                try:
                    if parse_path is None:
                        continue
                    parsed = parse_path(sub)
                    pts = []
                    for seg in parsed:
                        length = float(seg.length(error=1e-3))
                        count = max(2, min(64, int(length / max(0.5, curve_tolerance_px)) + 1))
                        for step in range(count):
                            pt = _apply_matrix(seg.point(step / count), mat)
                            pts.append((float(pt.real) * scale, (view_height - float(pt.imag)) * scale))
                    if len(pts) >= 3:
                        if pts[0] != pts[-1]:
                            pts.append(pts[0])
                        document.modelspace().add_polyline2d(pts, close=True, dxfattribs={"layer": "VECTOR_OUTLINES"})
                        entity_count += 1
                except Exception:
                    continue

    stream = io.StringIO()
    document.write(stream)
    return stream.getvalue().encode("utf-8"), entity_count
