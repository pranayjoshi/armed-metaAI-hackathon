// Simulation: the scripted story from h2m/scenario.py as a cinematic player.
// Everything shown is read from /api/replay/scenario (+ /timeline, the live frame and the live snapshot).
// Progress is derived from the real step statuses; nothing is hard-coded to advance.
import { Trace, modelFromEmg } from "./trace.js";
import { createTable } from "./table.js";
import { esc, getJSON, words, f2, num, provBadge, typeInto, ruleSentence, ruleChips, I, ICON } from "./util.js";

const BEATS = [
  { id: "ask", label: "Ask", steps: ["setup", "request", "confirm"], icon: I('<path d="M4 5h16v11H9l-5 4z"/>') },
  { id: "carry", label: "Carry", steps: ["carry"], icon: I('<path d="M5 9h14l-1.6 10H6.6z"/><path d="M8 9V5h8v4"/>') },
  { id: "brace", label: "Brace · freeze", steps: ["brace"], icon: I('<path d="M2 12h4l2.4-7 3.8 14 3-9 1.6 2H22"/>') },
  { id: "explain", label: "Explained", steps: ["incident", "explain"], icon: I('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>') },
  { id: "approve", label: "Rule approved", steps: ["approve"], icon: I('<path d="M5 12.5l4.5 4.5L19 7.5"/>') },
  { id: "reroute", label: "Long route", steps: ["rearm", "rerun_request", "rerun_confirm", "rerun_carry"], icon: I('<path d="M4 18c6 0 4-12 10-12h3"/><path d="M14 3l3 3-3 3"/><path d="M4 18h3"/>') },
];
const CARRYING = new Set(["carry", "release"]);
const LIVE_WINDOW = 12; // seconds of EMG kept for the live strip
const MIN_STEP = 0.004; // table units: ignore jitter in the trail

export class Sim {
  constructor(pane, hub) {
    this.pane = pane;
    this.hub = hub;
    this.ctx = hub.ctx;
    this.visible = false;
    this.sc = null;
    this.tl = null;
    this.pass = null;
    this.trails = new Map();
    this.pulsed = new Set();
    this.geoKey = "";
    this.geo = { zones: [], objects: [] };
    this.placed = {};
    this.buf = { g: [], o: [], b: [] };
    this.lastEmgT = -Infinity;
    this.freezeSeries = null; // incident /emg for the scenario's own clip
    this.freezeFor = null;
    this.traceMode = "auto"; // auto | live | freeze
    this.mode = "live";
    this.sigs = {};
    this.typed = "";
    this.frameBusy = false;
    this.firstUpdate = true;
  }

  async init() {
    const { pane } = this;
    pane.innerHTML = `
      <div class="sim-root">
        <div class="sim-trk"><ol class="trk">${BEATS.map((b, i) => `<li class="trk-n pending" data-i="${i}" data-id="${b.id}"><span class="dot">${b.icon}</span><b>${b.label}</b><small>—</small></li>`).join("")}<i class="trk-line"><i class="trk-prog"></i></i></ol></div>
        <div class="sim-main">
          <div class="sim-left">
            <div class="panel sim-stage">
              <div class="sim-tbl"></div>
              <div class="ov tl"><span class="kicker ov-k">Simulation</span><span class="ov-step"></span></div>
              <div class="ov tr ov-tags"></div>
              <div class="ov bl ov-legend"></div>
              <figure class="ov br sim-frame" title="The schematic frame the server renders for this simulation">
                <img alt="Server schematic of the virtual table" />
                <figcaption>server frame · schematic</figcaption>
              </figure>
              <div class="sim-banner" hidden></div>
              <div class="sim-nogeo" hidden>Table geometry (the avoid zone and object positions) arrives with the first incident clip. The gripper estimate is drawn already.</div>
            </div>
            <div class="panel sim-trace">
              <div class="trc-head">
                <div class="trc-title"><h3>EMG · synthetic</h3><span class="trc-sub"></span></div>
                <div class="seg trc-seg" role="group" aria-label="Trace view"><button data-m="live" aria-pressed="true">Live</button><button data-m="freeze" aria-pressed="false">Freeze window</button></div>
                <div class="trc-lat"><span class="num big">—</span><small>ms to freeze</small></div>
              </div>
              <div class="trc-host"></div>
            </div>
          </div>
          <div class="sim-right">
            <div class="panel sim-exp"></div>
            <div class="panel sim-appr"></div>
            <div class="panel sim-routes"></div>
          </div>
        </div>
      </div>`;
    const q = (s) => pane.querySelector(s);
    this.q = q;
    this.items = [...pane.querySelectorAll(".trk-n")];
    this.prog = q(".trk-prog");
    this.line = q(".trk-line");
    this.tblHost = q(".sim-tbl");
    this.ov = { k: q(".ov-k"), step: q(".ov-step"), tags: q(".ov-tags"), legend: q(".ov-legend") };
    this.frameImg = q(".sim-frame img");
    this.banner = q(".sim-banner");
    this.noGeo = q(".sim-nogeo");
    this.trace = new Trace(q(".trc-host"), { label: "Synthetic EMG trace" });
    this.latEl = q(".trc-lat .big");
    this.traceSub = q(".trc-sub");
    this.traceSeg = q(".trc-seg");
    this.traceSeg.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-m]");
      if (!b) return;
      this.traceMode = b.dataset.m;
      this.paintTrace(true);
    });
    this.exp = q(".sim-exp");
    this.appr = q(".sim-appr");
    this.routes = q(".sim-routes");
    this.table = await createTable(this.ctx, this.tblHost);
    this.frameImg.addEventListener("error", () => this.frameImg.closest(".sim-frame").classList.add("none"));
    this.frameImg.addEventListener("load", () => this.frameImg.closest(".sim-frame").classList.remove("none"));
  }

  // ── lifecycle ─────────────────────────────────────────────────────────────
  enter() {
    this.visible = true;
    this.table.start();
    this.table.resize();
    this.pollFrame();
    this.pane.querySelectorAll(".sim-trk, .sim-left, .sim-right").forEach((el, i) => {
      el.classList.remove("rise");
      void el.offsetWidth;
      el.style.setProperty("--i", String(i));
      el.classList.add("rise");
    });
    this.loadTimeline();
  }
  leave() {
    this.visible = false;
    this.table.stop();
    clearTimeout(this.frameT);
    this.typeCancel?.();
  }
  dispose() {
    this.leave();
    this.table.dispose();
    this.trace.dispose();
  }

  // ── data ──────────────────────────────────────────────────────────────────
  pollFrame() {
    clearTimeout(this.frameT);
    if (!this.visible) return;
    const sc = this.sc;
    const url = sc?.frame_url;
    if (url && !document.hidden && !this.frameBusy) {
      this.frameBusy = true;
      const done = () => (this.frameBusy = false);
      const img = this.frameImg;
      const once = () => (done(), img.removeEventListener("load", once), img.removeEventListener("error", once));
      img.addEventListener("load", once);
      img.addEventListener("error", once);
      img.src = `${url}?n=${(this.frameN = (this.frameN || 0) + 1)}`;
    }
    this.frameT = setTimeout(() => this.pollFrame(), 170);
  }

  async loadTimeline() {
    if (this.tlBusy) {
      this.tlAgain = true; // events changed while a fetch was in flight: fetch again, never keep a stale list
      return;
    }
    this.tlBusy = true;
    const r = await getJSON("/api/replay/scenario/timeline");
    this.tlBusy = false;
    if (this.tlAgain) {
      this.tlAgain = false;
      this.loadTimeline();
      return;
    }
    if (r.ok) {
      this.tl = r.body;
      this.tlAt = performance.now();
      this.afterTimeline();
    }
  }

  async ensureGeometry() {
    if (this.geoBusy) return;
    const sc = this.sc;
    const lib = this.hub.lib;
    // this pass's own clip first, else the first readable incident in the library (the bundled sample)
    const ids = [sc?.incident_id, ...lib.incidents.filter((i) => !i.error).map((i) => i.id)].filter(Boolean);
    const id = ids.find((x) => !(this.geoTried || new Set()).has(x)) || null;
    if (!id || id === this.geoKey) return;
    this.geoBusy = true;
    const r = await getJSON(`/api/replay/incident/${encodeURIComponent(id)}`);
    this.geoBusy = false;
    (this.geoTried ||= new Set()).add(id);
    if (!r.ok) return;
    const objs = r.body.objects || [];
    const zones = objs.filter((o) => o.kind === "avoid_zone" && Array.isArray(o.table_box)).map((o) => ({ name: o.name || o.id, box: o.table_box }));
    const objects = objs.filter((o) => o.kind !== "avoid_zone" && Array.isArray(o.table_xy)).map((o) => ({ id: o.id, name: o.name || o.id, xy: o.table_xy }));
    if (zones.length || objects.length) {
      this.geoKey = id;
      this.geo = { zones, objects };
    }
  }

  async ensureFreezeSeries() {
    const id = this.sc?.incident_id;
    if (!id || id === this.freezeFor) return;
    this.freezeFor = id;
    const r = await getJSON(`/api/replay/incident/${encodeURIComponent(id)}/emg`);
    this.freezeSeries = r.ok ? r.body : null;
    this.sigs.trace = "";
    this.paintTrace(true);
  }

  // ── per snapshot (~10 Hz) ─────────────────────────────────────────────────
  update(state) {
    const sc = state.scenario;
    if (!sc) return;
    const reset = this.pass != null && sc.run !== this.pass;
    if (reset || this.pass == null) {
      this.pass = sc.run;
      this.trails.clear();
      this.pulsed.clear();
      this.geoTried = new Set(this.geoKey ? [this.geoKey] : []);
      this.placed = {};
      this.freezeFor = null;
      this.freezeSeries = null;
      this.traceMode = "auto";
      this.typed = "";
      this.sigs = {};
      if (reset) this.loadTimeline();
    }
    this.sc = sc;
    this.state = state;
    this.ingestEmg(state.emg || sc.emg);
    this.collectTrails(state);
    if (!this.geoKey || (sc.incident_id && sc.incident_id !== this.geoKey)) this.ensureGeometry();
    if (sc.incident_id) this.ensureFreezeSeries();
    // the timeline grows as events arrive: refresh when the step sequence moved, and every couple of seconds while running
    const stepSig = sc.steps.map((s) => s.status).join("");
    if (stepSig !== this.sigs.steps) {
      this.sigs.steps = stepSig;
      this.loadTimeline();
    } else if (sc.state === "running" && performance.now() - (this.tlAt || 0) > 2500) this.loadTimeline();

    this.paintTracker(sc);
    this.paintStage(sc, state);
    this.paintRoutes(sc);
    this.paintExplanation(sc);
    this.paintApproval(sc);
    this.paintTrace();
    this.paintChrome(sc);
    this.firstUpdate = false;
  }

  afterTimeline() {
    this.sigs.trace = "";
    this.paintTrace(true);
    this.sigs.exp = "";
    if (this.sc) this.paintExplanation(this.sc);
  }

  ingestEmg(emg) {
    const e = this.state?.emg;
    if (!e) return;
    const push = (arr, t, v) => {
      arr.push([t, v]);
    };
    (e.trace || []).forEach((r) => {
      if (r[0] > this.lastEmgT) {
        push(this.buf.g, r[0], r[1]);
        push(this.buf.o, r[0], r[2]);
      }
    });
    (e.brace_trace || []).forEach((r) => {
      if (r[0] > (this.lastBraceT ?? -Infinity)) push(this.buf.b, r[0], r[1]);
    });
    const lastG = e.trace?.length ? e.trace[e.trace.length - 1][0] : this.lastEmgT;
    const lastB = e.brace_trace?.length ? e.brace_trace[e.brace_trace.length - 1][0] : this.lastBraceT;
    this.lastEmgT = Math.max(this.lastEmgT, lastG ?? -Infinity);
    this.lastBraceT = Math.max(this.lastBraceT ?? -Infinity, lastB ?? -Infinity);
    const now = Math.max(this.lastEmgT, this.lastBraceT);
    for (const k of ["g", "o", "b"]) {
      const a = this.buf[k];
      let i = 0;
      while (i < a.length && a[i][0] < now - LIVE_WINDOW - 1) i++;
      if (i) a.splice(0, i);
    }
    this.liveNow = now;
  }

  // ── trails + gripper ──────────────────────────────────────────────────────
  collectTrails(state) {
    const sc = this.sc;
    const arm = state.arm || sc.arm || {};
    const xy = sc.gripper_xy;
    const runs = sc.runs || [];
    runs.forEach((run, i) => {
      let tr = this.trails.get(i);
      if (!tr) {
        tr = { id: `run${sc.run}-${i}`, run: run.run, pts: [], end: null, color: "grip", active: false };
        this.trails.set(i, tr);
      }
      const frozen = typeof run.outcome === "string" && run.outcome.startsWith("frozen");
      const done = run.outcome === "completed";
      tr.color = frozen ? "brace" : (run.applied_rules || []).length ? "ok" : "grip";
      tr.active = i === runs.length - 1 && !frozen && !done && sc.state === "running";
      if (frozen && Array.isArray(run.frozen_at)) {
        tr.end = "frozen";
        tr.endAt = run.frozen_at;
        tr.endText = "froze here";
        if (!this.pulsed.has(i) && !this.firstUpdate) {
          this.pulsed.add(i);
          this.table.pulse(run.frozen_at, "brace");
          this.flash();
        } else this.pulsed.add(i);
      } else if (done) {
        tr.end = "done";
        tr.endAt = null;
        tr.endText = "placed";
        if (!tr.doneAt) tr.doneAt = tr.pts[tr.pts.length - 1] || null;
        if (tr.doneAt) {
          tr.endAt = tr.doneAt;
          if (run.run) this.placed.cup = tr.doneAt;
        }
      } else tr.end = null;
    });
    // append the live estimate to the latest run while the arm is actually moving
    const cur = this.trails.get(runs.length - 1);
    if (cur && xy && arm.state === "moving" && cur.active) {
      const last = cur.pts[cur.pts.length - 1];
      const carry = CARRYING.has(arm.stage);
      cur.carrying = carry;
      if (!last || Math.hypot(last[0] - xy[0], last[1] - xy[1]) > MIN_STEP) cur.pts.push([xy[0], xy[1], carry]);
      if (cur.pts.length > 800) cur.pts.splice(0, cur.pts.length - 800);
    }
    // a placed object is drawn where the estimate ended the run that completed
    const last = runs[runs.length - 1];
    if (!last || last.outcome !== "completed") delete this.placed.cup;
  }

  flash() {
    const el = this.q(".sim-stage");
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
  }

  // ── tracker ───────────────────────────────────────────────────────────────
  beatState(b, sc) {
    const st = b.steps.map((n) => sc.steps.find((s) => s.name === n)).filter(Boolean);
    if (!st.length) return "pending";
    if (st.some((s) => s.status === "failed")) return "fail";
    if (st.some((s) => s.status === "aborted")) return "fail";
    if (st.some((s) => s.status === "active")) return "active";
    if (st.every((s) => s.status === "done")) return "done";
    if (st.some((s) => s.status === "done")) return sc.state === "running" ? "active" : "pending";
    return "pending";
  }

  beatSub(b, sc, state) {
    const runs = sc.runs || [];
    if (state === "fail") return sc.state === "aborted" ? "aborted" : "failed";
    switch (b.id) {
      case "ask": return state === "pending" ? "—" : `“${sc.settings?.request || "request"}”`;
      case "carry": return runs[0]?.route ? `${words(runs[0].route)} route` : state === "pending" ? "—" : "starting";
      case "brace": return sc.brace?.detect_ms != null ? `${sc.brace.detect_ms} ms detect` : state === "active" ? "waiting for a brace" : "—";
      case "explain": return sc.explanation ? (sc.explanation.canned ? "canned text" : words(sc.explanation.source)) : sc.incident_id ? "clip saved" : "—";
      case "approve": return sc.rule ? "scripted · ui" : sc.proposal ? `UNAPPROVED · ${Math.ceil(sc.proposal.expires_in_s ?? 0)} s` : "—";
      case "reroute": return runs[1] ? `${words(runs[1].route)}${runs[1].outcome === "completed" ? " · placed" : "…"}` : state === "pending" ? "—" : "re-arming";
      default: return "—";
    }
  }

  paintTracker(sc) {
    const states = BEATS.map((b) => this.beatState(b, sc));
    const subs = BEATS.map((b, i) => this.beatSub(b, sc, states[i]));
    const sig = JSON.stringify([states, subs]);
    if (sig === this.sigs.trk) return;
    this.sigs.trk = sig;
    const prev = this.prevStates || [];
    this.prevStates = states;
    this.items.forEach((li, i) => {
      li.className = `trk-n ${states[i]}`;
      li.querySelector("small").textContent = subs[i];
      if (prev[i] && prev[i] !== states[i] && (states[i] === "done" || states[i] === "fail")) {
        li.classList.add("pop");
        setTimeout(() => li.classList.remove("pop"), 900);
      }
    });
    let last = -1;
    states.forEach((s, i) => {
      if (s === "done" || s === "fail") last = i;
    });
    const act = states.indexOf("active");
    if (act > last) last = act - 0.5;
    this.prog.style.setProperty("--p", String(Math.max(0, last) / (BEATS.length - 1)));
    this.line.classList.toggle("empty", last < 0);
  }

  // ── stage ─────────────────────────────────────────────────────────────────
  paintStage(sc, state) {
    const arm = state.arm || sc.arm || {};
    const runs = sc.runs || [];
    const cur = runs[runs.length - 1];
    const stage = arm.stage || sc.arm?.stage || null;
    const holding = CARRYING.has(stage) && (arm.target || "cup") && arm.state !== "ready" && arm.state !== "disarmed";
    const near = cur && !cur.outcome && cur.closest_to_zone != null && cur.closest_to_zone <= 0.02;
    const frozenRun = cur && typeof cur.outcome === "string" && cur.outcome.startsWith("frozen");
    const model = {
      zones: this.geo.zones,
      objects: this.geo.objects,
      grip: { xy: sc.gripper_xy || null, stage, state: arm.state, gripper: arm.joints?.gripper ?? sc.arm?.joints?.gripper, held: holding ? arm.target || "cup" : null },
      trails: [...this.trails.entries()].sort((a, b) => a[0] - b[0]).map(([, t]) => t),
      placed: this.placed,
      hot: !!(near || (frozenRun && arm.state !== "ready")),
    };
    this.table.set(model);

    const active = sc.steps.find((s) => s.status === "active");
    const done = sc.steps.filter((s) => s.status === "done").length;
    const stepText = active ? `step ${sc.steps.indexOf(active) + 1}/${sc.steps.length} · ${active.label}` : sc.state === "done" ? `all ${sc.steps.length} steps done` : sc.state === "idle" ? "starting" : `${done}/${sc.steps.length} steps`;
    if (this.ov.step.textContent !== stepText) this.ov.step.textContent = stepText;
    const kick = `Simulation · pass ${sc.run}`;
    if (this.ov.k.textContent !== kick) this.ov.k.textContent = kick;

    const tags = [];
    if (arm.hardware === false) tags.push(["VIRTUAL ARM", "warn"]);
    if ((state.emg || sc.emg)?.source === "synthetic") tags.push(["SYNTHETIC EMG", "warn"]);
    tags.push(["CANNED EXPLANATION", "warn"]);
    const th = tags.map(([t, c]) => `<span class="chip ${c}"><i></i>${t}</span>`).join("");
    if (this.sigs.tags !== th) this.ov.tags.innerHTML = this.sigs.tags = th;

    const lg = [...this.trails.values()].filter((t) => t.pts.length > 1).map((t) => `<span class="lg ${t.color}"><i></i>run ${t.run}</span>`).join("");
    const lgs = lg ? lg : "";
    if (this.sigs.lg !== lgs) this.ov.legend.innerHTML = this.sigs.lg = lgs;
    this.noGeo.hidden = !!(this.geo.zones.length || this.geo.objects.length);

    // banner: how the scenario really ended (never hidden, never prettier than it is)
    let b = "";
    let tone = "";
    if (sc.state === "aborted") (b = `Scenario aborted${sc.error ? ` · ${sc.error}` : ""}. The stop stays latched; nothing restarts it.`), (tone = "bad");
    else if (sc.state === "failed") (b = `Scenario failed${sc.error ? ` · ${sc.error}` : ""}. This is a finding, not a pass.`), (tone = "bad");
    else if (sc.state === "done") {
      const loopSeen = (sc.run || 1) > 1;
      if (loopSeen) {
        this.doneAt ??= performance.now();
        const left = Math.max(0, (sc.settings?.loop_pause_s ?? 0) - (performance.now() - this.doneAt) / 1000);
        b = `Complete · restarting in about ${Math.ceil(left)} s (loop pause ${sc.settings?.loop_pause_s} s)`;
      } else b = "Complete. It runs again only if `h2m demo` was started with --loop.";
      tone = "ok";
    } else this.doneAt = null;
    const bsig = b + tone;
    if (this.sigs.banner !== bsig) {
      this.sigs.banner = bsig;
      this.q(".sim-stage").classList.toggle("has-banner", !!b);
      this.banner.hidden = !b;
      this.banner.className = `sim-banner ${tone}`;
      this.banner.textContent = b;
    }
  }

  // ── routes (before / after) ───────────────────────────────────────────────
  paintRoutes(sc) {
    const runs = sc.runs || [];
    const zone = sc.settings?.brace_zone || "zone";
    const sig = JSON.stringify([runs, zone, sc.state]);
    if (sig === this.sigs.routes) return;
    this.sigs.routes = sig;
    const row = (run, i) => {
      if (!run) return `<div class="rt pend"><span class="rt-sw"></span><div class="rt-b"><b>Run ${i + 1}</b><span class="muted">${i === 1 ? "waiting for the rule and a second request" : "waiting for the first request"}</span></div></div>`;
      const frozen = typeof run.outcome === "string" && run.outcome.startsWith("frozen");
      const done = run.outcome === "completed";
      const dead = !frozen && !done && (sc.state === "aborted" || sc.state === "failed") && i === runs.length - 1;
      const cls = frozen || dead ? "bad" : done ? "ok" : "go";
      const verdict = frozen ? `${ICON.cross}blocked · ${esc(run.outcome.replace("frozen: ", "frozen by "))}` : done ? `${ICON.check}chosen · completed` : dead ? `${ICON.cross}not finished · ${esc(sc.state)}` : `in progress`;
      const rules = (run.applied_rules || []).length ? `rule …${esc(String(run.applied_rules[0]).slice(-4))} applied` : "no rule applied";
      const d = num(run.closest_to_zone);
      const bar = d == null ? "" : `<span class="rt-bar" title="closest the estimate got to the ${esc(zone)}"><i style="width:${Math.min(100, (d / 0.3) * 100).toFixed(0)}%"></i></span>`;
      return `<div class="rt ${cls}"><span class="rt-sw"></span><div class="rt-b"><div class="rt-top"><b>Run ${run.run}</b><code>${esc(words(run.route))}</code><span class="rt-v">${verdict}</span></div>
        <div class="rt-s">${rules} · closest to ${esc(zone)} <b class="num">${d == null ? "—" : d.toFixed(2)}</b>${bar}</div></div></div>`;
    };
    this.routes.innerHTML = `<div class="panel-head"><h3>Before · after</h3><span class="muted rt-hint">table units, schematic</span></div>${row(runs[0], 0)}${row(runs[1], 1)}`;
  }

  // ── explanation ───────────────────────────────────────────────────────────
  paintExplanation(sc) {
    const ex = sc.explanation;
    const evt = (this.tl?.events || []).filter((e) => e.kind === "incident.explained").pop();
    const problems = evt?.data?.problems || [];
    const sig = JSON.stringify([ex, problems, sc.rule?.id, sc.state, sc.step]);
    if (sig === this.sigs.exp) return;
    this.sigs.exp = sig;
    if (!ex) {
      const waiting = sc.incident_id ? "The clip is saved. The explanation is next." : sc.state === "running" ? "Nothing to explain yet. The explanation appears after a freeze." : "No explanation: the scenario never reached a freeze.";
      this.exp.className = "panel sim-exp idle";
      const script = sc.steps.map((st) => `<li class="${st.status}"><i></i><span>${esc(st.label)}</span></li>`).join("");
      this.exp.innerHTML = `<div class="panel-head"><h3>Why did it stop?</h3></div><p class="exp-idle">${esc(waiting)}</p><div class="script"><span class="kicker">The script · ${sc.steps.length} steps</span><ol>${script}</ol></div>`;
      this.typed = "";
      return;
    }
    const canned = ex.canned || ex.source === "canned";
    const rule = ex.rule;
    const conf = num(ex.confidence);
    this.exp.className = "panel sim-exp";
    const key = `${sc.run}:${ex.say}`;
    const firstTime = this.typed !== key;
    this.exp.innerHTML = `
      <div class="exp-head"><h3>Why did it stop?</h3>${canned ? '<span class="src canned" title="Fixed text from h2m/replay.py. No model was called.">CANNED · fixed demo text</span>' : `<span class="src model">${esc(words(ex.source))}</span>`}</div>
      <p class="exp-say"></p>
      <dl class="exp-facts">
        <div><dt>blamed</dt><dd>${esc(ex.cause_object || "—")}</dd></div>
        <div><dt>relation</dt><dd>${esc(words(ex.relation))}</dd></div>
        <div><dt>holding</dt><dd>${esc(words(ex.held_property))}</dd></div>
        <div class="conf"><dt>confidence</dt><dd><span class="num">${conf == null ? "—" : conf.toFixed(2)}</span><span class="confbar"><b style="width:${conf == null ? 0 : conf * 100}%"></b></span></dd></div>
      </dl>
      ${rule ? `<div class="exp-rule"><div class="exp-rule-h"><span class="kicker">Proposed rule</span>${sc.rule ? '<span class="stamp ok">APPROVED · scripted</span>' : '<span class="stamp">UNAPPROVED</span>'}</div><p class="rule-s">${esc(ruleSentence(rule))}</p><div class="chips">${ruleChips(rule)}</div></div>` : ""}
      ${problems.length ? `<ul class="exp-problems">${problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}
      ${ex.note ? `<p class="exp-note">${esc(ex.note)}</p>` : ""}`;
    const sayEl = this.exp.querySelector(".exp-say");
    if (firstTime) {
      this.typed = key;
      this.typeCancel?.();
      this.typeCancel = typeInto(sayEl, ex.say, { reduced: this.ctx.reduced, cps: 52 });
    } else sayEl.textContent = ex.say || "";
  }

  // ── approval ──────────────────────────────────────────────────────────────
  paintApproval(sc) {
    const p = sc.proposal;
    const rule = sc.rule;
    const exp = Math.ceil(p?.expires_in_s ?? 0);
    const sig = JSON.stringify([!!p, rule?.id, rule?.approved, exp, sc.state, sc.settings?.approve_after_s]);
    if (sig === this.sigs.appr) return;
    this.sigs.appr = sig;
    const by = (this.tl?.events || []).filter((e) => e.kind === "rule.approved").pop()?.data?.by;
    let cls = "idle";
    let html;
    if (rule) {
      cls = "done";
      html = `<div class="appr-top"><span class="appr-ic ok">${ICON.check}</span><div><b>Rule approved</b><span class="muted">scripted · by ${esc(by || "ui")} · …${esc(String(rule.id).slice(-6))}</span></div></div>
        <p class="appr-n">Answered by the demo script after ${esc(sc.settings?.approve_after_s)} s, not by a person. Approving never resumes the arm.</p>`;
    } else if (p) {
      cls = "wait";
      html = `<div class="appr-top"><span class="appr-ic wait">${exp}</span><div><b>Waiting for approval</b><span class="muted">UNAPPROVED · expires in ${exp} s</span></div></div>
        <p class="appr-n">No approve button exists in replay: the script reads the card for ${esc(sc.settings?.approve_after_s)} s, then answers.</p>`;
    } else if (sc.state === "aborted" || sc.state === "failed") {
      html = `<div class="appr-top"><span class="appr-ic bad">${ICON.cross}</span><div><b>Not approved</b><span class="muted">the scenario ended before a rule was approved</span></div></div>`;
    } else {
      html = `<div class="appr-top"><span class="appr-ic idle"></span><div><b>Approval</b><span class="muted">nothing proposed yet</span></div></div>`;
    }
    this.appr.className = `panel sim-appr ${cls}`;
    this.appr.innerHTML = html;
  }

  // ── EMG trace ─────────────────────────────────────────────────────────────
  autoMode(sc) {
    if (!this.freezeSeries || !this.freezeSeries.t?.length) return "live";
    const s = sc.step;
    if (sc.state === "done" || sc.state === "aborted" || sc.state === "failed") return "freeze";
    return ["incident", "explain", "approve", "rearm"].includes(s) ? "freeze" : "live";
  }

  paintTrace(force = false) {
    const sc = this.sc;
    if (!sc) return;
    const mode = this.traceMode === "auto" ? this.autoMode(sc) : this.traceMode;
    const canFreeze = !!this.freezeSeries?.t?.length;
    const useMode = mode === "freeze" && canFreeze ? "freeze" : "live";
    if (useMode !== this.mode) {
      this.mode = useMode;
      force = true;
    }
    this.traceSeg.querySelectorAll("button").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.m === useMode));
      b.disabled = b.dataset.m === "freeze" && !canFreeze;
    });
    const lat = sc.brace?.detect_ms;
    const latS = lat != null ? String(lat) : "—";
    if (this.latEl.textContent !== latS) this.latEl.textContent = latS;
    this.latEl.parentElement.classList.toggle("none", lat == null);

    if (useMode === "freeze") {
      if (!force && this.sigs.trace === `f${this.freezeFor}`) return;
      this.sigs.trace = `f${this.freezeFor}`;
      const fs = this.freezeSeries;
      const m = modelFromEmg(fs);
      if (sc.brace?.detect_ms != null && m.onset) m.latencyMs = sc.brace.detect_ms; // the detector's own measurement of the same interval
      this.trace.set(m, { animate: true });
      this.traceSub.textContent = `freeze window · saved with the incident clip · ${fs.onset?.source === "derived" ? "onset derived from the trace" : "onset as recorded"}`;
      return;
    }
    // live strip: the real recent EMG, newest on the right
    const now = this.liveNow;
    if (!Number.isFinite(now)) {
      this.trace.set({ lines: [], empty: "Waiting for the synthetic EMG stream…" });
      return;
    }
    const rel = (arr) => ({ t: arr.map((r) => r[0] - now), v: arr.map((r) => r[1]) });
    const g = rel(this.buf.g);
    const o = rel(this.buf.o);
    const b = rel(this.buf.b);
    let onset = null;
    let freeze = null;
    let latencyMs = null;
    const ev = this.tl?.events || [];
    const brace = ev.filter((e) => e.kind === "emg.brace").pop()?.data;
    const stopped = ev.filter((e) => e.kind === "arm.stopped").pop()?.data;
    if (brace?.onset_t != null && brace.onset_t - now >= -LIVE_WINDOW) onset = { t: brace.onset_t - now, label: "brace onset" };
    if (stopped?.latency_ref != null && stopped.latency_ref - now >= -LIVE_WINDOW) {
      const why = String(stopped.reason || "");
      freeze = { t: stopped.latency_ref - now, label: /brace|open hand/.test(why) || !why ? "arm frozen" : `arm stopped · ${why}` };
    }
    if (onset && freeze) latencyMs = (freeze.t - onset.t) * 1000;
    this.trace.set({
      lines: [
        { cls: "open", name: "open", ...o },
        { cls: "grip", name: "grip", ...g },
        { cls: "brace", name: "brace", ...b },
      ],
      domain: [-LIVE_WINDOW, 0],
      threshold: num((this.state?.emg || {}).brace_effort),
      onset,
      freeze,
      latencyMs,
      step: 2,
      axis: (v) => (Math.abs(v) < 1e-6 ? "now" : `−${Math.abs(v)}s`),
    });
    this.traceSub.textContent = "live · last 12 s of the synthetic stream";
  }

  paintChrome() {}
}
