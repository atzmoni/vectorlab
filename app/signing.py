from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from pathlib import Path

from fastapi import HTTPException

from .config import DOWNLOAD_SIGNING_SECRET

DOWNLOAD_SECRET = DOWNLOAD_SIGNING_SECRET  # compat alias


def sign_download(job_id: str, format_name: str, expires: int, secret: str | None = None) -> str:
    sec = secret or DOWNLOAD_SIGNING_SECRET
    payload = f"{job_id}:{format_name}:{expires}"
    digest = hmac.new(sec.encode(), payload.encode(), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return f"/api/v1/download/{job_id}/{format_name}?expires={expires}&signature={signature}"


def verify_download(job_id: str, format_name: str, expires: int, signature: str, secret: str | None = None) -> None:
    if expires < int(time.time()):
        raise HTTPException(status_code=410, detail="Download link expired")
    expected = sign_download(job_id, format_name, expires, secret=secret).split("signature=", 1)[1]
    if not secrets.compare_digest(signature, expected):
        raise HTTPException(status_code=403, detail="Invalid download signature")


def safe_output_path(output_dir: Path, job_id: str, format_name: str) -> Path:
    root = output_dir.resolve()
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
