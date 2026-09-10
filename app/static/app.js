// app.js — wiring: imports owners, binds controls, init. No state owns beyond imports.
import { Store } from "./store.js";
import { Api } from "./api.js";
import { Preview } from "./preview.js";
import { Queue } from "./queue.js";
import { $, bindRange, toast } from "./utils.js";

// ranges
bindRange("threshold", "thresholdValue");
bindRange("tolerance", "toleranceValue", (v) => `${Number(v).toFixed(1)} px`);
bindRange("corners", "cornersValue", (v) => `${v}%`);
bindRange("noise", "noiseValue");
bindRange("colorPrecision", "colorPrecisionValue");
bindRange("layerDifference", "layerDifferenceValue");
bindRange("cornerThreshold", "cornerThresholdValue", (v) => `${v}°`);
bindRange("spliceThreshold", "spliceThresholdValue", (v) => `${v}°`);

function refreshColorSection() {
  const show = Store.mode === "color";
  const sec = $("colorPrecisionSection");
  if (sec) sec.style.display = show ? "block" : "none";
}
document.querySelectorAll("[data-mode]").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll("[data-mode]").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    Store.mode = btn.dataset.mode;
    refreshColorSection();
  })
);
["simplify", "invert"].forEach((id) => $(id).addEventListener("click", () => $(id).classList.toggle("on")));
refreshColorSection();

const dropzone = $("dropzone"), fileInput = $("fileInput");

$("chooseButton").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (e) => acceptFiles([...e.target.files]));
["dragenter", "dragover"].forEach((n) => dropzone.addEventListener(n, (e) => { e.preventDefault(); dropzone.classList.add("drag"); }));
["dragleave", "drop"].forEach((n) => dropzone.addEventListener(n, (e) => { e.preventDefault(); dropzone.classList.remove("drag"); }));
dropzone.addEventListener("drop", (e) => acceptFiles([...e.dataTransfer.files]));

function acceptFiles(files) {
  const byExt = (n) => (n.split(".").pop() || "").toLowerCase();
  const isVec = (n) => ["svg", "pdf"].includes(byExt(n));
  const ok = files.filter((f) => ["image/png", "image/jpeg", "image/webp", "image/svg+xml", "application/pdf"].includes(f.type) || isVec(f.name));
  if (!ok.length) return toast("Please choose a PNG, JPG, WEBP, SVG, or PDF file.", true);
  Store.addFiles(ok);
  Preview.renderSource();
  Queue.render();
  Queue.renderHistory();
  toast(`${ok.length} file${ok.length > 1 ? "s" : ""} added to the queue.`);
}

// actions
$("vectorizeButton").addEventListener("click", async () => {
  const entry = Store.getActive();
  if (!entry) return toast("Add an image before vectorizing.", true);
  await Queue.vectorizeOne(entry);
});
$("vectorizeAllButton").addEventListener("click", () => Queue.vectorizeAll());
$("resetButton").addEventListener("click", () => {
  Store.clearHistory();
  $("dropzone").classList.remove("hidden"); $("previewWrap").classList.remove("visible"); $("emptyState").style.display = "flex";
  Queue.render(); Queue.renderHistory(); Preview.renderSource();
});
$("clearHistoryBtn").addEventListener("click", () => { Store.saveHistory([]); Queue.renderHistory(); toast("History cleared."); });
$("splitView").addEventListener("click", () => { $("splitView").classList.add("active"); $("compareView").classList.remove("active"); $("previewGrid").style.display = "grid"; $("compare").style.display = "none"; });
$("compareView").addEventListener("click", () => {
  const e = Store.getActive();
  if (!e?.svgUrl) return toast("Vectorize an image to compare both versions.", true);
  $("compareView").classList.add("active"); $("splitView").classList.remove("active"); $("previewGrid").style.display = "none"; $("compare").style.display = "block";
});
$("compareRange").addEventListener("input", (e) => {
  const v = e.target.value;
  $("compareLine").style.left = `${v}%`;
  const h = document.querySelector(".compare-handle"); if (h) h.style.left = `${v}%`;
  $("compareVector").style.clipPath = `inset(0 ${100 - v}% 0 0)`;
});
$("zoomIn").addEventListener("click", () => { Store.zoom = Math.min(2, Store.zoom + 0.1); applyZoom(); });
$("zoomOut").addEventListener("click", () => { Store.zoom = Math.max(0.5, Store.zoom - 0.1); applyZoom(); });
function applyZoom() {
  $("zoomValue").textContent = `${Math.round(Store.zoom * 100)}%`;
  document.querySelectorAll(".canvas img").forEach((img) => (img.style.transform = `scale(${Store.zoom})`));
  const cv = $("compareVector"); if (cv) cv.style.transform = `scale(${Store.zoom})`;
  const cr = $("compareRaster"); if (cr) cr.style.transform = `scale(${Store.zoom})`;
}

// init
Store.loadPersisted();
Queue.render(); Queue.renderHistory(); Preview.renderSource();
Api.fetchJobs().then((jobs) => {
  if (jobs.length) {
    const hist = Store.loadHistory();
    let added = 0;
    for (const j of jobs) {
      if (!hist.some((h) => h.payload.id === j.id)) { hist.push({ name: j.id, payload: j }); added++; }
    }
    if (added) { Store.saveHistory(hist); Queue.renderHistory(); }
  }
});

// expose for tests (stable)
window.__vectorlab = { Store, Api, Queue, Preview };
