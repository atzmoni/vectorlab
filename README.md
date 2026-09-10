# DXF.Vector

A self-contained raster-to-vector workspace and FastAPI backend for PNG, JPG, and WEBP images. Free and open — no API key or registration required. The SVG path engine is VTracer-first, with local OpenCV preprocessing and an OpenCV contour fallback when the VTracer wheel is unavailable. DXF output is generated locally with `ezdxf` as closed R2000 POLYLINE entities for broad CNC/CAM compatibility.

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
5. Parse SVG paths locally and sample curves into closed DXF R2000 `POLYLINE` entities on the `VECTOR_OUTLINES` layer. R2000 POLYLINE is broadly accepted by modern AutoCAD and CAM tools; the geometry is deliberately kept in the legacy POLYLINE entity family for compatibility.
6. Save outputs beneath `OUTPUT_DIR` and return short-lived HMAC-signed download URLs.

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

The response includes signed `files.svg` and `files.dxf` URLs, processing metadata, node statistics, closed contour counts, and DXF polyline counts. Download signatures expire according to `DOWNLOAD_TTL_SECONDS`.

## Notes

- VTracer `1.0.0-alpha.4` is pinned because it exposes the current `Config(...).convert_bytes(...)` Python API.
- The OpenCV fallback is intentional for resilient local development, not an external service.
- The frontend supports multiple queued files, raster/vector split view, a comparison slider, zoom controls, color/monochrome modes, and direct SVG/DXF downloads.
- `pytest` is not required by the runtime. `python -m compileall -q app run.py` is a dependency-free syntax check.
- On Python versions without every exact pinned wheel, use the nearest compatible wheels and run `python -m pip check`; the test suite records the installed engine version in its output.
