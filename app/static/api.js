// Api — single owner of fetch/signing
export const Api = {
  async fetchJson(url, opts) {
    const r = await fetch(url, opts);
    let body = null;
    const ct = r.headers.get("content-type") || "";
    try { body = ct.includes("json") ? await r.json() : JSON.parse(await r.text()); }
    catch { body = { detail: await r.text().catch(() => "request failed") }; }
    if (!r.ok) throw new Error(body.detail || body.message || `HTTP ${r.status}`);
    return body;
  },
  async vectorize(entry, settings, signal) {
    const fd = new FormData();
    if (!entry.file) throw new Error("File no longer available — re-add it");
    fd.append("image", entry.file);
    fd.append("settings", JSON.stringify(settings));
    const payload = await this.fetchJson("/api/v1/vectorize", { method: "POST", body: fd, signal });
    let svgUrl = null;
    try {
      const rr = await fetch(payload.files.svg, { signal });
      if (!rr.ok) throw new Error("SVG preview download failed");
      const txt = await rr.text();
      const blob = new Blob([txt], { type: "image/svg+xml" });
      svgUrl = URL.createObjectURL(blob);
    } catch (e) { if (e.name === "AbortError") throw e; }
    return { payload, svgUrl };
  },
  async fetchJobs() {
    try { const j = await this.fetchJson("/api/v1/jobs"); return j.jobs || []; } catch { return []; }
  },
};
