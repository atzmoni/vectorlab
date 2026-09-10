from __future__ import annotations

import io

import ezdxf

try:
    from svgpathtools import parse_path
except ImportError:  # pragma: no cover
    parse_path = None  # type: ignore

from .geometry import apply_matrix, color_to_aci, cubics_to_control_and_knots, subpath_to_cubics
from .svg_parse import extract_path_data, extract_path_infos, parse_transform, parse_viewbox


def svg_to_dxf(svg: str, units: str = "mm", curve_tolerance_px: float = 1.0) -> tuple[bytes, int]:
    """Convert SVG paths to native cubic SPLINE + HATCH entities preserving Bezier precision."""
    if units not in {"mm", "in"}:
        raise ValueError("units must be 'mm' or 'in'")
    _, view_height = parse_viewbox(svg)
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
        mat = parse_transform(info.get("transform", ""))
        fill_is_none = info.get("fill_is_none", True)
        stroke_is_none = info.get("stroke_is_none", True)
        fill_color = info.get("fill", "none")
        stroke_color = info.get("stroke", "none")
        if fill_is_none and stroke_is_none:
            fill_is_none, fill_color = False, "#000000"
        is_filled = not fill_is_none
        is_stroked = not stroke_is_none

        from .geometry import split_subpaths

        for sub in split_subpaths(d):
            if not sub.strip():
                continue
            try:
                cubics, is_closed = subpath_to_cubics(sub, mat)
            except ValueError:
                continue
            if not cubics:
                continue
            ctrl_svg, knots = cubics_to_control_and_knots(cubics)
            if len(ctrl_svg) < 2:
                continue
            ctrl_dxf = [(x * scale, (view_height - y) * scale) for (x, y) in ctrl_svg]

            if is_filled:
                try:
                    hatch = document.modelspace().add_hatch(color=color_to_aci(fill_color), dxfattribs={"layer": "VECTOR_HATCH"})
                    hatch.set_solid_fill(color=color_to_aci(fill_color), style=1)
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
                try:
                    spline = document.modelspace().add_spline(dxfattribs={"layer": "VECTOR_OUTLINES", "color": color_to_aci(fill_color)})
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
                    spline = document.modelspace().add_spline(dxfattribs={"layer": "VECTOR_OUTLINES", "color": color_to_aci(stroke_color)})
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
        # Polyline fallback — sampled, only when no SPLINE emitted
        for info in infos:
            d = info["d"]
            mat = parse_transform(info.get("transform", ""))
            from .geometry import split_subpaths

            for sub in split_subpaths(d):
                try:
                    if parse_path is None:
                        continue
                    parsed = parse_path(sub)
                    pts: list[tuple[float, float]] = []
                    for seg in parsed:
                        length = float(seg.length(error=1e-3))
                        count = max(2, min(64, int(length / max(0.5, curve_tolerance_px)) + 1))
                        for step in range(count):
                            pt = apply_matrix(seg.point(step / count), mat)
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


# Re-export for tests that import from app.dxf directly
from .svg_parse import extract_path_data as extract_path_data  # noqa: E402,F401
from .svg_parse import extract_path_infos as extract_path_infos  # noqa: E402,F401
