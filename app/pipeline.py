from __future__ import annotations

import re
from typing import Any

from .config import MAX_VECTOR_BYTES
from .dxf import svg_to_dxf_detailed
from .preprocessing import fallback_paths, fallback_svg, load_image, preprocess, preprocessed_png
from .settings import VectorizeSettings, normalize_settings
from .storage import save_outputs as _save_outputs  # re-export
from .svg_pdf import normalize_any_svg, pdf_to_svg
from .svg_utils import extract_svg_palette, svg_stats
from .vtracer_engine import normalize_vtracer_svg, vtracer_svg

try:
    import vtracer  # re-export for tests that patch vectorizer.vtracer
except ImportError:  # pragma: no cover
    vtracer = None  # type: ignore

# Back-compat: tests import save_outputs from pipeline/vectorizer
save_outputs = _save_outputs


def _dxf_document(svg: str, units: str) -> tuple[bytes, dict[str, Any]]:
    return svg_to_dxf_detailed(svg, units=units, curve_tolerance_px=1.0)


def _result(
    svg_text: str,
    width: float,
    height: float,
    settings: VectorizeSettings,
    processing: dict[str, Any],
) -> dict[str, Any]:
    """Assemble one vectorization result from the produced SVG.

    Contour counts come from the DXF builder rather than the entity total: a
    filled contour emits both a HATCH and an outline SPLINE, so the entity
    count is roughly double the geometry and never belonged in "contours".
    """
    nodes, _paths, _closed = svg_stats(svg_text)
    dxf, dxf_stats = _dxf_document(svg_text, settings.units)
    return {
        "svg": svg_text.encode("utf-8"),
        "dxf": dxf,
        "width": width,
        "height": height,
        "node_count": nodes,
        "contour_count": dxf_stats["subpaths"],
        "closed_paths": dxf_stats["closed_subpaths"],
        "open_paths": dxf_stats["open_subpaths"],
        "dxf_polylines": dxf_stats["entities"],
        "dxf_stats": dxf_stats,
        "palette": _palette_from_svg(svg_text),
        "processing": {
            "optimized": settings.simplify,
            "units": settings.units,
            "mode": settings.mode,
            "cut_ready": True,
            **processing,
        },
    }


def _palette_from_svg(svg: str) -> list[str]:
    pal = extract_svg_palette(svg)
    if not pal:
        for m in re.finditer(r'fill="(#[0-9a-fA-F]{6})"', svg):
            c = m.group(1)
            if c not in pal:
                pal.append(c)
            if len(pal) >= 12:
                break
    return pal


def vectorize(data: bytes, settings_dict: dict[str, Any] | None = None, source_format: str = "png") -> dict[str, Any]:
    settings = normalize_settings(settings_dict)
    src = source_format.lower().replace("jpg", "jpeg").lstrip(".")
    if src == "svg":
        if len(data) > MAX_VECTOR_BYTES:
            raise ValueError(f"File exceeds {MAX_VECTOR_BYTES:,}-byte limit")
        svg_text, width, height = normalize_any_svg(data.decode("utf-8", errors="strict"), settings)
        return _result(svg_text, width, height, settings, {
            "engine": "svg-passthrough",
            "engine_reference": "lossless SVG Bezier preservation",
            "preprocessing": ["SVG sanitization", "viewBox normalization"],
            "removed_components": 0,
        })
    if src == "pdf":
        svg_text, width, height = pdf_to_svg(data, settings)
        return _result(svg_text, width, height, settings, {
            "engine": "pdf-vector-extract",
            "engine_reference": "pymupdf vector extraction + spline DXF",
            "preprocessing": ["PDF vector outline extraction", "Bezier preservation"],
            "removed_components": 0,
        })
    if src not in {"png", "jpeg", "webp"}:
        raise ValueError("Unsupported raster format")
    rgb, width, height = load_image(data)
    engine = "vtracer-spline"
    cleanup: dict[str, int] = {"removed_components": 0, "min_component_area": 0}
    try:
        prepared, cleanup = preprocessed_png(rgb, settings)
        svg_text = normalize_vtracer_svg(vtracer_svg(prepared, "png", settings), width, height, settings)
    except Exception:
        engine = "opencv-contour-bezier-fallback"
        _, binary, cleanup = preprocess(rgb, settings)
        paths = fallback_paths(rgb, binary, 1.0, settings)
        svg_text, _nodes, _contours = fallback_svg(width, height, paths, settings)
    return _result(svg_text, width, height, settings, {
        "engine": engine,
        "engine_reference": "visioncortex/vtracer via PixelToPath workflow",
        "preprocessing": [
            "PixelToPath-inspired bilateral denoise",
            "CLAHE contrast enhancement",
            "connected-component speckle removal",
            "morphological gap closing",
        ],
        "removed_components": cleanup["removed_components"],
    })
