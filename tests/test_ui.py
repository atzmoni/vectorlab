from __future__ import annotations

from pathlib import Path


UI = Path(__file__).parents[1] / "app" / "static" / "index.html"


def test_static_ui_has_required_workspace_controls():
    html = UI.read_text(encoding="utf-8")
    for element_id in [
        "dropzone", "fileInput", "chooseButton", "threshold", "tolerance", "corners", "noise",
        "units", "simplify", "invert", "rasterCanvas", "vectorCanvas", "compare", "compareRange",
        "vectorizeButton", "svgButton", "dxfButton", "queueList",
    ]:
        assert f'id="{element_id}"' in html
    assert 'accept="image/png,image/jpeg,image/webp"' in html
    assert "multiple" in html
    assert "FormData" in html
    assert "URL.createObjectURL" in html


def test_static_ui_never_contains_the_supplied_api_secret():
    html = UI.read_text(encoding="utf-8")
    assert "B2PbdY5zS44vj" not in html
    assert "S44vjIfWaWEJzmoAEBILU0WdiVMmd41" not in html


def test_static_ui_is_free_no_token_required():
    html = UI.read_text(encoding="utf-8")
    assert 'id="apiToken"' not in html
    assert "Local API token" not in html
    assert "Authorization:" not in html
    assert "Bearer " not in html


def test_static_ui_supports_free_presigned_downloads():
    html = UI.read_text(encoding="utf-8")
    assert "payload.files.svg" in html
    assert "payload.files.dxf" in html
    assert "compareVector" in html
    assert "zoomIn" in html and "zoomOut" in html
