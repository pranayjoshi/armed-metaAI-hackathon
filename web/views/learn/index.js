// Learning: from brace to rule. Two linked views (Incident, Rules) behind one segmented control,
// a loop tracker across the top, and one shared table scene that moves between them.
import { buildModel, findEvents, fmtT, shortId } from "./data.js";
import { createTable } from "./scene.js";
import { Player, Timeline } from "./timeline.js";
import { MuseCard, ProposalCard } from "./muse.js";
import { Tracker, Thread } from "./tracker.js";
import { RulesView } from "./rules.js";
import { CardsView } from "./cards.js";

const I = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const ICON = {
  inc: I('<path d="M3 12h4l2.4-7 3.8 14 3-9 1.6 2H21"/>'),
  rules: I('<path d="M6 3h12v18l-6-4.5L6 21z"/>'),
  play: I('<path d="M7 4.5v15l12-7.5z"/>'),
  pause: I('<rect x="6" y="5" width="4.2" height="14" rx="1"/><rect x="13.8" y="5" width="4.2" height="14" rx="1"/>'),
  replay: I('<path d="M4 12a8 8 0 1 0 2.6-5.9M4 4v4.5h4.5"/>'),
  cards: I('<rect x="3.5" y="8" width="13" height="11" rx="2"/><path d="M7 8V6.2A1.7 1.7 0 0 1 8.7 4.5h9.6a1.7 1.7 0 0 1 1.7 1.7v7.1a1.7 1.7 0 0 1-1.7 1.7H16.5"/>'),
};

async function getJSON(url) {
  try {
    const r = await fetch(url);
    const body = await r.json().catch(() => ({}));
    return r.ok ? { ok: true, body } : { ok: false, body, status: r.status };
  } catch (err) {
    return { ok: false, body: { detail: String(err.message || err) }, status: 0 };
  }
}

class Learn {
  constructor(root, ctx) {
    this.root = root;
    this.ctx = ctx;
    this.tab = "incident";
    this.tableTab = "incident"; // which tab the shared table scene is mounted for (Cards leaves it where it was)
    this.ro = ctx.mode === "replay"; // read-only server: no approve / reject / delete route exists
    this.visible = false;
    this.learning = null;
    this.details = null; // {id, state, meta, explanation}
    this.detailKey = "";
    this.rules = null; // {rules, zones} | {error}
    this.geo = new Map(); // incident id -> replay model | null
    this.model = null;
    this.modelSig = "";
    this.playedFor = null;
    this.proposedFor = new Map();
    this.selected = null;
  }

  async init() {
    const { root, ctx } = this;
    ctx.css(import.meta.url, "style.css");
    root.innerHTML = `<div class="lrn" data-tab="incident">
      <canvas class="lrn-thread" aria-hidden="true"></canvas>
      <header class="lrn-head">
        <div class="lrn-title"><span class="kicker">Learning</span><h1>From brace to rule</h1></div>
        <div class="lrn-seg" role="tablist" aria-label="Learning view">
          <i class="seg-thumb"></i>
          <button role="tab" data-tab="incident" aria-selected="true"><span class="seg-ic">${ICON.inc}</span><span class="seg-t">Incident</span><span class="seg-s"><i class="dot"></i><span class="seg-st">none yet</span></span></button>
          <button role="tab" data-tab="rules" aria-selected="false"><span class="seg-ic">${ICON.rules}</span><span class="seg-t">Rules</span><span class="seg-s"><span class="seg-n num">0</span> saved</span></button>
          <button role="tab" data-tab="cards" aria-selected="false"><span class="seg-ic">${ICON.cards}</span><span class="seg-t">Cards</span><span class="seg-s"><span class="seg-c num">0</span> tasks</span></button>
        </div>
      </header>
      <div class="lrn-trk"></div>
      <div class="lrn-main">
        <section class="pane pane-incident" role="tabpanel">
          <div class="inc-left">
            <div class="panel scene-card">
              <div class="scene-host">
                <div class="hud tl"><span class="kicker hud-k">Replay</span><span class="hud-id num"></span><span class="hud-state"></span></div>
                <div class="hud tr"></div>
                <div class="hud bl"></div>
                <div class="scene-empty"><b>No incident yet.</b><span>When you brace, it appears here.</span></div>
              </div>
            </div>
            <div class="panel tl-card">
              <div class="tl-bar">
                <button class="pbtn" data-a="play" aria-label="Play">${ICON.play}</button>
                <div class="seg tl-speed" role="group" aria-label="Speed"><button data-s="0.25" aria-pressed="false">0.25×</button><button data-s="1" aria-pressed="true">1×</button></div>
                <div class="tl-read"><span class="num rd-t">—</span><span class="num rd-v muted"></span></div>
                <div class="lat"><span class="num big">—</span><small>ms onset → stop</small></div>
              </div>
              <div class="tl-host"></div>
            </div>
          </div>
          <div class="inc-right"><div class="muse-host"></div><div class="prop-host"></div></div>
        </section>
        <section class="pane pane-rules" role="tabpanel"></section>
        <section class="pane pane-cards" role="tabpanel"></section>
      </div>
    </div>`;
    const q = (s) => root.querySelector(s);
    this.el = q(".lrn");
    this.sceneHost = q(".scene-host");
    this.hud = { id: q(".hud-id"), st: q(".hud-state"), tr: q(".hud.tr"), bl: q(".hud.bl"), k: q(".hud-k") };
    this.tagsEl = this.hud.tr;
    this.tracker = new Tracker(q(".lrn-trk"));
    this.thread = new Thread(this.el, q(".lrn-thread"));
    this.muse = new MuseCard(q(".muse-host"), ctx);
    this.prop = new ProposalCard(q(".prop-host"), {
      readOnly: this.ro,
      onApprove: (id, b) => this.answer("approve", id, b),
      onReject: (id, b) => this.answer("reject", id, b),
      onSeeRule: () => this.setTab("rules"),
    });
    this.rv = new RulesView(q(".pane-rules"), ctx, {
      readOnly: this.ro,
      onSelect: (id) => this.select(id),
      onDelete: (id) => this.deleteRule(id),
      onReplay: () => this.setTab("incident", true),
    });
    this.cards = new CardsView(q(".pane-cards"), ctx, { readOnly: this.ro });
    this.timeline = new Timeline(q(".tl-host"), { onScrub: (t) => (this.player.pause(), this.player.seek(t)) });
    this.player = new Player(ctx.loop, (t, force) => this.tick(t, force));
    this.table = await createTable(ctx);
    this.table.mount(this.sceneHost);
    this.btnPlay = q(".pbtn");
    this.rdT = q(".rd-t");
    this.rdV = q(".rd-v");
    this.lat = q(".lat .big");
    this.sceneEmpty = q(".scene-empty");

    // controls
    q(".lrn-seg").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-tab]");
      if (b) this.setTab(b.dataset.tab);
    });
    q(".lrn-seg").addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        const order = ["incident", "rules", "cards"];
        const i = order.indexOf(this.tab) + (e.key === "ArrowRight" ? 1 : -1);
        this.setTab(order[(i + order.length) % order.length], false, true);
      }
    });
    this.btnPlay.onclick = () => this.player.toggle();
    q(".tl-speed").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-s]");
      if (!b) return;
      this.player.setSpeed(Number(b.dataset.s));
      q(".tl-speed").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      if (!this.player.playing) this.player.play();
    });

    ctx.bus.on("event", (e) => this.onEvent(e));
    const qs = new URLSearchParams(location.search); // ?lt=<s> parks the replay at a time; ?tab=rules opens Rules (for screenshots)
    this.debugT = qs.has("lt") ? Number(qs.get("lt")) : null;
    if (qs.get("tab") === "rules" || qs.get("tab") === "cards") this.setTab(qs.get("tab"));
    this.loadRules();
  }

  // ── data ─────────────────────────────────────────────────────────────────
  async loadRules() {
    const r = await getJSON("/api/rules");
    this.rules = r.ok ? r.body : { error: r.body?.detail || `HTTP ${r.status}`, rules: [], zones: [] };
    await this.loadGeo();
    this.afterData();
    return this.rules;
  }

  async loadGeo() {
    const ids = [...new Set((this.rules?.rules || []).map((r) => r.incident_id).filter(Boolean))];
    await Promise.all(
      ids
        .filter((id) => !this.geo.has(id))
        .map(async (id) => {
          const r = await getJSON(`/api/incident/${encodeURIComponent(id)}`);
          const meta = r.ok ? r.body.incident : null;
          this.geo.set(id, meta ? buildModel(meta, findEvents(this.ctx.store.history, meta.t_freeze)) : null);
        }),
    );
  }

  async fetchDetails(inc) {
    const key = `${inc.id}|${inc.state}`;
    if (!inc.id || key === this.detailKey) return;
    this.detailKey = key;
    const r = await getJSON(`/api/incident/${encodeURIComponent(inc.id)}`);
    if (this.detailKey !== key) return;
    this.detailsError = r.ok ? null : r.body?.detail || `HTTP ${r.status}`;
    this.details = r.ok ? { id: inc.id, state: inc.state, meta: r.body.incident, explanation: r.body.explanation } : null;
    this.syncModel();
    this.afterData();
  }

  onEvent(e) {
    const k = e.kind;
    if (k.startsWith("rule.")) {
      if (k === "rule.approved") {
        this.loadRules().then(() => this.celebrate(e.rule?.id));
      } else this.loadRules();
    }
    if (k === "incident.saved" || k === "incident.explained" || k === "incident.triggered" || k === "emg.brace" || k === "arm.stopped") {
      this.modelSig = ""; // events feed the replay model (onset, stop, trace)
      if (this.visible) this.afterData();
    }
    if (k === "incident.triggered" && this.visible) {
      this.tracker.items[0].classList.add("pop");
      this.sceneHost.classList.remove("flash");
      void this.sceneHost.offsetWidth;
      this.sceneHost.classList.add("flash");
    }
  }

  // ── derive + render ──────────────────────────────────────────────────────
  update(state) {
    this.state = state;
    // Replay with no running simulation: the snapshot has no `learning` at all. Show the empty loop, not nothing.
    const L = state.learning ?? (this.ro ? { proposal: null, incident: null, muse: false, noSim: true } : undefined);
    this.muse.label = state.label;
    this.muse.noSim = !!L?.noSim;
    this.prop.noSim = !!L?.noSim;
    const inc = L?.incident;
    const prev = this.learning?.incident;
    this.learning = L;
    if (prev && inc && prev.id !== inc.id) {
      this.details = null;
      this.detailsError = null;
      this.detailKey = "";
    }
    if (inc?.id && (!prev || prev.id !== inc.id || prev.state !== inc.state || !this.details)) this.fetchDetails(inc);
    if (L?.proposal && inc) this.proposedFor.set(inc.id ?? "pending", L.proposal.rule.id);
    this.afterData();
    this.paintCards();
  }

  paintCards() {
    if (!this.state) return;
    this.cards.render(this.state, this.visible && this.tab === "cards");
    const n = this.cards.order.length;
    const el = this.root.querySelector(".seg-c");
    if (el.textContent !== String(n)) el.textContent = n;
  }

  syncModel() {
    const inc = this.learning?.incident;
    const hist = this.ctx.store.history;
    const meta = this.details?.meta;
    if (!inc || !meta || meta.id !== inc.id) {
      if (this.model) this.setModel(null);
      return;
    }
    const ev = findEvents(hist, meta.t_freeze);
    const sig = `${meta.id}|${this.details.state}|${ev.brace?.seq}|${ev.stopped?.seq}|${ev.triggered?.seq}|${this.details.explanation?.cause_object}`;
    if (sig === this.modelSig) return;
    this.modelSig = sig;
    this.setModel(buildModel(meta, ev));
  }

  setModel(model) {
    const first = !this.model || this.model.id !== model?.id;
    this.model = model;
    const cause = this.details?.explanation?.cause_object || this.learning?.incident?.explanation?.cause_object;
    this.table.setModel(model, { cause });
    const thr = this.state?.emg?.brace_effort ?? null;
    this.timeline.setModel(model, thr);
    this.paintEmpty();
    this.el.classList.toggle("has-incident", !!model && !model.empty);
    if (model && !model.empty) {
      const start = model.bands.length ? Math.max(model.range[0], model.bands[0].t0 - 0.4) : model.range[0];
      this.player.range = model.range;
      if (first) {
        this.player.t = this.debugT != null ? this.debugT : this.ctx.reduced ? 0 : start;
        this.played = this.debugT != null;
      }
      this.player.onTick(this.player.t, true);
      this.lat.textContent = model.stop?.latencyMs != null ? String(model.stop.latencyMs) : "—";
      this.maybeAutoplay();
    } else {
      this.lat.textContent = "—";
      this.rdT.textContent = "—";
      this.rdV.textContent = "";
    }
  }

  paintEmpty() {
    const inc = this.learning?.incident;
    const m = this.model;
    let t = this.learning?.noSim
      ? ["No simulation running.", "Open Replay to scrub the recorded incidents."]
      : this.ro
        ? ["No incident yet.", "It appears here when the scripted brace happens."]
        : ["No incident yet.", "When you brace, it appears here."];
    if (inc && (!m || m.empty)) {
      if (inc.state === "capturing" || !inc.id) t = ["Recording the clip.", "One more second after the freeze, then it is saved."];
      else if (m?.empty) t = ["This clip has no joint samples.", "Nothing to replay; the explanation still applies."];
      else if (this.detailsError) t = ["The clip can’t be read.", this.detailsError];
      else t = ["Loading the clip.", ""];
    }
    this.sceneEmpty.hidden = !!(m && !m.empty);
    const [a, b] = this.sceneEmpty.children;
    if (a.textContent !== t[0]) a.textContent = t[0];
    if (b.textContent !== t[1]) b.textContent = t[1];
  }

  maybeAutoplay() {
    const m = this.model;
    if (!m || m.empty || this.played || !this.visible || this.tab !== "incident" || this.ctx.reduced) return;
    this.played = true;
    setTimeout(() => this.visible && this.tab === "incident" && !this.player.playing && this.player.play(), 450);
  }

  /** Everything that follows from new data: tracker, cards, tags, rules map. */
  afterData() {
    const L = this.learning;
    if (!L) return;
    if (!this.model && this.details?.meta) this.syncModel();
    else if (this.visible) this.syncModel();
    const { stages, outcome } = this.derive();
    this.tracker.render(stages);
    this.updateFlow(stages);
    const inc = L.incident;
    this.muse.render(inc, this.details && this.details.id === inc?.id ? this.details : null, L);
    this.prop.render(L.proposal, outcome);
    this.paintHeader();
    this.paintEmpty();
    this.paintRules();
  }

  derive() {
    const L = this.learning;
    const inc = L.incident;
    const rules = this.rules?.rules || [];
    const none = { state: "pending", sub: "—" };
    if (!inc) {
      return { stages: [{ ...none, sub: this.learning.noSim ? "no simulation" : this.ro ? "waiting for the script" : "waiting for a brace" }, none, none, none, none, none], outcome: { kind: "idle" } };
    }
    const hist = this.ctx.store.history;
    const ev = findEvents(hist, inc.t);
    const onsetAbs = ev.brace?.onset_t ?? ev.triggered?.onset_t ?? null;
    const exp = this.details?.id === inc.id ? this.details.explanation || inc.explanation : inc.explanation;
    const saved = inc.id ? rules.find((r) => r.incident_id === inc.id) : null;
    let justApproved = null;
    const approvedEv = saved && [...hist].reverse().find((e) => e.kind === "rule.approved" && e.rule?.id === saved.id);
    const proposed = L.proposal && (L.proposal.incident_id === inc.id || inc.id == null);
    const s = [];
    s[0] = { state: "done", sub: inc.source === "open_hand" ? "open hand" : ev.brace ? `peak ${ev.brace.peak_brace.toFixed(2)}` : "EMG brace" };
    const lat = ev.stopped && onsetAbs != null ? Math.round((ev.stopped.t - onsetAbs) * 1000) : null;
    s[1] = { state: "done", sub: lat != null ? `${lat} ms` : "arm stopped" };
    const errored = hist.some((e) => e.kind === "incident.error" && e.t >= inc.t - 0.2);
    s[2] = inc.state === "capturing" ? { state: "active", sub: "recording…" } : inc.id ? { state: "done", sub: ev.saved ? `+${(ev.saved.t - inc.t).toFixed(1)} s` : "saved" } : errored ? { state: "fail", sub: "not saved" } : { state: "active", sub: "recording…" };
    if (inc.state === "explained" && exp) {
      s[3] = { state: "done", sub: exp.source === "fallback" ? "local fallback" : exp.source === "canned" ? "canned (simulation)" : `Muse Wonder${ev.explained && ev.saved ? ` · ${(ev.explained.t - ev.saved.t).toFixed(1)} s` : ""}` };
    } else s[3] = inc.state === "explaining" ? { state: "active", sub: "reviewing" } : { state: "pending", sub: "—" };

    let outcome = { kind: "waiting" };
    if (inc.state !== "explained") {
      s[4] = { ...none };
      s[5] = { ...none };
    } else if (saved) {
      outcome = { kind: "saved", rule: saved, by: approvedEv?.by };
      s[4] = { state: "done", sub: approvedEv?.by ? `by ${approvedEv.by === "emg" ? "squeeze" : approvedEv.by}` : "approved" };
      s[5] = { state: "done", sub: `…${shortId(saved.id)}` };
    } else if (proposed) {
      outcome = { kind: "pending" };
      s[4] = { state: "active", sub: `waiting · ${Math.ceil(L.proposal.expires_in_s)} s` };
      s[5] = { ...none };
    } else if (!proposed && (justApproved = [...hist].reverse().find((e) => e.kind === "rule.approved" && e.t >= inc.t - 0.2))) {
      // approved, but GET /api/rules has not returned the new rule yet: not a failure, just not listed
      outcome = { kind: "saving" };
      s[4] = { state: "done", sub: justApproved.by ? `by ${justApproved.by === "emg" ? "squeeze" : justApproved.by}` : "approved" };
      s[5] = { state: "active", sub: "saving…" };
    } else if (!exp?.rule) {
      outcome = { kind: "none" };
      s[4] = { state: "skip", sub: "no rule proposed" };
      s[5] = { state: "skip", sub: "—" };
    } else {
      const pid = this.proposedFor.get(inc.id);
      const e = pid && [...hist].reverse().find((x) => (x.kind === "rule.rejected" || x.kind === "rule.expired") && x.rule_id === pid);
      const kind = e?.kind === "rule.expired" ? "expired" : e ? "rejected" : "lost";
      outcome = { kind };
      s[4] = { state: "fail", sub: kind === "expired" ? "expired" : kind === "rejected" ? "rejected" : "not saved" };
      s[5] = { state: "skip", sub: "—" };
    }
    return { stages: s, outcome };
  }

  updateFlow(stages) {
    const a = this.tracker.activeIdx;
    if (a > 0 && this.visible && this.tab !== "cards" && !this.ctx.reduced) {
      const colour = [null, "255,77,94", "170,182,198", "165,139,255", "90,240,164"][a] || "170,182,198";
      this.thread.flow(this.tracker.dot(a - 1), this.tracker.dot(a), colour);
    } else this.thread.flow(null, null);
  }

  paintHeader() {
    const inc = this.learning.incident;
    const st = this.root.querySelector(".seg-st");
    const dot = this.root.querySelector('[data-tab="incident"] .dot');
    const label = !inc ? "none yet" : { capturing: "recording", explaining: "Muse reviewing", explained: this.learning.proposal ? "awaiting you" : "explained" }[inc.state] || inc.state;
    if (st.textContent !== label) st.textContent = label;
    dot.className = `dot ${!inc ? "" : inc.state === "explained" && !this.learning.proposal ? "ok" : "hot"}`;
    this.hud.id.textContent = inc?.id ? `…${shortId(inc.id)}` : "";
    this.hud.st.textContent = inc ? { capturing: "freeze · keeping one more second", explaining: "clip saved · Muse reviewing", explained: "explained" }[inc.state] || "" : "";
    this.hud.st.className = `hud-state ${inc && inc.state !== "explained" ? "live" : ""}`;
    const s = this.state;
    const tags = [];
    if (this.ro) tags.push([`READ-ONLY · ${s?.label || "REPLAY"}`, ""]);
    if (s?.arm && s.arm.hardware === false) tags.push(["VIRTUAL ARM", "warn"]);
    if (s?.emg?.source === "synthetic") tags.push(["SYNTHETIC EMG", "warn"]);
    const html = tags.map(([t, c]) => `<span class="chip ${c}"><i></i>${t}</span>`).join("");
    if (this._tags !== html) this.hud.tr.innerHTML = this._tags = html;
    const cap = this.model && !this.model.empty ? "arm pose: schematic kinematics from the recorded joint angles" : "";
    if (this.hud.bl.textContent !== cap) this.hud.bl.textContent = cap;
  }

  paintRules() {
    const n = this.rules?.rules?.length ?? 0;
    const el = this.root.querySelector(".seg-n");
    if (el.textContent !== String(n)) el.textContent = n;
    const rules = this.rules?.rules || [];
    if (!this.selected || !rules.some((r) => r.id === this.selected)) this.selected = rules.length ? rules[rules.length - 1].id : null;
    if (this.tab !== "rules") return;
    this.rv.render({ data: this.rules, geo: this.geo, state: this.state, latestIncident: this.learning?.incident?.id, selected: this.selected, routesError: this.learning?.routes_error });
    this.rv.renderCarries(this.ctx.store.history, rules);
    this.pushMap();
  }

  pushMap() {
    const rules = this.rules?.rules || [];
    const sel = rules.find((r) => r.id === this.selected);
    const zones = new Map();
    const paths = [];
    const seenInc = new Set();
    [...rules].reverse().forEach((r) => {
      const m = this.geo.get(r.incident_id);
      if (!m) return;
      m.zones.forEach((z) => zones.has(z.name) || zones.set(z.name, z));
      if (seenInc.has(r.incident_id)) return;
      seenInc.add(r.incident_id);
      paths.push({ model: m, focus: sel ? sel.incident_id === r.incident_id : false, ruleId: r.id });
    });
    const sig = JSON.stringify([rules.map((r) => r.id), [...zones.keys()], this.selected, paths.length]);
    if (sig === this.mapSig) return;
    this.mapSig = sig;
    this.table.setRules({ zones: [...zones.values()], paths, focusZones: sel ? [sel.avoid] : [] });
  }

  // ── interaction ──────────────────────────────────────────────────────────
  setTab(tab, replay = false, keepFocus = false) {
    const changed = tab !== this.tab;
    if (changed) {
      this.el.classList.remove("shift");
      void this.el.offsetWidth;
      this.el.classList.add("shift");
    }
    this.tab = tab;
    this.el.dataset.tab = tab;
    this.root.querySelectorAll(".lrn-seg [role=tab]").forEach((b) => {
      const on = b.dataset.tab === tab;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
      if (on && keepFocus) b.focus();
    });
    if (changed) {
      if (tab === "cards") {
        this.player.pause();
        this.paintCards();
        this.cards.dirty = true;
        this.cards.render(this.state || {}, true);
      } else {
        if (this.tableTab !== tab) {
          this.tableTab = tab;
          const host = tab === "incident" ? this.sceneHost : this.rv.mapHost;
          this.table.mount(host);
          this.table.setMode(tab === "incident" ? "incident" : "rules");
        }
        if (tab === "rules") {
          this.player.pause();
          this.mapSig = "";
          this.paintRules();
          this.loadRules();
        } else {
          this.table.setCursor(this.player.t);
          this.maybeAutoplay();
        }
        requestAnimationFrame(() => this.table.resize());
      }
      this.thread.resize();
    }
    if (replay && this.model && !this.model.empty) {
      this.played = true;
      this.player.seek(this.model.bands.length ? Math.max(this.model.range[0], this.model.bands[0].t0 - 0.4) : this.model.range[0]);
      this.player.play();
    }
    this.updateFlow([]);
  }

  select(id) {
    this.selected = id;
    this.mapSig = "";
    this.paintRules();
  }

  async answer(kind, id, btn) {
    if (this.ro) return; // /api/rules/proposal/* does not exist on the replay server
    btn.dataset.busy = "1";
    btn.disabled = true;
    const r = await this.ctx.api(`/api/rules/proposal/${encodeURIComponent(id)}/${kind}`);
    btn.dataset.busy = "0";
    if (!r?.ok) this.prop.sig = ""; // let the card repaint from the next snapshot
  }

  async deleteRule(id) {
    if (this.ro) return false; // DELETE /api/rules/* does not exist on the replay server
    const r = await this.ctx.api.del(`/api/rules/${encodeURIComponent(id)}`);
    if (r?.ok) {
      await this.loadRules();
      return true;
    }
    return false;
  }

  /** The thread flies from the tracker into the new rule (or the Rules tab). */
  celebrate(ruleId) {
    if (!this.visible || this.ctx.reduced) return;
    const from = this.tracker.dot(5);
    if (this.tab === "rules") {
      this.select(ruleId);
      requestAnimationFrame(() => {
        const card = this.rv.cardEl(ruleId);
        if (!card) return;
        this.thread.fly(from, card, { onArrive: () => (card.classList.add("landed"), setTimeout(() => card.classList.remove("landed"), 1400)) });
      });
    } else {
      const tab = this.root.querySelector('.lrn-seg [data-tab="rules"]');
      this.thread.fly(from, tab, { onArrive: () => (tab.classList.add("landed"), setTimeout(() => tab.classList.remove("landed"), 1400)) });
    }
  }

  // ── per frame ────────────────────────────────────────────────────────────
  tick(t, force) {
    const m = this.model;
    this.btnPlay.innerHTML = this.player.playing ? ICON.pause : this.player.t >= this.player.range[1] - 0.001 && m ? ICON.replay : ICON.play;
    this.btnPlay.setAttribute("aria-label", this.player.playing ? "Pause" : "Play");
    this.btnPlay.disabled = !m || m.empty;
    if (!m || m.empty) return;
    this.table.setCursor(t);
    this.timeline.setCursor(t);
    const v = this.timeline.valuesAt(t);
    this.rdT.textContent = fmtT(t);
    this.rdV.textContent = v ? `grip ${v.f.toFixed(2)} · open ${v.e.toFixed(2)} · brace ${v.b.toFixed(2)}` : "";
    const p = this.table.pose;
    if (p) {
      const k = `${p.stage || ""}|${p.holding}|${t >= 0}`;
      if (k !== this._hudk) {
        this._hudk = k;
        const held = m.objectName || (m.held ? String(m.held).replace(/\s*\(.*$/, "") : "");
        this.hud.k.textContent = t >= 0 ? "Frozen" : p.stage ? `Replay · ${p.stage}${p.holding && held ? ` · ${held}` : ""}` : "Replay";
        this.hud.k.className = `kicker hud-k ${t >= 0 ? "brace" : ""}`;
      }
    }
  }

  frame(dt) {
    this.thread.frame(dt);
    this.prop.tick();
  }

  enter() {
    this.visible = true;
    this.stopFrame = this.ctx.loop.add((dt) => this.frame(dt));
    this.table.start();
    this.table.resize();
    this.thread.resize();
    this.root.querySelectorAll(".lrn-head, .lrn-trk, .lrn-main").forEach((el, i) => {
      el.classList.remove("rise");
      void el.offsetWidth;
      el.style.setProperty("--i", String(i));
      el.classList.add("rise");
    });
    this.loadRules();
    this.afterData();
    this.paintCards();
    this.maybeAutoplay();
  }

  leave() {
    this.visible = false;
    this.stopFrame?.();
    this.table.stop();
    this.player.pause();
    this.thread.flow(null, null);
  }

  dispose() {
    this.leave();
    this.table.dispose();
    this.timeline.dispose();
    this.thread.dispose();
    this.cards.dispose();
  }
}

export default {
  async mount(root, ctx) {
    const L = new Learn(root, ctx);
    root._learn = L;
    await L.init();
    this.L = L;
  },
  enter(ctx) {
    this.L?.enter(ctx);
  },
  leave(ctx) {
    this.L?.leave(ctx);
  },
  update(state, ctx) {
    this.L?.update(state, ctx);
  },
};
