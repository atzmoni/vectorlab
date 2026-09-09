from __future__ import annotations

import io
import os
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from .dxf import svg_to_dxf

MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", "50000000"))
MAX_VECTOR_BYTES = int(os.getenv("MAX_VECTOR_BYTES", "8000000"))

try:
    import vtracer
except ImportError:  # pragma: no cover
    vtracer = None  # type: ignore


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


def _normalize_settings(settings: dict[str, Any] | None) -> VectorizeSettings:
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
    )


def _load_image(data: bytes) -> tuple[np.ndarray, int, int]:
    image = Image.open(io.BytesIO(data))
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise ValueError(f"Image dimensions exceed the {MAX_IMAGE_PIXELS:,}-pixel limit")
    image = image.convert("RGBA")
    rgba = np.array(image)
    rgb = rgba[:, :, :3]
    alpha = rgba[:, :, 3]
    background = np.full_like(rgb, 255)
    alpha_factor = alpha[:, :, None].astype(np.float32) / 255.0
    composited = (rgb * alpha_factor + background * (1 - alpha_factor)).astype(np.uint8)
    return composited, image.width, image.height


def _preprocess(rgb: np.ndarray, settings: VectorizeSettings) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if settings.noise_filter > 0:
        diameter = max(3, settings.noise_filter * 2 + 1)
        denoised = cv2.bilateralFilter(gray, diameter, 32 + settings.noise_filter * 8, 32 + settings.noise_filter * 8)
    else:
        denoised = gray
    clahe = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8))
    enhanced = clahe.apply(denoised)
    if settings.invert:
        enhanced = cv2.bitwise_not(enhanced)
    _, binary = cv2.threshold(enhanced, settings.threshold, 255, cv2.THRESH_BINARY_INV)
    kernel_size = 1 if settings.noise_filter == 0 else 2
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((kernel_size, kernel_size), np.uint8))
    closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    min_component_area = 1 if settings.noise_filter == 0 else max(4, settings.noise_filter * settings.noise_filter * 2)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    cleaned = np.zeros_like(closed)
    removed_components = 0
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= min_component_area:
            cleaned[labels == label] = 255
        else:
            removed_components += 1
    return enhanced, cleaned, {"removed_components": removed_components, "min_component_area": min_component_area}


def _preprocessed_png(rgb: np.ndarray, settings: VectorizeSettings) -> tuple[bytes, dict[str, int]]:
    enhanced, cleaned, cleanup = _preprocess(rgb, settings)
    if settings.mode == "monochrome":
        prepared = cv2.cvtColor(cv2.bitwise_not(cleaned), cv2.COLOR_GRAY2RGB)
    else:
        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
        lab[:, :, 0] = enhanced
        prepared = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
        if settings.invert:
            prepared = cv2.bitwise_not(prepared)
    ok, encoded = cv2.imencode(".png", cv2.cvtColor(prepared, cv2.COLOR_RGB2BGR))
    if not ok:
        raise ValueError("Could not encode preprocessed raster")
    return encoded.tobytes(), cleanup


def _hex(color: np.ndarray) -> str:
    return "#{:02x}{:02x}{:02x}".format(int(color[0]), int(color[1]), int(color[2]))


def _contour_path(
    contour: np.ndarray,
    scale: float,
    tolerance: float,
    corner_suppression: float,
    simplify: bool,
) -> tuple[str, list[tuple[float, float]]]:
    perimeter = cv2.arcLength(contour, True)
    epsilon_factor = 1.0 if simplify else 0.2
    epsilon = max(0.25, perimeter * (tolerance / 1000.0) * epsilon_factor * (1.15 - corner_suppression * 0.45))
    approx = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
    points = [(round(float(x) * scale, 3), round(float(y) * scale, 3)) for x, y in approx]
    if len(points) < 3:
        return "", points
    commands = [f"M {points[0][0]:.3f} {points[0][1]:.3f}"]
    for index in range(1, len(points)):
        current = points[index]
        following = points[(index + 1) % len(points)]
        if len(points) >= 5 and simplify:
            midpoint = ((current[0] + following[0]) / 2, (current[1] + following[1]) / 2)
            commands.append(f"Q {current[0]:.3f} {current[1]:.3f} {midpoint[0]:.3f} {midpoint[1]:.3f}")
        else:
            commands.append(f"L {current[0]:.3f} {current[1]:.3f}")
    commands.append("Z")
    return " ".join(commands), points


def _fallback_paths(rgb: np.ndarray, binary: np.ndarray, scale: float, settings: VectorizeSettings) -> list[tuple[str, str, float, int]]:
    min_area = max(12.0, rgb.shape[0] * rgb.shape[1] * 0.00008)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    results: list[tuple[str, str, float, int]] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        path, points = _contour_path(contour, scale, settings.tolerance, settings.corner_suppression, settings.simplify)
        if path:
            x, y, w, h = cv2.boundingRect(contour)
            sample = rgb[min(y + max(1, h // 2), rgb.shape[0] - 1), min(x + max(1, w // 2), rgb.shape[1] - 1)]
            fill = "#101216" if settings.mode == "monochrome" else _hex(sample)
            results.append((path, fill, area * scale * scale, len(points)))
    results.sort(key=lambda item: item[2], reverse=True)
    return results


def _fallback_svg(width: int, height: int, paths: list[tuple[str, str, float, int]], settings: VectorizeSettings) -> tuple[str, int, int]:
    physical_scale = 25.4 / 96 if settings.units == "mm" else 1 / 96
    physical_width, physical_height = width * physical_scale, height * physical_scale
    nodes = sum(item[3] for item in paths)
    svg_paths = [
        f'    <path id="contour-{index + 1}" d="{path}" fill="{fill}" fill-rule="evenodd"/>'
        for index, (path, fill, _, _) in enumerate(paths)
    ]
    svg = "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg"',
        f'  width="{physical_width:.3f}{settings.units}" height="{physical_height:.3f}{settings.units}"',
        f'  viewBox="0 0 {width:.3f} {height:.3f}" role="img">',
        '  <title>Local vectorization output</title>',
        '  <g id="vector-layer" stroke="none" stroke-linejoin="round">',
        *svg_paths,
        '  </g>',
        '</svg>',
    ])
    return svg, nodes, len(paths)


def _vtracer_svg(data: bytes, source_format: str, settings: VectorizeSettings) -> str:
    if vtracer is None:  # type: ignore
        raise RuntimeError("VTracer is not installed")
    if hasattr(vtracer, "Config") and hasattr(vtracer.Config, "convert_bytes"):  # type: ignore
        kwargs: dict[str, Any] = {
            "clustering": "bw" if settings.mode == "monochrome" else "color-cluster",
            "hierarchical": "stacked" if settings.mode == "monochrome" else "cutout",
            "mode": "spline",
            "filter_speckle": max(1, settings.noise_filter * 2),
            "path_precision": 3,
            "optimize": 2,
        }
        if settings.mode == "monochrome":
            kwargs["binary_threshold"] = settings.threshold
        if settings.simplify:
            kwargs["simplify"] = settings.tolerance
        return vtracer.Config(**kwargs).convert_bytes(data, format=source_format)  # type: ignore
    if hasattr(vtracer, "convert_raw_image_to_svg"):
        kwargs = {
            "colormode": "binary" if settings.mode == "monochrome" else "color",
            "hierarchical": "cutout" if settings.mode == "color" else "stacked",
            "mode": "spline",
            "filter_speckle": max(1, settings.noise_filter * 2),
            "path_precision": 3,
        }
        if settings.mode == "monochrome":
            kwargs["threshold"] = settings.threshold
        if settings.simplify:
            kwargs["simplify"] = settings.tolerance
        try:
            return vtracer.convert_raw_image_to_svg(data, img_format=source_format, **kwargs)  # type: ignore
        except TypeError:
            legacy = {key: value for key, value in kwargs.items() if key in {"colormode", "hierarchical", "mode", "filter_speckle", "color_precision", "layer_difference", "corner_threshold", "length_threshold", "max_iterations", "splice_threshold"}}
            return vtracer.convert_raw_image_to_svg(data, img_format=source_format, **legacy)  # type: ignore
    if hasattr(vtracer, "convert_bytes"):
        return vtracer.convert_bytes(data)  # type: ignore
    raise RuntimeError("Unsupported VTracer Python binding")


def _normalize_vtracer_svg(svg: str, width: int, height: int, settings: VectorizeSettings) -> str:
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
    return svg[:root_match.start()] + root + svg[root_match.end():]


def _sanitize_vector_svg(svg_text: str) -> str:
    """Strip scripts, event handlers, and cap size for safe handling."""
    if len(svg_text) > MAX_VECTOR_BYTES:
        raise ValueError(f"SVG exceeds {MAX_VECTOR_BYTES:,}-byte limit")
    # Remove <script> blocks
    svg_text = re.sub(r"<script[^>]*>.*?</script\s*>", "", svg_text, flags=re.DOTALL | re.IGNORECASE)
    # Remove event handler attributes (onload, onclick, etc.)
    svg_text = re.sub(r"\s+on\w+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", "", svg_text, flags=re.IGNORECASE)
    # Block external references that could leak
    if re.search(r"xlink:href\s*=\s*['\"][^'\"]*https?://", svg_text, flags=re.IGNORECASE):
        svg_text = re.sub(r"xlink:href\s*=\s*['\"][^'\"]*https?://[^'\"]*['\"]", "", svg_text, flags=re.IGNORECASE)
    # Validate well-formed
    try:
        ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise ValueError("Invalid SVG document") from exc
    return svg_text


def _normalize_any_svg(svg_text: str, settings: VectorizeSettings) -> tuple[str, int, int]:
    """Normalize any input SVG (not just VTracer) to consistent mm/in units."""
    svg_text = _sanitize_vector_svg(svg_text)
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise ValueError("Invalid SVG document") from exc
    # Determine viewBox or width/height for bounds
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
    # Fallback width/height
    def numeric(v: str) -> float | None:
        m = re.search(r"[-+]?(?:\d+\.?\d*|\.\d+)", v)
        return float(m.group(0)) if m else None
    if vb_w is None:
        vb_w = numeric(root.attrib.get("width", "")) or 100
    if vb_h is None:
        vb_h = numeric(root.attrib.get("height", "")) or 100
    # Physical scaling
    physical_scale = 25.4 / 96 if settings.units == "mm" else 1 / 96
    # Assume viewBox units are at 96 dpi if no explicit dpi; keep viewBox as-is, only adjust width/height attrs
    phys_w = vb_w * physical_scale
    phys_h = vb_h * physical_scale
    # Rewrite root attrs preserving viewBox
    root_match = re.search(r"<svg\b[^>]*>", svg_text, flags=re.IGNORECASE)
    if root_match:
        root_tag = root_match.group(0)
        if not re.search(r"\bviewBox\s*=", root_tag, flags=re.IGNORECASE):
            root_tag = root_tag[:-1] + f' viewBox="0 0 {vb_w:.3f} {vb_h:.3f}">'
        # Remove existing width/height and set new
        root_tag = re.sub(r"\s+(?:width|height)\s*=\s*(['\"]).*?\1", "", root_tag, flags=re.IGNORECASE)
        root_tag = root_tag[:-1] + f' width="{phys_w:.3f}{settings.units}" height="{phys_h:.3f}{settings.units}">'
        svg_text = svg_text[:root_match.start()] + root_tag + svg_text[root_match.end():]
        try:
            ET.fromstring(svg_text)
        except ET.ParseError:
            pass
    # Stats
    nodes, contours, closed = _svg_stats(svg_text)
    return svg_text, int(vb_w), int(vb_h)


def _pdf_to_svg(data: bytes, settings: VectorizeSettings) -> tuple[str, int, int]:
    """Extract precise vector outlines from PDF via PyMuPDF (no rasterization)."""
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
    # Combine all pages? For now take first page; multi-page PDFs: combine with page offsets
    # Use get_svg_image for highest fidelity (preserves Beziers)
    svgs: list[str] = []
    max_w = 0
    max_h = 0
    for page in doc:
        svg = page.get_svg_image(matrix=fitz.Matrix(1, 1))
        # Strip outer <svg> wrapper, keep inner paths; instead we can concatenate pages with translated offsets
        # For simplicity, take first page's viewBox and if multipage, stack vertically
        # Remove xml header from svg
        # Extract inner content and dimensions
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
            # fallback: use whole svg
            svgs.append(svg)
    if not svgs:
        raise ValueError("PDF contains no vector content")
    # Build combined SVG with proper viewBox
    combined_inner = "\n".join(svgs)
    combined = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {max_w:.3f} {max_h:.3f}">{combined_inner}</svg>'
    normalized, w, h = _normalize_any_svg(combined, settings)
    return normalized, w, h


def _svg_stats(svg: str) -> tuple[int, int, int]:
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError("Invalid SVG document") from exc
    path_data = [element.attrib.get("d", "") for element in root.iter() if isinstance(element.tag, str) and element.tag.rsplit("}", 1)[-1].lower() == "path"]
    nodes = sum(len(re.findall(r"[MLHVCSQTAZmlhvcsqtaz]", data)) for data in path_data)
    closed = sum(1 for data in path_data if re.search(r"z\s*$", data, flags=re.IGNORECASE))
    return nodes, len(path_data), closed


def _dxf_document(svg: str, units: str) -> tuple[bytes, int]:
    return svg_to_dxf(svg, units=units, curve_tolerance_px=1.0)


def vectorize(data: bytes, settings_dict: dict[str, Any] | None = None, source_format: str = "png") -> dict[str, Any]:
    settings = _normalize_settings(settings_dict)
    src = source_format.lower().replace("jpg", "jpeg").lstrip(".")
    # Vector path: SVG or PDF — preserve Beziers losslessly, no raster trace
    if src in {"svg"}:
        if len(data) > MAX_VECTOR_BYTES:
            raise ValueError(f"File exceeds {MAX_VECTOR_BYTES:,}-byte limit")
        svg_text_raw = data.decode("utf-8", errors="strict")
        svg_text, width, height = _normalize_any_svg(svg_text_raw, settings)
        nodes, contours, closed = _svg_stats(svg_text)
        dxf, dxf_polylines = _dxf_document(svg_text, settings.units)
        contours = dxf_polylines
        closed = dxf_polylines
        return {
            "svg": svg_text.encode("utf-8"),
            "dxf": dxf,
            "width": width,
            "height": height,
            "node_count": nodes,
            "contour_count": contours,
            "closed_paths": closed,
            "dxf_polylines": dxf_polylines,
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
        svg_text, width, height = _pdf_to_svg(data, settings)
        nodes, contours, closed = _svg_stats(svg_text)
        dxf, dxf_polylines = _dxf_document(svg_text, settings.units)
        contours = dxf_polylines
        closed = dxf_polylines
        return {
            "svg": svg_text.encode("utf-8"),
            "dxf": dxf,
            "width": width,
            "height": height,
            "node_count": nodes,
            "contour_count": contours,
            "closed_paths": closed,
            "dxf_polylines": dxf_polylines,
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
    # Raster path
    rgb, width, height = _load_image(data)
    if src not in {"png", "jpeg", "webp"}:
        raise ValueError("Unsupported raster format")
    engine = "vtracer-spline"
    cleanup = {"removed_components": 0, "min_component_area": 0}
    try:
        prepared, cleanup = _preprocessed_png(rgb, settings)
        svg_text = _normalize_vtracer_svg(_vtracer_svg(prepared, "png", settings), width, height, settings)
        nodes, contours, closed = _svg_stats(svg_text)
    except Exception:
        engine = "opencv-contour-bezier-fallback"
        _, binary, cleanup = _preprocess(rgb, settings)
        paths = _fallback_paths(rgb, binary, 1.0, settings)
        svg_text, nodes, contours = _fallback_svg(width, height, paths, settings)
        closed = contours
    dxf, dxf_polylines = _dxf_document(svg_text, settings.units)
    contours = dxf_polylines
    closed = dxf_polylines
    return {
        "svg": svg_text.encode("utf-8"),
        "dxf": dxf,
        "width": width,
        "height": height,
        "node_count": nodes,
        "contour_count": contours,
        "closed_paths": closed,
        "dxf_polylines": dxf_polylines,
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
