from __future__ import annotations

import io
import re

import httpx
import pytest
from PIL import Image, ImageDraw

from app import main


def png_bytes() -> bytes:
    image = Image.new("RGB", (64, 48), "white")
    ImageDraw.Draw(image).rectangle((8, 8, 56, 40), fill="black")
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def svg_bytes() -> bytes:
    return b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 80"><path d="M5 5 C20 0 30 0 40 5 Z" fill="#000" stroke="none"/></svg>'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(main, "DOWNLOAD_TTL_SECONDS", 3600)
    transport = httpx.ASGITransport(app=main.app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.anyio
async def test_health_is_open(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.anyio
async def test_vectorize_is_open_no_auth_required(client):
    response = await client.post(
        "/api/v1/vectorize",
        files={"image": ("x.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_multipart_vectorize_downloads_and_signed_expiry(client, monkeypatch):
    response = await client.post(
        "/api/v1/vectorize",
        files={"image": ("../safe.png", png_bytes(), "image/png")},
        data={"settings": '{"mode":"monochrome","units":"mm"}'},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["files"]["svg"].startswith("/api/v1/download/")
    assert payload["statistics"]["closed_paths"] >= 1
    svg = await client.get(payload["files"]["svg"])
    assert svg.status_code == 200
    assert svg.headers["content-type"].startswith("image/svg+xml")
    dxf = await client.get(payload["files"]["dxf"])
    assert dxf.status_code == 200
    assert dxf.headers["content-type"].startswith("application/dxf")
    expires = re.search(r"expires=(\d+)", payload["files"]["svg"]).group(1)
    monkeypatch.setattr(main, "DOWNLOAD_TTL_SECONDS", 60)
    expired_url = payload["files"]["svg"].replace(f"expires={expires}", "expires=1")
    expired = await client.get(expired_url)
    assert expired.status_code == 410


@pytest.mark.anyio
async def test_jobs_lists_history_and_color_palette(client):
    r1 = await client.post("/api/v1/vectorize", files={"image": ("a.png", png_bytes(), "image/png")}, data={"settings": '{"mode":"monochrome"}'})
    assert r1.status_code == 200
    # color request with precision mapping — should surface palette
    r2 = await client.post("/api/v1/vectorize", files={"image": ("b.png", png_bytes(), "image/png")}, data={"settings": '{"mode":"color","color_precision":6,"layer_difference":16}'})
    assert r2.status_code == 200
    assert "palette" in r2.json()
    jobs = await client.get("/api/v1/jobs")
    assert jobs.status_code == 200
    assert len(jobs.json()["jobs"]) >= 2


@pytest.mark.anyio
async def test_svg_vector_passthrough_endpoint(client):
    response = await client.post(
        "/api/v1/vectorize",
        files={"image": ("drawing.svg", svg_bytes(), "image/svg+xml")},
        data={"settings": '{"units":"mm"}'},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["statistics"]["closed_paths"] >= 1
    assert payload["processing"]["engine"] == "svg-passthrough"
    svg = await client.get(payload["files"]["svg"])
    assert svg.status_code == 200


@pytest.mark.anyio
async def test_signed_download_rejects_tampering_and_symlinks(client, tmp_path):
    response = await client.post("/api/v1/vectorize", files={"image": ("x.png", png_bytes(), "image/png")})
    payload = response.json()
    tampered = payload["files"]["svg"].replace("signature=", "signature=bad")
    assert (await client.get(tampered)).status_code == 403
    job_id = "symlinktest"
    target = tmp_path / "outside.svg"
    target.write_text("outside", encoding="utf-8")
    link = tmp_path / f"evil-{job_id}.svg"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    expires = int(__import__("time").time()) + 3600
    signed = main._sign_download(job_id, "svg", expires)
    assert (await client.get(signed)).status_code == 404


@pytest.mark.anyio
async def test_invalid_settings_type_and_content_type(client):
    invalid_json = await client.post("/api/v1/vectorize", files={"image": ("x.png", png_bytes(), "image/png")}, data={"settings": "[]"})
    assert invalid_json.status_code == 422
    invalid_mime = await client.post("/api/v1/vectorize", files={"image": ("x.txt", b"not image", "text/plain")})
    assert invalid_mime.status_code == 415


@pytest.mark.anyio
async def test_upload_limit_reads_only_one_byte_over_limit(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 1)
    oversized = b"0" * (1024 * 1024 + 1)
    response = await client.post("/api/v1/vectorize", files={"image": ("x.png", oversized, "image/png")})
    assert response.status_code == 413


@pytest.mark.anyio
async def test_statistics_report_geometry_and_dxf_entities_separately(client):
    """Contours count geometry; entities count what the DXF holds. They differ."""
    response = await client.post(
        "/api/v1/vectorize",
        files={"image": ("shape.svg", svg_bytes(), "image/svg+xml")},
        data={"settings": '{"units":"mm"}'},
    )
    assert response.status_code == 200, response.text
    stats = response.json()["statistics"]
    for key in ("contours", "closed_paths", "open_paths", "dxf_entities", "splines", "hatches", "layers"):
        assert key in stats, f"missing statistic {key}"
    assert stats["contours"] == stats["closed_paths"] + stats["open_paths"]
    assert stats["dxf_entities"] == stats["splines"] + stats["hatches"]
    # A filled contour emits a HATCH and an outline SPLINE, so entities exceed contours.
    assert stats["dxf_entities"] > stats["contours"]
    assert stats["dxf_polylines"] == stats["dxf_entities"]  # legacy alias


@pytest.mark.anyio
async def test_statistics_name_the_dxf_layers(client):
    response = await client.post(
        "/api/v1/vectorize",
        files={"image": ("shape.svg", svg_bytes(), "image/svg+xml")},
    )
    layers = response.json()["statistics"]["layers"]
    assert layers and all(isinstance(name, str) for name in layers)
