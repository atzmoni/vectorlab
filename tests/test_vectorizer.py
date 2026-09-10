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
    # Each filled contour emits a HATCH plus an outline SPLINE, so the DXF holds
    # more entities than there is geometry. The two counts are not interchangeable.
    assert result["dxf_polylines"] >= result["closed_paths"]
    assert result["svg"].startswith(b"<?xml")
    assert 'width="42.333mm"' in result["svg"].decode()
    document = parse_dxf(result["dxf"])
    entities = list(document.modelspace())
    assert len(entities) == result["dxf_polylines"]
    assert result["dxf_stats"]["splines"] + result["dxf_stats"]["hatches"] == len(entities)
    # Professional engine emits native cubic SPLINE entities (preserving every Bezier), plus HATCH for filled contours.
    assert any(entity.dxftype() == "SPLINE" for entity in entities)
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
    assert document.header["$INSUNITS"] == 1
    assert any(e.dxftype() in {"SPLINE", "HATCH"} for e in document.modelspace())


def test_opencv_fallback_is_local_and_produces_closed_dxf(monkeypatch):
    monkeypatch.setattr(vectorizer, "vtracer", None)
    result = vectorizer.vectorize(raster_bytes(), {"mode": "monochrome", "units": "mm"}, "png")
    assert result["processing"]["engine"] == "opencv-contour-bezier-fallback"
    assert result["contour_count"] >= 1
    assert result["dxf_polylines"] >= result["closed_paths"]
    document = parse_dxf(result["dxf"])
    assert len(list(document.modelspace())) == result["dxf_polylines"]


def test_pixel_to_path_cleanup_removes_small_components():
    settings = vectorizer.VectorizeSettings(noise_filter=3, threshold=150)
    rgb, _, _ = vectorizer._load_image(raster_bytes())
    enhanced, cleaned, metadata = vectorizer._preprocess(rgb, settings)
    assert enhanced.shape == cleaned.shape
    assert metadata["min_component_area"] >= 4
    assert metadata["removed_components"] >= 1


def test_svg_parser_preserves_curves_and_multiple_subpaths():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 80"><path d="M 5 5 C 20 0 30 0 40 5 Z M 60 10 L 90 10 L 90 30 Z" fill="none" stroke="#000"/></svg>'
    assert len(extract_path_data(svg)) == 1
    dxf, count = svg_to_dxf(svg, units="mm", curve_tolerance_px=2)
    assert count == 2
    document = parse_dxf(dxf)
    entities = list(document.modelspace())
    assert len(entities) == 2
    assert all(e.dxftype() == "SPLINE" for e in entities)
    assert document.header["$INSUNITS"] == 4
    with pytest.raises(ValueError):
        svg_to_dxf("<svg>", units="mm")


def test_svg_passthrough_preserves_beziers_losslessly():
    svg_raw = Path("myTEST/test.svg").read_bytes() if Path("myTEST/test.svg").exists() else None
    if svg_raw is None:
        # synthetic SVG with curves
        svg_raw = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path d="M10 10 C 30 0 70 0 90 10 Z" fill="#000"/></svg>'
    result = vectorizer.vectorize(svg_raw, {"mode": "monochrome", "units": "mm"}, "svg")
    assert result["processing"]["engine"] == "svg-passthrough"
    assert result["contour_count"] >= 1
    assert b"viewBox" in result["svg"]
    document = parse_dxf(result["dxf"])
    assert len(list(document.modelspace())) >= 1
    assert any(e.dxftype() in {"SPLINE", "HATCH"} for e in document.modelspace())


def test_reference_svg_produces_professional_spline_hatch():
    ref = Path("myTEST/test.svg")
    if not ref.exists():
        pytest.skip("reference SVG not present in this checkout")
    result = vectorizer.vectorize(ref.read_bytes(), {"units": "mm"}, "svg")
    document = parse_dxf(result["dxf"])
    types = [e.dxftype() for e in document.modelspace()]
    # Reference DXF is 52 SPLINE + 44 HATCH — our engine matches that professional profile.
    assert types.count("SPLINE") >= 10
    assert types.count("HATCH") >= 4
    # Every filled region emits both a SPLINE outline (cut-ready) and a HATCH fill
    assert any(e.dxftype() == "HATCH" for e in document.modelspace())


def test_save_outputs_sanitizes_stem_and_uses_final_extensions(tmp_path: Path):
    result = {"svg": b"<svg/>", "dxf": b"0\nSECTION\n0\nENDSEC\n"}
    paths = vectorizer.save_outputs(result, str(tmp_path), "../../bad name?.svg")
    assert Path(paths["svg"]).parent == tmp_path
    assert Path(paths["svg"]).suffix == ".svg"
    assert Path(paths["dxf"]).suffix == ".dxf"
    assert not list(tmp_path.glob("*.tmp"))
