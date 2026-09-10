from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv  # type: ignore
except ImportError:  # pragma: no cover
    def load_dotenv() -> None:  # type: ignore
        return None

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Vercel serverless has read-only filesystem except /tmp
_is_vercel = bool(os.getenv("VERCEL"))
_env_output = os.getenv("OUTPUT_DIR", "").strip()
if _env_output:
    OUTPUT_DIR = Path(_env_output).resolve()
    # On Vercel, remap any non-/tmp path to /tmp to avoid read-only errors
    if _is_vercel and not str(OUTPUT_DIR).startswith("/tmp"):
        # Preserve only the last path component under /tmp/output
        OUTPUT_DIR = Path("/tmp/output")
elif _is_vercel:
    OUTPUT_DIR = Path("/tmp/output")
else:
    OUTPUT_DIR = (BASE_DIR.parent / "output").resolve()
try:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass  # lambda cold start / read-only fallback; will retry on write
MAX_UPLOAD_MB = max(1, int(os.getenv("MAX_UPLOAD_MB", "20")))
DOWNLOAD_TTL_SECONDS = max(60, int(os.getenv("DOWNLOAD_TTL_SECONDS", "3600")))
SWEEP_INTERVAL_SECONDS = max(30, int(os.getenv("SWEEP_INTERVAL_SECONDS", "300")))
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",") if o.strip()]

DOWNLOAD_SIGNING_SECRET = os.getenv("DOWNLOAD_SIGNING_SECRET", "vectorlab-local-secret")

MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", "50000000"))
MAX_VECTOR_BYTES = int(os.getenv("MAX_VECTOR_BYTES", "8000000"))
MAX_SVG_CHARS = int(os.getenv("MAX_SVG_CHARS", "20000000"))

# File-token contract: save_outputs uses uuid.hex[:10] -> [0-9a-f]{10}, filename is "<safe_stem>-<token>.ext"
TOKEN_HEX_LEN = 10
