from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .config import MAX_IMAGE_PIXELS, MAX_VECTOR_BYTES  # re-export for compat

__all__ = ["VectorizeSettings", "normalize_settings", "MAX_IMAGE_PIXELS", "MAX_VECTOR_BYTES"]


@dataclass
class VectorizeSettings:
    mode: str = "monochrome"
    threshold: int = 150
    tolerance: float = 1.6
    corner_suppression: float = 0.35
    noise_filter: int = 2
    simplify: bool = True
    units: str = "mm"
    invert: bool = False
    color_precision: int = 6
    layer_difference: int = 16
    corner_threshold: int = 60
    splice_threshold: int = 45


def normalize_settings(settings: dict[str, Any] | None) -> VectorizeSettings:
    raw = settings or {}
    return VectorizeSettings(
        mode=raw.get("mode", "monochrome") if raw.get("mode") in {"monochrome", "color"} else "monochrome",
        threshold=int(np.clip(float(raw.get("threshold", 150)), 0, 255)),
        tolerance=float(np.clip(float(raw.get("tolerance", 1.6)), 0.2, 10)),
        corner_suppression=float(np.clip(float(raw.get("corner_suppression", 0.35)), 0, 1)),
        noise_filter=int(np.clip(float(raw.get("noise_filter", 2)), 0, 8)),
        simplify=bool(raw.get("simplify", True)),
        units=raw.get("units", "mm") if raw.get("units") in {"mm", "in"} else "mm",
        invert=bool(raw.get("invert", False)),
        color_precision=int(np.clip(float(raw.get("color_precision", 6)), 1, 8)),
        layer_difference=int(np.clip(float(raw.get("layer_difference", 16)), 2, 64)),
        corner_threshold=int(np.clip(float(raw.get("corner_threshold", 60)), 0, 180)),
        splice_threshold=int(np.clip(float(raw.get("splice_threshold", 45)), 0, 180)),
    )
