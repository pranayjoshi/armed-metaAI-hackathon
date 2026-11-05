// Context: the overhead frame, the SAM 3.1 masks painted onto it, and the packet the runtime builds from it.
// Baseline data today: /api/camera/scene.jpg, state.vision, state.selection, store.history, /api/rules.
// Optional fields layered on top (all absent today, shown honestly as empty until the runtime sends them):
//   state.scene = { masks:[{phrase, polygon:[[x,y]..]|bbox:[x0,y0,x1,y1]}], frame_url?, scan_age_s, scan_ms, packet? }
//   state.scene.packet (or state.context) = { clock, objects:[{name,slot,age_s}], present:[], hand:{where,s}, sounds:[], last_actions:[], routines_due:[], rules:[] }
//   state.calls = [{ model, for, elapsed_ms, ms }]   `for` = a packet row id, or "scan"
import { MaskLayer, phraseColors } from "./masks.js";
import { ROWS, buildRows, litRows, rowHtml } from "./packet.js";

const html = `
<div class="cx">
  <header class="cx-head rise" style="--i:0">
    <div class="cx-title"><span class="kicker">What the arm knows about the desk</span><p class="cx-line" id="cx-line">Waiting for the overhead camera…</p></div>
    <div class="cx-badges" id="cx-badges"></div>
  </header>
  <div class="cx-body" id="cx-body">
    <section class="panel cx-frame-panel rise" style="--i:1" aria-label="Overhead frame">
      <div class="cx-stage" id="cx-stage">
        <div class="cx-fit" id="cx-fit"><img class="cx-img" id="cx-img" alt="Overhead camera frame" draggable="false" /></div>
        <div class="cx-nofr" id="cx-nofr" hidden></div>
        <figure class="cx-pip" id="cx-pip" hidden><img id="cx-laptop" alt="Laptop camera frame" draggable="false" /><figcaption>Laptop camera · what Muse sees of you</figcaption></figure>
      </div>
      <div class="cx-foot"><span class="cx-cap" id="cx-cap"></span><span id="cx-fcalls" class="cx-fcalls"></span></div>
    </section>
    <section class="panel cx-packet rise" style="--i:2" aria-label="Packet">
      <div class="panel-head"><h3>Packet</h3><span class="cx-age num" id="cx-age"></span></div>
      <div class="cx-rows" id="cx-rows"></div>
      <div class="cx-trig" id="cx-trig" hidden>
        <span class="kicker">Make it happen now</span>
        <div class="cx-trig-row" id="cx-trig-routines"></div>
        <div class="cx-trig-row"><button data-sound="phone buzz">Phone buzz</button><button data-sound="knock">Knock</button><em class="muted">mock sound</em></div>
      </div>
    </section>
  </div>
</div>`;

const chipHtml = (c) => {
  const key = `${c.model}|${c.for}`;
  return `<span class="chip muse cx-chip" data-ck="${key.replace(/"/g, "")}"><i></i><b></b></span>`;
};

export default {
  async mount(root, ctx) {
    ctx.css(import.meta.url, "style.css");
    root.innerHTML = html;
    const $ = (s) => root.querySelector(s);
    const { esc } = ctx;
    const s = (this.s = {
      root, ctx, $, state: null, rules: null, shownSig: null, frozen: false, natural: null, timers: [], removeLoop: null,
      calls: new Map(), phase: "idle", slideT: 0, masks: new MaskLayer($("#cx-fit")), fetchTimer: 0, active: false,
    });

    new ResizeObserver(() => this.fit()).observe($("#cx-stage"));
    $("#cx-img").addEventListener("load", () => {
      s.natural = [$("#cx-img").naturalWidth, $("#cx-img").naturalHeight];
      this.fit();
    });
    this.esc = esc;
    // Trigger strip (live only): a routine made due now, or a mock sound. Either still
    // only becomes a card; nothing here moves the arm.
    $("#cx-trig").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.routine) ctx.api("/api/routines", { run_now: b.dataset.routine });
      else if (b.dataset.sound) ctx.api("/api/sound/mock", { label: b.dataset.sound });
    });
  },

  async loadRoutines() {
    const s = this.s;
    if (s.ctx.mode === "replay") return;
    try {
      const r = await fetch("/api/routines");
      if (!r.ok) return;
      const data = await r.json();
      s.$("#cx-trig").hidden = !data.enabled;
      const list = (data.routines || []).filter((x) => !x.profile || x.profile === data.profile || !data.profile);
      this.setHtml("#cx-trig-routines", list.map((x) => `<button data-routine="${this.esc(x.name)}" title="Make this routine due now">${this.esc(x.label || x.name)}</button>`).join(""));
    } catch {}
  },

  // size the frame box to the image's aspect ratio inside the stage
  fit() {
    const s = this.s;
    if (!s.natural) return;
    const st = s.$("#cx-stage").getBoundingClientRect();
    const [w, h] = s.natural;
    const k = Math.min(st.width / w, st.height / h);
    const fit = s.$("#cx-fit");
    fit.style.width = `${Math.floor(w * k)}px`;
    fit.style.height = `${Math.floor(h * k)}px`;
  },

  enter(ctx) {
    const s = this.s;
    s.active = true;
    s.visibleAt = performance.now();
    this.pollFrame();
    s.timers.push(setInterval(() => this.pollFrame(), 700));
    this.loadRules();
    s.timers.push(setInterval(() => this.loadRules(), 15000));
    this.loadRoutines();
    s.removeLoop = ctx.loop.add(() => this.tick());
    if (ctx.store.state) this.update(ctx.store.state, ctx);
  },

  leave() {
    const s = this.s;
    s.active = false;
    s.timers.forEach(clearInterval);
    s.timers = [];
    s.removeLoop?.();
    s.removeLoop = null;
  },

  async loadRules() {
    try {
      const r = await fetch("/api/rules");
      if (!r.ok) return;
      this.s.rules = (await r.json()).rules || [];
    } catch {}
  },

  // The raw frame, refreshed ~1.4 Hz until a scan freezes it. A scan's own frame_url wins.
  pollFrame() {
    const s = this.s;
    if (!s.active) return;
    this.pollLaptop();
    if (s.frozen) return;
    const probe = new Image();
    probe.onload = () => {
      if (s.frozen) return;
      s.$("#cx-img").src = probe.src;
      s.$("#cx-nofr").hidden = true;
      s.hasFrame = true;
    };
    probe.onerror = () => {
      if (!s.hasFrame) this.noFrame();
    };
    probe.src = `/api/camera/scene.jpg?t=${Date.now()}`;
  },

  // The laptop camera (vision.laptop_camera), inset over the overhead frame; hidden when it is off.
  pollLaptop() {
    const s = this.s;
    if (s.ctx.mode === "replay" || s.state?.vision?.laptop_live !== true) {
      s.$("#cx-pip").hidden = true;
      return;
    }
    const probe = new Image();
    probe.onload = () => {
      s.$("#cx-laptop").src = probe.src;
      s.$("#cx-pip").hidden = false;
    };
    probe.onerror = () => { s.$("#cx-pip").hidden = true; };
    probe.src = `/api/camera/laptop.jpg?t=${Date.now()}`;
  },

  noFrame() {
    const s = this.s;
    const virt = s.state?.vision?.virtual;
    const el = s.$("#cx-nofr");
    el.hidden = false;
    el.innerHTML = virt
      ? `<b>No camera frame</b><span>This run has a virtual table, not a camera. Nothing is drawn in its place.</span>`
      : `<b>No camera frame</b><span>The overhead camera is not delivering frames.</span>`;
  },

  sceneSig(scene) {
    const m = scene?.masks;
    if (!Array.isArray(m) || !m.length) return null;
    const first = m[0].polygon?.[0] || m[0].bbox || [];
    return [scene.scan_ms, scene.frame_url, m.length, m.map((x) => x.phrase).join(","), first.join(",")].join("|");
  },

  startScan(scene, instant) {
    const s = this.s;
    clearTimeout(s.slideT);
    s.frozen = true;
    if (scene.frame_url) s.$("#cx-img").src = scene.frame_url;
    else if (!s.hasFrame) s.$("#cx-img").src = `/api/camera/scene.jpg?t=${Date.now()}`; // a scan landed before any frame
    const body = s.$("#cx-body");
    const done = s.masks.paint(scene.masks, { instant });
    if (instant || this.s.ctx.reduced) {
      body.classList.remove("wide");
      s.phase = "packet";
      return;
    }
    body.classList.add("wide"); // masks paint on the full-width frame, then it slides left
    s.phase = "paint";
    s.slideT = setTimeout(() => {
      body.classList.remove("wide");
      s.phase = "packet";
    }, done);
  },

  update(state, ctx) {
    const s = this.s;
    s.state = state;
    const wasPrimed = s.primed;
    s.primed = true;
    const scene = state.scene || {};
    const sig = this.sceneSig(scene);
    const hasScan = !!sig;

    if (sig && sig !== s.shownSig) {
      const first = !wasPrimed;
      s.shownSig = sig;
      // already-landed scan seen for the first time -> settled; a scan that lands while watching -> paint it in
      this.startScan(scene, first);
    } else if (!sig && s.shownSig) {
      s.shownSig = null;
      s.frozen = false;
      s.masks.clear();
      s.$("#cx-body").classList.remove("wide");
      s.phase = "idle";
      this.pollFrame();
    }

    // live calls
    const now = performance.now();
    const seen = new Set();
    for (const c of Array.isArray(state.calls) ? state.calls : []) {
      const key = `${c.model}|${c.for}`;
      seen.add(key);
      const prev = s.calls.get(key) || {};
      s.calls.set(key, { model: c.model, for: c.for, elapsed: c.elapsed_ms ?? prev.elapsed ?? 0, recv: now, ms: c.ms ?? null, doneAt: c.ms != null ? prev.doneAt || now : null });
    }
    for (const [k, c] of s.calls) {
      if (!seen.has(k) && c.ms == null) {
        c.ms = Math.round(c.elapsed + (now - c.recv)); // vanished without a measured time: say how long we saw it
        c.doneAt = now;
      }
    }

    // header
    const badges = [];
    if (state.vision?.virtual) badges.push(`<span class="chip warn"><i></i>VIRTUAL TABLE</span>`);
    if (hasScan && scene.simulated) badges.push(`<span class="chip"><i></i>simulated boxes · ${scene.masks.length} · no SAM call</span>`);
    else if (hasScan) badges.push(`<span class="chip muse"><i></i>SAM 3.1 · ${scene.masks.length} masks${scene.scan_ms != null ? ` · ${Math.round(scene.scan_ms)} ms` : ""}</span>`);
    this.setHtml("#cx-badges", badges.join(""));
    s.$("#cx-line").textContent = hasScan
      ? `${scene.masks.length} thing${scene.masks.length === 1 ? "" : "s"} found on the desk`
      : state.vision?.live || s.hasFrame
        ? "No scan yet. This is the raw frame."
        : "No scan yet.";

    // frame caption: object phrases in their mask colours, else the live baseline
    const cols = hasScan ? phraseColors(scene.masks) : null;
    this.setHtml(
      "#cx-cap",
      hasScan
        ? [...cols].map(([p, c]) => `<span class="cx-key" style="--c:${c}">${ctx.esc(p)}</span>`).join("")
        : state.vision?.objects?.length
          ? `<span class="muted">camera sees ${state.vision.objects.length} object${state.vision.objects.length === 1 ? "" : "s"} (detector, not a scan)</span>`
          : "",
    );

    // packet
    const rows = buildRows(state, { store: ctx.store, describeEvent: ctx.describeEvent, rules: s.rules });
    const { lit, color } = litRows(state, rows);
    const byRow = this.callsByRow();
    const dim = !hasScan;
    s.$("#cx-rows").classList.toggle("dim", dim);
    this.setHtml(
      "#cx-rows",
      (dim ? `<div class="cx-noscan">No scan yet</div>` : "") +
        ROWS.map(([id, label]) =>
          rowHtml(id, label, rows[id], { lit: lit.has(id), litColor: color || "var(--grip)", calls: (byRow[id] || []).map(chipHtml).join(""), noScan: dim }),
        ).join(""),
    );
    this.setHtml("#cx-fcalls", (byRow.scan || []).concat(byRow.other || []).map(chipHtml).join(""));
    s.$("#cx-rows").classList.toggle("has-lit", lit.size > 0);
    this.tick();
  },

  callsByRow() {
    const ids = new Set(ROWS.map((r) => r[0]));
    const out = {};
    for (const c of this.s.calls.values()) {
      const k = ids.has(c.for) ? c.for : c.for === "scan" || c.for === "frame" ? "scan" : "other";
      (out[k] ||= []).push(c);
    }
    return out;
  },

  setHtml(sel, h) {
    const el = this.s.$(sel);
    if (el._h !== h) el.innerHTML = el._h = h;
  },

  // every frame: counting clocks (calls, scan age). Numbers only ever come from the snapshot plus elapsed wall time since it arrived.
  tick() {
    const s = this.s;
    if (!s.state) return;
    const now = performance.now();
    for (const [k, c] of s.calls) {
      if (c.doneAt && now - c.doneAt > 1000) s.calls.delete(k);
    }
    for (const el of s.root.querySelectorAll("[data-ck]")) {
      const c = s.calls.get(el.dataset.ck);
      const b = el.querySelector("b");
      if (!c) {
        b.textContent = "";
        continue;
      }
      b.textContent = c.ms != null ? `${c.model} · ${Math.round(c.ms)} ms` : `calling ${c.model} · ${((c.elapsed + (now - c.recv)) / 1000).toFixed(1)} s`;
      el.classList.toggle("done", c.ms != null);
    }
    const sc = s.state.scene;
    const age = s.$("#cx-age");
    if (this.sceneSig(sc) && sc.scan_age_s != null) {
      if (!s.ageAnchor || s.ageAnchor.v !== sc.scan_age_s) s.ageAnchor = { v: sc.scan_age_s, at: now };
      age.textContent = `scan ${Math.round(sc.scan_age_s + (now - s.ageAnchor.at) / 1000)} s ago`;
    } else age.textContent = "";
  },
};
