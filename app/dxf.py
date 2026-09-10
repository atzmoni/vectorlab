from __future__ import annotations

import io
from typing import Any

import ezdxf

try:
    from svgpathtools import parse_path
except ImportError:  # pragma: no cover
    parse_path = None  # type: ignore

from .geometry import (
    Matrix,
    apply_matrix,
    color_layer_name,
    color_to_aci,
    color_to_true_color,
    cubics_to_control_and_knots,
    split_subpaths,
    subpath_to_cubics,
)
from .svg_parse import extract_path_data, extract_path_infos, parse_transform, parse_viewbox, parse_viewbox_rect

OUTLINE_LAYER = "VECTOR_OUTLINES"
HATCH_LAYER = "VECTOR_HATCH"

# Beyond this many distinct colors a per-color layer set stops helping a CAM
# operator and just clutters the layer list; true_color still rides along.
MAX_COLOR_LAYERS = 64


def _empty_stats() -> dict[str, Any]:
    return {
        "entities": 0, "splines": 0, "hatches": 0, "polylines": 0,
        "subpaths": 0, "closed_subpaths": 0, "open_subpaths": 0,
        "filled_regions": 0, "stroked_paths": 0, "layers": [], "colors": [],
    }


class _Builder:
    """Owns the ezdxf document and the layer/color bookkeeping for one export."""

    def __init__(self, units: str, color_layers: bool) -> None:
        # R2004 is the first version with 24-bit color (group code 420); every
        # entity also carries a nearest-ACI index for readers that ignore it.
        self.document = ezdxf.new("R2004", setup=True)
        self.document.header["$INSUNITS"] = 4 if units == "mm" else 1
        self.msp = self.document.modelspace()
        self.color_layers = color_layers
        self.stats = _empty_stats()
        for name in (OUTLINE_LAYER, HATCH_LAYER):
            if name not in self.document.layers:
                self.document.layers.add(name, color=7)

    def layer_for(self, color: str, fallback: str) -> str:
        """Layer for a paint color, creating a per-color layer when useful."""
        if not self.color_layers:
            return fallback
        name = color_layer_name(color)
        if name == OUTLINE_LAYER:
            return fallback
        if name not in self.document.layers:
            if len(self.document.layers) >= MAX_COLOR_LAYERS:
                return fallback
            true_color = color_to_true_color(color)
            self.document.layers.add(name, color=color_to_aci(color))
            if true_color is not None:
                self.document.layers.get(name).rgb = (
                    (true_color >> 16) & 0xFF, (true_color >> 8) & 0xFF, true_color & 0xFF,
                )
        return name

    def attribs(self, color: str, layer: str) -> dict[str, Any]:
        attribs: dict[str, Any] = {"layer": layer, "color": color_to_aci(color)}
        true_color = color_to_true_color(color)
        if true_color is not None:
            attribs["true_color"] = true_color
        return attribs

    def add_spline(self, control_points, knots, closed: bool, color: str, layer: str) -> bool:
        try:
            spline = self.msp.add_spline(dxfattribs=self.attribs(color, layer))
            spline.control_points = [(x, y, 0) for (x, y) in control_points]
            spline.knots = knots
            spline.weights = [1.0] * len(control_points)
            spline.dxf.degree = 3
            spline.closed = bool(closed)
            if closed:
                try:
                    spline.set_flag_state(spline.PERIODIC, True)
                except Exception:
                    pass
            self.stats["splines"] += 1
            self.stats["entities"] += 1
            return True
        except Exception:
            try:
                self.msp.add_lwpolyline(control_points, close=closed, dxfattribs=self.attribs(color, layer))
                self.stats["polylines"] += 1
                self.stats["entities"] += 1
                return True
            except Exception:
                return False

    def add_hatch(self, control_points, knots, closed: bool, color: str, layer: str) -> bool:
        aci = color_to_aci(color)
        try:
            hatch = self.msp.add_hatch(color=aci, dxfattribs=self.attribs(color, layer))
            hatch.set_solid_fill(color=aci, style=1)
            true_color = color_to_true_color(color)
            if true_color is not None:
                hatch.dxf.true_color = true_color
            edge_path = hatch.paths.add_edge_path(flags=1)
            edge_path.add_spline(
                control_points=[(x, y) for (x, y) in control_points],
                knot_values=knots,
                degree=3,
                periodic=1 if closed else 0,
            )
            self.stats["hatches"] += 1
            self.stats["entities"] += 1
            return True
        except Exception:
            try:
                hatch = self.msp.add_hatch(color=aci, dxfattribs=self.attribs(color, layer))
                hatch.set_solid_fill(color=aci, style=1)
                hatch.paths.add_polyline_path(control_points, is_closed=closed, flags=1)
                self.stats["hatches"] += 1
                self.stats["entities"] += 1
                return True
            except Exception:
                return False

    def finish(self) -> tuple[bytes, dict[str, Any]]:
        used = sorted({e.dxf.layer for e in self.msp})
        self.stats["layers"] = used
        stream = io.StringIO()
        self.document.write(stream)
        return stream.getvalue().encode("utf-8"), self.stats


def _to_dxf_points(control_points, scale: float, min_x: float, max_y: float) -> list[tuple[float, float]]:
    """User space to DXF model space: shift by the viewBox origin, flip Y, scale to units."""
    return [((x - min_x) * scale, (max_y - y) * scale) for (x, y) in control_points]


def svg_to_dxf_detailed(
    svg: str,
    units: str = "mm",
    curve_tolerance_px: float = 1.0,
    color_layers: bool = True,
) -> tuple[bytes, dict[str, Any]]:
    """Convert SVG geometry to native cubic SPLINE + HATCH entities.

    Every ancestor ``transform`` is applied, the viewBox origin is honored, and
    each paint color keeps its true 24-bit value plus an optional per-color
    layer so CAM can assign one operation per color.
    """
    if units not in {"mm", "in"}:
        raise ValueError("units must be 'mm' or 'in'")
    min_x, min_y, view_width, view_height = parse_viewbox_rect(svg)
    max_y = min_y + view_height
    scale = 25.4 / 96 if units == "mm" else 1 / 96
    builder = _Builder(units, color_layers)

    infos = extract_path_infos(svg)
    if not infos:
        for d in extract_path_data(svg):
            infos.append({"d": d, "fill": "none", "stroke": "#000000", "fill_is_none": False,
                          "stroke_is_none": False, "transform": "", "matrix": None})

    for info in infos:
        d = info["d"]
        matrix: Matrix | None = info.get("matrix")
        if matrix is None and info.get("transform"):
            matrix = parse_transform(info["transform"])
        fill_is_none = info.get("fill_is_none", True)
        stroke_is_none = info.get("stroke_is_none", True)
        fill_color = info.get("fill", "none")
        stroke_color = info.get("stroke", "none")
        if fill_is_none and stroke_is_none:
            fill_is_none, fill_color = False, "#000000"
        is_filled = not fill_is_none
        is_stroked = not stroke_is_none
        if is_filled:
            builder.stats["filled_regions"] += 1
        elif is_stroked:
            builder.stats["stroked_paths"] += 1

        for sub in split_subpaths(d):
            if not sub.strip():
                continue
            try:
                cubics, is_closed = subpath_to_cubics(sub, matrix)
            except ValueError:
                continue
            if not cubics:
                continue
            ctrl_svg, knots = cubics_to_control_and_knots(cubics)
            if len(ctrl_svg) < 2:
                continue
            builder.stats["subpaths"] += 1
            builder.stats["closed_subpaths" if is_closed else "open_subpaths"] += 1
            ctrl_dxf = _to_dxf_points(ctrl_svg, scale, min_x, max_y)

            if is_filled:
                hatch_layer = builder.layer_for(fill_color, HATCH_LAYER)
                builder.add_hatch(ctrl_dxf, knots, is_closed, fill_color, hatch_layer)
                builder.add_spline(ctrl_dxf, knots, is_closed, fill_color, builder.layer_for(fill_color, OUTLINE_LAYER))
                continue
            if is_stroked:
                builder.add_spline(ctrl_dxf, knots, is_closed, stroke_color, builder.layer_for(stroke_color, OUTLINE_LAYER))

    if builder.stats["entities"] == 0:
        _sampled_polyline_fallback(builder, infos, scale, min_x, max_y, curve_tolerance_px)

    used_colors: list[str] = []
    for info in infos:
        for color in (info.get("fill", "none"), info.get("stroke", "none")):
            if color and color != "none" and color not in used_colors:
                used_colors.append(color)
    data, stats = builder.finish()
    stats["colors"] = used_colors[:32]
    return data, stats


def _sampled_polyline_fallback(builder: _Builder, infos, scale: float, min_x: float, max_y: float, tolerance: float) -> None:
    """Last resort when no SPLINE could be emitted — sample curves into POLYLINEs."""
    if parse_path is None:
        return
    for info in infos:
        matrix: Matrix | None = info.get("matrix")
        for sub in split_subpaths(info["d"]):
            try:
                parsed = parse_path(sub)
                pts: list[tuple[float, float]] = []
                for seg in parsed:
                    length = float(seg.length(error=1e-3))
                    count = max(2, min(64, int(length / max(0.5, tolerance)) + 1))
                    for step in range(count):
                        pt = apply_matrix(seg.point(step / count), matrix)
                        pts.append(((float(pt.real) - min_x) * scale, (max_y - float(pt.imag)) * scale))
                if len(pts) >= 3:
                    if pts[0] != pts[-1]:
                        pts.append(pts[0])
                    builder.msp.add_polyline2d(pts, close=True, dxfattribs={"layer": OUTLINE_LAYER})
                    builder.stats["polylines"] += 1
                    builder.stats["entities"] += 1
                    builder.stats["subpaths"] += 1
                    builder.stats["closed_subpaths"] += 1
            except Exception:
                continue


def svg_to_dxf(svg: str, units: str = "mm", curve_tolerance_px: float = 1.0) -> tuple[bytes, int]:
    """Back-compatible wrapper returning ``(dxf_bytes, entity_count)``."""
    data, stats = svg_to_dxf_detailed(svg, units=units, curve_tolerance_px=curve_tolerance_px)
    return data, stats["entities"]


__all__ = [
    "svg_to_dxf", "svg_to_dxf_detailed", "extract_path_data", "extract_path_infos",
    "parse_viewbox", "parse_viewbox_rect", "OUTLINE_LAYER", "HATCH_LAYER",
]
