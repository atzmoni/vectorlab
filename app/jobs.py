from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .config import DOWNLOAD_TTL_SECONDS, OUTPUT_DIR
from .storage import extract_token


@dataclass
class JobRecord:
    id: str
    expires_at: int
    svg_path: str
    dxf_path: str
    statistics: dict
    processing: dict


_registry: dict[str, JobRecord] = {}


def register_job(record: JobRecord) -> None:
    _registry[record.id] = record


def list_jobs(now: int | None = None, output_dir: Path | str | None = None) -> list[JobRecord]:
    sweep_expired(now=now, output_dir=output_dir)
    return sorted(_registry.values(), key=lambda r: r.expires_at, reverse=True)


def sweep_expired(now: int | None = None, output_dir: Path | str | None = None) -> int:
    """Remove expired registry entries and delete stale output files. Returns count swept."""
    cur = int(now if now is not None else time.time())
    expired_ids = [jid for jid, rec in _registry.items() if rec.expires_at < cur]
    for jid in expired_ids:
        rec = _registry.pop(jid)
        for p in (rec.svg_path, rec.dxf_path):
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
    try:
        root = Path(output_dir).resolve() if output_dir is not None else OUTPUT_DIR
        if root.exists():
            for path in root.glob("*-*.svg"):
                try:
                    token = extract_token(path)
                    if token is None or token in _registry:
                        continue
                    if path.stat().st_mtime + DOWNLOAD_TTL_SECONDS < cur:
                        path.unlink(missing_ok=True)
                        prefix = path.stem[: -(len(token) + 1)]
                        alt = root / f"{prefix}-{token}.dxf"
                        try:
                            if alt.exists() and alt.stat().st_mtime + DOWNLOAD_TTL_SECONDS < cur:
                                alt.unlink(missing_ok=True)
                        except OSError:
                            pass
                except OSError:
                    continue
            for path in list(root.glob("*-*.dxf")):
                try:
                    token = extract_token(path)
                    if token is None or token in _registry:
                        continue
                    if path.stat().st_mtime + DOWNLOAD_TTL_SECONDS < cur:
                        path.unlink(missing_ok=True)
                except OSError:
                    continue
            for path in list(root.glob("*.tmp")):
                try:
                    if path.stat().st_mtime + DOWNLOAD_TTL_SECONDS < cur:
                        path.unlink(missing_ok=True)
                except OSError:
                    continue
    except OSError:
        pass
    return len(expired_ids)


def serialize_job(record: JobRecord, sign_fn) -> dict:
    return {
        "id": record.id,
        "expires_at": record.expires_at,
        "statistics": record.statistics,
        "processing": record.processing,
        "files": {
            "svg": sign_fn(record.id, "svg", record.expires_at),
            "dxf": sign_fn(record.id, "dxf", record.expires_at),
        },
    }
