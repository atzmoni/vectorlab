from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

from .config import MAX_SVG_CHARS
from .geometry import (
    Matrix,
    compose_matrix,
    rotation_matrix,
    scale_matrix,
    skew_matrix,
    translation_matrix,
)

# Elements whose children define reusable content rather than painted geometry.
# Anything under them must never reach the DXF as a cut path.
NON_RENDERED_TAGS = {"defs", "clippath", "mask", "marker", "symbol", "pattern", "metadata", "title", "desc"}

# Properties that inherit down the SVG tree.
_INHERITED_PROPS = ("fill", "stroke", "stroke-width", "fill-opacity", "stroke-opacity")

_NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_TRANSFORM_RE = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)", re.IGNORECASE)


def _root(svg: str) -> ET.Element:
    if not isinstance(svg, str) or not svg.strip() or len(svg) > MAX_SVG_CHARS:
        raise ValueError("SVG document is empty or exceeds the size limit")
    try:
        return ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError("Invalid SVG document") from exc


def _local_tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower() if isinstance(element.tag, str) else ""


def _numbers(text: str) -> list[float]:
    return [float(x) for x in _NUM_RE.findall(text or "")]


def _number(value: str, default: float = 0.0) -> float:
    """First number in an SVG length. Unit suffixes (px/pt/%) are ignored, as before."""
    m = _NUM_RE.search(value or "")
    return float(m.group(0)) if m else default


def parse_viewbox_rect(svg: str) -> tuple[float, float, float, float]:
    """Return the full ``(min_x, min_y, width, height)`` user-space rectangle.

    The min-x/min-y origin matters: DXF y grows upward, so the flip has to be
    taken about the viewBox rectangle, not about a bare height.
    """
    root = _root(svg)
    viewbox = root.attrib.get("viewBox", "").replace(",", " ").split()
    if len(viewbox) == 4:
        try:
            x, y, w, h = (float(v) for v in viewbox)
            if w > 0 and h > 0:
                return x, y, w, h
        except ValueError:
            pass
    return 0.0, 0.0, max(1.0, _number(root.attrib.get("width", ""))), max(1.0, _number(root.attrib.get("height", "")))


def parse_viewbox(svg: str) -> tuple[float, float]:
    """Back-compatible ``(width, height)`` view of :func:`parse_viewbox_rect`."""
    _, _, w, h = parse_viewbox_rect(svg)
    return w, h


def parse_style_classes(svg: str) -> dict[str, dict[str, str]]:
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


def _declarations(style: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (style or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def parse_transform(transform: str) -> Matrix | None:
    """Compose a full SVG transform list into one matrix.

    Handles ``matrix``, ``translate``, ``scale``, ``rotate`` (with optional
    center), ``skewX`` and ``skewY``. A transform list applies right-to-left,
    so ``translate(50,0) scale(2)`` scales first, then translates.
    """
    if not transform:
        return None
    result: Matrix | None = None
    for m in _TRANSFORM_RE.finditer(transform):
        name = m.group(1).lower()
        nums = _numbers(m.group(2))
        primitive: Matrix | None = None
        if name == "matrix" and len(nums) >= 6:
            primitive = (nums[0], nums[1], nums[2], nums[3], nums[4], nums[5])
        elif name == "translate" and nums:
            primitive = translation_matrix(nums[0], nums[1] if len(nums) > 1 else 0.0)
        elif name == "scale" and nums:
            primitive = scale_matrix(nums[0], nums[1] if len(nums) > 1 else nums[0])
        elif name == "rotate" and nums:
            primitive = rotation_matrix(nums[0], nums[1] if len(nums) > 2 else 0.0, nums[2] if len(nums) > 2 else 0.0)
        elif name == "skewx" and nums:
            primitive = skew_matrix(nums[0], "x")
        elif name == "skewy" and nums:
            primitive = skew_matrix(nums[0], "y")
        if primitive is not None:
            result = compose_matrix(result, primitive)
    return result


def _arc_flags_path(cx: float, cy: float, rx: float, ry: float) -> str:
    """Closed ellipse as two half-arcs — exact, unlike a sampled polygon."""
    return (
        f"M {cx - rx:.6g} {cy:.6g} "
        f"A {rx:.6g} {ry:.6g} 0 1 0 {cx + rx:.6g} {cy:.6g} "
        f"A {rx:.6g} {ry:.6g} 0 1 0 {cx - rx:.6g} {cy:.6g} Z"
    )


def shape_to_path_data(element: ET.Element) -> str:
    """Convert a basic SVG shape to equivalent path data.

    ``<rect>``, ``<circle>``, ``<ellipse>``, ``<line>``, ``<polyline>`` and
    ``<polygon>`` are ordinary geometry in every drawing tool, so they have to
    reach the DXF exactly like ``<path>`` does.
    """
    tag = _local_tag(element)
    attrib = element.attrib
    if tag == "rect":
        x, y = _number(attrib.get("x", "0")), _number(attrib.get("y", "0"))
        w, h = _number(attrib.get("width", "0")), _number(attrib.get("height", "0"))
        if w <= 0 or h <= 0:
            return ""
        rx_raw, ry_raw = attrib.get("rx"), attrib.get("ry")
        rx = _number(rx_raw, 0.0) if rx_raw is not None else (_number(ry_raw, 0.0) if ry_raw is not None else 0.0)
        ry = _number(ry_raw, 0.0) if ry_raw is not None else rx
        rx, ry = min(max(rx, 0.0), w / 2), min(max(ry, 0.0), h / 2)
        if rx <= 0 or ry <= 0:
            return f"M {x:.6g} {y:.6g} H {x + w:.6g} V {y + h:.6g} H {x:.6g} Z"
        return (
            f"M {x + rx:.6g} {y:.6g} H {x + w - rx:.6g} A {rx:.6g} {ry:.6g} 0 0 1 {x + w:.6g} {y + ry:.6g} "
            f"V {y + h - ry:.6g} A {rx:.6g} {ry:.6g} 0 0 1 {x + w - rx:.6g} {y + h:.6g} "
            f"H {x + rx:.6g} A {rx:.6g} {ry:.6g} 0 0 1 {x:.6g} {y + h - ry:.6g} "
            f"V {y + ry:.6g} A {rx:.6g} {ry:.6g} 0 0 1 {x + rx:.6g} {y:.6g} Z"
        )
    if tag == "circle":
        r = _number(attrib.get("r", "0"))
        if r <= 0:
            return ""
        return _arc_flags_path(_number(attrib.get("cx", "0")), _number(attrib.get("cy", "0")), r, r)
    if tag == "ellipse":
        rx, ry = _number(attrib.get("rx", "0")), _number(attrib.get("ry", "0"))
        if rx <= 0 or ry <= 0:
            return ""
        return _arc_flags_path(_number(attrib.get("cx", "0")), _number(attrib.get("cy", "0")), rx, ry)
    if tag == "line":
        x1, y1 = _number(attrib.get("x1", "0")), _number(attrib.get("y1", "0"))
        x2, y2 = _number(attrib.get("x2", "0")), _number(attrib.get("y2", "0"))
        if math.isclose(x1, x2) and math.isclose(y1, y2):
            return ""
        return f"M {x1:.6g} {y1:.6g} L {x2:.6g} {y2:.6g}"
    if tag in {"polyline", "polygon"}:
        pts = _numbers(attrib.get("points", ""))
        if len(pts) < 4:
            return ""
        pairs = [f"{pts[i]:.6g} {pts[i + 1]:.6g}" for i in range(0, len(pts) - 1, 2)]
        d = "M " + " L ".join(pairs)
        return d + " Z" if tag == "polygon" else d
    return ""


def path_data_for(element: ET.Element) -> str:
    """Path data for any geometry element — ``d`` for paths, converted for shapes."""
    if _local_tag(element) == "path":
        return element.attrib.get("d", "").strip()
    return shape_to_path_data(element)


def collect_g_fills(svg: str) -> dict[ET.Element, str]:
    """Inherited ``fill`` per element. Kept for callers that only need fills."""
    try:
        root = _root(svg)
    except ValueError:
        return {}
    g_fill: dict[ET.Element, str] = {}

    def walk(elem: ET.Element, inherited: str | None) -> None:
        cur = inherited
        if "fill" in elem.attrib:
            cur = elem.attrib["fill"].strip()
        declared = _declarations(elem.attrib.get("style", "")).get("fill")
        if declared:
            cur = declared
        g_fill[elem] = cur if cur is not None else ""
        for child in elem:
            walk(child, cur)

    walk(root, None)
    return g_fill


def effective_style(elem: ET.Element, style_classes: dict[str, dict[str, str]], g_fill: dict[ET.Element, str]) -> dict[str, str]:
    """Resolved style for one element, in CSS cascade order.

    Presentation attributes lose to class rules, which lose to the inline
    ``style`` attribute — the order a browser uses.
    """
    style: dict[str, str] = {}
    for key in _INHERITED_PROPS:
        if key in elem.attrib:
            style[key] = elem.attrib[key].strip()
    for cls in elem.attrib.get("class", "").split():
        if cls in style_classes:
            style.update(style_classes[cls])
    style.update(_declarations(elem.attrib.get("style", "")))
    if "fill" not in style:
        inherited = g_fill.get(elem, "")
        if inherited:
            style["fill"] = inherited
    return style


def extract_path_data(svg: str) -> list[str]:
    """Path data for every rendered geometry element, shapes included."""
    out: list[str] = []
    for element, _matrix, _style in iter_geometry(_root(svg), parse_style_classes(svg)):
        d = path_data_for(element)
        if d:
            out.append(d)
    return out


def iter_geometry(root: ET.Element, style_classes: dict[str, dict[str, str]]):
    """Walk rendered geometry, carrying accumulated transform and inherited style.

    Yields ``(element, matrix, style)``. ``matrix`` is the composition of every
    ancestor ``transform`` with the element's own — without it, group transforms
    are silently dropped and the geometry lands in the wrong place.
    """
    results: list[tuple[ET.Element, Matrix | None, dict[str, str]]] = []

    def walk(elem: ET.Element, parent_matrix: Matrix | None, inherited: dict[str, str]) -> None:
        tag = _local_tag(elem)
        if tag in NON_RENDERED_TAGS:
            return
        style = dict(inherited)
        for key in _INHERITED_PROPS:
            if key in elem.attrib:
                style[key] = elem.attrib[key].strip()
        for cls in elem.attrib.get("class", "").split():
            if cls in style_classes:
                style.update(style_classes[cls])
        style.update(_declarations(elem.attrib.get("style", "")))
        if style.get("display", "").strip().lower() == "none" or style.get("visibility", "").strip().lower() == "hidden":
            return
        matrix = compose_matrix(parent_matrix, parse_transform(elem.attrib.get("transform", "").strip()))
        if tag in {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}:
            results.append((elem, matrix, style))
        for child in elem:
            walk(child, matrix, style)

    walk(root, None, {})
    return results


def _paint(style: dict[str, str], key: str, initial: str) -> tuple[str, bool]:
    """Resolve one paint property to ``(color, is_none)`` using SVG initial values."""
    value = (style.get(key) or "").strip()
    if not value:
        value = initial
    if value.lower() in {"none", "transparent"} or value.lower().startswith("url("):
        return "none", True
    opacity = style.get(f"{key}-opacity")
    if opacity:
        try:
            if float(opacity.rstrip("%")) / (100.0 if opacity.endswith("%") else 1.0) <= 0:
                return "none", True
        except ValueError:
            pass
    return value, False


def extract_path_infos(svg: str) -> list[dict]:
    """Geometry plus resolved paint and accumulated transform, ready for DXF export."""
    root = _root(svg)
    style_classes = parse_style_classes(svg)
    infos: list[dict] = []
    for element, matrix, style in iter_geometry(root, style_classes):
        d = path_data_for(element)
        if not d:
            continue
        # SVG initial values: fill is black, stroke is none.
        fill, fill_is_none = _paint(style, "fill", "#000000")
        stroke, stroke_is_none = _paint(style, "stroke", "none")
        infos.append({
            "d": d,
            "fill": fill,
            "stroke": stroke,
            "fill_is_none": fill_is_none,
            "stroke_is_none": stroke_is_none,
            "transform": element.attrib.get("transform", "").strip(),
            "matrix": matrix,
            "tag": _local_tag(element),
        })
    return infos
