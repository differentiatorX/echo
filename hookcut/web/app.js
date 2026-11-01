const $ = (s, el = document) => el.querySelector(s);
const main = $("#main");
let poll = null;
let META = { presets: {} };
const prefs = { count: 4, preset: "hookcut", cold_open: true };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (t) => { t = Math.max(0, Math.round(t || 0)); const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60; return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`; };
const ago = (ts) => { const d = (Date.now() / 1000 - ts); if (d < 60) return "just now"; if (d < 3600) return `${Math.floor(d / 60)} min ago`; if (d < 86400) return `${Math.floor(d / 3600)} h ago`; return new Date(ts * 1000).toLocaleDateString(); };

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts, body: opts.body && typeof opts.body !== "string" && !(opts.body instanceof FormData) ? JSON.stringify(opts.body) : opts.body });
  if (!res.ok) { let msg = res.statusText; try { msg = (await res.json()).detail || msg; } catch {} throw new Error(msg); }
  return res.json();
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2600); }

/* ---------------- router ---------------- */
async function route() {
  clearInterval(poll);
  const [, view, id] = location.hash.split("/");
  document.querySelectorAll("nav a").forEach((a) => a.removeAttribute("aria-current"));
  const nav = { "": "home", job: "home", queue: "queue", settings: "settings" }[view || ""] || "home";
  document.querySelector(`nav a[data-nav="${nav}"]`)?.setAttribute("aria-current", "page");
  try {
    if (view === "job" && id) await jobView(id);
    else if (view === "queue") await queueView();
    else if (view === "settings") await settingsView();
    else await homeView();
  } catch (e) { main.innerHTML = `<div class="banner"><b>Couldn't load this page.</b><pre>${esc(e.message)}</pre></div>`; }
  main.classList.remove("enter"); void main.offsetWidth; main.classList.add("enter");
  main.focus({ preventScroll: true });
  countUp();
}

function countUp() {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.querySelectorAll("[data-score]:not([data-counted])").forEach((el) => {
    el.dataset.counted = "1";
    const target = +el.dataset.score, node = el.firstChild;
    if (reduce || !node) return;
    const t0 = performance.now(), dur = 1400;
    const tick = (now) => { const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 4); node.textContent = Math.round(target * e); if (p < 1) requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  });
}
document.addEventListener("pointermove", (e) => {
  const card = e.target.closest?.(".clip"); if (!card) return;
  const r = card.getBoundingClientRect();
  card.style.setProperty("--mx", `${e.clientX - r.left}px`); card.style.setProperty("--my", `${e.clientY - r.top}px`);
});
window.addEventListener("hashchange", route);

/* ---------------- studio ---------------- */
async function homeView() {
  const jobs = await api("/api/jobs");
  const presets = Object.entries(META.presets);
  main.innerHTML = `
  <section class="hero">
    <h1 class="sheen">Long video in.<br>The few Reels that hit out.</h1>
    <p>Hookcut reads the transcript with Claude, checks it against what real viewers rewatched and quoted in the comments, and cuts only the clips worth posting.</p>
    <form class="dropbar" id="go">
      <input id="src" type="url" inputmode="url" autocomplete="off" placeholder="Paste a YouTube link" aria-label="YouTube link" required>
      <span class="file btn ghost" title="Upload a video file">Upload
        <input type="file" id="file" accept="video/*" aria-label="Upload a video file"></span>
      <button class="btn primary" type="submit">Find clips</button>
    </form>
    <div class="opts">
      <div class="opt">Clips <div class="seg" role="group" aria-label="Number of clips">
        ${[3, 4, 5].map((n) => `<button type="button" data-count="${n}" aria-pressed="${prefs.count === n}">${n}</button>`).join("")}
      </div></div>
      <div class="opt">Captions <div class="seg" role="group" aria-label="Caption style">
        ${presets.map(([k, p]) => `<button type="button" data-preset="${k}" aria-pressed="${prefs.preset === k}"><span class="swatch" style="background:${p.active}"></span> ${esc(p.label)}</button>`).join("")}
      </div></div>
      <label class="switch opt"><input type="checkbox" id="cold" ${prefs.cold_open ? "checked" : ""}> Cold open teaser</label>
    </div>
  </section>
  <section class="jobs" aria-labelledby="recent">
    <h2 id="recent">Recent videos</h2>
    ${jobs.length ? `<ul class="joblist">${jobs.map((j, i) => jobRow(j, i)).join("")}</ul>` : `<p class="empty">Nothing cut yet. Paste a podcast or interview link above; conversations with strong opinions clip best.</p>`}
  </section>`;

  main.querySelectorAll("[data-count]").forEach((b) => b.onclick = () => { prefs.count = +b.dataset.count; homeView(); });
  main.querySelectorAll("[data-preset]").forEach((b) => b.onclick = () => { prefs.preset = b.dataset.preset; homeView(); });
  $("#cold").onchange = (e) => prefs.cold_open = e.target.checked;
  const bar = $("#go");
  ["dragenter", "dragover"].forEach((ev) => bar.addEventListener(ev, (e) => { e.preventDefault(); bar.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => bar.addEventListener(ev, () => bar.classList.remove("drag")));
  bar.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) startUpload(e.dataTransfer.files[0]); });
  $("#file").onchange = (e) => e.target.files[0] && startUpload(e.target.files[0]);
  bar.onsubmit = async (e) => { e.preventDefault(); await start($("#src").value.trim()); };
  if (jobs.some((j) => j.state === "running" || j.state === "queued")) poll = setInterval(async () => { if (!location.hash.startsWith("#/job")) { const j = await api("/api/jobs"); const ul = $(".joblist"); if (ul) ul.innerHTML = j.map((x) => jobRow(x, 0)).join(""); } }, 2500);
}

function jobRow(j, i = 0) {
  const label = { queued: "Waiting", running: "Working", done: `${j.clip_count} clips`, error: "Failed" }[j.state] || j.state;
  return `<li style="--i:${i}"><a href="#/job/${encodeURIComponent(j.id)}">
    ${j.thumbnail ? `<img src="${esc(j.thumbnail)}" alt="" loading="lazy">` : `<span class="ph"></span>`}
    <span><span class="t">${esc(j.title)}</span><br><span class="s">${esc(j.channel || "")}${j.channel ? ", " : ""}${ago(j.created)}${j.best ? `, best score ${Math.round(j.best)}` : ""}</span></span>
    <span class="pill ${j.state}">${label}</span></a></li>`;
}

async function start(source) {
  if (!source) return;
  try {
    const job = await api("/api/jobs", { method: "POST", body: { source, count: prefs.count, preset: prefs.preset, cold_open: prefs.cold_open } });
    location.hash = `#/job/${job.id}`;
  } catch (e) { toast(e.message); }
}
async function startUpload(file) {
  toast(`Uploading ${file.name}`);
  const fd = new FormData(); fd.append("file", file);
  try { const r = await api("/api/upload", { method: "POST", body: fd, headers: {} }); await start(r.path); }
  catch (e) { toast(e.message); }
}

/* ---------------- job ---------------- */
const STAGES = [["info", "Read video"], ["captions", "Rough transcript"], ["scout", "Scout moments"], ["judge", "Judge and rank"], ["cut", "Cut and render"]];
const CLIP_STATES = { queued: "Waiting its turn", fetching: "Downloading this part in 1080p", transcribing: "Timing every word", framing: "Finding the speaker", rendering: "Rendering" };
let lastJob = null;

async function jobView(id) {
  const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
  lastJob = job;
  main.innerHTML = `
    <div class="jobhead"><div>
      <h1>${esc(job.title)}</h1>
      <div class="meta">${esc(job.channel || "")}${job.duration ? `${job.channel ? ", " : ""}${fmt(job.duration)} long` : ""}${signalText(job)}</div>
    </div>
    <div class="row">${job.state === "error" ? `<button class="btn" id="retry">Try again</button>` : ""}
      <button class="btn ghost" id="del">Delete</button></div></div>
    <section class="map" aria-label="Replay map">${mapSVG(job)}</section>
    <ol class="steps" id="steps">${stepsHTML(job)}</ol>
    ${job.state === "error" ? `<div class="banner"><b>This run stopped.</b><pre>${esc(job.error)}</pre>Fix the issue above, then try again. Finished stages are kept.</div>` : ""}
    <section class="clips" id="clips" aria-label="Clips">${job.clips.length ? job.clips.map((c) => clipHTML(job, c)).join("") : waitingHTML(job)}</section>`;
  wireJob(job);
  const busy = () => lastJob.state === "running" || lastJob.state === "queued" || lastJob.clips.some((c) => ["queued", "fetching", "transcribing", "framing", "rendering"].includes(c.state) || c.publishing);
  if (busy()) poll = setInterval(() => refreshJob(id), 1500);
}

function signalText(job) {
  const s = job.signals; if (!s) return "";
  const bits = [];
  if (s.heatmap) bits.push("replay data found");
  if (s.comments) bits.push(`${s.comments} timestamped comments`);
  return bits.length ? `, ${bits.join(" and ")}` : ", no replay data on this video";
}

function waitingHTML(job) {
  if (job.state === "error") return "";
  return `<p class="empty">Claude is reading the video. Clips land here as soon as they're judged.</p>`;
}

function stepsHTML(job) {
  return STAGES.map(([k, label]) => {
    const st = (job.stages || {})[k] || {};
    const t = st.status === "done" ? `${st.secs ?? 0}s` : st.status === "running" ? "working" : st.status === "error" ? "stopped" : "";
    return `<li class="${st.status || ""}"><b>${label}</b><span>${t}</span></li>`;
  }).join("");
}

function mapSVG(job) {
  const D = job.duration || Math.max(1, ...job.candidates.map((c) => c.end), ...job.clips.map((c) => c.end || 0));
  const W = 1000, H = 150, top = 26;
  const x = (t) => (t / D) * W;
  const hm = job.heatmap || [];
  let curve = "";
  if (hm.length) {
    const pts = hm.map((h) => [x((h.start_time + h.end_time) / 2), top + (1 - h.value) * (H - top - 20)]);
    const line = "M" + pts.map((p) => p.map((v) => v.toFixed(1)).join(",")).join(" L");
    curve = `<path class="curve" d="${line} L${W},${H - 20} L0,${H - 20} Z"/><path class="curve-line" d="${line}"/>`;
  }
  const cands = job.candidates.map((c) => `<rect class="cand" x="${x(c.start)}" y="${top}" width="${Math.max(2, x(c.end) - x(c.start))}" height="${H - top - 20}" rx="3"><title>${esc(c.hook_title || "")} ${Math.round(c.score || 0)}</title></rect>`).join("");
  const picks = job.clips.map((c) => `<rect class="pick" style="--i:${c.rank}" data-jump="${c.id}" x="${x(c.start)}" y="${top - 8}" width="${Math.max(4, x(c.end) - x(c.start))}" height="${H - top - 12}" rx="4"><title>${esc(c.hook_title)}</title></rect>`).join("");
  const labels = job.clips.map((c) => `<span class="map-rank" style="left:${(c.start / D) * 100}%;--i:${c.rank}">${c.rank}</span>`).join("");
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => `<span>${fmt(f * D)}</span>`).join("");
  const note = hm.length ? "The glowing curve is YouTube's Most replayed data. Blue windows are your clips, dashed ones were judged and dropped." : "This video has no replay curve yet, so ranking leans on Claude's judgment and comments. Blue windows are your clips.";
  return `<div class="map-head"><h2>Replay map</h2><p>${note}</p></div>
  <div class="map-plot">${labels}<span class="playhead" aria-hidden="true"></span><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Video timeline with replay intensity and selected clips">
    <defs><linearGradient id="heatfill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#4FD1FF" stop-opacity=".45"/><stop offset="1" stop-color="#2F6BFF" stop-opacity="0"/></linearGradient><linearGradient id="heatline" x1="0" x2="1" y1="0" y2="0"><stop offset="0" stop-color="#2F6BFF"/><stop offset=".5" stop-color="#4FD1FF"/><stop offset="1" stop-color="#2F6BFF"/></linearGradient></defs>
    <line class="base" x1="0" x2="${W}" y1="${H - 20}" y2="${H - 20}" vector-effect="non-scaling-stroke"/>${curve}${cands}${picks}</svg></div><div class="map-axis">${ticks}</div>`;
}

function bar(label, v, max, cls = "") {
  if (v === null || v === undefined) return "";
  const pct = Math.round((v / max) * 100);
  return `<div class="bar ${cls}" style="--i:${bar.i = (bar.i || 0) + 1}"><div>${label}<b>${max === 10 ? v : Math.round(v)}</b></div><i style="--w:${pct}%;--c:${pct >= 70 ? "var(--good)" : pct >= 45 ? "var(--champagne)" : "var(--bad)"}"></i></div>`;
}

function clipHTML(job, c) {
  const b = c.breakdown || {};
  bar.i = 0;
  const ready = c.state === "ready";
  const media = ready
    ? `<video controls playsinline preload="metadata" poster="/media/${job.id}/${c.cover}?v=${c.rendered}" src="/media/${job.id}/${c.video}?v=${c.rendered}"></video>`
    : c.state === "error" ? `<div class="state"><b>Render failed</b><span class="small">${esc(c.error || "")}</span></div>`
    : `<div class="state"><div class="spinner"></div><span>${CLIP_STATES[c.state] || "Working"}</span></div>`;
  return `<article class="clip" style="--i:${c.rank - 1}" id="clip-${c.id}" aria-labelledby="h-${c.id}">
    <div class="player">${media}</div>
    <div>
      <div class="clip-top">
        <div class="score" data-score="${Math.round(c.score)}" aria-label="Score ${Math.round(c.score)} out of 100">${Math.round(c.score)}<small>score</small></div>
        <div><div class="rank" id="h-${c.id}">Clip ${c.rank}, ${fmt(c.start)} to ${fmt(c.end)}${c.duration ? `, ${Math.round(c.duration)}s after cuts` : ""}</div>
        ${c.approved ? `<span class="pill done">Approved</span>` : ""} ${c.posted ? `<span class="pill done">Posted</span>` : ""}</div>
      </div>
      <div class="bars">
        ${bar("Hook", b.hook, 10)}${bar("Flow", b.flow, 10)}${bar("Value", b.value, 10)}${bar("Shareable", b.share, 10)}${bar("Loop", b.loop, 10)}
        ${bar("Replayed", b.heat, 100, "audience")}${bar("Comment buzz", b.crowd, 100, "audience")}
      </div>
      <p class="why">${esc(c.why)}</p>
      <div class="field hookfield"><label for="hook-${c.id}">On-screen hook</label><input id="hook-${c.id}" type="text" value="${esc(c.hook_title)}" data-field="hook_title" data-clip="${c.id}"></div>
      <div class="field"><label for="cap-${c.id}">Instagram caption</label><textarea id="cap-${c.id}" data-field="ig_caption" data-clip="${c.id}">${esc(c.ig_caption)}</textarea>
        <small class="tags">${esc((c.hashtags || []).join(" "))}</small></div>
      <div class="row">
        <button class="btn ${c.approved ? "good" : "primary"}" data-act="approve" data-clip="${c.id}">${c.approved ? "Approved" : "Approve"}</button>
        <button class="btn" data-act="edit" data-clip="${c.id}" ${ready ? "" : "disabled"}>Edit words</button>
        <button class="btn" data-act="render" data-clip="${c.id}" ${ready || c.state === "error" ? "" : "disabled"}>Re-render</button>
        <button class="btn" data-act="queue" data-clip="${c.id}" ${ready ? "" : "disabled"}>Add to posting queue</button>
        <button class="btn" data-act="copy" data-clip="${c.id}">Copy caption</button>
        ${ready ? `<a class="btn ghost" href="/media/${job.id}/${c.video}" download="${esc(job.id)}-${c.id}.mp4">Download</a>` : ""}
      </div>
      ${c.publishing && !c.posted && !c.publish_error ? `<p class="note">Posting to Instagram…</p>` : ""}
      ${c.publish_error ? `<p class="note err">${esc(c.publish_error)}</p>` : ""}
    </div></article>`;
}

function wireJob(job) {
  $("#retry")?.addEventListener("click", async () => { await api(`/api/jobs/${job.id}/retry`, { method: "POST" }); route(); });
  $("#del").onclick = async () => { if (confirm("Delete this video and all its clips?")) { await api(`/api/jobs/${job.id}`, { method: "DELETE" }); location.hash = "#/"; } };
  main.querySelectorAll("[data-jump]").forEach((r) => r.addEventListener("click", () => {
    const el = $(`#clip-${r.dataset.jump}`); el?.scrollIntoView({ behavior: "smooth" }); el?.classList.add("flash"); setTimeout(() => el?.classList.remove("flash"), 1200);
  }));
  wireClips(job);
}

function wireClips(job) {
  main.querySelectorAll("[data-field]").forEach((inp) => inp.addEventListener("change", async () => {
    await api(`/api/jobs/${job.id}/clips/${inp.dataset.clip}`, { method: "PATCH", body: { [inp.dataset.field]: inp.value } });
    toast(inp.dataset.field === "hook_title" ? "Hook saved. Re-render to burn it in." : "Caption saved");
  }));
  main.querySelectorAll("[data-act]").forEach((btn) => btn.addEventListener("click", async () => {
    const cid = btn.dataset.clip, c = lastJob.clips.find((x) => x.id === cid);
    try {
      if (btn.dataset.act === "approve") { await api(`/api/jobs/${job.id}/clips/${cid}`, { method: "PATCH", body: { approved: !c.approved } }); }
      if (btn.dataset.act === "render") { await api(`/api/jobs/${job.id}/clips/${cid}/render`, { method: "POST" }); toast("Re-rendering"); }
      if (btn.dataset.act === "queue") { await api("/api/queue", { method: "POST", body: { job: job.id, clip: cid } }); toast("Added to posting queue"); }
      if (btn.dataset.act === "copy") { await navigator.clipboard.writeText(`${c.ig_caption}\n\n${(c.hashtags || []).join(" ")}`); toast("Caption copied"); return; }
      if (btn.dataset.act === "edit") { return openEditor(job.id, c); }
      await refreshJob(job.id, true);
    } catch (e) { toast(e.message); }
  }));
}

async function refreshJob(id, force = false) {
  const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
  const prev = lastJob; lastJob = job;
  $("#steps").innerHTML = stepsHTML(job);
  const changedClips = force || JSON.stringify(job.clips.map((c) => [c.id, c.state, c.rendered, c.approved, c.posted, c.publish_error, c.publishing])) !== JSON.stringify(prev.clips.map((c) => [c.id, c.state, c.rendered, c.approved, c.posted, c.publish_error, c.publishing]));
  if (prev.state !== job.state || prev.clips.length !== job.clips.length) { clearInterval(poll); return jobView(id); }
  if (changedClips) {
    const active = document.activeElement?.dataset?.field ? document.activeElement.id : null;
    for (const c of job.clips) {
      const old = prev.clips.find((x) => x.id === c.id);
      if (force || !old || old.state !== c.state || old.rendered !== c.rendered || old.approved !== c.approved || old.posted !== c.posted || old.publish_error !== c.publish_error || old.publishing !== c.publishing) {
        const el = $(`#clip-${c.id}`); if (el) el.outerHTML = clipHTML(job, c);
      }
    }
    wireClips(job);
    countUp();
    if (active) $(`#${active}`)?.focus();
  }
  const busy = job.state === "running" || job.state === "queued" || job.clips.some((c) => ["queued", "fetching", "transcribing", "framing", "rendering"].includes(c.state) || (c.publishing && !c.posted && !c.publish_error));
  if (!busy) clearInterval(poll); else if (!poll || force) { clearInterval(poll); poll = setInterval(() => refreshJob(id), 1500); }
}

/* ---------------- word editor ---------------- */
async function openEditor(jobId, c) {
  const dlg = $("#editor");
  const data = await api(`/api/jobs/${jobId}/clips/${c.id}/words`);
  const removed = new Set(data.removed);
  const ov = c.overrides || {};
  dlg.innerHTML = `<div class="dlg-head"><h2 id="editor-title">Edit words</h2><button class="btn ghost" data-close aria-label="Close">Close</button></div>
    <p class="muted small" style="padding:14px 22px 0;margin:0">Click any word to cut it. Cuts become jump cuts with a punch-in, so trim freely.</p>
    <div class="words">${data.words.map((w) => `<button type="button" data-i="${w.i}" class="${removed.has(w.i) ? "off" : ""}" aria-pressed="${removed.has(w.i)}">${esc(w.w)}</button>`).join(" ")}</div>
    <div class="dlg-foot"><div class="row">
      <label class="switch"><input type="checkbox" id="ed-cold" ${ov.cold_open !== false ? "checked" : ""}> Cold open teaser</label>
      <select id="ed-preset" aria-label="Caption style">${Object.entries(META.presets).map(([k, p]) => `<option value="${k}" ${(ov.preset || "") === k ? "selected" : ""}>${esc(p.label)} captions</option>`).join("")}<option value="" ${!ov.preset ? "selected" : ""}>Default captions</option></select></div>
      <button class="btn primary" id="ed-save">Save and re-render</button></div>`;
  dlg.querySelectorAll("[data-i]").forEach((b) => b.onclick = () => { const i = +b.dataset.i; removed.has(i) ? removed.delete(i) : removed.add(i); b.classList.toggle("off"); b.setAttribute("aria-pressed", removed.has(i)); });
  dlg.querySelector("[data-close]").onclick = () => dlg.close();
  $("#ed-save").onclick = async () => {
    await api(`/api/jobs/${jobId}/clips/${c.id}`, { method: "PATCH", body: { overrides: { removed: [...removed], cold_open: $("#ed-cold").checked, preset: $("#ed-preset").value || null } } });
    await api(`/api/jobs/${jobId}/clips/${c.id}/render`, { method: "POST" });
    dlg.close(); toast("Re-rendering with your edits"); refreshJob(jobId, true);
  };
  dlg.showModal();
}

/* ---------------- posting ---------------- */
async function queueView() {
  const { queue, posted } = await api("/api/queue");
  main.innerHTML = `<h1 style="font-size:clamp(36px,5vw,64px)">Posting</h1>
  <p class="muted" style="max-width:640px">Approve clips, line them up here, and let your scheduler post one at a time with <code>hookcut post-next</code>. Posting 1 to 2 Reels a day at the same times beats dumping five at once.</p>
  <div class="grid2">
    <section class="card"><h2>Up next</h2>
      ${queue.length ? queue.map((q, i) => `<div class="qitem"><img src="/media/${q.job}/${q.cover}" alt=""><div><b>${esc(q.hook_title)}</b><br><span class="muted small">Score ${Math.round(q.score || 0)}${i === 0 ? ", posts next" : ""}</span></div>
        <div class="row"><button class="btn" data-post="${q.job}|${q.clip}">Post now</button><button class="btn ghost" data-rm="${q.job}|${q.clip}" aria-label="Remove from queue">Remove</button></div></div>`).join("") : `<p class="muted">Queue is empty. Add clips from any video.</p>`}
    </section>
    <section class="card"><h2>Posted</h2>
      ${posted.length ? posted.map((p) => `<div class="qitem"><span></span><div><b>${esc(p.hook_title)}</b><br><span class="muted small">${ago(p.posted)}${p.trial ? ", trial reel" : ""}${p.stats ? `, ${p.stats.views ?? "?"} views, ${p.stats.shares ?? 0} shares` : ""}</span></div><span></span></div>`).join("") : `<p class="muted">Nothing posted yet.</p>`}
      <div class="row" style="margin-top:16px"><button class="btn" id="sync">Pull Instagram stats</button></div>
      <p class="muted small">Stats feed back into the judge, so it learns which hooks work on your account.</p>
    </section></div>`;
  main.querySelectorAll("[data-rm]").forEach((b) => b.onclick = async () => { const [j, c] = b.dataset.rm.split("|"); await api(`/api/queue/${j}/${c}`, { method: "DELETE" }); queueView(); });
  main.querySelectorAll("[data-post]").forEach((b) => b.onclick = async () => { const [j, c] = b.dataset.post.split("|"); b.disabled = true; await api(`/api/jobs/${j}/clips/${c}/publish`, { method: "POST", body: {} }); await api(`/api/queue/${j}/${c}`, { method: "DELETE" }); toast("Posting. Check the clip page for status."); queueView(); });
  $("#sync").onclick = async () => { try { await api("/api/stats/sync", { method: "POST" }); toast("Stats updated"); queueView(); } catch (e) { toast(e.message); } };
}

/* ---------------- settings ---------------- */
async function settingsView() {
  const [s, checks] = await Promise.all([api("/api/settings"), api("/api/doctor")]);
  const v = (path) => path.split(".").reduce((o, k) => o?.[k], s) ?? "";
  main.innerHTML = `<h1 style="font-size:clamp(36px,5vw,64px)">Settings</h1>
  <div class="grid2">
    <section class="card"><h2>Setup check</h2><ul class="checks">${checks.map((c) => `<li><span class="dot ${c.ok ? "" : "no"}"></span><span><b>${esc(c.name)}</b> <span class="muted">${esc(c.detail)}</span>${c.fix ? `<small>${esc(c.fix)}</small>` : ""}</span></li>`).join("")}</ul></section>
    <form class="card" id="sf"><h2>Brand and edit</h2>
      <div class="field"><label for="wm">Watermark</label><input id="wm" type="text" data-k="style.watermark" value="${esc(v("style.watermark"))}" placeholder="@yourhandle"><small>Shown small at the bottom of every clip. Leave empty for none.</small></div>
      <div class="field"><label for="pr">Default captions</label><select id="pr" data-k="style.preset">${Object.entries(META.presets).map(([k, p]) => `<option value="${k}" ${v("style.preset") === k ? "selected" : ""}>${esc(p.label)}</option>`).join("")}</select></div>
      <div class="field"><label for="mn">Clip length (seconds)</label><div class="row"><input id="mn" type="number" min="10" max="85" data-k="clips.min_len" value="${v("clips.min_len")}" style="width:96px"> to <input type="number" min="15" max="90" data-k="clips.max_len" value="${v("clips.max_len")}" aria-label="Maximum length" style="width:96px"></div></div>
      <label class="switch field"><input type="checkbox" data-k="style.progress_bar" ${v("style.progress_bar") ? "checked" : ""}> Progress bar</label>
      <label class="switch field"><input type="checkbox" data-k="edit.remove_fillers" ${v("edit.remove_fillers") ? "checked" : ""}> Remove um and uh</label>
      <div class="field"><label for="llm">Clip judge</label><select id="llm" data-k="llm.backend"><option value="claude-code" ${v("llm.backend") === "claude-code" ? "selected" : ""}>Claude Code (your subscription)</option><option value="api" ${v("llm.backend") === "api" ? "selected" : ""}>Anthropic API key</option></select></div>
      <button class="btn primary">Save settings</button></form>
    <form class="card" id="ig"><h2>Instagram</h2>
      <p class="muted small" style="margin-top:-6px">Needs an Instagram professional account and a Meta app with publishing permission. The README walks through it in about 15 minutes.</p>
      <div class="field"><label for="igid">Instagram user ID</label><input id="igid" type="text" data-k="publish.ig_user_id" value="${esc(v("publish.ig_user_id"))}"></div>
      <div class="field"><label for="tok">Access token</label><input id="tok" type="password" data-k="publish.access_token" value="${esc(v("publish.access_token"))}" autocomplete="off"></div>
      <div class="field"><label for="mode">Login type</label><select id="mode" data-k="publish.mode"><option value="facebook" ${v("publish.mode") === "facebook" ? "selected" : ""}>Facebook Login (uploads from this computer)</option><option value="instagram" ${v("publish.mode") === "instagram" ? "selected" : ""}>Instagram Login (needs a public URL)</option></select></div>
      <div class="field"><label for="pub">Public URL for this studio</label><input id="pub" type="url" data-k="publish.public_base_url" value="${esc(v("publish.public_base_url"))}" placeholder="https://your-tunnel.example.com"><small>Only for Instagram Login. A free tunnel to this studio works.</small></div>
      <label class="switch field"><input type="checkbox" data-k="publish.trial" ${v("publish.trial") ? "checked" : ""}> Post as trial reels first (shown to non-followers, auto-shared to followers if they perform)</label>
      <button class="btn primary">Save Instagram</button></form>
  </div>`;
  const save = async (form) => {
    const body = {};
    form.querySelectorAll("[data-k]").forEach((el) => {
      const keys = el.dataset.k.split("."); let o = body;
      keys.slice(0, -1).forEach((k) => o = o[k] = o[k] || {});
      o[keys.at(-1)] = el.type === "checkbox" ? el.checked : el.type === "number" ? +el.value : el.value;
    });
    await api("/api/settings", { method: "PUT", body }); toast("Saved");
  };
  $("#sf").onsubmit = (e) => { e.preventDefault(); save(e.target); };
  $("#ig").onsubmit = (e) => { e.preventDefault(); save(e.target); };
}

api("/api/meta").then((m) => { META = m; route(); }).catch(() => route());
