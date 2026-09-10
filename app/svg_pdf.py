from __future__ import annotations

import re

from .config import MAX_VECTOR_BYTES
from .settings import VectorizeSettings
from .svg_utils import normalize_svg_root, sanitize_vector_svg

# Re-export shared helpers so existing imports keep working
__all__ = ["sanitize_vector_svg", "normalize_any_svg", "svg_stats", "pdf_to_svg", "extract_svg_palette"]

from .svg_utils import extract_svg_palette, svg_stats  # noqa: E402


def normalize_any_svg(svg_text: str, settings: VectorizeSettings) -> tuple[str, int, int]:
    svg_text = sanitize_vector_svg(svg_text)
    patched, vb_w, vb_h = normalize_svg_root(svg_text, settings.units)
    return patched, int(vb_w), int(vb_h)


def pdf_to_svg(data: bytes, settings: VectorizeSettings) -> tuple[str, int, int]:
    if len(data) > MAX_VECTOR_BYTES:
        raise ValueError(f"PDF exceeds {MAX_VECTOR_BYTES:,}-byte limit")
    if data[:5] != b"%PDF-":
        raise ValueError("Invalid PDF file")
    try:
        import fitz  # pymupdf
    except ImportError as exc:
        raise RuntimeError("pymupdf is required for PDF vector extraction") from exc
    doc = fitz.open(stream=data, filetype="pdf")
    if len(doc) == 0:
        raise ValueError("Empty PDF")
    svgs: list[str] = []
    max_w = max_h = 0.0
    for page in doc:
        svg = page.get_svg_image(matrix=fitz.Matrix(1, 1))
        m = re.search(r'<svg[^>]*viewBox="([^"]+)"[^>]*>(.*)</svg\s*>', svg, flags=re.DOTALL | re.IGNORECASE)
        if m:
            vb = m.group(1).strip().replace(",", " ").split()
            try:
                pw, ph = float(vb[2]), float(vb[3])
            except ValueError:
                pw, ph = page.rect.width, page.rect.height
            max_w = max(max_w, pw)
            max_h += ph
            svgs.append(m.group(2))
        else:
            svgs.append(svg)
    if not svgs:
        raise ValueError("PDF contains no vector content")
    combined = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {max_w:.3f} {max_h:.3f}">{"".join(svgs)}</svg>'
    return normalize_any_svg(combined, settings)
