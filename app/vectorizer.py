from __future__ import annotations

"""Backward-compatible facade: re-exports the split engine.

This module existed as a 491-line god file; it is now a thin owner-delegating
facade so that ``from app.vectorizer import vectorize`` and
``vectorizer.vtracer`` patching keep working while real logic lives in
focused modules (preprocessing, vtracer_engine, svg_pdf, pipeline).
"""

from .pipeline import save_outputs, vectorize  # noqa: F401
from .preprocessing import fallback_paths, fallback_svg, load_image, preprocess, preprocessed_png  # noqa: F401
from .settings import MAX_IMAGE_PIXELS, MAX_VECTOR_BYTES, VectorizeSettings, normalize_settings  # noqa: F401
from .svg_pdf import normalize_any_svg, pdf_to_svg, sanitize_vector_svg, svg_stats  # noqa: F401
from .vtracer_engine import normalize_vtracer_svg, vtracer_svg  # noqa: F401

try:
    import vtracer as _vtracer  # type: ignore
except ImportError:  # pragma: no cover
    _vtracer = None  # type: ignore

vtracer = _vtracer  # re-export for tests that do ``monkeypatch.setattr(vectorizer, "vtracer", None)``

# Back-compat: old private names used directly by tests
_normalize_settings = normalize_settings  # type: ignore[assignment]
_load_image = load_image  # type: ignore[assignment]
_preprocess = preprocess  # type: ignore[assignment]
_preprocessed_png = preprocessed_png  # type: ignore[assignment]
_contour_path = None  # removed — now in preprocessing
_fallback_paths = fallback_paths  # type: ignore[assignment]
_fallback_svg = fallback_svg  # type: ignore[assignment]
_vtracer_svg = vtracer_svg  # type: ignore[assignment]
_normalize_vtracer_svg = normalize_vtracer_svg  # type: ignore[assignment]
_sanitize_vector_svg = sanitize_vector_svg  # type: ignore[assignment]
_normalize_any_svg = normalize_any_svg  # type: ignore[assignment]
_pdf_to_svg = pdf_to_svg  # type: ignore[assignment]
_svg_stats = svg_stats  # type: ignore[assignment]

__all__ = [
    "vectorize",
    "save_outputs",
    "VectorizeSettings",
    "vtracer",
    "MAX_IMAGE_PIXELS",
    "MAX_VECTOR_BYTES",
]
