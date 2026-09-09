from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

from .settings import MAX_IMAGE_PIXELS, VectorizeSettings


def load_image(data: bytes) -> tuple[np.ndarray, int, int]:
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


def preprocess(rgb: np.ndarray, settings: VectorizeSettings) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
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


def preprocessed_png(rgb: np.ndarray, settings: VectorizeSettings) -> tuple[bytes, dict[str, int]]:
    enhanced, cleaned, cleanup = preprocess(rgb, settings)
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


def fallback_paths(rgb: np.ndarray, binary: np.ndarray, scale: float, settings: VectorizeSettings) -> list[tuple[str, str, float, int]]:
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


def fallback_svg(width: int, height: int, paths: list[tuple[str, str, float, int]], settings: VectorizeSettings) -> tuple[str, int, int]:
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
