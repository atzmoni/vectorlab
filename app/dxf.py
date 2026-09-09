from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET

import ezdxf
from svgpathtools import parse_path

MAX_SVG_CHARS = 20_000_000


def _root(svg: str) -> ET.Element:
    if not isinstance(svg, str) or not svg.strip() or len(svg) > MAX_SVG_CHARS:
        raise ValueError("SVG document is empty or exceeds the size limit")
    try:
        return ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError("Invalid SVG document") from exc


def extract_path_data(svg: str) -> list[str]:
    """Extract SVG path data with XML parsing, excluding scripts and non-path elements."""
    paths: list[str] = []
    for element in _root(svg).iter():
        tag = element.tag.rsplit("}", 1)[-1].lower() if isinstance(element.tag, str) else ""
        if tag == "path" and element.attrib.get("d", "").strip():
            paths.append(element.attrib["d"].strip())
    return paths


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


def _split_subpaths(path_data: str) -> list[str]:
    """Split at move commands without changing any SVG numeric values."""
    matches = list(re.finditer(r"(?<![eE])[Mm]", path_data))
    if not matches:
        return [path_data]
    return [path_data[matches[index].start(): matches[index + 1].start() if index + 1 < len(matches) else None].strip() for index in range(len(matches))]


def _sample_subpaths(path_data: str, tolerance_px: float = 1.0) -> list[list[tuple[float, float]]]:
    """Sample one SVG path while preserving relative commands and multiple subpaths."""
    contours: list[list[tuple[float, float]]] = []
    for subpath in _split_subpaths(path_data):
        try:
            parsed = parse_path(subpath)
        except Exception as exc:
            raise ValueError("Invalid SVG path data") from exc
        points: list[tuple[float, float]] = []
        for segment in parsed:
            length = float(segment.length(error=1e-3))
            count = max(2, min(96, int(length / max(0.5, tolerance_px)) + 1))
            for step in range(count):
                point = segment.point(step / count)
                candidate = (round(float(point.real), 5), round(float(point.imag), 5))
                if not points or candidate != points[-1]:
                    points.append(candidate)
        if len(points) >= 3:
            if points[0] != points[-1]:
                points.append(points[0])
            contours.append(points)
    return contours


def svg_to_dxf(svg: str, units: str = "mm", curve_tolerance_px: float = 1.0) -> tuple[bytes, int]:
    """Convert every SVG subpath to a closed R2000 POLYLINE with physical units."""
    if units not in {"mm", "in"}:
        raise ValueError("units must be 'mm' or 'in'")
    _, view_height = _parse_viewbox(svg)
    scale = 25.4 / 96 if units == "mm" else 1 / 96
    document = ezdxf.new("R2000", setup=True)
    document.header["$INSUNITS"] = 4 if units == "mm" else 1
    document.layers.add("VECTOR_OUTLINES", color=7)
    entity_count = 0
    for path_data in extract_path_data(svg):
        for contour in _sample_subpaths(path_data, curve_tolerance_px):
            if len(contour) < 4:
                continue
            # SVG y grows downward; CAD y grows upward.
            scaled = [(x * scale, (view_height - y) * scale) for x, y in contour]
            if scaled[0] != scaled[-1]:
                scaled.append(scaled[0])
            document.modelspace().add_polyline2d(scaled, close=True, dxfattribs={"layer": "VECTOR_OUTLINES"})
            entity_count += 1
    stream = io.StringIO()
    document.write(stream)
    return stream.getvalue().encode("utf-8"), entity_count
