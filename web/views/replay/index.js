// Replay: the software demo. No arm, no key, no network.
//   Simulation  the scripted brace -> freeze -> rule story on the virtual arm (SIMULATION)
//   Incidents   a library of saved incident clips, playable with their EMG
//   Sessions    recorded study sessions
// Every payload carries a provenance label; it is shown, never hidden.
import { Sim } from "./sim.js";
import { Incidents } from "./incidents.js";
import { Sessions } from "./sessions.js";
import { esc, getJSON, ICON } from "./util.js";

const TAGLINE = "Hands never moved. One muscle, one twitch.";
const SCOPE = "This build simulates the brace → freeze → rule loop. The twitch and card selection is not in it yet.";
const TABS = [
  { id: "sim", label: "Simulation", icon: ICON.sim, hue: "warn" },
  { id: "incidents", label: "Incidents", icon: ICON.inc, hue: "brace" },
  { id: "sessions", label: "Sessions", icon: ICON.ses, hue: "grip" },
];

class Replay {
  constructor(root, ctx) {
    this.root = root;
    this.ctx = ctx;
    this.visible = false;
    this.tab = null;
    this.lib = { incidents: [], sessions: [], scenario: false, label: null, note: "", zones: [] };
    this.tabs = [];
    this.libSig = "";
    this.libAt = 0;
    this.state = null;
  }

  async init() {
    const { root, ctx } = this;
    ctx.css(import.meta.url, "style.css");
    root.innerHTML = `
      <div class="rpl" data-tab="">
        <div class="rpl-ribbon" role="status"><span class="rb-label">REPLAY</span><span class="rb-note"></span><span class="rb-r">read-only · no arm · no key · no network</span></div>
        <header class="rpl-head">
          <div class="rpl-title"><span class="kicker">Software demo</span><h1>Replay</h1><p class="rpl-tag">${esc(TAGLINE)}</p><p class="rpl-scope">${esc(SCOPE)}</p></div>
          <div class="rpl-seg" role="tablist" aria-label="Replay view"><i class="seg-thumb"></i></div>
        </header>
        <div class="rpl-main">
          <section class="pane" data-pane="sim" role="tabpanel"></section>
          <section class="pane" data-pane="incidents" role="tabpanel"></section>
          <section class="pane" data-pane="sessions" role="tabpanel"></section>
          <section class="rpl-empty" hidden>
            <div class="panel">
              <svg viewBox="0 0 120 70" aria-hidden="true"><rect x="8" y="8" width="104" height="54" rx="8" class="e-t"/><path d="M8 35h104M60 8v54" class="e-g"/><rect x="28" y="18" width="22" height="16" class="e-z"/><path d="M34 52c14-2 6-24 30-24h22" class="e-p"/></svg>
              <b>Nothing to replay yet</b>
              <p>There is no simulation running and no recording in this folder.</p>
              <div class="cmds">
                <div><span class="kicker">Run the scripted story</span><code>h2m demo</code></div>
                <div><span class="kicker">Make the bundled sample clip</span><code>python scripts/make_demo_sample.py</code></div>
                <div><span class="kicker">Replay your own folder</span><code>h2m replay data/incidents</code></div>
              </div>
            </div>
          </section>
        </div>
      </div>`;
    this.el = root.querySelector(".rpl");
    this.segEl = root.querySelector(".rpl-seg");
    this.thumb = root.querySelector(".seg-thumb");
    this.ribLabel = root.querySelector(".rb-label");
    this.ribNote = root.querySelector(".rb-note");
    this.ribbon = root.querySelector(".rpl-ribbon");
    this.emptyEl = root.querySelector(".rpl-empty");
    this.panes = {
      sim: root.querySelector('[data-pane="sim"]'),
      incidents: root.querySelector('[data-pane="incidents"]'),
      sessions: root.querySelector('[data-pane="sessions"]'),
    };
    await this.refreshLib(true);
    const qs = new URLSearchParams(location.search); // ?tab=incidents&inc=<id>&t=<s> park the view (for screenshots)
    this.qs = qs;

    this.inc = new Incidents(this.panes.incidents, this);
    this.ses = new Sessions(this.panes.sessions, this);
    await Promise.all([this.inc.init(), this.ses.init()]);
    const st = this.ctx.store.state;
    if (this.lib.scenario || st?.scenario) await this.ensureSim();

    this.buildSeg();
    this.segEl.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-tab]");
      if (b) this.setTab(b.dataset.tab, true);
    });
    this.segEl.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      e.preventDefault();
      const ids = this.tabs.map((t) => t.id);
      const i = ids.indexOf(this.tab);
      this.setTab(ids[(i + (e.key === "ArrowRight" ? 1 : ids.length - 1)) % ids.length], true, true);
    });
    addEventListener("resize", () => this.placeThumb());
    const want = qs.get("tab");
    this.setTab(this.tabs.some((t) => t.id === want) ? want : this.defaultTab(), false);
    if (qs.get("inc")) this.inc.select(qs.get("inc"), false).then(() => qs.has("t") && (this.inc.seek(Number(qs.get("t"))), qs.get("cam") && this.inc.setCam(qs.get("cam"))));
    if (qs.get("ses")) this.ses.select(qs.get("ses"));
  }

  async ensureSim() {
    if (this.sim) return;
    this.sim = new Sim(this.panes.sim, this);
    await this.sim.init();
  }

  defaultTab() {
    if (this.sim) return "sim";
    if (this.lib.incidents.length) return "incidents";
    if (this.lib.sessions.length) return "sessions";
    return "incidents";
  }

  // ── library ───────────────────────────────────────────────────────────────
  async refreshLib(force = false) {
    if (this.libBusy) return;
    this.libBusy = true;
    const r = await getJSON("/api/replay");
    this.libBusy = false;
    this.libAt = performance.now();
    if (!r.ok) return;
    const b = r.body;
    const next = { incidents: b.incidents || [], sessions: b.sessions || [], scenario: !!b.scenario, label: b.label, note: b.note, zones: b.zones || [] };
    const sig = JSON.stringify(next);
    if (sig === this.libSig && !force) return;
    this.libSig = sig;
    this.lib = next;
    if (this.inc) {
      this.inc.renderList();
      this.ses.renderList();
    }
    if (this.segEl && this.tabs.length) this.paintSegSubs();
    this.paintRibbon();
    this.paintEmpty();
  }

  openIncident(id) {
    this.setTab("incidents", true);
    this.inc.select(id, true);
  }

  // ── chrome ────────────────────────────────────────────────────────────────
  buildSeg() {
    const hasSim = !!this.sim;
    this.tabs = TABS.filter((t) => t.id !== "sim" || hasSim);
    this.segEl.querySelectorAll("button").forEach((b) => b.remove());
    this.segEl.style.setProperty("--cnt", String(this.tabs.length));
    this.segEl.insertAdjacentHTML(
      "beforeend",
      this.tabs
        .map((t) => `<button role="tab" data-tab="${t.id}" data-hue="${t.hue}" aria-selected="false" tabindex="-1"><span class="seg-ic">${t.icon}</span><span class="seg-t">${t.label}</span><span class="seg-s"><i class="dot"></i><span class="seg-st">—</span></span></button>`)
        .join(""),
    );
    this.paintSegSubs();
  }

  paintSegSubs() {
    const set = (id, text, dot = "") => {
      const b = this.segEl.querySelector(`[data-tab="${id}"]`);
      if (!b) return;
      const st = b.querySelector(".seg-st");
      if (st.textContent !== text) st.textContent = text;
      b.querySelector(".dot").className = `dot ${dot}`;
    };
    const n = this.lib.incidents.length;
    set("incidents", n ? `${n} recorded` : "none recorded");
    const m = this.lib.sessions.length;
    set("sessions", m ? `${m} recorded` : "none recorded");
    const sc = this.state?.scenario;
    if (this.sim) {
      if (!sc) set("sim", "starting");
      else {
        const done = sc.steps.filter((s) => s.status === "done").length;
        const label = { running: `step ${Math.min(done + 1, sc.steps.length)}/${sc.steps.length}`, done: "complete", aborted: "aborted", failed: "failed", idle: "idle" }[sc.state] || sc.state;
        set("sim", label, sc.state === "running" ? "hot" : sc.state === "done" ? "ok" : sc.state === "aborted" || sc.state === "failed" ? "bad" : "");
      }
    }
  }

  paintRibbon() {
    const s = this.state;
    const label = s?.label || this.lib.label || "REPLAY";
    const note = s?.note || this.lib.note || "";
    const sim = label === "SIMULATION";
    this.ribbon.className = `rpl-ribbon ${sim ? "is-sim" : "is-rep"}`;
    if (this.ribLabel.textContent !== label) this.ribLabel.textContent = label;
    if (this.ribNote.textContent !== note) this.ribNote.textContent = note;
  }

  paintEmpty() {
    const none = !this.lib.incidents.length && !this.lib.sessions.length && !this.lib.scenario && !this.state?.scenario;
    this.emptyEl.hidden = !none;
    this.el.classList.toggle("is-empty", none);
  }

  placeThumb() {
    const b = this.segEl.querySelector(`[data-tab="${this.tab}"]`);
    if (!b || !this.thumb) return;
    this.thumb.style.width = `${b.offsetWidth}px`;
    this.thumb.style.transform = `translateX(${b.offsetLeft - 5}px)`;
    this.thumb.dataset.hue = b.dataset.hue;
  }

  setTab(tab, user = false, keepFocus = false) {
    if (!this.tabs.some((t) => t.id === tab)) tab = this.defaultTab();
    const prev = this.tab;
    const changed = tab !== prev;
    this.tab = tab;
    this.el.dataset.tab = tab;
    this.segEl.querySelectorAll("[role=tab]").forEach((b) => {
      const on = b.dataset.tab === tab;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
      if (on && keepFocus) b.focus();
    });
    this.placeThumb();
    requestAnimationFrame(() => this.placeThumb());
    if (changed) {
      this.el.classList.remove("shift");
      void this.el.offsetWidth;
      this.el.classList.add("shift");
      if (prev === "sim") this.sim?.leave();
      if (prev === "incidents") this.inc.hide();
      if (prev === "sessions") this.ses.hide();
    }
    if (this.visible) this.activate(tab);
  }

  activate(tab) {
    if (tab === "sim") {
      this.sim?.enter();
      if (this.state?.scenario) this.sim?.update(this.state);
    } else if (tab === "incidents") this.inc.show();
    else if (tab === "sessions") this.ses.show();
  }

  // ── lifecycle ─────────────────────────────────────────────────────────────
  enter() {
    this.visible = true;
    this.el.querySelectorAll(".rpl-ribbon, .rpl-head, .rpl-main").forEach((el, i) => {
      el.classList.remove("rise");
      void el.offsetWidth;
      el.style.setProperty("--i", String(i));
      el.classList.add("rise");
    });
    this.refreshLib();
    this.placeThumb();
    this.activate(this.tab);
  }

  leave() {
    this.visible = false;
    this.sim?.leave();
    this.inc?.hide();
    this.ses?.hide();
  }

  async update(state) {
    this.state = state;
    this.paintRibbon();
    this.paintEmpty();
    if (state.scenario && !this.sim && !this.simInit) {
      this.simInit = true;
      await this.ensureSim();
      this.buildSeg();
      this.setTab(this.tab, false);
    }
    // the library grows while the scenario saves incidents
    const r = state.replay;
    if (r && (r.incidents !== this.lib.incidents.length || r.sessions !== this.lib.sessions.length)) this.refreshLib();
    else if (this.visible && performance.now() - this.libAt > 15000) this.refreshLib();
    this.paintSegSubs();
    if (state.scenario) this.sim?.update(state);
  }
}

export default {
  async mount(root, ctx) {
    this.R = new Replay(root, ctx);
    await this.R.init();
  },
  enter(ctx) {
    this.R?.enter(ctx);
  },
  leave(ctx) {
    this.R?.leave(ctx);
  },
  update(state, ctx) {
    this.R?.update(state, ctx);
  },
};
