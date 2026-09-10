// Preview — owner of active-entry DOM + zoom/compare/palette
import { Store } from "./store.js";
import { $, escapeHtml, formatBytes, downloadFile, formatEngineLine, statChips } from "./utils.js";

function bindResultDownloads(payload, entry) {
  $("svgButton").disabled = false;
  $("dxfButton").disabled = false;
  $("svgButton").onclick = () => downloadFile(payload.files.svg, `${entry.name.replace(/\.[^/.]+$/, "")}.svg`);
  $("dxfButton").onclick = () => downloadFile(payload.files.dxf, `${entry.name.replace(/\.[^/.]+$/, "")}.dxf`);
}

export const Preview = {
  renderSource() {
    const entries = Store.entries;
    const dropzone = $("dropzone"), previewWrap = $("previewWrap"), emptyState = $("emptyState");
    if (!entries.length) {
      dropzone.classList.remove("hidden"); emptyState.style.display = "flex"; previewWrap.classList.remove("visible");
      $("fileCrumb").textContent = "No file selected";
      return;
    }
    dropzone.classList.add("hidden"); emptyState.style.display = "none"; previewWrap.classList.add("visible");
    const entry = Store.getActive();
    if (!entry) return;
    $("fileCrumb").textContent = entry.name;
    const isVec = entry.type === "image/svg+xml" || entry.type === "application/pdf" || /\.(svg|pdf)$/i.test(entry.name);
    $("sourceType").textContent = isVec ? "VECTOR SOURCE" : (entry.type.split("/")[1] || "SOURCE").toUpperCase();
    if (isVec && entry.name.toLowerCase().endsWith(".svg")) {
      $("rasterCanvas").innerHTML = `<img src="${entry.objectUrl || ""}" alt="${escapeHtml(entry.name)}" id="rasterPreview" />`;
    } else if (isVec && entry.name.toLowerCase().endsWith(".pdf")) {
      $("rasterCanvas").innerHTML = `<div class="empty-vector"><strong>${escapeHtml(entry.name)}</strong>PDF vector source — will extract every curve precisely.</div>`;
    } else {
      $("rasterCanvas").innerHTML = entry.objectUrl ? `<img src="${entry.objectUrl}" alt="${escapeHtml(entry.name)}" id="rasterPreview" />` : `<div class="empty-vector">${escapeHtml(entry.name)}</div>`;
    }
    if (entry.svgUrl) {
      $("vectorCanvas").innerHTML = `<img src="${entry.svgUrl}" alt="Generated optimized SVG" id="svgPreview" />`;
      $("vectorStatus").textContent = "COMPLETE";
      $("svgButton").disabled = false; $("dxfButton").disabled = false;
      const s = entry.payload?.statistics;
      // The chip strip carries the counts; this line carries engine provenance.
      $("statsText").textContent = formatEngineLine(entry.payload?.processing);
      this.renderStats(s, entry.payload?.processing);
      bindResultDownloads(entry.payload, entry);
      this.renderPalette(entry.payload?.palette || []);
    } else if (entry.status === "vectorizing") {
      $("vectorCanvas").innerHTML = '<div class="empty-vector"><strong>Vectorizing…</strong>Running local engine.</div>';
      $("vectorStatus").textContent = "RUNNING";
      $("svgButton").disabled = true; $("dxfButton").disabled = true;
      $("statsText").textContent = "Vectorizing…";
      this.renderStats(null);
      $("palettePreview").innerHTML = "";
      $("paletteHint").textContent = "after vectorize";
    } else if (entry.status === "error") {
      $("vectorCanvas").innerHTML = `<div class="empty-vector"><strong>Error</strong>${escapeHtml(entry.error || "failed")}</div>`;
      $("vectorStatus").textContent = "ERROR";
      $("svgButton").disabled = true; $("dxfButton").disabled = true;
      $("statsText").textContent = entry.error || "Error";
      this.renderStats(null);
    } else {
      $("vectorCanvas").innerHTML = '<div class="empty-vector"><strong>Ready to vectorize</strong>Adjust settings, then run the local engine.</div>';
      $("vectorStatus").textContent = "READY";
      $("svgButton").disabled = true; $("dxfButton").disabled = true;
      $("statsText").textContent = `${formatBytes(entry.size)} · ${(entry.type.split("/")[1] || "file").toUpperCase()} source`;
      this.renderStats(null);
      $("palettePreview").innerHTML = "";
      $("paletteHint").textContent = "after vectorize";
    }
    $("compare").style.display = "none"; $("previewGrid").style.display = "grid";
    const cmpR = $("compareRaster"), cmpV = $("compareVector");
    if (entry.objectUrl && cmpR) cmpR.src = entry.objectUrl;
    if (entry.svgUrl && cmpV) {
      cmpV.style.backgroundImage = `url(${entry.svgUrl})`;
      cmpV.style.backgroundSize = "contain"; cmpV.style.backgroundPosition = "center"; cmpV.style.backgroundRepeat = "no-repeat";
    }
  },
  renderStats(stats, processing) {
    const strip = $("statStrip");
    if (!strip) return;
    const chips = statChips(stats, processing);
    strip.classList.toggle("visible", chips.length > 0);
    strip.innerHTML = chips.map((c) =>
      `<div class="stat-chip"><span class="stat-value">${escapeHtml(c.value)}</span><span class="stat-label">${escapeHtml(c.label)}</span><span class="stat-note">${escapeHtml(c.note)}</span></div>`
    ).join("");
  },
  renderPalette(palette) {
    const box = $("palettePreview"), hint = $("paletteHint");
    if (!palette || !palette.length) { if (box) box.innerHTML = ""; if (hint) hint.textContent = "no palette"; return; }
    if (hint) hint.textContent = `${palette.length} colors`;
    if (box) box.innerHTML = palette.map((c) => `<span class="swatch" title="${escapeHtml(c)}" style="background:${escapeHtml(c)}"></span>`).join("");
  },
};
