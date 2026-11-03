// Incidents: a library of recorded (or simulated) incidents and a player for the selected one.
// Frames, EMG, timeline and the explanation all come from /api/replay/incident/<id>[...].
import { Trace, modelFromEmg } from "./trace.js";
import { esc, getJSON, words, f2, num, fmtT, shortId, provBadge, typeInto, ruleSentence, ruleChips, ICON } from "./util.js";

const JOINT_SHORT = { shoulder_pan: "pan", shoulder_lift: "lift", elbow_flex: "elbow", wrist_flex: "wrist", wrist_roll: "roll", gripper: "grip" };
const SOURCE = {
  canned: ["CANNED · fixed demo text", "canned", "Fixed text from h2m/replay.py or the scripted scenario. No model was called."],
  fallback: ["LOCAL FALLBACK · not a model", "canned", "The runtime's own fallback question. It proposes no rule."],
};
function sourceBadge(exp) {
  if (!exp) return "";
  if (exp.stored === false) return `<span class="src canned" title="This incident has no stored explanation.json. This is fixed demo text.">NO STORED EXPLANATION · fixed demo text</span>`;
  const s = SOURCE[exp.source];
  if (s) return `<span class="src ${s[1]}" title="${esc(s[2])}">${esc(s[0])}</span>`;
  return `<span class="src model" title="Saved with the incident">${esc(words(exp.source, "unknown source"))}</span>`;
}

export class Incidents {
  constructor(pane, hub) {
    this.pane = pane;
    this.hub = hub;
    this.ctx = hub.ctx;
    this.visible = false;
    this.sel = null;
    this.data = null; // incident payload
    this.emg = null;
    this.cam = "overhead";
    this.t = 0;
    this.range = [0, 1];
    this.speed = 1;
    this.playing = false;
    this.stopLoop = null;
    this.frameIdx = -1;
    this.listSig = "";
    this.loadId = 0;
    this.imgCache = new Map();
  }

  async init() {
    this.pane.innerHTML = `
      <div class="inc">
        <aside class="panel inc-list">
          <div class="panel-head"><h3>Library</h3><span class="muted inc-count num"></span></div>
          <div class="inc-cards" role="listbox" aria-label="Incidents"></div>
        </aside>
        <section class="inc-center">
          <div class="panel inc-view">
            <div class="iv-stage">
              <img class="iv-img" alt="" hidden />
              <div class="ov tl"><div class="seg iv-cam" role="group" aria-label="Camera"><button data-c="overhead" aria-pressed="true">Overhead</button><button data-c="wrist" aria-pressed="false">Wrist</button></div></div>
              <div class="ov tr iv-prov"></div>
              <div class="ov bl"><span class="iv-label num"></span></div>
              <div class="iv-empty"></div>
              <div class="iv-flash"></div>
            </div>
            <div class="iv-joints" aria-label="Joint angles at the playhead"></div>
          </div>
          <div class="panel inc-tl">
            <div class="tp-bar">
              <button class="rbtn" data-a="play" aria-label="Play">${ICON.play}</button>
              <div class="seg tp-step" role="group" aria-label="Step frame"><button data-a="prev" aria-label="Previous frame" title="Previous frame (←)">${ICON.prev}</button><button data-a="next" aria-label="Next frame" title="Next frame (→)">${ICON.next}</button></div>
              <div class="seg tp-speed" role="group" aria-label="Speed"><button data-s="0.25" aria-pressed="false">0.25×</button><button data-s="1" aria-pressed="true">1×</button></div>
              <div class="tp-read"><div class="rd-l1"><span class="num rd-t">—</span><span class="rd-s muted"></span></div><span class="rd-v muted"></span></div>
              <div class="tp-lat"><span class="num big">—</span><small>ms onset → freeze</small></div>
            </div>
            <div class="inc-trace"></div>
          </div>
        </section>
        <aside class="inc-side">
          <div class="panel inc-exp"></div>
          <div class="panel inc-prop"></div>
          <div class="panel inc-events"></div>
        </aside>
      </div>`;
    const q = (s) => this.pane.querySelector(s);
    this.q = q;
    this.listEl = q(".inc-cards");
    this.img = q(".iv-img");
    this.imgEmpty = q(".iv-empty");
    this.labelEl = q(".iv-label");
    this.provEl = q(".iv-prov");
    this.jointsEl = q(".iv-joints");
    this.btnPlay = q(".rbtn");
    this.rdT = q(".rd-t");
    this.rdV = q(".rd-v");
    this.rdS = q(".rd-s");
    this.latEl = q(".tp-lat .big");
    this.trace = new Trace(q(".inc-trace"), { label: "Incident EMG trace", onScrub: (t) => (this.pause(), this.seek(t)) });
    this.expEl = q(".inc-exp");
    this.propEl = q(".inc-prop");
    this.evEl = q(".inc-events");

    this.listEl.addEventListener("click", (e) => {
      const c = e.target.closest("[data-id]");
      if (c) this.select(c.dataset.id, true);
    });
    this.listEl.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const cards = [...this.listEl.querySelectorAll("[data-id]")];
        const i = cards.findIndex((c) => c.dataset.id === this.sel);
        const n = cards[Math.max(0, Math.min(cards.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))];
        if (n) (n.focus(), this.select(n.dataset.id, true));
      }
    });
    q(".iv-cam").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-c]");
      if (b && !b.disabled) this.setCam(b.dataset.c);
    });
    this.btnPlay.onclick = () => this.toggle();
    q(".tp-step").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-a]");
      if (b) this.step(b.dataset.a === "next" ? 1 : -1);
    });
    q(".tp-speed").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-s]");
      if (!b) return;
      this.speed = Number(b.dataset.s);
      q(".tp-speed").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      if (!this.playing) this.play();
    });
    this.evEl.addEventListener("click", (e) => {
      const li = e.target.closest("[data-t]");
      if (li && li.dataset.t !== "") (this.pause(), this.seek(Number(li.dataset.t)));
    });
    this.img.addEventListener("error", () => this.showImgError());
    this.img.addEventListener("load", () => {
      this.imgErr = false;
      this.img.hidden = false;
      this.imgEmpty.hidden = true;
    });
    this.onKey = (e) => {
      if (!this.visible || this.hub.tab !== "incidents") return;
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "ArrowRight") (e.preventDefault(), this.pause(), this.step(1));
      else if (e.key === "ArrowLeft") (e.preventDefault(), this.pause(), this.step(-1));
      else if (e.key === "Home") (e.preventDefault(), this.pause(), this.seek(this.range[0]));
      else if (e.key === "End") (e.preventDefault(), this.pause(), this.seek(this.range[1]));
      else if (e.key === "p" || e.key === "P") this.toggle();
    };
    addEventListener("keydown", this.onKey);
    this.renderList();
  }

  show() {
    this.visible = true;
    this.renderList();
    if (!this.sel) {
      const first = this.hub.lib.incidents.find((i) => !i.error) || this.hub.lib.incidents[0];
      if (first) this.select(first.id, false);
    }
    this.pane.querySelectorAll(".inc-list, .inc-center, .inc-side").forEach((el, i) => {
      el.classList.remove("rise");
      void el.offsetWidth;
      el.style.setProperty("--i", String(i));
      el.classList.add("rise");
    });
    this.trace.draw();
  }
  hide() {
    this.visible = false;
    this.pause();
  }
  dispose() {
    this.pause();
    removeEventListener("keydown", this.onKey);
    this.trace.dispose();
  }

  // ── library ───────────────────────────────────────────────────────────────
  renderList() {
    const items = this.hub.lib.incidents;
    const sig = JSON.stringify([items, this.sel]);
    if (sig === this.listSig) return;
    this.listSig = sig;
    this.q(".inc-count").textContent = String(items.length);
    if (!items.length) {
      this.listEl.innerHTML = `<div class="empty-mini"><b>No incidents recorded in this folder</b><p>Incidents are saved when a brace or an open hand freezes the arm. Try <code>h2m replay data/incidents</code> or <code>h2m demo</code>.</p></div>`;
      return;
    }
    const keep = this.listEl.scrollTop;
    const anim = !this.listAnimated;
    this.listAnimated = true;
    this.listEl.innerHTML = items
      .map((it, i) => {
        if (it.error) return `<div class="icard err" style="--i:${i}"><b class="mono">${esc(it.id)}</b><span class="bad">${esc(it.error)}</span></div>`;
        return `<button class="icard${it.id === this.sel ? " sel" : ""}${anim ? " anim" : ""}" data-id="${esc(it.id)}" role="option" aria-selected="${it.id === this.sel}" style="--i:${Math.min(i, 14)}">
          <div class="ic-top">${provBadge(it.label)}<span class="ic-id num" title="${esc(it.id)}">${esc(shortId(it.id))}</span></div>
          <div class="ic-held">${esc((it.held || "nothing recorded as held").replace(/\s*\(tags:.*\)/, ""))}</div>
          <div class="ic-meta"><span class="chip"><i></i>${esc(words(it.trigger, "trigger unknown"))}</span><span class="chip"><i></i><span class="num">${it.frames ?? 0}</span>&nbsp;frames</span></div>
          <div class="ic-say">${esc(it.say || "")}</div>
          <div class="ic-src">${it.explanation_source === "canned" ? "canned explanation" : esc(words(it.explanation_source, "no explanation"))}</div>
        </button>`;
      })
      .join("");
    this.listEl.scrollTop = keep;
  }

  async select(id, user) {
    if (!id) return;
    this.sel = id;
    this.pause();
    const my = ++this.loadId;
    this.listSig = "";
    this.renderList();
    this.data = null;
    this.emg = null;
    this.frameIdx = -1;
    this.paintLoading();
    const [d, e] = await Promise.all([getJSON(`/api/replay/incident/${encodeURIComponent(id)}`), getJSON(`/api/replay/incident/${encodeURIComponent(id)}/emg`)]);
    if (my !== this.loadId) return;
    if (!d.ok) {
      this.data = null;
      this.paintError(d.error || "unreadable");
      return;
    }
    this.data = d.body;
    this.emg = e.ok ? e.body : { t: [], channels: {}, error: e.error };
    this.prepare();
    this.paintAll();
    if (user && !this.ctx.reduced) this.flashFreeze();
  }

  prepare() {
    const d = this.data;
    const frames = (d.frames || []).map((f, i) => ({ ...f, t: num(f.t) ?? i }));
    this.frames = frames;
    const ts = [...frames.map((f) => f.t), ...(this.emg?.t || []), ...(d.joints?.t || [])].filter(Number.isFinite);
    const lo = ts.length ? Math.min(...ts, 0) : -1;
    const hi = ts.length ? Math.max(...ts, 0) : 1;
    this.hasData = ts.length > 0;
    this.range = this.hasData ? [lo, hi] : [0, 0];
    // start a little before the brace so the first thing seen is the carry
    const onset = this.emg?.onset?.t;
    this.t = Number.isFinite(onset) ? Math.max(lo, onset - 2.2) : lo;
    this.cam = frames.some((f) => f.cams?.overhead) ? "overhead" : frames.some((f) => f.cams?.wrist) ? "wrist" : "overhead";
    // preload so scrubbing is instant
    frames.forEach((f) => Object.values(f.cams || {}).forEach((u) => this.preload(u)));
  }
  preload(url) {
    if (this.imgCache.has(url)) return;
    const im = new Image();
    im.src = url;
    this.imgCache.set(url, im);
    if (this.imgCache.size > 64) this.imgCache.delete(this.imgCache.keys().next().value);
  }

  // ── paint ─────────────────────────────────────────────────────────────────
  paintLoading() {
    this.imgEmpty.hidden = false;
    this.imgEmpty.innerHTML = `<b>Loading the clip…</b>`;
    this.img.hidden = true;
  }
  paintError(msg) {
    this.imgEmpty.hidden = false;
    this.imgEmpty.innerHTML = `<b>This incident can’t be read.</b><span>${esc(msg)}</span>`;
    this.img.hidden = true;
    this.expEl.innerHTML = `<div class="panel-head"><h3>Why did it stop?</h3></div><p class="exp-idle">Nothing to show for an unreadable incident.</p>`;
    this.propEl.innerHTML = "";
    this.evEl.innerHTML = "";
    this.trace.set({ lines: [], empty: "No data." });
  }
  paintAll() {
    const d = this.data;
    this.provEl.innerHTML = provBadge(d.label, { big: true }) + (d.synthetic ? '<span class="chip warn"><i></i>SYNTHETIC SAMPLE</span>' : "");
    this.paintTrace();
    this.paintSide();
    this.q(".iv-cam").querySelectorAll("button").forEach((b) => {
      b.disabled = !this.frames.some((f) => f.cams?.[b.dataset.c]);
    });
    this.setCam(this.cam, true);
    const lat = this.emg?.onset && Number.isFinite(this.emg.onset.t) ? Math.round(-this.emg.onset.t * 1000) : null;
    this.latEl.textContent = lat == null ? "—" : String(lat);
    this.q(".tp-lat").classList.toggle("none", lat == null);
    this.q(".tp-lat small").textContent = this.emg?.onset?.source === "derived" ? "ms onset → freeze (derived)" : "ms onset → freeze";
    this.onTick(true);
  }

  stageBands() {
    const st = this.data.stages || [];
    return st
      .filter((s) => Number.isFinite(s.t))
      .map((s, i, a) => ({ t0: s.t, t1: i + 1 < a.length ? a[i + 1].t : Math.max(0, s.t), label: s.stage }));
  }

  paintTrace() {
    const ticks = this.frames.map((f) => ({ t: f.t, label: f.label }));
    const model = modelFromEmg(this.emg, { bands: this.stageBands(), ticks });
    if (!model.lines.length) {
      model.empty = this.emg?.error ? `No EMG for this incident (${this.emg.error}).` : "This incident has no EMG samples.";
      model.domain = this.range;
      model.bands = this.stageBands();
      model.ticks = ticks;
      model.lines = [];
    } else model.domain = [Math.min(model.domain[0], this.range[0]), Math.max(model.domain[1], this.range[1])];
    this.trace.set(model, { animate: true });
    this.trace.setCursor(this.t);
  }

  paintSide() {
    const d = this.data;
    const exp = d.explanation;
    const pr = d.proposed_rule;
    // explanation
    const conf = num(exp?.confidence);
    const rule = exp?.rule;
    this.expEl.innerHTML = `
      <div class="exp-head"><h3>Why did it stop?</h3>${sourceBadge(exp)}</div>
      <p class="exp-say"></p>
      <dl class="exp-facts">
        <div><dt>blamed</dt><dd>${esc(exp?.cause_object || "—")}</dd></div>
        <div><dt>relation</dt><dd>${esc(words(exp?.relation))}</dd></div>
        <div><dt>holding</dt><dd>${esc(words(exp?.held_property))}</dd></div>
        <div class="conf"><dt>confidence</dt><dd><span class="num">${conf == null ? "—" : conf.toFixed(2)}</span><span class="confbar"><b style="width:${conf == null ? 0 : conf * 100}%"></b></span></dd></div>
      </dl>
      ${exp?.note ? `<p class="exp-note">${esc(exp.note)}</p>` : ""}
      ${d.note ? `<details class="src-note"><summary>About this recording</summary><p class="exp-note mono">${esc(d.note)}</p></details>` : ""}`;
    this.typeCancel?.();
    this.typeCancel = typeInto(this.expEl.querySelector(".exp-say"), exp?.say || "", { reduced: this.ctx.reduced, cps: 54 });
    // proposed rule: never approved here
    if (!pr) {
      this.propEl.innerHTML = `<div class="panel-head"><h3>Proposed rule</h3><span class="stamp">NONE</span></div><p class="exp-idle">No rule was proposed with this incident.</p>`;
    } else {
      const probs = pr.problems || [];
      this.propEl.innerHTML = `
        <div class="panel-head"><h3>Proposed rule</h3><span class="stamp">UNAPPROVED</span></div>
        <p class="rule-s">${esc(ruleSentence(pr.rule))}</p>
        <div class="chips">${ruleChips(pr.rule)}</div>
        <div class="verdict ${pr.valid ? "ok" : "bad"}">${pr.valid ? `${ICON.check}<span>The runtime would accept this rule as written.</span>` : `${ICON.cross}<span>The runtime would refuse it:</span>`}</div>
        ${probs.length ? `<ul class="exp-problems">${probs.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}
        <p class="exp-note">${esc(pr.note || "Proposed only. Replay never saves a rule.")}</p>`;
    }
    // timeline list
    const items = d.timeline || [];
    this.evEl.innerHTML = `<div class="panel-head"><h3>Timeline</h3><span class="muted num">${items.length}</span></div>
      <ol class="ev-list">${items
        .map((it) => {
          const hasT = Number.isFinite(it.t);
          const kind = it.kind.replace(".", "-");
          return `<li class="ev ${esc(kind)}" ${hasT ? `data-t="${it.t}" tabindex="0"` : 'data-t=""'}><span class="ev-t num">${hasT ? fmtT(it.t) : "·"}</span><span class="ev-l">${esc(it.label || it.kind)}</span></li>`;
        })
        .join("")}</ol>`;
  }

  // ── viewer ────────────────────────────────────────────────────────────────
  setCam(cam, quiet) {
    this.cam = cam;
    this.q(".iv-cam").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.c === cam)));
    this.frameIdx = -1;
    if (!quiet) this.onTick(true);
  }

  frameAt(t) {
    const fs = this.frames || [];
    if (!fs.length) return -1;
    let idx = 0;
    for (let i = 0; i < fs.length; i++) if (fs[i].t <= t + 1e-6) idx = i;
    return idx;
  }

  showFrame(idx) {
    const f = this.frames[idx];
    if (!f) return;
    const cam = f.cams?.[this.cam] ? this.cam : Object.keys(f.cams || {})[0];
    this.frameIdx = idx;
    this.labelEl.textContent = `${f.label || `frame ${f.index}`} · ${idx + 1}/${this.frames.length}${cam !== this.cam ? ` · ${cam} only` : ""}`;
    if (!cam) {
      this.img.hidden = true;
      this.imgEmpty.hidden = false;
      this.imgEmpty.innerHTML = `<b>This frame has no image.</b>`;
      return;
    }
    const url = f.cams[cam];
    if (this.img.getAttribute("src") !== url) this.img.src = url;
  }
  showImgError() {
    this.imgErr = true;
    this.img.hidden = true;
    this.imgEmpty.hidden = false;
    this.imgEmpty.innerHTML = `<b>The frame image could not be loaded.</b><span>The file is missing or unreadable on the server.</span>`;
  }

  flashFreeze() {
    const el = this.q(".iv-flash");
    el.classList.remove("go");
    void el.offsetWidth;
    el.classList.add("go");
  }

  jointsAt(t) {
    const j = this.data?.joints;
    if (!j || !j.t?.length) return null;
    if (t <= j.t[0]) return j.q[0];
    if (t >= j.t[j.t.length - 1]) return j.q[j.q.length - 1];
    let i = 0;
    while (i < j.t.length - 2 && j.t[i + 1] < t) i++;
    const k = (t - j.t[i]) / (j.t[i + 1] - j.t[i] || 1);
    return j.q[i].map((v, n) => v + (j.q[i + 1][n] - v) * k);
  }
  stageAt(t) {
    const st = (this.data?.stages || []).filter((s) => Number.isFinite(s.t));
    let cur = null;
    for (const s of st) if (s.t <= t) cur = s;
    return cur;
  }
  emgAt(t) {
    const e = this.emg;
    if (!e?.t?.length) return null;
    if (t < e.t[0] || t > e.t[e.t.length - 1]) return null;
    let i = 0;
    while (i < e.t.length - 2 && e.t[i + 1] < t) i++;
    const k = (t - e.t[i]) / (e.t[i + 1] - e.t[i] || 1);
    const out = {};
    for (const [n, v] of Object.entries(e.channels || {})) out[n] = v[i] + ((v[i + 1] ?? v[i]) - v[i]) * k;
    return out;
  }

  onTick(force) {
    if (!this.data) {
      this.btnPlay.disabled = true;
      return;
    }
    const t = this.t;
    const idx = this.frameAt(t);
    if (idx !== this.frameIdx || force) {
      if (idx >= 0) this.showFrame(idx);
      else {
        this.img.hidden = true;
        this.imgEmpty.hidden = false;
        this.imgEmpty.innerHTML = `<b>No keyframes in this incident.</b><span>The trace and the explanation still apply.</span>`;
        this.labelEl.textContent = "";
      }
    }
    this.trace.setCursor(t);
    const frozen = t >= -1e-6;
    const stage = this.stageAt(t);
    const v = this.emgAt(t);
    this.rdT.textContent = this.hasData ? fmtT(t) : "—";
    this.rdT.classList.toggle("brace", frozen && this.hasData);
    this.rdS.textContent = !this.hasData ? "no recorded data" : frozen ? "arm held" : stage ? `${stage.stage}${stage.route ? ` · ${words(stage.route)} route` : ""}` : "";
    this.rdV.textContent = !this.hasData ? "no frames, EMG or joint samples were saved" : v ? `grip ${f2(v.flexor)} · open ${f2(v.extensor)} · brace ${f2(v.brace)}` : "no EMG at this moment";
    this.btnPlay.disabled = !this.hasData || this.range[1] - this.range[0] < 0.01;
    this.btnPlay.innerHTML = this.playing ? ICON.pause : t >= this.range[1] - 0.001 ? ICON.replay : ICON.play;
    this.btnPlay.setAttribute("aria-label", this.playing ? "Pause" : "Play");
    // joints
    const q = this.jointsAt(t);
    const names = this.data.joints?.names || [];
    const sig = q ? q.map((x) => Math.round(x)).join(",") : "";
    if (sig !== this._jsig) {
      this._jsig = sig;
      this.jointsEl.innerHTML = q
        ? names.map((n, i) => `<span class="jt"><b>${esc(JOINT_SHORT[n] || n)}</b><span class="num">${Math.round(q[i])}${n === "gripper" ? "%" : "°"}</span></span>`).join("")
        : '<span class="muted">No joint samples in this incident.</span>';
    }
    this.pane.querySelector(".iv-stage").classList.toggle("held", frozen);
  }

  // ── transport ─────────────────────────────────────────────────────────────
  seek(t) {
    this.t = Math.min(this.range[1], Math.max(this.range[0], t));
    this.onTick(false);
  }
  play() {
    if (!this.data) return;
    if (this.t >= this.range[1] - 0.001) this.t = this.range[0];
    this.playing = true;
    this.stopLoop ||= this.ctx.loop.add((dt) => {
      const prev = this.t;
      this.t = Math.min(this.range[1], this.t + dt * this.speed);
      if (prev < 0 && this.t >= 0 && !this.ctx.reduced) this.flashFreeze();
      if (this.t >= this.range[1]) this.pause();
      this.onTick(false);
    });
    this.onTick(true);
  }
  pause() {
    this.playing = false;
    this.stopLoop?.();
    this.stopLoop = null;
    if (this.data) this.onTick(false);
  }
  toggle() {
    this.playing ? this.pause() : this.play();
  }
  step(dir) {
    if (!this.frames?.length) return;
    const idx = Math.max(0, Math.min(this.frames.length - 1, (this.frameIdx < 0 ? this.frameAt(this.t) : this.frameIdx) + dir));
    this.seek(this.frames[idx].t);
  }
}
