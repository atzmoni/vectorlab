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

try:
    import vtracer
except ImportError:  # pragma: no cover - exercised only when optional wheel is unavailable
    vtracer = None


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
    """PixelToPath-inspired local cleanup: edge-preserving denoise, contrast, speckle removal, and gap closing."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if settings.noise_filter > 0:
        diameter = max(3, settings.noise_filter * 2 + 1)
        # Bilateral filtering removes isolated pixel noise without smearing drawing edges.
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
    # Remove connected components smaller than the requested speckle area.
    min_component_area = 1 if settings.noise_filter == 0 else max(4, settings.noise_filter * settings.noise_filter * 2)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    cleaned = np.zeros_like(closed)
    removed_components = 0
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= min_component_area:
            cleaned[labels == label] = 255
        else:
            removed_components += 1
    # Preserve the source geometry but expose a stable mask for fallback contour tracing.
    return enhanced, cleaned, {"removed_components": removed_components, "min_component_area": min_component_area}


def _preprocessed_png(rgb: np.ndarray, settings: VectorizeSettings) -> tuple[bytes, dict[str, int]]:
    """Prepare a clean raster for VTracer while preserving chroma in color mode."""
    enhanced, cleaned, cleanup = _preprocess(rgb, settings)
    if settings.mode == "monochrome":
        # VTracer's binary frontend expects dark foreground pixels. Feeding the
        # cleaned mask itself ensures thresholding and speckle removal are real
        # preprocessing stages even with legacy VTracer bindings.
        prepared = cv2.cvtColor(cv2.bitwise_not(cleaned), cv2.COLOR_GRAY2RGB)
    else:
        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
        lab[:, :, 0] = enhanced
        prepared = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
        # Keep the full color field intact; VTracer's color clustering and
        # filter_speckle handle region cleanup without erasing light artwork.
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
    if vtracer is None:
        raise RuntimeError("VTracer is not installed")
    if hasattr(vtracer, "Config") and hasattr(vtracer.Config, "convert_bytes"):
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
        return vtracer.Config(**kwargs).convert_bytes(data, format=source_format)
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
            return vtracer.convert_raw_image_to_svg(data, img_format=source_format, **kwargs)
        except TypeError:
            # 0.6.x does not expose simplify/path_precision; do not pass
            # unsupported keywords into the legacy native extension.
            legacy = {key: value for key, value in kwargs.items() if key in {"colormode", "hierarchical", "mode", "filter_speckle", "color_precision", "layer_difference", "corner_threshold", "length_threshold", "max_iterations", "splice_threshold"}}
            return vtracer.convert_raw_image_to_svg(data, img_format=source_format, **legacy)
    if hasattr(vtracer, "convert_bytes"):
        return vtracer.convert_bytes(data)
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
    # Centralized XML/path handling avoids corrupting SVG numbers with regex scaling.
    return svg_to_dxf(svg, units=units, curve_tolerance_px=1.0)


def vectorize(data: bytes, settings_dict: dict[str, Any] | None = None, source_format: str = "png") -> dict[str, Any]:
    settings = _normalize_settings(settings_dict)
    rgb, width, height = _load_image(data)
    source_format = source_format.lower().replace("jpg", "jpeg")
    if source_format not in {"png", "jpeg", "webp"}:
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
    # Count actual closed CAM contours, including holes or multiple subpaths
    # emitted inside a single SVG <path> element.
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
