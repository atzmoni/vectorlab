from __future__ import annotations

from pathlib import Path


UI = Path(__file__).parents[1] / "app" / "static" / "index.html"
STATIC_DIR = Path(__file__).parents[1] / "app" / "static"


def _all_static_text() -> str:
    # Collect index.html + all static modules (behavior lives across split files)
    parts: list[str] = []
    for p in [UI, *STATIC_DIR.glob("*.js"), *STATIC_DIR.glob("*.css")]:
        try:
            parts.append(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return "\n".join(parts)


def test_static_ui_has_required_workspace_controls():
    html = UI.read_text(encoding="utf-8")
    for element_id in [
        "dropzone", "fileInput", "chooseButton", "threshold", "tolerance", "corners", "noise",
        "units", "simplify", "invert", "rasterCanvas", "vectorCanvas", "compare", "compareRange",
        "vectorizeButton", "svgButton", "dxfButton", "queueList",
    ]:
        assert f'id="{element_id}"' in html
    all_text = _all_static_text()
    assert 'accept="image/png,image/jpeg,image/webp' in html
    assert "image/svg+xml" in html
    assert "application/pdf" in html
    assert "multiple" in html
    assert "FormData" in all_text
    assert "URL.createObjectURL" in all_text


def test_static_ui_never_contains_the_supplied_api_secret():
    all_text = _all_static_text()
    assert "B2PbdY5zS44vj" not in all_text
    assert "S44vjIfWaWEJzmoAEBILU0WdiVMmd41" not in all_text


def test_static_ui_is_free_no_token_required():
    all_text = _all_static_text()
    assert 'id="apiToken"' not in all_text
    assert "Local API token" not in all_text
    assert "Authorization:" not in all_text
    assert "Bearer " not in all_text


def test_static_ui_supports_free_presigned_downloads():
    all_text = _all_static_text()
    assert "payload.files.svg" in all_text
    assert "payload.files.dxf" in all_text
    assert "compareVector" in all_text
    assert "zoomIn" in all_text and "zoomOut" in all_text


def test_static_ui_has_batch_queue_and_professional_controls():
    html = UI.read_text(encoding="utf-8")
    for element_id in ["vectorizeAllButton", "palettePreview", "colorPrecision", "layerDifference", "historyList"]:
        assert f'id="{element_id}"' in html
    all_text = _all_static_text()
    for token in ["Store", "Queue", "Preview", "Api", "localStorage", "AbortController"]:
        assert token in all_text
    assert "__vectorlab" in all_text


def test_static_ui_is_split_into_single_owner_modules():
    html = UI.read_text(encoding="utf-8")
    # Thin shell: link + import, not inline god file
    assert 'href="/static/style.css"' in html
    assert 'src="/static/app.js"' in html
    assert html.count("<style") == 0, "CSS should be in style.css, not inline"
    # No inline Store/Queue definitions — they live in modules
    assert html.count("const Store") == 0
    assert html.count("const Queue") == 0
    # Modules exist and own single concerns
    assert (STATIC_DIR / "style.css").exists()
    assert (STATIC_DIR / "store.js").exists()
    assert (STATIC_DIR / "api.js").exists()
    assert (STATIC_DIR / "preview.js").exists()
    assert (STATIC_DIR / "queue.js").exists()
    assert (STATIC_DIR / "utils.js").exists()
    assert (STATIC_DIR / "app.js").exists()
    # Each module owns one concern
    assert "localStorage" in (STATIC_DIR / "store.js").read_text(encoding="utf-8")
    assert "fetch" in (STATIC_DIR / "api.js").read_text(encoding="utf-8")
    assert "AbortController" in (STATIC_DIR / "queue.js").read_text(encoding="utf-8") or "AbortController" in (STATIC_DIR / "api.js").read_text(encoding="utf-8")
    assert "renderSource" in (STATIC_DIR / "preview.js").read_text(encoding="utf-8")
    assert "palette" in (STATIC_DIR / "preview.js").read_text(encoding="utf-8")
    assert "vectorizeAll" in (STATIC_DIR / "queue.js").read_text(encoding="utf-8")


def test_static_ui_has_the_vectorization_stat_readout():
    html = UI.read_text(encoding="utf-8")
    assert 'id="statStrip"' in html
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    for selector in [".stat-strip", ".stat-chip", ".stat-value", ".stat-label", ".stat-note"]:
        assert selector in css, f"missing style for {selector}"


def test_stat_readout_separates_contours_from_dxf_entities():
    """The two counts differ — a filled contour exports as a HATCH plus a SPLINE."""
    utils = (STATIC_DIR / "utils.js").read_text(encoding="utf-8")
    assert "statChips" in utils
    assert "dxf_entities" in utils
    assert "closed_paths" in utils and "open_paths" in utils
    assert "splines" in utils and "hatches" in utils
    preview = (STATIC_DIR / "preview.js").read_text(encoding="utf-8")
    assert "renderStats" in preview
    # The old readout labelled the entity total as closed contours.
    assert "closed contours" not in preview


def test_stat_readout_reports_physical_cut_size_and_engine():
    utils = (STATIC_DIR / "utils.js").read_text(encoding="utf-8")
    assert "formatSize" in utils and "cut size" in utils
    assert "formatEngineLine" in utils and "duration_ms" in utils


def test_history_reports_contours_and_entities_separately():
    queue = (STATIC_DIR / "queue.js").read_text(encoding="utf-8")
    assert "s.contours" in queue
    assert "dxf_entities" in queue
