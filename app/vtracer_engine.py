from __future__ import annotations

import re
from typing import Any

from .settings import VectorizeSettings

try:
    import vtracer
except ImportError:  # pragma: no cover
    vtracer = None  # type: ignore


def vtracer_svg(data: bytes, source_format: str, settings: VectorizeSettings) -> str:
    # Honor test patch on the facade (app.vectorizer.vtracer) without circular import at import time.
    try:
        import app.vectorizer as _vf  # lazy

        if getattr(_vf, "vtracer", vtracer) is None:  # patched off in tests
            raise RuntimeError("VTracer is not installed")
    except ImportError:
        pass
    if vtracer is None:  # type: ignore[truthy-bool]
        raise RuntimeError("VTracer is not installed")

    # Preferred current API (1.0.0a4)
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
        # Filter to accepted keys for the installed wheel
        try:
            return vtracer.Config(**kwargs).convert_bytes(data, format=source_format)  # type: ignore[union-attr]
        except TypeError:
            # Some wheels reject newer keys — retry with known-good subset
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
    physical_scale = 25.4 / 96 if settings.units == "mm" else 1 / 96
    physical_width, physical_height = width * physical_scale, height * physical_scale
    root_match = re.search(r"<svg\b[^>]*>", svg, flags=re.IGNORECASE)
    if not root_match:
        raise ValueError("VTracer returned invalid SVG")
    root = root_match.group(0)
    if not re.search(r"\bviewBox\s*=", root, flags=re.IGNORECASE):
        root = root[:-1] + f' viewBox="0 0 {width} {height}">'
    root = re.sub(r"\s+(?:width|height)\s*=\s*(['\"]).*?\1", "", root, flags=re.IGNORECASE)
    root = root[:-1] + f' width="{physical_width:.3f}{settings.units}" height="{physical_height:.3f}{settings.units}">'
    return svg[: root_match.start()] + root + svg[root_match.end() :]
