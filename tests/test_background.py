from __future__ import annotations

import asyncio
import io
import time

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


@pytest.mark.anyio
async def test_vectorization_does_not_block_health(monkeypatch, tmp_path):
    async_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver")
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)

    def slow_vectorize(*args, **kwargs):
        time.sleep(0.20)
        return {
            "svg": b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0 L10 0 L10 10 Z"/></svg>',
            "dxf": b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n",
            "width": 10,
            "height": 10,
            "node_count": 4,
            "contour_count": 1,
            "closed_paths": 1,
            "dxf_polylines": 1,
            "processing": {"engine": "test", "cut_ready": True},
        }

    monkeypatch.setattr(main, "vectorize", slow_vectorize)
    try:
        vector_task = asyncio.create_task(async_client.post("/api/v1/vectorize", files={"image": ("x.png", png_bytes(), "image/png")}))
        await asyncio.sleep(0.02)
        start = time.perf_counter()
        health = await async_client.get("/health")
        health_elapsed = time.perf_counter() - start
        result = await vector_task
    finally:
        await async_client.aclose()
    assert health.status_code == 200
    assert health_elapsed < 0.15
    assert result.status_code == 200
