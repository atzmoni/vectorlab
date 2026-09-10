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
