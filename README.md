# DXF.Vector

A self-contained raster-to-vector workspace and FastAPI backend for PNG, JPG, and WEBP images. Free and open — no API key or registration required. The SVG path engine is VTracer-first, with local OpenCV preprocessing and an OpenCV contour fallback when the VTracer wheel is unavailable. DXF output is generated locally with `ezdxf` as native cubic `SPLINE` and solid `HATCH` entities in R2004, preserving every Bezier rather than sampling it.

## Local setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env  # Windows
# cp .env.example .env  # macOS/Linux
python run.py
```

Open `http://127.0.0.1:8000`. No login, no token — just upload and vectorize.

The UI is informed by the interaction patterns in NeoSVG, 302 Vector Graphics Generation, and PixelToPath (drag/drop import, presets/controls, queue, zoom, and before/after comparison), while all conversion work remains local.

## Pipeline

1. Decode and composite the raster locally with Pillow.
2. Run a PixelToPath-inspired cleanup stage: bilateral edge-preserving denoise, CLAHE contrast enhancement, threshold/morphological cleanup, connected-component speckle removal, and gap closing.
3. Trace the cleaned raster with VTracer spline mode. Monochrome requests use VTracer `bw`; color requests use `color-cluster` with seam-free `cutout` hierarchy.
4. Normalize SVG dimensions to the selected mm/in units and report node/contour metadata.
5. Parse the SVG locally and emit native cubic `SPLINE` entities, plus a solid `HATCH` for every filled region. The parser resolves the full CSS cascade and applies each ancestor `transform`, converts `rect`/`circle`/`ellipse`/`line`/`polyline`/`polygon` to geometry, honors the `viewBox` origin, and skips non-rendered content (`defs`, `clipPath`, `mask`, `marker`, `symbol`, `pattern`) and hidden subtrees. Sampled `POLYLINE` output remains only as a fallback when no spline can be emitted.
6. Carry color through twice: an exact 24-bit `true_color` on every entity and a nearest-ACI index for readers that ignore it, with each distinct color on its own `COLOR_RRGGBB` layer so CAM can assign one operation per color. R2004 is the earliest DXF version that stores true color; R2000 silently discards it.
7. Save outputs beneath `OUTPUT_DIR` and return short-lived HMAC-signed download URLs.

## API

All endpoints are open — no `Authorization` header needed.

`POST /api/v1/vectorize` accepts `multipart/form-data`:

- `image`: PNG, JPEG, or WEBP upload
- `settings`: JSON string, for example:

```json
{
  "mode": "monochrome",
  "threshold": 150,
  "tolerance": 1.6,
  "corner_suppression": 0.35,
  "noise_filter": 2,
  "simplify": true,
  "units": "mm",
  "invert": false
}
```

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/vectorize \
  -F "image=@logo.png" \
  -F 'settings={"mode":"monochrome","units":"mm","noise_filter":3}'
```

Download URLs:

```bash
# files.svg and files.dxf are HMAC-signed and expire per DOWNLOAD_TTL_SECONDS
curl -L "http://127.0.0.1:8000$(jq -r .files.svg response.json)" -o out.svg
```

The response includes signed `files.svg` and `files.dxf` URLs, processing metadata and a `statistics` object. Download signatures expire according to `DOWNLOAD_TTL_SECONDS`.

`statistics` reports geometry and DXF content as separate figures, because a filled contour exports as a `HATCH` plus an outline `SPLINE` and so the entity total runs ahead of the geometry:

| Field | Meaning |
| --- | --- |
| `contours` | Subpaths in the drawing — `closed_paths` + `open_paths` |
| `closed_paths` / `open_paths` | How those subpaths split |
| `dxf_entities` | Entities written to the DXF — `splines` + `hatches` |
| `splines` / `hatches` | Entity breakdown |
| `layers` | Names of the layers actually used |
| `nodes` | Bezier control points |
| `width` / `height` | Source size in CSS pixels (96 dpi) |

`dxf_polylines` remains as an alias for `dxf_entities` for existing clients.

## Deployment

The service is packaged as a container. Any host that runs one — Railway,
Render, Fly.io, a VPS — works without changes:

```bash
docker compose up --build          # local, http://127.0.0.1:8000
# or
docker build -t vectorlab . && docker run -p 8000:8000 \
  -e DOWNLOAD_SIGNING_SECRET="$(openssl rand -hex 32)" \
  -v vectorlab-output:/data vectorlab
```

Set these before exposing the service:

| Variable | Why |
| --- | --- |
| `DOWNLOAD_SIGNING_SECRET` | Download URLs are HMAC-signed with it. Unset, it falls back to the placeholder in `app/config.py`, which is public — anyone could forge links. |
| `CORS_ORIGINS` | Defaults to localhost; set it to your real origin. |
| `OUTPUT_DIR` | Defaults to `/data/output` in the image, backed by a volume. |

`/data` must be a real volume, and a second replica needs shared storage behind
`OUTPUT_DIR` rather than its own disk. A vectorize request writes the SVG/DXF
and the download request reads it back, so the two have to see the same
filesystem.

### Railway

`railway.json` pins the Dockerfile builder and points the healthcheck at
`/health`; Railway injects `PORT`, which the image already honours.

1. **New Project → Deploy from GitHub repo**, and pick this repository.
2. **Variables** → set `DOWNLOAD_SIGNING_SECRET` (`openssl rand -hex 32`) and
   `CORS_ORIGINS` to the domain Railway assigns.
3. **Volumes** → attach one mounted at `/data`. Without it, outputs live in the
   container's writable layer and every redeploy invalidates outstanding
   download links.
4. Deploys follow pushes to the connected branch.

Keep the service at one replica unless `OUTPUT_DIR` is moved to shared storage
first — see the note above on why vectorize and download must share a
filesystem.

### Why not serverless

`vercel.json` and `api/index.py` are kept for reference, but the app does not
fit Vercel's Python runtime:

- **Downloads break.** Vectorize and download are separate invocations that can
  land on different instances, each with its own `/tmp`. The download 404s
  whenever it misses. The in-memory job registry in `app/jobs.py` splits the
  same way.
- **The bundle is too large.** The dependencies measure 73 MB compressed and
  220 MB unzipped, against the 50 MB `maxLambdaSize` in `vercel.json` and a
  250 MB uncompressed ceiling — before application code. `cv2` alone is 74 MB,
  `fitz` 49 MB, `numpy` 46 MB.
- **Trimming does not rescue it.** Dropping PDF support saves 20 MB and still
  misses the cap. Getting under it means dropping OpenCV, which sits on the
  primary path (`pipeline.py` → `preprocessed_png`), not on the fallback — so
  the cost is the preprocessing quality the engine is built around, and the
  download problem would remain regardless.

## Notes

- VTracer `1.0.0-alpha.4` is pinned because it exposes the current `Config(...).convert_bytes(...)` Python API.
- The OpenCV fallback is intentional for resilient local development, not an external service.
- The frontend supports multiple queued files, raster/vector split view, a comparison slider, zoom controls, color/monochrome modes, and direct SVG/DXF downloads.
- `pytest` is not required by the runtime. `python -m compileall -q app run.py` is a dependency-free syntax check.
- On Python versions without every exact pinned wheel, use the nearest compatible wheels and run `python -m pip check`; the test suite records the installed engine version in its output.
