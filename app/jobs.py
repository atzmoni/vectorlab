from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(Path(__file__).resolve().parent.parent / "output"))).resolve()
DOWNLOAD_TTL_SECONDS = max(60, int(os.getenv("DOWNLOAD_TTL_SECONDS", "3600")))


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


def list_jobs(now: int | None = None) -> list[JobRecord]:
    sweep_expired(now=now)
    return sorted(_registry.values(), key=lambda r: r.expires_at, reverse=True)


def sweep_expired(now: int | None = None) -> int:
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
    # Also sweep orphan files whose names no longer have a registry entry but have aged past TTL
    try:
        root = OUTPUT_DIR
        if root.exists():
            for path in root.glob("*-*.svg"):
                try:
                    if path.stat().st_mtime + DOWNLOAD_TTL_SECONDS < cur:
                        token = path.stem.rsplit("-", 1)[-1]
                        if token not in _registry:
                            path.unlink(missing_ok=True)
                            dxf_side = path.with_suffix("").with_suffix(".dxf")
                            # stem includes token; rebuild without double-suffix confusion
                            alt = root / f"{path.stem.rsplit('-',1)[0]}-{token}.dxf"
                            try:
                                alt.unlink(missing_ok=True)
                            except OSError:
                                pass
                except OSError:
                    continue
            for path in list(root.glob("*-*.dxf")):
                try:
                    if path.stat().st_mtime + DOWNLOAD_TTL_SECONDS < cur:
                        token = path.stem.rsplit("-", 1)[-1]
                        if token not in _registry:
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
