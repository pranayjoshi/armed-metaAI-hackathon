// Cards: the proposal history of this session (docs/SELECTION_API.md, docs/UI.md §4).
// One row per task: the cards offered, what the person did to each (stepped past, selected, accepted, refused,
// lapsed), time to motion, the ranking source and the mode. Built only from runtime events (`selection.*`,
// `task.*`), seeded from store.history and kept live from the bus. Nothing is invented: a value the events did
// not carry is shown as "—", and the tally never extrapolates (n is always printed).

const BY = {
  twitch: "twitch", double_twitch: "double twitch", brace: "brace", open_hand: "open hand",
  ui_accept: "click (accept)", ui_step: "click (next)", ui_dismiss: "click (refuse)", ui: "click",
};
const by = (b) => BY[b] || (b ? String(b).replaceAll("_", " ") : "input");
const OUTCOME = {
  completed: ["completed", "ok"], dismissed: ["every card refused", "brace"], stopped: ["stopped", "brace"], fault: ["fault", "brace"],
  cancelled: ["cancelled", ""], interrupted: ["interrupted", ""], incomplete: ["incomplete", ""],
};
const fmtS = (v) => (v == null || !Number.isFinite(v) ? "—" : `${v < 10 ? v.toFixed(1) : Math.round(v)} s`);

const SELECTION_KINDS = new Set(["selection.shown", "selection.step", "selection.select", "selection.announced", "selection.unselected", "selection.lapsed", "selection.accept", "selection.dismiss", "selection.degraded", "task.completed", "task.abandoned"]);

export class CardsView {
  constructor(host, ctx, { readOnly = false } = {}) {
    this.ctx = ctx;
    this.ro = readOnly;
    this.host = host;
    this.tasks = new Map(); // task id -> record
    this.order = [];        // task ids, oldest first
    this.rows = new Map();  // task id -> { el, sig }
    this.pendingDegrade = null;
    this.seq = -1;
    this.sel = null;        // latest state.selection
    this.snap = null;
    this.sig = "";
    this.seeded = false;
    host.innerHTML = `
      <section class="panel cd-head">
        <div class="cd-top">
          <div><span class="kicker">Cards</span><h2>Proposal history</h2></div>
          <div class="cd-status" data-r="status"></div>
        </div>
        <div class="cd-tally" data-r="tally" role="group" aria-label="Tally over the tasks listed below"></div>
      </section>
      <section class="panel cd-list-wrap">
        <div class="cd-empty" data-r="empty" hidden></div>
        <ol class="cd-list" data-r="list" aria-label="Tasks, newest first"></ol>
      </section>`;
    this.r = {};
    host.querySelectorAll("[data-r]").forEach((n) => (this.r[n.dataset.r] = n));
    // seed from everything the page has seen, then follow the bus
    for (const e of ctx.store.history) this.ingest(e, true);
    this.seeded = true;
    this.off = ctx.bus.on("event", (e) => {
      if (!this.ingest(e, false)) return;
      this.dirty = true;
      if (this.visible) this.paintRows();
    });
    this.dirty = true;
  }

  dispose() { this.off?.(); }

  // ── ingest ──────────────────────────────────────────────────────────────
  task(id, e) {
    let t = this.tasks.get(id);
    if (!t) {
      t = { id, mode: e?.mode || null, offers: [], acts: [], labels: new Map(), done: null, t0: e?.t ?? null, degraded: this.pendingDegrade, upgraded: false, source: null, firstCard: null };
      this.pendingDegrade = null;
      this.tasks.set(id, t);
      this.order.push(id);
      if (this.order.length > 200) { const old = this.order.shift(); this.tasks.delete(old); this.rows.get(old)?.el.remove(); this.rows.delete(old); }
    }
    return t;
  }

  /** @returns {boolean} true when the event changed a row */
  ingest(e, seed) {
    if (!SELECTION_KINDS.has(e.kind) || e.seq <= this.seq) return false;
    this.seq = e.seq;
    if (e.kind === "selection.degraded") {
      const open = [...this.order].reverse().map((i) => this.tasks.get(i)).find((t) => !t.done);
      const d = { note: e.note || "", from: e.from, to: e.to };
      if (open) open.degraded = d; else this.pendingDegrade = d;
      return !!open;
    }
    if (e.task == null) return false;
    const t = this.task(e.task, e);
    if (e.mode && !t.mode) t.mode = e.mode;
    const label = (id) => (id && t.labels.get(id)) || null;
    switch (e.kind) {
      case "selection.shown": {
        const ids = e.cards || [], labs = e.labels || [];
        ids.forEach((id, i) => labs[i] != null && t.labels.set(id, labs[i]));
        const cursor = Number.isInteger(e.cursor) ? e.cursor : 0;
        const prev = t.offers.find((o) => o.menu === e.menu);
        if (prev) { // the same menu shown again: Muse reordered the cards behind the highlighted one
          prev.ids = ids; prev.labels = labs; prev.cursor = cursor;
          if (e.upgraded) { prev.upgraded = true; t.upgraded = true; }
        } else t.offers.push({ menu: e.menu, ids, labels: labs, cursor, source: e.source ?? null, upgraded: !!e.upgraded, t: e.t });
        if (e.upgraded) t.upgraded = true;
        t.source = e.source ?? t.source;
        if (t.firstCard == null && ids.length) t.firstCard = ids[Math.min(cursor, ids.length - 1)];
        break;
      }
      case "selection.step":
        t.acts.push(e.ok === false ? { k: "refused-input", by: e.by, text: `step refused${e.detail ? `: ${e.detail}` : ""}` } : { k: e.back ? "back" : "step", by: e.by, label: e.label || label(e.card) });
        if (e.card && e.label) t.labels.set(e.card, e.label);
        break;
      case "selection.select":
        if (e.card && e.label) t.labels.set(e.card, e.label);
        t.acts.push(e.ok === false ? { k: "refused-input", by: e.by, text: `select refused${e.detail ? `: ${e.detail}` : ""}` } : { k: "select", by: e.by, label: e.label || label(e.card) });
        break;
      case "selection.announced":
        t.acts.push({ k: "announced", label: label(e.card) });
        break;
      case "selection.unselected":
        t.acts.push({ k: "unselected", label: label(e.card), text: e.reason });
        break;
      case "selection.lapsed":
        t.acts.push({ k: "lapsed", label: label(e.card) });
        break;
      case "selection.accept":
        if (e.ok === false) t.acts.push({ k: "refused-input", by: e.by, text: `accept refused${e.detail ? `: ${e.detail}` : ""}` });
        else t.acts.push({ k: "accept", by: e.by, label: label(e.card), answered: e.answered });
        break;
      case "selection.dismiss":
        t.acts.push(e.ok === false ? { k: "refused-input", by: e.by, text: `refuse refused${e.detail ? `: ${e.detail}` : ""}` } : { k: "dismiss", by: e.by, label: label(e.card), remaining: e.remaining });
        break;
      case "task.completed":
      case "task.abandoned":
        t.done = { ...e, completed: e.kind === "task.completed" };
        t.mode = e.mode || t.mode;
        break;
      default:
    }
    t.dirty = true;
    return true;
  }

  // ── render ──────────────────────────────────────────────────────────────
  /** Per snapshot. `visible` is whether this pane is on screen. */
  render(state, visible) {
    this.visible = visible;
    this.snap = state;
    this.sel = state.selection || null;
    this.paintHead();
    if (visible) this.paintRows();
  }

  stats() {
    const done = this.order.map((i) => this.tasks.get(i)).filter((t) => t.done?.completed);
    const n = done.length;
    const aband = this.order.filter((i) => this.tasks.get(i).done && !this.tasks.get(i).done.completed).length;
    const sel = done.map((t) => t.done.selections).filter(Number.isFinite);
    const mot = done.map((t) => t.done.to_motion_s).filter(Number.isFinite);
    const hits = done.filter((t) => t.firstCard != null && t.done.card != null && t.done.card === t.firstCard).length;
    const known = done.filter((t) => t.firstCard != null && t.done.card != null).length;
    return {
      n, aband,
      meanSel: sel.length ? sel.reduce((a, b) => a + b, 0) / sel.length : null, nSel: sel.length,
      meanMot: mot.length ? mot.reduce((a, b) => a + b, 0) / mot.length : null, nMot: mot.length,
      hit: known ? hits / known : null, hits, known,
    };
  }

  paintHead() {
    const { esc } = this.ctx;
    const s = this.snap, sel = this.sel;
    const chips = [];
    if (this.ro) chips.push(`<span class="chip warn"><i></i>SIMULATION · read-only${s?.label && s.label !== "SIMULATION" ? ` · ${esc(s.label)}` : ""}</span>`);
    if (s?.emg && (s.emg.source === "synthetic" || s.emg.hardware === false)) chips.push('<span class="chip warn"><i></i>SYNTHETIC EMG</span>');
    if (s?.arm && s.arm.hardware === false) chips.push('<span class="chip warn"><i></i>VIRTUAL ARM</span>');
    if (!sel) chips.push('<span class="chip"><i></i>selection: not in this snapshot</span>');
    else if (!sel.enabled) chips.push('<span class="chip"><i></i>selection off</span>');
    else {
      chips.push(`<span class="chip ok"><i></i>selection on · ${esc(sel.mode || "—")}</span>`);
      if (sel.degraded) chips.push(`<span class="chip bad" title="${esc(sel.degraded)}"><i></i>degraded → scanning</span>`);
      if (sel.blocked) chips.push(`<span class="chip" title="${esc(sel.blocked)}"><i></i>${esc(sel.blocked)}</span>`);
    }
    const html = chips.join("");
    if (this._status !== html) this.r.status.innerHTML = this._status = html;

    const st = this.stats();
    const stat = (k, v, sub, tone = "") => `<div class="cd-stat ${tone}"><b class="num">${v}</b><span>${k}</span><em class="num">${sub}</em></div>`;
    const tally = [
      stat("tasks completed", String(st.n), `${st.aband} abandoned`),
      stat("mean selections per task", st.meanSel == null ? "—" : st.meanSel.toFixed(1), `n = ${st.nSel}`),
      stat("top-1 hit rate", st.hit == null ? "—" : `${Math.round(st.hit * 100)}%`, `${st.hit == null ? "" : `${st.hits} of ${st.known} · `}n = ${st.known}`, "grip"),
      stat("mean time to motion", st.meanMot == null ? "—" : fmtS(st.meanMot), `n = ${st.nMot}`),
    ].join("") + '<p class="cd-note">Over the tasks listed below, from the runtime’s recent events and everything since this page loaded. Top-1: the accepted card was the first card shown.</p>';
    if (this._tally !== tally) this.r.tally.innerHTML = this._tally = tally;
  }

  emptyText() {
    const sel = this.sel;
    if (this.ro) return ["No cards in this replay.", "The scripted demo runs with selection off, so no proposal is ever shown here."];
    if (sel && sel.enabled) return ["No cards yet.", `Selection is on (${sel.mode || "—"}). ${sel.blocked ? `Held: ${sel.blocked}.` : "Arm the arm and answer a card; its task appears here."}`];
    return ["No cards yet.", "Enable selection (selection.enabled: true) and arm the arm."];
  }

  paintRows() {
    if (!this.dirty && this.seeded === "done") return;
    const empty = this.order.length === 0;
    this.r.empty.hidden = !empty;
    this.r.list.hidden = empty;
    if (empty) {
      const [a, b] = this.emptyText();
      const html = `<b>${this.ctx.esc(a)}</b><span>${this.ctx.esc(b)}</span>`;
      if (this._empty !== html) this.r.empty.innerHTML = this._empty = html;
    }
    const fresh = this.seeded === "done";
    // oldest first so each new row is prepended above the previous ones
    for (const id of this.order) {
      const t = this.tasks.get(id);
      let row = this.rows.get(id);
      const sig = this.rowSig(t);
      if (!row) {
        const el = document.createElement("li");
        el.className = "cd-row";
        el.dataset.task = String(id);
        this.r.list.prepend(el);
        row = { el, sig: "" };
        this.rows.set(id, row);
        if (fresh && !this.ctx.reduced) el.classList.add("fresh");
      }
      if (row.sig !== sig) {
        row.sig = sig;
        row.el.innerHTML = this.rowHtml(t);
        row.el.dataset.state = t.done ? (t.done.completed ? "ok" : "ended") : "live";
        if (fresh && row.el.classList.contains("fresh") === false && !this.ctx.reduced) { row.el.classList.remove("bump"); void row.el.offsetWidth; row.el.classList.add("bump"); }
      }
    }
    this.seeded = "done";
    this.dirty = false;
  }

  rowSig(t) { return `${t.acts.length}|${t.offers.map((o) => o.menu + o.ids.join()).join(";")}|${t.done ? t.done.seq : "live"}|${t.upgraded}|${t.degraded?.note || ""}|${t.mode}`; }

  rowHtml(t) {
    const { esc } = this.ctx;
    const o0 = t.offers[0];
    const lastOffer = t.offers[t.offers.length - 1];
    const d = t.done;
    const accepted = d?.completed ? d.card : null;
    const [oText, oTone] = d ? (OUTCOME[d.outcome] || [String(d.outcome || "ended").replaceAll("_", " "), ""]) : ["in progress", "live"];
    // the cards offered: the last menu the person saw, the first card marked
    const offered = lastOffer
      ? lastOffer.ids.map((id, i) => {
        const lab = lastOffer.labels[i] ?? t.labels.get(id) ?? id;
        const first = t.firstCard != null && id === t.firstCard;
        return `<span class="cd-card${id === accepted ? " acc" : ""}${first ? " first" : ""}">${esc(lab)}${first ? '<i title="the first card shown">first</i>' : ""}</span>`;
      }).join("")
      : '<span class="muted">—</span>';
    // what the person did
    const acts = t.acts.map((a) => {
      const who = a.by ? `<u>${esc(by(a.by))}</u>` : "";
      const L = a.label ? ` “${esc(a.label)}”` : "";
      switch (a.k) {
        case "step": return `<li class="a-step">${who} stepped to${L}</li>`;
        case "back": return `<li class="a-step">${who} went back to the menu</li>`;
        case "select": return `<li class="a-sel">${who} selected${L} and asked to confirm</li>`;
        case "announced": return `<li class="a-sel muted">asked “confirm?”${L ? "" : ""}</li>`;
        case "unselected": return `<li class="a-lapse">select undone${a.text ? `: ${esc(a.text)}` : ""}</li>`;
        case "lapsed": return `<li class="a-lapse">no answer, lapsed back to the menu</li>`;
        case "accept": return `<li class="a-acc">${who} accepted${L}${a.answered === false ? " (still waiting for squeeze, yes or click)" : ""}</li>`;
        case "dismiss": return `<li class="a-ref">${who} refused${L}${a.remaining != null ? ` · ${a.remaining} left` : ""}</li>`;
        default: return `<li class="a-no">${who} ${esc(a.text || "refused")}</li>`;
      }
    }).join("") || `<li class="muted">${d ? "no input recorded" : "waiting for the person"}</li>`;
    const modeTag = t.mode === "scanning" ? `<span class="cd-tag scan" title="fixed scene order, no model">scanning</span>` : `<span class="cd-tag">${esc(t.mode || "—")}</span>`;
    const src = t.source ? `<span class="cd-tag${/muse/i.test(t.source) ? " muse" : ""}" title="the ranking source reported by the runtime">source ${esc(t.source)}</span>` : "";
    const up = t.upgraded ? '<span class="cd-tag muse" title="Muse reordered the cards behind the highlighted one">Muse reordered behind the highlight</span>' : "";
    const deg = t.degraded ? `<span class="cd-tag bad" title="${esc(t.degraded.note)}">degraded${t.degraded.to ? ` → ${esc(t.degraded.to)}` : ""}${t.degraded.note ? ` · ${esc(t.degraded.note)}` : ""}</span>` : "";
    const counts = d?.counts ? Object.entries(d.counts).filter(([, n]) => n > 0).map(([k, n]) => `${n}× ${esc(by(k))}`).join(" · ") : "";
    return `<div class="cd-id"><b class="num">#${esc(t.id)}</b><span class="cd-out ${oTone}">${esc(oText)}</span></div>
      <div class="cd-body">
        <div class="cd-tags">${modeTag}${src}${up}${deg}${d?.reason ? `<span class="cd-tag" title="reason">${esc(d.reason)}</span>` : ""}</div>
        <div class="cd-offered"><span class="cd-k">offered</span>${offered}</div>
        <div class="cd-did"><span class="cd-k">did</span><ol>${acts}</ol></div>
      </div>
      <div class="cd-side">
        <dl class="cd-num">
          <dt>to motion</dt><dd class="num">${fmtS(d?.to_motion_s)}</dd>
          <dt>selections</dt><dd class="num">${d?.selections ?? "—"}</dd>
          <dt>elapsed</dt><dd class="num">${fmtS(d?.elapsed_s)}</dd>
        </dl>
        ${counts ? `<p class="cd-inputs num">${counts}</p>` : ""}
      </div>`;
  }
}
