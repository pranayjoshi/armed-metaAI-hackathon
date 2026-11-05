// Stage extras (docs/UI.md §10.3): speed readout, twin rehearsal picture-in-picture, the investigation feed and the
// architecture frame. Everything is read from the /api/live snapshot or a runtime event; a missing field renders "—"
// or an honest empty state, never a made-up value.
//
// Optional fields read (all absent-safe):
//   state.arm.speed_frac                      0..1, the executor's commanded speed fraction
//   state.learning.proposal.rehearsal | .rule.rehearsal | proposal itself:
//       { old_route, new_route, progress?, state?, note? }  a route is a polyline [[x,y],...] in table 0..1,
//       {points:[...]}, or the name of a route in /api/rules `routes`
//   event incident.tool { tool|name|model, result|line|summary|detail|text, ok?, ticks?: [bool,bool] | checks?: [{ok}] }
//   state.counts { spark_calls, wonder, sam_scans, transcribed_min }   else counted from events since page load
//   event speech.utterance { audio_s | duration_s }                    minutes transcribed, when the runtime gives it
import { DIM } from "./kin.js";

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const MUSE = "muse", ARMED = "armed";

export const speedText = (v) => (Number.isFinite(v) ? (v <= 0 ? "0" : `${Math.round(Math.min(1, v) * 100)}%`) : "—");

const CHIPS = [
  [/wonder/i, "Muse Wonder 1.0", MUSE],
  [/sam/i, "SAM 3.1", MUSE],
  [/measure/i, "Armed measure", ARMED],
  [/check|verif/i, "Armed checker", ARMED],
];
function chipFor(e) {
  const raw = String(e.tool ?? e.name ?? e.model ?? "");
  for (const [re, label, hue] of CHIPS) if (re.test(raw)) return { label, hue, checker: label === "Armed checker" };
  return { label: raw || "tool", hue: MUSE, checker: false };
}
const lineOf = (e) => e.result ?? e.line ?? e.summary ?? e.detail ?? e.text ?? "";
function ticksOf(e) {
  const t = Array.isArray(e.ticks) ? e.ticks : Array.isArray(e.checks) ? e.checks.map((c) => (c && typeof c === "object" ? c.ok : c)) : null;
  return [0, 1].map((i) => (t && typeof t[i] === "boolean" ? t[i] : null));
}

/** Resolve a route (name | polyline | {points}) to [[x,y]...] in table 0..1, or null. */
function routeOf(r, routes) {
  if (typeof r === "string") r = routes && !Array.isArray(routes) ? routes[r] : null;
  if (r && !Array.isArray(r) && Array.isArray(r.points)) r = r.points;
  return Array.isArray(r) && r.length > 1 && r.every((p) => Array.isArray(p) && p.length >= 2 && p.every(Number.isFinite)) ? r : null;
}

// the nine-step chain (docs/ARMED_IDEA.md)
const CHAIN = [
  { n: 1, chips: [["Muse Spark 1.3", MUSE]], why: "image in, tools out, under 2 s", key: "spark" },
  { n: 2, chips: [["EMG decoder", ARMED]], why: "signal processing, no weights" },
  { n: 3, chips: [["Armed gate", ARMED]], why: "a pure function; taught poses only" },
  { n: 4, chips: [["Armed stop controller", ARMED]], why: "no model, no network" },
  { n: 5, chips: [["Muse Wonder 1.0", MUSE]], why: "video in, parallel tools, the day in memory", key: "wonder" },
  { n: 6, chips: [["SAM 3.1", MUSE], ["Armed measure", ARMED]], why: "phrase to masks and tracks; the ruler", key: "sam" },
  { n: 7, chips: [["Armed checker", ARMED]], why: "removes options only" },
  { n: 8, chips: [["Armed gate", ARMED]], why: "the person approves; the next run applies it" },
  { n: 9, chips: [["Muse Voice Transcribe 1.0", MUSE]], why: "who said it", key: "voice" },
];

export class Extras {
  constructor(stg, ctx, { getRoutes }) {
    this.stg = stg; this.ctx = ctx; this.getRoutes = getRoutes;
    this.h = {};
    const view = stg.querySelector(".stg-view");
    // behind tools/tag/freeze in z-order (they come later in the DOM at the same or higher z)
    view.insertAdjacentHTML("afterbegin", `<section class="arch" data-x="arch" hidden aria-label="Architecture: what ran today"></section>`);
    view.insertAdjacentHTML("beforeend", `
      <div class="rh" data-x="rh" hidden aria-label="Twin rehearsal"></div>
      <ol class="inv" data-x="inv" hidden aria-label="Investigation feed"></ol>`);
    this.x = {};
    stg.querySelectorAll("[data-x]").forEach((n) => (this.x[n.dataset.x] = n));
    // speed readout beside the muscle meter, and a copy in film mode (the side panel is hidden there)
    stg.querySelector(".mu .panel-head")?.insertAdjacentHTML("beforeend", `<span class="spd" data-x="spd" title="Executor's commanded speed fraction (state.arm.speed_frac)"><em>speed</em><b class="num">—</b></span>`);
    stg.querySelector(".fm-tc")?.insertAdjacentHTML("beforeend", `<div class="spd spd-film" data-x="spdf"><em>arm speed</em><b class="num">—</b></div>`);
    this.x.spd = stg.querySelector('.mu [data-x="spd"]');
    this.x.spdf = stg.querySelector('[data-x="spdf"]');
    this.arch = false;
    this.cache = { n: -1, lastSeq: -1 };
  }

  put(key, el, html) {
    if (this.h[key] === html) return;
    this.h[key] = html;
    el.innerHTML = html;
  }

  setFrame(name) {
    this.arch = name === "architecture";
    this.stg.dataset.frame = this.arch ? "architecture" : "";
    this.x.arch.hidden = !this.arch;
  }

  update(s, filmOn) {
    this.speed(s);
    this.rehearsal(s);
    this.feed(s);
    if (this.arch) this.architecture(s, filmOn);
  }

  speed(s) {
    const v = s.arm?.speed_frac;
    const txt = speedText(typeof v === "number" ? v : NaN);
    for (const el of [this.x.spd, this.x.spdf]) {
      if (!el) continue;
      const b = el.querySelector("b");
      if (b.textContent !== txt) b.textContent = txt;
      el.classList.toggle("none", txt === "—");
      el.classList.toggle("zero", txt === "0");
    }
  }

  // ── twin rehearsal ────────────────────────────────────────────────────────
  rehearsal(s) {
    const el = this.x.rh;
    const p = s.learning?.proposal;
    if (!p) { el.hidden = true; this.h.rh = ""; return; }
    el.hidden = false;
    const rh = p.rehearsal ?? p.rule?.rehearsal ?? p;
    const routes = this.getRoutes();
    const oldR = routeOf(rh.old_route ?? p.rule?.old_route, routes);
    const newR = routeOf(rh.new_route ?? p.rule?.new_route, routes);
    const prog = Number.isFinite(rh.progress) ? Math.max(0, Math.min(1, rh.progress)) : null;
    const objs = (s.vision?.objects || []).filter((o) => o.table_xy);
    const fmt = (r) => r.map(([x, y]) => `${(x * 100).toFixed(1)},${(y * 100).toFixed(1)}`).join(" ");
    let svg = '<rect class="rh-mat" x="0" y="0" width="100" height="100" rx="2"/>';
    for (const o of objs) svg += `<circle class="rh-obj" cx="${o.table_xy[0] * 100}" cy="${o.table_xy[1] * 100}" r="2.6"/><text x="${o.table_xy[0] * 100}" y="${o.table_xy[1] * 100 + 7}" text-anchor="middle">${esc(o.name)}</text>`;
    svg += `<circle class="rh-base" cx="50" cy="${DIM.BASE_Y * 100}" r="2.4"/>`;
    if (oldR) svg += `<polyline class="rh-old" points="${fmt(oldR)}"/>`;
    if (newR) {
      svg += `<polyline class="rh-new" points="${fmt(newR)}"/>`;
      if (prog != null) {
        const segs = newR.slice(1).map((q, i) => Math.hypot(q[0] - newR[i][0], q[1] - newR[i][1]));
        let d = prog * segs.reduce((a, b) => a + b, 0), i = 0;
        while (i < segs.length - 1 && d > segs[i]) d -= segs[i++];
        const f = segs[i] ? d / segs[i] : 0, a = newR[i], b = newR[i + 1];
        svg += `<circle class="rh-dot" cx="${(a[0] + (b[0] - a[0]) * f) * 100}" cy="${(a[1] + (b[1] - a[1]) * f) * 100}" r="2.4"/>`;
      }
    }
    const avoid = p.rule?.avoid;
    const arm = s.arm?.state;
    const note = !oldR && !newR ? "No route in this proposal: the runtime sent a rule but no old or new route to rehearse." : !newR ? "No new route yet." : "";
    const html = `
      <div class="rh-h"><span class="chip muse"><i></i>rehearsal</span><span class="rh-sim">TWIN · simulated</span></div>
      <svg viewBox="-3 -3 106 106" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Old route in red, new route in green">${svg}</svg>
      <div class="rh-k"><span class="o"><i></i>old route${oldR ? "" : " —"}</span><span class="n"><i></i>new route${newR ? "" : " —"}</span></div>
      ${avoid ? `<div class="rh-m">avoid · ${esc(avoid)}${p.rule?.response ? ` · ${esc(String(p.rule.response).replaceAll("_", " "))}` : ""}</div>` : ""}
      ${rh.state ? `<div class="rh-m">${esc(rh.state)}${rh.note ? ` · ${esc(rh.note)}` : ""}</div>` : ""}
      ${note ? `<div class="rh-e">${esc(note)}</div>` : ""}
      <div class="rh-m">real arm: ${esc(arm || "—")} · main view not driven by this</div>`;
    this.put("rh", el, html);
  }

  // ── investigation feed ────────────────────────────────────────────────────
  feed(s) {
    const el = this.x.inv;
    const hist = this.ctx.store.history;
    let from = 0;
    for (let i = hist.length - 1; i >= 0; i--) if (hist[i].kind === "incident.triggered") { from = i; break; }
    const rows = hist.slice(from).filter((e) => e.kind === "incident.tool");
    const last = rows.at(-1);
    const stale = last && Number.isFinite(s.t) && s.t - last.t > 45 && !s.learning?.proposal;
    if (!rows.length || stale) { el.hidden = true; this.h.inv = ""; return; }
    el.hidden = false;
    const html = rows.slice(-8).map((e) => {
      const c = chipFor(e), tk = c.checker ? ticksOf(e) : null;
      const ticks = tk ? `<span class="inv-t">${tk.map((v) => `<b class="${v === true ? "y" : v === false ? "n" : ""}">${v === true ? "✓" : v === false ? "✗" : "·"}</b>`).join("")}</span>` : "";
      const bad = e.ok === false;
      return `<li class="${bad ? "bad" : ""}"><span class="chip ${c.hue === MUSE ? "muse" : "armed"}"><i></i>${esc(c.label)}</span><span class="inv-l">${esc(lineOf(e)) || "—"}</span>${ticks}</li>`;
    }).join("");
    this.put("inv", el, html);
  }

  // ── architecture frame ────────────────────────────────────────────────────
  counts() {
    const s = this.ctx.store.state, hist = this.ctx.store.history;
    if (this.cache.n !== hist.length || this.cache.lastSeq !== hist.at(-1)?.seq) {
      const c = { spark: 0, wonder: 0, sam: 0, voice: null };
      for (const e of hist) {
        if (e.kind === "compiler.llm") c.spark++;
        else if (e.kind === "incident.tool") { const l = chipFor(e).label; if (l === "Muse Wonder 1.0") c.wonder++; else if (l === "SAM 3.1") c.sam++; }
        else if (/^(vision|scene|context)\.scan/.test(e.kind)) c.sam++;
        else if (e.kind === "speech.utterance") { const sec = e.audio_s ?? e.duration_s; if (Number.isFinite(sec)) c.voice = (c.voice ?? 0) + sec / 60; }
      }
      this.cache = { n: hist.length, lastSeq: hist.at(-1)?.seq, c, lower: hist.length >= 400 };
    }
    const k = s?.counts, c = { ...this.cache.c };
    let src = "events since this page loaded" + (this.cache.lower ? " (lower bounds)" : "");
    if (k && typeof k === "object") {
      src = "state.counts";
      c.spark = k.spark_calls ?? c.spark; c.wonder = k.wonder ?? c.wonder; c.sam = k.sam_scans ?? c.sam; c.voice = k.transcribed_min ?? c.voice;
    }
    return { c, src };
  }

  architecture(s, filmOn) {
    const { c, src } = this.counts();
    const lit = { spark: c.spark > 0, wonder: c.wonder > 0, sam: c.sam > 0, voice: c.voice > 0 };
    const n = (v, d = 0) => (Number.isFinite(v) ? v.toFixed(d) : "—");
    const nodes = CHAIN.map((st) => `
      <li class="${st.key ? (lit[st.key] ? "lit" : "idle") : ""}">
        <span class="arch-n num">${st.n}</span>
        <div class="arch-c">${st.chips.map(([l, h]) => `<span class="chip ${h === MUSE ? "muse" : "armed"}"><i></i>${esc(l)}</span>`).join("")}</div>
        <p>${esc(st.why)}</p>
      </li>`).join("");
    const tags = [];
    if (s.emg && (s.emg.source === "synthetic" || s.emg.hardware === false)) tags.push("SYNTHETIC EMG");
    if (s.arm && s.arm.hardware === false) tags.push("VIRTUAL ARM");
    const html = `
      <div class="arch-h"><span class="fm-k">architecture · nine steps, left to right</span>${tags.map((t) => `<span class="fm-tag">${t}</span>`).join("")}</div>
      <ol class="arch-chain">${nodes}</ol>
      <div class="arch-ct">
        <div><b class="num">${n(c.spark)}</b><span>Spark calls</span></div>
        <div><b class="num">${n(c.wonder)}</b><span>Wonder</span></div>
        <div><b class="num">${n(c.sam)}</b><span>SAM scans</span></div>
        <div><b class="num">${c.voice == null ? "—" : n(c.voice, 1)}</b><span>minutes transcribed</span></div>
        <div class="const"><b class="num">0</b><span>joint values from a model</span><em>by design: a constant, not a measurement</em></div>
      </div>
      <p class="arch-src">counts: ${esc(src)} · nodes with a counter light only when the log shows that model ran${filmOn ? "" : " · film mode off"}</p>`;
    this.put("arch", this.x.arch, html);
  }
}
