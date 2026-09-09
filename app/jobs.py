from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(Path(__file__).resolve().parent.parent / "output"))).resolve()
DOWNLOAD_TTL_SECONDS = max(60, int(os.getenv("DOWNLOAD_TTL_SECONDS", "3600")))

# Robust token pattern: safe_stem (sanitized [A-Za-z0-9_-]) + "-" + 10 hex chars
# save_outputs uses uuid.uuid4().hex[:10] -> [0-9a-f]{10}
_TOKEN_RE = re.compile(r"^(?P<prefix>.+)-(?P<token>[0-9a-f]{10})$")


def _extract_token(path: Path) -> str | None:
    """Return token if filename matches '*-<10hex>.ext', else None."""
    m = _TOKEN_RE.match(path.stem)
    if not m:
        return None
    return m.group("token")


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
    # Sweep orphan files whose names match our output pattern but have no registry entry and have aged past TTL.
    # output_dir can be overridden (tests monkeypatch main.OUTPUT_DIR); default to module OUTPUT_DIR.
    try:
        if output_dir is not None:
            root = Path(output_dir).resolve()
        else:
            root = OUTPUT_DIR
        if root.exists():
            # SVG orphans + paired DXF
            for path in root.glob("*-*.svg"):
                try:
                    token = _extract_token(path)
                    if token is None:
                        continue
                    if token in _registry:
                        continue
                    if path.stat().st_mtime + DOWNLOAD_TTL_SECONDS < cur:
                        path.unlink(missing_ok=True)
                        # also try to delete paired DXF atomically
                        prefix = path.stem[: -(len(token) + 1)]
                        alt = root / f"{prefix}-{token}.dxf"
                        try:
                            # only delete if same mtime logic allows; but if SVG is expired, DXF is too
                            if alt.exists() and alt.stat().st_mtime + DOWNLOAD_TTL_SECONDS < cur:
                                alt.unlink(missing_ok=True)
                            elif alt.exists() and _extract_token(alt) == token:
                                # still check age separately in dxf loop; delete now if orphan
                                pass
                        except OSError:
                            pass
                        # also check if still orphan dxf with same token but different prefix (defensive)
                except OSError:
                    continue
            for path in list(root.glob("*-*.dxf")):
                try:
                    token = _extract_token(path)
                    if token is None:
                        continue
                    if token in _registry:
                        continue
                    if path.stat().st_mtime + DOWNLOAD_TTL_SECONDS < cur:
                        path.unlink(missing_ok=True)
                except OSError:
                    continue
            # stale tmp files from save_outputs atomic write
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
