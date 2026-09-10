// Queue — owner of batch orchestration (sequential Vectorize All, per-file Cancel/Retry/Go)
import { Store } from "./store.js";
import { Api } from "./api.js";
import { Preview } from "./preview.js";
import { $, escapeHtml, toast, downloadFile, collectSettings } from "./utils.js";

export const Queue = {
  render() {
    const list = $("queueList");
    $("queueCount").textContent = `${Store.entries.length} file${Store.entries.length === 1 ? "" : "s"}`;
    if (!Store.entries.length) { list.innerHTML = '<div class="queue-empty">Your recent vectorizations will appear here.</div>'; return; }
    list.innerHTML = Store.entries.map((e) => {
      const active = e.id === Store.activeId;
      const dot = e.status === "completed" ? "green" : e.status === "vectorizing" ? "orange" : e.status === "error" ? "red" : "";
      const statusLabel = e.status === "completed" ? "Completed" : e.status === "vectorizing" ? "Vectorizing" : e.status === "error" ? "Error" : "Queued";
      const actions = e.status === "vectorizing"
        ? `<button class="qbtn" data-cancel="${e.id}">Cancel</button>`
        : e.status === "error"
          ? `<button class="qbtn" data-retry="${e.id}">Retry</button><button class="qbtn" data-remove="${e.id}">Remove</button>`
          : e.status === "completed"
            ? `<button class="qbtn" data-download-svg="${e.id}">SVG</button><button class="qbtn" data-remove="${e.id}">✕</button>`
            : `<button class="qbtn" data-vectorize="${e.id}">Go</button><button class="qbtn" data-remove="${e.id}">✕</button>`;
      return `<div class="queue-item ${active ? "active" : ""} ${e.status === "vectorizing" ? "vectorizing" : ""}" data-id="${e.id}"><div class="thumb"><img src="${e.objectUrl || ""}" alt="" onerror="this.style.display='none'" /></div><div class="queue-meta"><div class="queue-name">${escapeHtml(e.name)}</div><div class="queue-status"><i class="dot ${dot}"></i>${statusLabel}</div>${e.status === "vectorizing" ? '<div class="progress"><i></i></div>' : ""}</div><div class="queue-actions">${actions}</div></div>`;
    }).join("");
    list.querySelectorAll("[data-id]").forEach((el) => el.addEventListener("click", (e) => {
      if (e.target.closest("[data-cancel]") || e.target.closest("[data-retry]") || e.target.closest("[data-remove]") || e.target.closest("[data-vectorize]") || e.target.closest("[data-download-svg]")) return;
      Store.setActive(el.dataset.id);
      Store.persist();
      Preview.renderSource(); Queue.render();
    }));
    list.querySelectorAll("[data-cancel]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); Queue.cancel(b.dataset.cancel); }));
    list.querySelectorAll("[data-retry]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); Queue.retry(b.dataset.retry); }));
    list.querySelectorAll("[data-vectorize]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); const id = b.dataset.vectorize; Store.setActive(id); Store.persist(); Preview.renderSource(); Queue.render(); Queue.vectorizeOne(Store.entries.find((x) => x.id === id)); }));
    list.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); Store.remove(b.dataset.remove); Preview.renderSource(); Queue.render(); Queue.renderHistory(); }));
    list.querySelectorAll("[data-download-svg]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); const ent = Store.entries.find((x) => x.id === b.dataset.downloadSvg); if (ent?.payload) downloadFile(ent.payload.files.svg, ent.name.replace(/\.[^/.]+$/, "") + ".svg"); }));
  },
  renderHistory() {
    const box = $("historyList");
    if (!box) return;
    const hist = Store.loadHistory();
    if (!hist.length) { box.innerHTML = '<div class="queue-empty">Past vectors persist here after reload.</div>'; return; }
    box.innerHTML = hist.slice(0, 12).map((h) => {
      const p = h.payload; const s = p.statistics || {};
      return `<div class="queue-item"><div class="thumb"><span style="font-size:15px">♡</span></div><div class="queue-meta"><div class="queue-name">${escapeHtml(h.name || p.id)}</div><div class="queue-status"><i class="dot green"></i>${s.contours ?? "?"} contours · ${s.dxf_entities ?? s.dxf_polylines ?? "?"} entities${p.palette && p.palette.length ? ` · ${p.palette.length} colors` : ""}</div></div><div class="queue-actions"><button class="qbtn" data-hist-svg="${p.files.svg}">SVG</button><button class="qbtn" data-hist-dxf="${p.files.dxf}">DXF</button></div></div>`;
    }).join("");
    box.querySelectorAll("[data-hist-svg]").forEach((b) => b.addEventListener("click", () => downloadFile(b.dataset.histSvg, "vector.svg")));
    box.querySelectorAll("[data-hist-dxf]").forEach((b) => b.addEventListener("click", () => downloadFile(b.dataset.histDxf, "vector.dxf")));
  },
  async vectorizeOne(entry) {
    if (!entry) return;
    if (entry.status === "vectorizing") return;
    const ctrl = new AbortController();
    Store.update(entry.id, { status: "vectorizing", error: null, controller: ctrl });
    Preview.renderSource(); Queue.render();
    try {
      const settings = collectSettings(() => Store.mode);
      const { payload, svgUrl } = await Api.vectorize(entry, settings, ctrl.signal);
      if (entry.svgUrl) try { URL.revokeObjectURL(entry.svgUrl); } catch {}
      Store.update(entry.id, { status: "completed", payload, svgUrl, controller: null });
      const hist = Store.loadHistory();
      hist.unshift({ name: entry.name, payload });
      Store.saveHistory(hist);
      Preview.renderSource(); Queue.render(); Queue.renderHistory();
      toast("Vector ready — closed contours exported for CAD/CAM.");
    } catch (err) {
      if (err.name === "AbortError") {
        Store.update(entry.id, { status: "queued", controller: null, error: "Cancelled" });
      } else {
        Store.update(entry.id, { status: "error", controller: null, error: err.message || String(err) });
        Preview.renderSource(); Queue.render();
        toast(err.message || "Vectorization failed", true);
        return;
      }
      Preview.renderSource(); Queue.render();
    }
  },
  async vectorizeAll() {
    const todo = Store.entries.filter((e) => e.status === "queued" || e.status === "error");
    if (!todo.length) return toast("Nothing to vectorize.", true);
    const btn = $("vectorizeAllButton"); if (btn) btn.disabled = true;
    for (const e of todo) {
      Store.setActive(e.id);
      Preview.renderSource(); Queue.render();
      await Queue.vectorizeOne(e);
    }
    if (btn) btn.disabled = false;
    Preview.renderSource(); Queue.render();
  },
  cancel(id) {
    const e = Store.entries.find((x) => x.id === id);
    if (e?.controller) try { e.controller.abort(); } catch {}
  },
  retry(id) {
    const e = Store.entries.find((x) => x.id === id);
    if (e) Queue.vectorizeOne(e);
  },
};
