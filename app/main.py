from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import jobs
from .config import CORS_ORIGINS, DOWNLOAD_TTL_SECONDS, MAX_UPLOAD_MB, OUTPUT_DIR, STATIC_DIR, SWEEP_INTERVAL_SECONDS
from .pipeline import save_outputs, vectorize
from .signing import safe_output_path, sign_download, verify_download

logger = logging.getLogger("vectorlab.api")

# Re-export for tests that patch main.OUTPUT_DIR / main._sign_download etc.
_sign_download = sign_download  # type: ignore[assignment]
_verify_download = verify_download  # type: ignore[assignment]
_safe_output_path = safe_output_path  # type: ignore[assignment]
DOWNLOAD_SIGNING_SECRET = __import__("os").getenv("DOWNLOAD_SIGNING_SECRET", "vectorlab-local-secret")

vectorize_fn = vectorize  # allow tests to monkeypatch main.vectorize


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = asyncio.Event()

    async def sweeper() -> None:
        while not stop.is_set():
            try:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=SWEEP_INTERVAL_SECONDS)
                    break
                except asyncio.TimeoutError:
                    pass
                try:
                    await run_in_threadpool(jobs.sweep_expired, None, OUTPUT_DIR)
                except Exception:
                    logger.exception("Background sweep failed")
            except asyncio.CancelledError:
                break

    task = asyncio.create_task(sweeper())
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="DXF.Vector Local Vectorization API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    max_age=600,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "engine": "local-vtracer-opencv-ezdxf"}


@app.get("/api/v1/jobs")
async def list_jobs() -> dict:
    await run_in_threadpool(jobs.sweep_expired, None, OUTPUT_DIR)
    records = jobs.list_jobs(output_dir=OUTPUT_DIR)
    return {"jobs": [jobs.serialize_job(r, sign_download) for r in records]}


@app.post("/api/v1/vectorize")
async def vectorize_endpoint(
    image: Annotated[UploadFile, File(...)],
    settings: Annotated[str | None, Form()] = None,
) -> dict:
    vector_mimes = {"image/svg+xml", "application/pdf"}
    raster_mimes = {"image/png", "image/jpeg", "image/webp"}
    allowed = raster_mimes | vector_mimes
    filename_lower = (image.filename or "").lower()
    is_vector_by_ext = filename_lower.endswith(".svg") or filename_lower.endswith(".pdf")
    if image.content_type not in allowed and not is_vector_by_ext:
        raise HTTPException(status_code=415, detail="Only PNG, JPG, WEBP, SVG, and PDF are supported")
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    content = await image.read(max_bytes + 1)
    await image.close()
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Upload exceeds {MAX_UPLOAD_MB} MB limit")
    if len(settings or "") > 16_384:
        raise HTTPException(status_code=413, detail="settings field is too large")
    try:
        parsed_settings = json.loads(settings or "{}")
        if not isinstance(parsed_settings, dict):
            raise ValueError("settings must be an object")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="settings must be valid JSON object") from exc
    started = time.perf_counter()
    try:
        extension = Path(image.filename or "source.png").suffix.lower().lstrip(".") or "png"
        if image.content_type == "image/svg+xml" and not extension:
            extension = "svg"
        if image.content_type == "application/pdf" and not extension:
            extension = "pdf"
        await run_in_threadpool(jobs.sweep_expired, None, OUTPUT_DIR)
        fn = vectorize_fn
        try:
            result = await run_in_threadpool(fn, content, parsed_settings, extension)
        except TypeError:
            result = await run_in_threadpool(fn, content, parsed_settings)  # type: ignore[call-arg]
        paths = await run_in_threadpool(save_outputs, result, str(OUTPUT_DIR), Path(image.filename or "vectorization").stem)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Vectorization failed")
        raise HTTPException(status_code=422, detail="Vectorization failed for this file") from exc
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    expires = int(time.time()) + DOWNLOAD_TTL_SECONDS
    statistics = {
        "width": result["width"],
        "height": result["height"],
        "nodes": result["node_count"],
        "contours": result["contour_count"],
        "closed_paths": result["closed_paths"],
        "dxf_polylines": result["dxf_polylines"],
    }
    processing = {**result["processing"], "duration_ms": elapsed_ms}
    try:
        jobs.register_job(
            jobs.JobRecord(
                id=paths["id"],
                expires_at=expires,
                svg_path=paths["svg"],
                dxf_path=paths["dxf"],
                statistics=statistics,
                processing=processing,
            )
        )
    except Exception:
        logger.exception("Job registry failed")
    return {
        "id": paths["id"],
        "status": "completed",
        "files": {
            "svg": sign_download(paths["id"], "svg", expires),
            "dxf": sign_download(paths["id"], "dxf", expires),
        },
        "expires_at": expires,
        "statistics": statistics,
        "processing": processing,
        "palette": result.get("palette", []),
    }


@app.get("/api/v1/download/{job_id}/{format_name}")
async def download(
    job_id: str,
    format_name: str,
    expires: int,
    signature: str,
) -> FileResponse:
    if format_name not in {"svg", "dxf"} or not job_id.isalnum():
        raise HTTPException(status_code=404, detail="File not found")
    verify_download(job_id, format_name, expires, signature)
    output_path = safe_output_path(OUTPUT_DIR, job_id, format_name)
    media_type = "image/svg+xml" if format_name == "svg" else "application/dxf"
    return FileResponse(output_path, media_type=media_type, filename=output_path.name)


vectorize = vectorize_fn  # type: ignore[no-redef]
