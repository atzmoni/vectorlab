from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .settings import MAX_VECTOR_BYTES, VectorizeSettings


def sanitize_vector_svg(svg_text: str) -> str:
    if len(svg_text) > MAX_VECTOR_BYTES:
        raise ValueError(f"SVG exceeds {MAX_VECTOR_BYTES:,}-byte limit")
    svg_text = re.sub(r"<script[^>]*>.*?</script\s*>", "", svg_text, flags=re.DOTALL | re.IGNORECASE)
    svg_text = re.sub(r"\s+on\w+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", "", svg_text, flags=re.IGNORECASE)
    if re.search(r"xlink:href\s*=\s*['\"][^'\"]*https?://", svg_text, flags=re.IGNORECASE):
        svg_text = re.sub(r"xlink:href\s*=\s*['\"][^'\"]*https?://[^'\"]*['\"]", "", svg_text, flags=re.IGNORECASE)
    try:
        ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise ValueError("Invalid SVG document") from exc
    return svg_text


def normalize_any_svg(svg_text: str, settings: VectorizeSettings) -> tuple[str, int, int]:
    svg_text = sanitize_vector_svg(svg_text)
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise ValueError("Invalid SVG document") from exc
    viewbox = root.attrib.get("viewBox", "").strip()
    vb_w = vb_h = None
    if viewbox:
        parts = viewbox.replace(",", " ").split()
        if len(parts) == 4:
            try:
                vb_w = float(parts[2])
                vb_h = float(parts[3])
            except ValueError:
                vb_w = vb_h = None

    def numeric(v: str) -> float | None:
        m = re.search(r"[-+]?(?:\d+\.?\d*|\.\d+)", v)
        return float(m.group(0)) if m else None

    if vb_w is None:
        vb_w = numeric(root.attrib.get("width", "")) or 100
    if vb_h is None:
        vb_h = numeric(root.attrib.get("height", "")) or 100
    physical_scale = 25.4 / 96 if settings.units == "mm" else 1 / 96
    phys_w = vb_w * physical_scale
    phys_h = vb_h * physical_scale
    root_match = re.search(r"<svg\b[^>]*>", svg_text, flags=re.IGNORECASE)
    if root_match:
        root_tag = root_match.group(0)
        if not re.search(r"\bviewBox\s*=", root_tag, flags=re.IGNORECASE):
            root_tag = root_tag[:-1] + f' viewBox="0 0 {vb_w:.3f} {vb_h:.3f}">'
        root_tag = re.sub(r"\s+(?:width|height)\s*=\s*(['\"]).*?\1", "", root_tag, flags=re.IGNORECASE)
        root_tag = root_tag[:-1] + f' width="{phys_w:.3f}{settings.units}" height="{phys_h:.3f}{settings.units}">'
        svg_text = svg_text[: root_match.start()] + root_tag + svg_text[root_match.end() :]
        try:
            ET.fromstring(svg_text)
        except ET.ParseError:
            pass
    return svg_text, int(vb_w), int(vb_h)


def svg_stats(svg: str) -> tuple[int, int, int]:
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError("Invalid SVG document") from exc
    path_data = [e.attrib.get("d", "") for e in root.iter() if isinstance(e.tag, str) and e.tag.rsplit("}", 1)[-1].lower() == "path"]
    nodes = sum(len(re.findall(r"[MLHVCSQTAZmlhvcsqtaz]", data)) for data in path_data)
    closed = sum(1 for data in path_data if re.search(r"z\s*$", data, flags=re.IGNORECASE))
    return nodes, len(path_data), closed


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
    max_w = 0.0
    max_h = 0.0
    for page in doc:
        svg = page.get_svg_image(matrix=fitz.Matrix(1, 1))
        m = re.search(r'<svg[^>]*viewBox="([^"]+)"[^>]*>(.*)</svg\s*>', svg, flags=re.DOTALL | re.IGNORECASE)
        if m:
            vb = m.group(1).strip().replace(",", " ").split()
            try:
                pw = float(vb[2])
                ph = float(vb[3])
            except ValueError:
                pw = page.rect.width
                ph = page.rect.height
            max_w = max(max_w, pw)
            max_h += ph
            svgs.append(m.group(2))
        else:
            svgs.append(svg)
    if not svgs:
        raise ValueError("PDF contains no vector content")
    combined_inner = "\n".join(svgs)
    combined = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {max_w:.3f} {max_h:.3f}">{combined_inner}</svg>'
    return normalize_any_svg(combined, settings)


def extract_svg_palette(svg_text: str, limit: int = 12) -> list[str]:
    """Return distinct fill colors present in an SVG (for palette preview)."""
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        return []
    seen: list[str] = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        fill = el.attrib.get("fill", "").strip()
        style = el.attrib.get("style", "")
        if style and "fill" in style.lower():
            for part in style.split(";"):
                if ":" in part and part.split(":", 1)[0].strip().lower() == "fill":
                    fill = part.split(":", 1)[1].strip()
        klass = el.attrib.get("class", "")
        # style-class fills handled via css classes are not resolved here; palette is best-effort from attributes
        if fill and fill.lower() != "none" and fill not in seen:
            # normalize shorthand
            if re.match(r"^#[0-9a-fA-F]{3,8}$", fill):
                seen.append(fill)
            elif fill.lower() in {"black", "white", "red", "blue", "green"}:
                seen.append(fill)
            if len(seen) >= limit:
                break
        # also scan style-class block for fills? keep cheap — caller can compute post-vectorize palette from vtracer output itself
        _ = klass
    return seen
