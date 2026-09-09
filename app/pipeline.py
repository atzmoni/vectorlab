from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

from .dxf import svg_to_dxf
from .preprocessing import fallback_paths, fallback_svg, load_image, preprocessed_png, preprocess
from .settings import MAX_VECTOR_BYTES, VectorizeSettings, normalize_settings
from .svg_pdf import normalize_any_svg, pdf_to_svg, svg_stats, extract_svg_palette
from .vtracer_engine import normalize_vtracer_svg, vtracer_svg

try:
    import vtracer  # re-export for tests that patch vectorizer.vtracer
except ImportError:  # pragma: no cover
    vtracer = None  # type: ignore


def _dxf_document(svg: str, units: str) -> tuple[bytes, int]:
    return svg_to_dxf(svg, units=units, curve_tolerance_px=1.0)


def _palette_from_svg(svg: str) -> list[str]:
    pal = extract_svg_palette(svg)
    # Also scan hex fills directly as fallback
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
    if src in {"svg"}:
        if len(data) > MAX_VECTOR_BYTES:
            raise ValueError(f"File exceeds {MAX_VECTOR_BYTES:,}-byte limit")
        svg_text_raw = data.decode("utf-8", errors="strict")
        svg_text, width, height = normalize_any_svg(svg_text_raw, settings)
        nodes, contours, closed = svg_stats(svg_text)
        dxf, dxf_polylines = _dxf_document(svg_text, settings.units)
        palette = _palette_from_svg(svg_text)
        return {
            "svg": svg_text.encode("utf-8"),
            "dxf": dxf,
            "width": width,
            "height": height,
            "node_count": nodes,
            "contour_count": dxf_polylines,
            "closed_paths": dxf_polylines,
            "dxf_polylines": dxf_polylines,
            "palette": palette,
            "processing": {
                "engine": "svg-passthrough",
                "engine_reference": "lossless SVG Bezier preservation",
                "preprocessing": ["SVG sanitization", "viewBox normalization"],
                "removed_components": 0,
                "optimized": settings.simplify,
                "units": settings.units,
                "mode": settings.mode,
                "cut_ready": True,
            },
        }
    if src in {"pdf"}:
        svg_text, width, height = pdf_to_svg(data, settings)
        nodes, contours, closed = svg_stats(svg_text)
        dxf, dxf_polylines = _dxf_document(svg_text, settings.units)
        palette = _palette_from_svg(svg_text)
        return {
            "svg": svg_text.encode("utf-8"),
            "dxf": dxf,
            "width": width,
            "height": height,
            "node_count": nodes,
            "contour_count": dxf_polylines,
            "closed_paths": dxf_polylines,
            "dxf_polylines": dxf_polylines,
            "palette": palette,
            "processing": {
                "engine": "pdf-vector-extract",
                "engine_reference": "pymupdf vector extraction + spline DXF",
                "preprocessing": ["PDF vector outline extraction", "Bezier preservation"],
                "removed_components": 0,
                "optimized": settings.simplify,
                "units": settings.units,
                "mode": settings.mode,
                "cut_ready": True,
            },
        }
    rgb, width, height = load_image(data)
    if src not in {"png", "jpeg", "webp"}:
        raise ValueError("Unsupported raster format")
    engine = "vtracer-spline"
    cleanup: dict[str, int] = {"removed_components": 0, "min_component_area": 0}
    try:
        prepared, cleanup = preprocessed_png(rgb, settings)
        svg_text = normalize_vtracer_svg(vtracer_svg(prepared, "png", settings), width, height, settings)
        nodes, contours, closed = svg_stats(svg_text)
    except Exception:
        engine = "opencv-contour-bezier-fallback"
        _, binary, cleanup = preprocess(rgb, settings)
        paths = fallback_paths(rgb, binary, 1.0, settings)
        svg_text, nodes, contours = fallback_svg(width, height, paths, settings)
        closed = contours
    dxf, dxf_polylines = _dxf_document(svg_text, settings.units)
    palette = _palette_from_svg(svg_text)
    return {
        "svg": svg_text.encode("utf-8"),
        "dxf": dxf,
        "width": width,
        "height": height,
        "node_count": nodes,
        "contour_count": dxf_polylines,
        "closed_paths": dxf_polylines,
        "dxf_polylines": dxf_polylines,
        "palette": palette,
        "processing": {
            "engine": engine,
            "engine_reference": "visioncortex/vtracer via PixelToPath workflow",
            "preprocessing": [
                "PixelToPath-inspired bilateral denoise",
                "CLAHE contrast enhancement",
                "connected-component speckle removal",
                "morphological gap closing",
            ],
            "removed_components": cleanup["removed_components"],
            "optimized": settings.simplify,
            "units": settings.units,
            "mode": settings.mode,
            "cut_ready": True,
        },
    }


def save_outputs(result: dict[str, Any], output_dir: str, stem: str | None = None) -> dict[str, str]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    safe_stem = (re.sub(r"[^a-zA-Z0-9_-]+", "-", stem or "vectorization").strip("-") or "vectorization")[:80]
    token = uuid.uuid4().hex[:10]
    svg_path = directory / f"{safe_stem}-{token}.svg"
    dxf_path = directory / f"{safe_stem}-{token}.dxf"
    svg_tmp = svg_path.with_suffix(".svg.tmp")
    dxf_tmp = dxf_path.with_suffix(".dxf.tmp")
    try:
        svg_tmp.write_bytes(result["svg"])
        dxf_tmp.write_bytes(result["dxf"])
        os.replace(svg_tmp, svg_path)
        os.replace(dxf_tmp, dxf_path)
    finally:
        svg_tmp.unlink(missing_ok=True)
        dxf_tmp.unlink(missing_ok=True)
    return {"svg": str(svg_path), "dxf": str(dxf_path), "id": token}
