from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

from .config import TOKEN_HEX_LEN

_TOKEN_RE = re.compile(rf"^(?P<prefix>.+)-(?P<token>[0-9a-f]{{{TOKEN_HEX_LEN}}})$")


def extract_token(path: Path) -> str | None:
    m = _TOKEN_RE.match(path.stem)
    return m.group("token") if m else None


def save_outputs(result: dict[str, Any], output_dir: str | Path, stem: str | None = None) -> dict[str, str]:
    """Atomically write SVG/DXF outputs. Returns {svg, dxf, id}."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    safe_stem = (re.sub(r"[^a-zA-Z0-9_-]+", "-", stem or "vectorization").strip("-") or "vectorization")[:80]
    token = uuid.uuid4().hex[:TOKEN_HEX_LEN]
    svg_path = directory / f"{safe_stem}-{token}.svg"
    dxf_path = directory / f"{safe_stem}-{token}.dxf"
    svg_tmp = svg_path.with_suffix(".svg.tmp")
    dxf_tmp = dxf_path.with_suffix(".dxf.tmp")
    try:
        svg_tmp.write_bytes(result["svg"])
        dxf_tmp.write_bytes(result["dxf"])
        os.replace(svg_tmp, svg_path)
        os.replace(dxf_tmp, dxf_path)
    finally:
        svg_tmp.unlink(missing_ok=True)
        dxf_tmp.unlink(missing_ok=True)
    return {"svg": str(svg_path), "dxf": str(dxf_path), "id": token}


__all__ = ["extract_token", "save_outputs", "_TOKEN_RE"]
