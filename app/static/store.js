// Store — single owner of queue entries + localStorage queue/history
// Stable id per file; no Map<File> leak.
const LS_KEY = "vectorlab:queue:v1";
const LS_HIST = "vectorlab:history:v1";

export const Store = {
  /** @type {Array<{id:string,file:File|null,name:string,size:number,type:string,objectUrl:string|null,status:string,payload:any,svgUrl:string|null,error:string|null,controller:AbortController|null}>} */
  entries: [],
  activeId: null,
  mode: "monochrome",
  zoom: 1,
  makeId() {
    return Math.random().toString(36).slice(2, 9) + Date.now().toString(36).slice(-4);
  },
  getActive() {
    return this.entries.find((e) => e.id === this.activeId) || this.entries[0] || null;
  },
  setActive(id) {
    this.activeId = id;
  },
  addFiles(fileList) {
    const ids = [];
    for (const f of fileList) {
      const id = this.makeId();
      let url = null;
      try {
        url = URL.createObjectURL(f);
      } catch {}
      this.entries.push({ id, file: f, name: f.name, size: f.size, type: f.type, objectUrl: url, status: "queued", payload: null, svgUrl: null, error: null, controller: null });
      ids.push(id);
    }
    if (ids.length) this.activeId = ids[0];
    this.persist();
    return ids;
  },
  remove(id) {
    const idx = this.entries.findIndex((e) => e.id === id);
    if (idx === -1) return;
    const e = this.entries[idx];
    try { if (e.objectUrl) URL.revokeObjectURL(e.objectUrl); } catch {}
    try { if (e.svgUrl) URL.revokeObjectURL(e.svgUrl); } catch {}
    try { e.controller?.abort(); } catch {}
    this.entries.splice(idx, 1);
    if (this.activeId === id) this.activeId = this.entries[0]?.id || null;
    this.persist();
  },
  update(id, patch) {
    const e = this.entries.find((x) => x.id === id);
    if (e) Object.assign(e, patch);
    this.persist();
  },
  persist() {
    try {
      const slim = this.entries.map((e) => ({
        id: e.id, name: e.name, size: e.size, type: e.type, status: e.status, error: e.error,
        payload: e.payload ? { id: e.payload.id, statistics: e.payload.statistics, processing: e.payload.processing, palette: e.payload.palette, expires_at: e.payload.expires_at, files: e.payload.files } : null,
      }));
      localStorage.setItem(LS_KEY, JSON.stringify({ activeId: this.activeId, entries: slim }));
    } catch {}
  },
  loadPersisted() {
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (!raw) return;
      const obj = JSON.parse(raw);
      if (Array.isArray(obj.entries)) {
        const hist = this.loadHistory();
        for (const s of obj.entries) {
          if (s.payload && !hist.some((h) => h.id === s.payload.id)) hist.push({ name: s.name, payload: s.payload });
        }
        this.saveHistory(hist);
      }
    } catch {}
  },
  loadHistory() {
    try { return JSON.parse(localStorage.getItem(LS_HIST) || "[]"); } catch { return []; }
  },
  saveHistory(list) {
    try { localStorage.setItem(LS_HIST, JSON.stringify(list.slice(0, 40))); } catch {}
  },
  clearHistory() {
    try { localStorage.removeItem(LS_HIST); localStorage.removeItem(LS_KEY); } catch {}
    this.entries.forEach((e) => {
      try { if (e.objectUrl) URL.revokeObjectURL(e.objectUrl); } catch {}
      try { if (e.svgUrl) URL.revokeObjectURL(e.svgUrl); } catch {}
    });
    this.entries = [];
    this.activeId = null;
    this.persist();
  },
};
