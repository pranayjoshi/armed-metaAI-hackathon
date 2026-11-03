// Sessions: recorded study sessions. List + detail from /api/replay/session/<id>.
// Success rate is computed from the trials' real outcomes; "—" when there are none.
import { Trace } from "./trace.js";
import { esc, getJSON, words, num, shortId, provBadge } from "./util.js";

const EVENT_CAP = 300;
const OUTCOME_TONE = { success: "ok", wrong_action: "bad", wrong_object: "bad", dropped: "bad", crushed: "bad", no_action: "warn", abandoned: "warn" };

export class Sessions {
  constructor(pane, hub) {
    this.pane = pane;
    this.hub = hub;
    this.ctx = hub.ctx;
    this.sel = null;
    this.data = null;
    this.listSig = "";
    this.loadId = 0;
    this.showAll = false;
    this.braceIdx = 0;
  }

  async init() {
    this.pane.innerHTML = `
      <div class="ses">
        <aside class="panel ses-list">
          <div class="panel-head"><h3>Sessions</h3><span class="muted ses-count num"></span></div>
          <div class="ses-cards"></div>
        </aside>
        <section class="ses-detail"></section>
      </div>`;
    this.listEl = this.pane.querySelector(".ses-cards");
    this.detail = this.pane.querySelector(".ses-detail");
    this.listEl.addEventListener("click", (e) => {
      const c = e.target.closest("[data-id]");
      if (c) this.select(c.dataset.id);
    });
    this.detail.addEventListener("click", (e) => {
      const more = e.target.closest("[data-more]");
      if (more) {
        this.showAll = true;
        this.paintEvents();
        return;
      }
      const inc = e.target.closest("[data-inc]");
      if (inc) this.hub.openIncident(inc.dataset.inc);
      const br = e.target.closest("[data-brace]");
      if (br) {
        this.braceIdx = Number(br.dataset.brace);
        this.paintBrace();
      }
    });
    this.renderList();
  }

  show() {
    this.renderList();
    if (!this.sel) {
      const first = this.hub.lib.sessions.find((s) => !s.error);
      if (first) this.select(first.id);
      else this.paintDetail();
    }
    this.pane.querySelectorAll(".ses-list, .ses-detail").forEach((el, i) => {
      el.classList.remove("rise");
      void el.offsetWidth;
      el.style.setProperty("--i", String(i));
      el.classList.add("rise");
    });
    this.trace?.draw();
  }
  hide() {}
  dispose() {
    this.trace?.dispose();
  }

  renderList() {
    const items = this.hub.lib.sessions;
    const sig = JSON.stringify([items, this.sel]);
    if (sig === this.listSig) return;
    this.listSig = sig;
    this.pane.querySelector(".ses-count").textContent = String(items.length);
    if (!items.length) {
      this.listEl.innerHTML = `<div class="empty-mini"><b>No sessions recorded in this folder</b><p>A session is a folder with <code>meta.json</code>, <code>events.jsonl</code> and <code>trials.jsonl</code>, written while recording from the Lab. Try <code>h2m replay data/sessions</code>.</p></div>`;
      return;
    }
    const keep = this.listEl.scrollTop;
    const anim = !this.listAnimated;
    this.listAnimated = true;
    this.listEl.innerHTML = items
      .map((s, i) => {
        if (s.error) return `<div class="icard err" style="--i:${i}"><b class="mono">${esc(s.id)}</b><span class="bad">${esc(s.error)}</span></div>`;
        return `<button class="icard${s.id === this.sel ? " sel" : ""}${anim ? " anim" : ""}" data-id="${esc(s.id)}" style="--i:${Math.min(i, 14)}">
          <div class="ic-top">${provBadge(s.label)}<span class="ic-id num" title="${esc(s.id)}">${esc(shortId(s.id))}</span></div>
          <div class="ic-held">${esc(s.participant || "no participant recorded")}</div>
          <div class="ic-meta"><span class="chip"><i></i><span class="num">${s.trials ?? 0}</span>&nbsp;trials</span><span class="chip"><i></i><span class="num">${s.events ?? 0}</span>&nbsp;events</span>${(s.incidents || []).length ? `<span class="chip muse"><i></i>${s.incidents.length} incident${s.incidents.length > 1 ? "s" : ""}</span>` : ""}</div>
        </button>`;
      })
      .join("");
    this.listEl.scrollTop = keep;
  }

  async select(id) {
    this.sel = id;
    this.showAll = false;
    this.braceIdx = 0;
    const my = ++this.loadId;
    this.listSig = "";
    this.renderList();
    this.detail.innerHTML = `<div class="panel ses-empty"><b>Loading the session…</b></div>`;
    const r = await getJSON(`/api/replay/session/${encodeURIComponent(id)}`);
    if (my !== this.loadId) return;
    this.data = r.ok ? r.body : null;
    this.error = r.ok ? null : r.error;
    this.paintDetail();
  }

  paintDetail() {
    const d = this.data;
    if (!this.hub.lib.sessions.length) {
      this.detail.innerHTML = `<div class="panel ses-empty"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="3.5" width="16" height="17" rx="2.5"/><path d="M8 8.5h8M8 12h8M8 15.5h5"/></svg><b>No sessions recorded in this folder</b><p>Point replay at a folder of sessions: <code>h2m replay data/sessions</code>.</p></div>`;
      return;
    }
    if (!d) {
      this.detail.innerHTML = `<div class="panel ses-empty"><b>This session can’t be read.</b><p>${esc(this.error || "")}</p></div>`;
      return;
    }
    const trials = d.trials || [];
    const counts = {};
    trials.forEach((t) => (counts[t.outcome || "unknown"] = (counts[t.outcome || "unknown"] || 0) + 1));
    const ok = counts.success || 0;
    const rate = trials.length ? ok / trials.length : null;
    const order = ["success", ...Object.keys(counts).filter((k) => k !== "success").sort((a, b) => counts[b] - counts[a])];
    const known = new Set(this.hub.lib.incidents.map((i) => i.id));
    const meta = d.meta || {};
    this.detail.innerHTML = `
      <div class="panel ses-head">
        <div class="sh-id"><span class="kicker">Session</span><h2 class="num">${esc(d.id)}</h2><div class="sh-sub">${provBadge(d.label)}<span class="muted">${esc(d.participant || "no participant recorded")}${meta.started ? ` · started ${esc(meta.started)}` : ""}</span></div>${meta.notes ? `<p class="sh-notes">${esc(meta.notes)}</p>` : ""}</div>
        <div class="sh-stats">
          <div class="stat"><span class="num big">${trials.length}</span><small>trials</small></div>
          <div class="stat"><span class="num big ${rate == null ? "none" : rate >= 0.8 ? "ok" : rate < 0.5 ? "bad" : ""}">${rate == null ? "—" : `${Math.round(rate * 100)}%`}</span><small>${rate == null ? "no trials to rate" : `success · ${ok} of ${trials.length}`}</small></div>
          <div class="stat"><span class="num big">${num(d.events) ?? (d.timeline || []).length}</span><small>events${d.truncated ? " (capped)" : ""}</small></div>
          <div class="stat"><span class="num big">${(d.incidents || []).length}</span><small>incidents</small></div>
        </div>
        ${trials.length ? `<div class="sh-bar" role="img" aria-label="Outcomes">${order.map((k) => `<i class="${OUTCOME_TONE[k] || ""}" style="flex:${counts[k]}" title="${esc(words(k))}: ${counts[k]}"></i>`).join("")}</div>
        <div class="sh-leg">${order.map((k) => `<span class="${OUTCOME_TONE[k] || ""}"><i></i>${esc(words(k))} <b class="num">${counts[k]}</b></span>`).join("")}</div>` : ""}
        ${(d.incidents || []).length ? `<div class="sh-inc"><span class="kicker">Incidents from this session</span>${d.incidents.map((n) => (known.has(n) ? `<button class="chip muse" data-inc="${esc(n)}"><i></i>${esc(shortId(n))}</button>` : `<span class="chip" title="Not in this replay library"><i></i>${esc(shortId(n))}</span>`)).join("")}</div>` : ""}
      </div>
      <div class="ses-cols">
        <div class="panel ses-trials"><div class="panel-head"><h3>Trials</h3><span class="muted num">${trials.length}</span></div>${this.trialsHtml(trials)}</div>
        <div class="ses-right">
          <div class="panel ses-brace"></div>
          <div class="panel ses-events"></div>
        </div>
      </div>`;
    this.trace?.dispose();
    this.trace = null;
    this.paintBrace();
    this.paintEvents();
  }

  trialsHtml(trials) {
    if (!trials.length) return `<p class="exp-idle">No trials were recorded in this session.</p>`;
    return `<div class="tr-scroll"><table class="tr"><thead><tr><th>#</th><th>expected</th><th>condition</th><th>outcome</th><th class="r">sec</th></tr></thead><tbody>${trials
      .map((t) => {
        const dur = num(t.t_end) != null && num(t.t_start) != null ? (t.t_end - t.t_start).toFixed(1) : "—";
        const tone = OUTCOME_TONE[t.outcome] || "";
        return `<tr><td class="num">${esc(t.trial ?? "")}</td><td>${esc(words(t.expected_action))}${t.expected_object ? ` <span class="muted">${esc(t.expected_object)}</span>` : ""}</td><td class="muted">${esc(t.condition || "—")}</td><td><span class="oc ${tone}"><i></i>${esc(words(t.outcome, "unknown"))}</span></td><td class="num r">${dur}</td></tr>`;
      })
      .join("")}</tbody></table></div>`;
  }

  paintBrace() {
    const host = this.detail.querySelector(".ses-brace");
    if (!host) return;
    const braces = this.data?.braces || [];
    this.trace?.dispose();
    this.trace = null;
    if (!braces.length) {
      host.innerHTML = `<div class="panel-head"><h3>Brace in this session</h3></div><p class="exp-idle">No brace with a stored EMG trace in this session’s events.</p>`;
      return;
    }
    const b = braces[Math.min(this.braceIdx, braces.length - 1)];
    const tr = b.trace || {};
    const t0 = b.t ?? 0;
    const rel = (arr) => (arr || []).map((v) => v - t0);
    const tt = rel(tr.t);
    const onset = b.onset_t != null ? { t: b.onset_t - t0, label: "brace onset" } : null;
    host.innerHTML = `<div class="panel-head"><h3>Brace in this session</h3>${braces.length > 1 ? `<div class="seg">${braces.map((_, i) => `<button data-brace="${i}" aria-pressed="${i === this.braceIdx}">${i + 1}</button>`).join("")}</div>` : `<span class="muted num">${braces.length}</span>`}</div><div class="trc-host ses-trc"></div>`;
    const trc = new Trace(host.querySelector(".trc-host"), { label: "Brace EMG trace" });
    this.trace = trc;
    if (!tt.length) {
      trc.set({ lines: [], empty: "This brace event stored no trace." });
      return;
    }
    trc.set(
      {
        lines: [
          { cls: "open", name: "open", t: tt, v: tr.extensor || [] },
          { cls: "grip", name: "grip", t: tt, v: tr.flexor || [] },
          { cls: "brace", name: "brace", t: tt, v: tr.brace || [] },
        ],
        domain: [Math.min(...tt), Math.max(...tt)],
        onset,
        freeze: { t: 0, label: "brace event" },
        latencyMs: onset ? -onset.t * 1000 : null,
        axis: (v) => (Math.abs(v) < 1e-6 ? "event" : `${v < 0 ? "−" : "+"}${Math.abs(v)}s`),
        zero: 0,
      },
      { animate: true },
    );
  }

  paintEvents() {
    const host = this.detail.querySelector(".ses-events");
    if (!host) return;
    const all = this.data?.timeline || [];
    const rows = this.showAll ? all : all.slice(0, EVENT_CAP);
    const kind = (k) => this.ctx.EVENT_KIND[k] || [k.split(".").pop(), "other"];
    const desc = (e) => {
      try {
        return this.ctx.describeEvent(e) || "";
      } catch {
        return "";
      }
    };
    host.innerHTML = `<div class="panel-head"><h3>Events</h3><span class="muted num">${all.length}${this.data?.truncated ? "+" : ""}</span></div>
      ${all.length ? `<ol class="ev-list ses-ev">${rows
        .map((e) => {
          const [label, cls] = kind(e.kind);
          return `<li class="ev k-${esc(cls)}"><span class="ev-t num">${num(e.t) == null ? "—" : `${e.t.toFixed(1)}s`}</span><span class="ev-k">${esc(label)}</span><span class="ev-l">${esc(desc(e) || e.kind)}</span></li>`;
        })
        .join("")}</ol>${all.length > rows.length ? `<button class="btn sm more" data-more>Show all ${all.length}</button>` : ""}` : `<p class="exp-idle">This session logged no events.</p>`}`;
  }
}
