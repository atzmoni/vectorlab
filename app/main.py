from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Annotated

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> None:
        return None

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .vectorizer import save_outputs, vectorize

load_dotenv()
logger = logging.getLogger("vectorlab.api")
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(BASE_DIR.parent / "output"))).resolve()
DOWNLOAD_SECRET = os.getenv("DOWNLOAD_SIGNING_SECRET", "vectorlab-local-secret")
MAX_UPLOAD_MB = max(1, int(os.getenv("MAX_UPLOAD_MB", "20")))
DOWNLOAD_TTL_SECONDS = max(60, int(os.getenv("DOWNLOAD_TTL_SECONDS", "3600")))
CORS_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",") if origin.strip()]

app = FastAPI(title="VectorLab Local Vectorization API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET", "POST"], allow_headers=["Content-Type"], max_age=600)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _sign_download(job_id: str, format_name: str, expires: int) -> str:
    payload = f"{job_id}:{format_name}:{expires}"
    digest = hmac.new(DOWNLOAD_SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return f"/api/v1/download/{job_id}/{format_name}?expires={expires}&signature={signature}"


def _verify_download(job_id: str, format_name: str, expires: int, signature: str) -> None:
    if expires < int(time.time()):
        raise HTTPException(status_code=410, detail="Download link expired")
    expected = _sign_download(job_id, format_name, expires).split("signature=", 1)[1]
    if expected != signature:
        import secrets as _secrets
        if not _secrets.compare_digest(signature, expected):
            raise HTTPException(status_code=403, detail="Invalid download signature")


def _safe_output_path(job_id: str, format_name: str) -> Path:
    root = OUTPUT_DIR.resolve()
    candidates = list(root.glob(f"*-{job_id}.{format_name}"))
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (FileNotFoundError, OSError, ValueError):
            continue
        if resolved.is_file() and not candidate.is_symlink():
            return resolved
    raise HTTPException(status_code=404, detail="File not found")


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "engine": "local-vtracer-opencv-ezdxf"}


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
        result = await run_in_threadpool(vectorize, content, parsed_settings, extension)
        paths = await run_in_threadpool(save_outputs, result, str(OUTPUT_DIR), Path(image.filename or "vectorization").stem)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Vectorization failed")
        raise HTTPException(status_code=422, detail="Vectorization failed for this file") from exc
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    expires = int(time.time()) + DOWNLOAD_TTL_SECONDS
    return {
        "id": paths["id"],
        "status": "completed",
        "files": {
            "svg": _sign_download(paths["id"], "svg", expires),
            "dxf": _sign_download(paths["id"], "dxf", expires),
        },
        "expires_at": expires,
        "statistics": {
            "width": result["width"],
            "height": result["height"],
            "nodes": result["node_count"],
            "contours": result["contour_count"],
            "closed_paths": result["closed_paths"],
            "dxf_polylines": result["dxf_polylines"],
        },
        "processing": {**result["processing"], "duration_ms": elapsed_ms},
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
    _verify_download(job_id, format_name, expires, signature)
    output_path = _safe_output_path(job_id, format_name)
    media_type = "image/svg+xml" if format_name == "svg" else "application/dxf"
    return FileResponse(output_path, media_type=media_type, filename=output_path.name)
