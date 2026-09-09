from __future__ import annotations

import io
import re
from pathlib import Path

import ezdxf
import pytest
from PIL import Image, ImageDraw

from app import vectorizer
from app.dxf import extract_path_data, svg_to_dxf


def raster_bytes(mode: str = "monochrome", format_name: str = "PNG") -> bytes:
    image = Image.new("RGB", (160, 120), "white")
    draw = ImageDraw.Draw(image)
    if mode == "color":
        draw.rectangle((12, 16, 76, 100), fill=(220, 38, 48))
        draw.ellipse((70, 24, 142, 98), fill=(28, 94, 220))
        draw.rectangle((82, 42, 112, 72), fill=(38, 190, 116))
    else:
        draw.rectangle((20, 20, 140, 100), fill="black")
        draw.ellipse((55, 35, 105, 85), fill="white")
        draw.rectangle((4, 4, 5, 5), fill="black")
    stream = io.BytesIO()
    image.save(stream, format_name)
    return stream.getvalue()


def parse_dxf(data: bytes):
    import io as _io
    return ezdxf.read(_io.StringIO(data.decode("utf-8")))


def test_real_vtracer_monochrome_svg_and_cut_ready_dxf():
    if vectorizer.vtracer is None:
        pytest.skip("VTracer is not installed")
    result = vectorizer.vectorize(raster_bytes(), {"mode": "monochrome", "units": "mm", "noise_filter": 2}, "png")
    assert result["processing"]["engine"] == "vtracer-spline"
    assert result["contour_count"] >= 1
    assert result["closed_paths"] == result["contour_count"]
    assert result["dxf_polylines"] == result["closed_paths"]
    assert result["svg"].startswith(b"<?xml")
    assert 'width="42.333mm"' in result["svg"].decode()
    document = parse_dxf(result["dxf"])
    entities = list(document.modelspace())
    assert len(entities) == result["dxf_polylines"]
    assert all(entity.dxftype() == "POLYLINE" for entity in entities)
    assert all(entity.dxf.flags & 1 for entity in entities)
    assert document.header["$INSUNITS"] == 4


def test_real_vtracer_color_mode_produces_multiple_regions():
    if vectorizer.vtracer is None:
        pytest.skip("VTracer is not installed")
    result = vectorizer.vectorize(raster_bytes("color"), {"mode": "color", "units": "in", "noise_filter": 2}, "png")
    svg = result["svg"].decode()
    assert result["processing"]["engine"] == "vtracer-spline"
    assert result["contour_count"] >= 2
    assert result["closed_paths"] == result["contour_count"]
    assert 'width="1.667in"' in svg
    assert len(re.findall(r"fill=\"#[0-9a-fA-F]{6}\"", svg)) >= 2
    document = parse_dxf(result["dxf"])
    assert all(entity.dxf.flags & 1 for entity in document.modelspace())
    assert document.header["$INSUNITS"] == 1


def test_opencv_fallback_is_local_and_produces_closed_dxf(monkeypatch):
    monkeypatch.setattr(vectorizer, "vtracer", None)
    result = vectorizer.vectorize(raster_bytes(), {"mode": "monochrome", "units": "mm"}, "png")
    assert result["processing"]["engine"] == "opencv-contour-bezier-fallback"
    assert result["contour_count"] >= 1
    assert result["dxf_polylines"] == result["closed_paths"]
    document = parse_dxf(result["dxf"])
    assert all(entity.dxf.flags & 1 for entity in document.modelspace())


def test_pixel_to_path_cleanup_removes_small_components():
    settings = vectorizer.VectorizeSettings(noise_filter=3, threshold=150)
    image = Image.new("RGB", (120, 90), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 100, 70), fill="black")
    draw.point((3, 3), fill="black")
    rgb, _, _ = vectorizer._load_image(raster_bytes())
    enhanced, cleaned, metadata = vectorizer._preprocess(rgb, settings)
    assert enhanced.shape == cleaned.shape
    assert metadata["min_component_area"] >= 4
    assert metadata["removed_components"] >= 1


def test_svg_parser_preserves_curves_and_multiple_subpaths():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 80"><path d="M 5 5 C 20 0 30 0 40 5 Z M 60 10 L 90 10 L 90 30 Z"/></svg>'
    assert len(extract_path_data(svg)) == 1
    dxf, count = svg_to_dxf(svg, units="mm", curve_tolerance_px=2)
    assert count == 2
    document = parse_dxf(dxf)
    entities = list(document.modelspace())
    assert len(entities) == 2
    assert all(entity.dxf.flags & 1 for entity in entities)
    assert document.header["$INSUNITS"] == 4
    with pytest.raises(ValueError):
        svg_to_dxf("<svg>", units="mm")


def test_save_outputs_sanitizes_stem_and_uses_final_extensions(tmp_path: Path):
    result = {"svg": b"<svg/>", "dxf": b"0\nSECTION\n0\nENDSEC\n"}
    paths = vectorizer.save_outputs(result, str(tmp_path), "../../bad name?.svg")
    assert Path(paths["svg"]).parent == tmp_path
    assert Path(paths["svg"]).suffix == ".svg"
    assert Path(paths["dxf"]).suffix == ".dxf"
    assert not list(tmp_path.glob("*.tmp"))
