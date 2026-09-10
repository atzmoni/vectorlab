from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .config import MAX_VECTOR_BYTES


def sanitize_vector_svg(svg_text: str) -> str:
    if len(svg_text) > MAX_VECTOR_BYTES:
        raise ValueError(f"SVG exceeds {MAX_VECTOR_BYTES:,}-byte limit")
    # Strip scripts and event handlers, block remote xlink hrefs — shared policy
    svg_text = re.sub(r"<script[^>]*>.*?</script\s*>", "", svg_text, flags=re.DOTALL | re.IGNORECASE)
    svg_text = re.sub(r"\s+on\w+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", "", svg_text, flags=re.IGNORECASE)
    if re.search(r"xlink:href\s*=\s*['\"][^'\"]*https?://", svg_text, flags=re.IGNORECASE):
        svg_text = re.sub(r"xlink:href\s*=\s*['\"][^'\"]*https?://[^'\"]*['\"]", "", svg_text, flags=re.IGNORECASE)
    try:
        ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise ValueError("Invalid SVG document") from exc
    return svg_text


def _viewbox_dims(svg_text: str) -> tuple[float, float]:
    """Extract viewBox width/height or infer from width/height attributes."""
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        return 100.0, 100.0
    vb = root.attrib.get("viewBox", "").strip()
    vb_w = vb_h = None
    if vb:
        parts = vb.replace(",", " ").split()
        if len(parts) == 4:
            try:
                vb_w, vb_h = float(parts[2]), float(parts[3])
            except ValueError:
                vb_w = vb_h = None

    def numeric(v: str) -> float | None:
        m = re.search(r"[-+]?(?:\d+\.?\d*|\.\d+)", v)
        return float(m.group(0)) if m else None

    if vb_w is None:
        vb_w = numeric(root.attrib.get("width", "")) or 100.0
    if vb_h is None:
        vb_h = numeric(root.attrib.get("height", "")) or 100.0
    return float(vb_w), float(vb_h)


def normalize_svg_root(svg_text: str, units: str) -> tuple[str, float, float]:
    """Ensure SVG has viewBox and physical width/height in requested units. Returns (svg, vb_w, vb_h)."""
    vb_w, vb_h = _viewbox_dims(svg_text)
    physical_scale = 25.4 / 96 if units == "mm" else 1 / 96
    phys_w, phys_h = vb_w * physical_scale, vb_h * physical_scale
    root_match = re.search(r"<svg\b[^>]*>", svg_text, flags=re.IGNORECASE)
    if not root_match:
        # wrap bare content (should not happen for valid SVG, but be defensive)
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb_w:.3f} {vb_h:.3f}" width="{phys_w:.3f}{units}" height="{phys_h:.3f}{units}">{svg_text}</svg>', vb_w, vb_h
    root_tag = root_match.group(0)
    if not re.search(r"\bviewBox\s*=", root_tag, flags=re.IGNORECASE):
        root_tag = root_tag[:-1] + f' viewBox="0 0 {vb_w:.3f} {vb_h:.3f}">'
    root_tag = re.sub(r"\s+(?:width|height)\s*=\s*(['\"]).*?\1", "", root_tag, flags=re.IGNORECASE)
    root_tag = root_tag[:-1] + f' width="{phys_w:.3f}{units}" height="{phys_h:.3f}{units}">'
    patched = svg_text[: root_match.start()] + root_tag + svg_text[root_match.end():]
    try:
        ET.fromstring(patched)
    except ET.ParseError:
        # fall back to original if patch invalid
        return svg_text, vb_w, vb_h
    return patched, vb_w, vb_h


def svg_stats(svg: str) -> tuple[int, int, int]:
    """Return (node_count, path_count, closed_count) for an SVG."""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError("Invalid SVG document") from exc
    path_data = [e.attrib.get("d", "") for e in root.iter() if isinstance(e.tag, str) and e.tag.rsplit("}", 1)[-1].lower() == "path"]
    nodes = sum(len(re.findall(r"[MLHVCSQTAZmlhvcsqtaz]", d)) for d in path_data)
    closed = sum(1 for d in path_data if re.search(r"z\s*$", d, flags=re.IGNORECASE))
    return nodes, len(path_data), closed


def extract_svg_palette(svg_text: str, limit: int = 12) -> list[str]:
    """Best-effort distinct fill colors from inline fill/style attributes."""
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
        if fill and fill.lower() != "none" and fill not in seen:
            if re.match(r"^#[0-9a-fA-F]{3,8}$", fill):
                seen.append(fill)
            elif fill.lower() in {"black", "white", "red", "blue", "green"}:
                seen.append(fill)
            if len(seen) >= limit:
                break
        _ = klass
    return seen
