export const $ = (id) => document.getElementById(id);

export function escapeHtml(v) {
  return String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[c]);
}
export function formatBytes(b) {
  if (b < 1024 * 1024) return `${Math.round(b / 1024)} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
}
export function toast(msg, err = false) {
  const el = $("toast");
  if (!el) return;
  el.textContent = msg;
  el.className = `toast show${err ? " error" : ""}`;
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(() => (el.className = "toast"), 3600);
}
export function updateRange(input) {
  const min = Number(input.min);
  const max = Number(input.max);
  const pct = max === min ? 0 : ((Number(input.value) - min) / (max - min)) * 100;
  input.style.setProperty("--fill", `${pct}%`);
}
export function bindRange(id, outId, fmt = (v) => v) {
  const inp = $(id);
  const out = outId ? $(outId) : null;
  if (!inp) return;
  const sync = () => {
    if (out) out.textContent = fmt(inp.value);
    updateRange(inp);
  };
  inp.addEventListener("input", sync);
  sync();
}
export function collectSettings(getMode) {
  return {
    mode: getMode(),
    threshold: Number($("threshold").value),
    tolerance: Number($("tolerance").value),
    corner_suppression: Number($("corners").value) / 100,
    noise_filter: Number($("noise").value),
    simplify: $("simplify").classList.contains("on"),
    invert: $("invert").classList.contains("on"),
    units: $("units").value,
    color_precision: Number($("colorPrecision").value),
    layer_difference: Number($("layerDifference").value),
    corner_threshold: Number($("cornerThreshold").value),
    splice_threshold: Number($("spliceThreshold").value),
  };
}
export async function downloadFile(url, filename) {
  try {
    const r = await fetch(url);
    if (!r.ok) {
      let detail = "Download failed";
      try {
        const j = await r.json();
        detail = j.detail || detail;
      } catch {
        detail = await r.text().catch(() => detail);
      }
      throw new Error(detail);
    }
    const blob = await r.blob();
    const a = document.createElement("a");
    const href = URL.createObjectURL(blob);
    a.href = href;
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(href), 4000);
  } catch (e) {
    toast(e.message || "Download failed", true);
  }
}

const PX_TO_UNIT = { mm: 25.4 / 96, in: 1 / 96 };

export function formatNumber(n) {
  return Number(n || 0).toLocaleString();
}

/** Physical cut size for a source measured in CSS pixels. */
export function formatSize(widthPx, heightPx, units = "mm") {
  const factor = PX_TO_UNIT[units] ?? PX_TO_UNIT.mm;
  const round = (v) => (v * factor).toFixed(units === "in" ? 2 : 1);
  return `${round(widthPx)} × ${round(heightPx)} ${units}`;
}

/**
 * Stat chips for a completed vectorization.
 *
 * Contours and DXF entities are deliberately separate figures: a filled
 * contour exports as a HATCH plus an outline SPLINE, so the entity total runs
 * ahead of the geometry and the two must never be shown as one number.
 */
export function statChips(stats, processing = {}) {
  if (!stats || stats.contours == null) return [];
  const chips = [];
  const closed = Number(stats.closed_paths || 0);
  const open = Number(stats.open_paths || 0);
  const contours = Number(stats.contours ?? closed + open);
  chips.push({
    value: formatNumber(contours),
    label: contours === 1 ? "contour" : "contours",
    note: open ? `${formatNumber(closed)} closed · ${formatNumber(open)} open` : "all closed",
  });
  const entities = Number(stats.dxf_entities ?? stats.dxf_polylines ?? 0);
  if (entities) {
    const parts = [];
    if (stats.splines) parts.push(`${formatNumber(stats.splines)} spline${stats.splines === 1 ? "" : "s"}`);
    if (stats.hatches) parts.push(`${formatNumber(stats.hatches)} hatch${stats.hatches === 1 ? "" : "es"}`);
    chips.push({
      value: formatNumber(entities),
      label: entities === 1 ? "DXF entity" : "DXF entities",
      note: parts.join(" · ") || "cut-ready",
    });
  }
  const layers = Array.isArray(stats.layers) ? stats.layers : [];
  if (layers.length) {
    chips.push({
      value: formatNumber(layers.length),
      label: layers.length === 1 ? "layer" : "layers",
      note: "one operation per color",
    });
  }
  if (stats.width && stats.height) {
    chips.push({ value: formatSize(stats.width, stats.height, processing.units || "mm"), label: "cut size", note: "at 96 dpi" });
  }
  if (stats.nodes) chips.push({ value: formatNumber(stats.nodes), label: "nodes", note: "Bezier control points" });
  return chips;
}

/** Engine provenance line — complements the stat chips rather than repeating them. */
export function formatEngineLine(processing) {
  if (!processing) return "Completed";
  const parts = [];
  if (processing.engine) parts.push(processing.engine);
  if (processing.duration_ms != null) parts.push(`${formatNumber(processing.duration_ms)} ms`);
  if (processing.removed_components) parts.push(`${formatNumber(processing.removed_components)} speckles removed`);
  if (processing.cut_ready) parts.push("cut-ready");
  return parts.join(" · ") || "Completed";
}
