from __future__ import annotations

import re
from typing import Any

from .settings import VectorizeSettings
from .svg_utils import normalize_svg_root

try:
    import vtracer
except ImportError:  # pragma: no cover
    vtracer = None  # type: ignore


def vtracer_svg(data: bytes, source_format: str, settings: VectorizeSettings) -> str:
    try:
        import app.vectorizer as _vf  # lazy — honors test patch

        if getattr(_vf, "vtracer", vtracer) is None:
            raise RuntimeError("VTracer is not installed")
    except ImportError:
        pass
    if vtracer is None:  # type: ignore[truthy-bool]
        raise RuntimeError("VTracer is not installed")

    if hasattr(vtracer, "Config") and hasattr(vtracer.Config, "convert_bytes"):  # type: ignore[attr-defined]
        kwargs: dict[str, Any] = {
            "clustering": "bw" if settings.mode == "monochrome" else "color-cluster",
            "hierarchical": "stacked" if settings.mode == "monochrome" else "cutout",
            "mode": "spline",
            "filter_speckle": max(1, settings.noise_filter * 2),
            "path_precision": 3,
            "optimize": 2,
            "color_precision": settings.color_precision,
            "layer_difference": settings.layer_difference,
            "corner_threshold": settings.corner_threshold,
            "splice_threshold": settings.splice_threshold,
            "length_threshold": 4.0,
            "max_iterations": 3,
        }
        if settings.mode == "monochrome":
            kwargs["binary_threshold"] = settings.threshold
        if settings.simplify:
            kwargs["simplify"] = settings.tolerance
        try:
            return vtracer.Config(**kwargs).convert_bytes(data, format=source_format)  # type: ignore[union-attr]
        except TypeError:
            keep = {"clustering", "hierarchical", "mode", "filter_speckle", "path_precision", "optimize", "binary_threshold", "simplify"}
            return vtracer.Config(**{k: v for k, v in kwargs.items() if k in keep}).convert_bytes(data, format=source_format)  # type: ignore[union-attr]

    if hasattr(vtracer, "convert_raw_image_to_svg"):
        kwargs: dict[str, Any] = {
            "colormode": "binary" if settings.mode == "monochrome" else "color",
            "hierarchical": "cutout" if settings.mode == "color" else "stacked",
            "mode": "spline",
            "filter_speckle": max(1, settings.noise_filter * 2),
            "path_precision": 3,
            "color_precision": settings.color_precision,
            "layer_difference": settings.layer_difference,
            "corner_threshold": settings.corner_threshold,
            "splice_threshold": settings.splice_threshold,
        }
        if settings.mode == "monochrome":
            kwargs["threshold"] = settings.threshold
        if settings.simplify:
            kwargs["simplify"] = settings.tolerance
        try:
            return vtracer.convert_raw_image_to_svg(data, img_format=source_format, **kwargs)  # type: ignore[union-attr]
        except TypeError:
            legacy = {k: v for k, v in kwargs.items() if k in {"colormode", "hierarchical", "mode", "filter_speckle", "color_precision", "layer_difference", "corner_threshold", "length_threshold", "max_iterations", "splice_threshold"}}
            return vtracer.convert_raw_image_to_svg(data, img_format=source_format, **legacy)  # type: ignore[union-attr]

    if hasattr(vtracer, "convert_bytes"):
        return vtracer.convert_bytes(data)  # type: ignore[union-attr]
    raise RuntimeError("Unsupported VTracer Python binding")


def normalize_vtracer_svg(svg: str, width: int, height: int, settings: VectorizeSettings) -> str:
    """Patch VTracer output width/height to physical units; viewBox is injected if missing."""
    if not re.search(r"<svg\b[^>]*>", svg, flags=re.IGNORECASE):
        raise ValueError("VTracer returned invalid SVG")
    # Delegate viewBox fallback + physical sizing to shared helper.
    # Ensure viewBox exists with raster dims so normalize_svg_root can size correctly.
    if not re.search(r"\bviewBox\s*=", svg, flags=re.IGNORECASE):
        svg = re.sub(r"<svg\b([^>]*)>", rf'<svg\1 viewBox="0 0 {width} {height}">', svg, count=1, flags=re.IGNORECASE)
    patched, _, _ = normalize_svg_root(svg, settings.units)
    return patched
