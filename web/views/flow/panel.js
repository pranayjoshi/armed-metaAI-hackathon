// Side panel: the trace ribbon (events flowing upward) and the node inspector.
import { esc } from "../../js/core.js";
import { NODE_BY_ID, EDGES } from "./graph.js";
import { fmtMs, short } from "./events.js";

const KIND_LABEL = {
  "speech.utterance": "heard", intent: "decision", "compiler.llm": "muse", "arm.started": "arm start", "arm.completed": "arm done",
  "arm.stopped": "stopped", "arm.fault": "fault", "emg.squeeze": "squeeze", "emg.squeeze_rejected": "squeeze ✕", "emg.open_hand": "open hand",
  "emg.brace": "brace", "intent.cancelled": "cancelled", "intent.expired": "expired", "intent.blocked": "blocked",
  "incident.triggered": "incident", "incident.saved": "clip", "incident.explained": "wonder", "incident.error": "incident ✕",
  "rule.approved": "rule saved", "rule.rejected": "rule declined", "rule.expired": "rule expired", "rule.error": "rule ✕",
  "speech.ambiguous": "ambiguous", "speech.error": "speech ✕", "vision.error": "vision ✕", "emg.error": "emg ✕",
};

const jsonOf = (raw) =>
  JSON.stringify(
    raw,
    (k, v) => (Array.isArray(v) && v.length > 8 && v.every((x) => typeof x === "number" || Array.isArray(x)) ? `[${v.length} samples]` : v),
    2,
  );

function ageText(sec) {
  if (!Number.isFinite(sec)) return "";
  if (sec < 60) return `${sec.toFixed(sec < 10 ? 1 : 0)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  return ">1h";
}

function sparkSvg(vals, w = 248, h = 52) {
  if (!vals.length) return "";
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const span = hi - lo || 1;
  const px = (i) => (vals.length === 1 ? w / 2 : 6 + (i / (vals.length - 1)) * (w - 12));
  const py = (v) => h - 8 - ((v - lo) / span) * (h - 16);
  const pts = vals.map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" ");
  const dots = vals.map((v, i) => `<circle cx="${px(i).toFixed(1)}" cy="${py(v).toFixed(1)}" r="${i === vals.length - 1 ? 3.2 : 1.8}"/>`).join("");
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" role="img" aria-label="latency history"><polyline points="${pts}" fill="none"/>${dots}</svg>`;
}

export class SidePanel {
  /**
   * @param host      <aside>
   * @param api       { data(id), live(id), names, onPick(e), onClose(), onSelect(id), hueOfNode(id) }
   */
  constructor(host, api) {
    this.host = host;
    this.api = api;
    const replay = api.mode === "replay";
    this.items = []; // {e, el}
    this.cap = 14;
    this.selectedSeq = null;
    this.node = null;
    host.innerHTML = `
      <header class="sp-tabs" role="tablist">
        <button role="tab" data-tab="trace" aria-selected="true">Trace</button>
        <button role="tab" data-tab="node" aria-selected="false">Inspector</button>
        <span class="sp-count num" aria-live="off"></span>
      </header>
      <section class="sp-trace" role="tabpanel">
        <p class="sp-hint">Newest at the bottom. Click an event to replay its path through the graph.</p>
        <div class="sp-rail-wrap"><i class="sp-rail"></i><i class="sp-pulse"></i>
          <ol class="sp-list"></ol>
          <p class="sp-empty">Nothing has happened since this page loaded.<br><span>${replay ? "Events appear here while a simulation runs; each one sends a packet across the graph." : "Say or type a request, squeeze, or brace: the event appears here and its packet crosses the graph."}</span></p>
        </div>
      </section>
      <section class="sp-insp" role="tabpanel" hidden></section>`;
    this.list = host.querySelector(".sp-list");
    this.empty = host.querySelector(".sp-empty");
    this.pulseEl = host.querySelector(".sp-pulse");
    this.count = host.querySelector(".sp-count");
    this.insp = host.querySelector(".sp-insp");
    this.traceEl = host.querySelector(".sp-trace");
    host.querySelector(".sp-tabs").addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-tab]");
      if (b) this.tab(b.dataset.tab);
    });
    this.list.addEventListener("click", (ev) => {
      const li = ev.target.closest("li[data-seq]");
      if (!li) return;
      const it = this.items.find((x) => String(x.e.seq) === li.dataset.seq);
      if (it) this.pick(it);
    });
    this.insp.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-go]");
      if (b) this.api.onSelect(b.dataset.go);
      if (ev.target.closest("[data-close]")) this.api.onClose();
    });
  }

  tab(name) {
    const node = name === "node";
    this.host.querySelectorAll(".sp-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    this.traceEl.hidden = node;
    this.insp.hidden = !node;
  }

  // ── ribbon ───────────────────────────────────────────────────────────────────────
  add(e, { animate = true, state } = {}) {
    const li = document.createElement("li");
    li.dataset.seq = e.seq;
    const hue = this.api.hueOfEvent(e);
    li.style.setProperty("--h", `var(--${hue})`);
    li.innerHTML = `<i class="dot"></i><div class="it"><div class="it-h"><b>${esc(KIND_LABEL[e.kind] || e.kind)}</b><span class="it-a num"></span></div><div class="it-t">${esc(short(e, this.api.names()))}</div></div>`;
    li.tabIndex = 0;
    li.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        li.click();
      }
    });
    const item = { e, el: li, age: li.querySelector(".it-a"), cache: "" };
    this.items.push(item);
    const shift = animate ? 0 : 0;
    this.list.append(li);
    while (this.items.length > this.cap) {
      const old = this.items.shift();
      old.el.remove();
    }
    this.empty.hidden = true;
    if (animate) {
      const h = li.offsetHeight + 8;
      this.list.style.transition = "none";
      this.list.style.transform = `translateY(${h}px)`;
      void this.list.offsetHeight;
      this.list.style.transition = "";
      this.list.style.transform = "";
      li.classList.add("new");
      this.pulseEl.classList.remove("go");
      void this.pulseEl.offsetWidth;
      this.pulseEl.classList.add("go");
    }
    void shift;
    if (state) this.tick(state);
    this.count.textContent = `${this.items.length} shown`;
  }

  pick(item) {
    this.items.forEach((x) => x.el.classList.toggle("sel", x === item));
    this.selectedSeq = item.e.seq;
    this.api.onPick(item.e);
  }

  clearPick() {
    this.selectedSeq = null;
    this.items.forEach((x) => x.el.classList.remove("sel"));
  }

  tick(state) {
    if (!state) return;
    for (const it of this.items) {
      const t = ageText(state.t - it.e.t);
      if (t !== it.cache) {
        it.cache = t;
        it.age.textContent = t;
      }
    }
  }

  // ── inspector ────────────────────────────────────────────────────────────────────
  show(id) {
    this.node = id;
    this.tab("node");
    this.render();
  }

  hide() {
    this.node = null;
    this.tab("trace");
  }

  render(state) {
    const id = this.node;
    if (!id) return;
    const n = NODE_BY_ID[id];
    const d = this.api.data(id);
    const L = this.api.live(id);
    const sig = `${id}|${d.n}|${d.lat.length}|${d.line}|${L?.stat}|${L?.tag}|${L?.status}`;
    if (sig === this._sig && !state) return;
    if (sig === this._sig) {
      this._updateAge(state);
      return;
    }
    this._sig = sig;
    this.insp.style.setProperty("--h", `var(--${n.hue})`);
    const lats = d.lat.map((x) => x.v);
    const sorted = [...lats].sort((a, b) => a - b);
    const med = sorted.length ? sorted[Math.floor(sorted.length / 2)] : null;
    const ins = EDGES.filter((e) => e.to === id).map((e) => e.from);
    const outs = EDGES.filter((e) => e.from === id).map((e) => e.to);
    const chips = (ids) => ids.map((x) => `<button class="chip-go" data-go="${x}">${esc(NODE_BY_ID[x].title)}</button>`).join("") || '<span class="muted">—</span>';
    this.insp.innerHTML = `
      <div class="in-head" style="--h: var(--${n.hue})">
        <button class="in-back" data-close aria-label="Back to the trace">←</button>
        <div><h2>${esc(n.title)}</h2><span class="kicker">${esc(n.lane)} lane · ${esc(n.src)}</span></div>
      </div>
      <p class="in-role">${esc(n.role)}</p>
      <dl class="in-kv">
        <dt>Now</dt><dd class="num">${esc(L?.stat || "—")}${L?.tag ? ` <em class="in-tag">${esc(L.tag)}</em>` : ""}</dd>
        <dt>Events seen</dt><dd class="num">${d.n} since load</dd>
        <dt>Last seen</dt><dd class="num in-age">${d.raw ? esc(ageText((this.api.now() ?? 0) - d.raw.t)) + " ago" : "never"}</dd>
      </dl>
      <h3>Latest payload</h3>
      ${d.raw ? `<pre class="in-json num" tabindex="0">${esc(jsonOf(d.raw))}</pre>` : '<p class="muted in-none">This node has not seen an event since the page loaded.</p>'}
      <h3>Latency${d.latLabel ? ` · ${esc(d.latLabel)}` : ""}</h3>
      ${
        lats.length
          ? `<div class="in-spark">${sparkSvg(lats)}</div>
             <div class="in-lat num"><span>last <b>${esc(fmtMs(lats.at(-1)))}</b></span><span>median <b>${esc(fmtMs(med))}</b></span><span>max <b>${esc(fmtMs(sorted.at(-1)))}</b></span><span>n=${lats.length}</span></div>`
          : '<p class="muted in-none">No measured latency yet. Only real timings are shown here.</p>'
      }
      <h3>Wired from</h3><div class="in-chips">${chips(ins)}</div>
      <h3>Wired to</h3><div class="in-chips">${chips(outs)}</div>`;
  }

  _updateAge(state) {
    const el = this.insp.querySelector(".in-age");
    const d = this.node && this.api.data(this.node);
    if (el && d?.raw && state) el.textContent = `${ageText(state.t - d.raw.t)} ago`;
  }
}
