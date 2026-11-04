// Film mode for Stage (docs/UI.md §4): the proof layer the camera records beside the arm.
// The two EMG traces burned in large with a shared timecode, the four counters, the current card mirrored small,
// and the robot's sentence as a caption. Everything is read from the /api/live snapshot or a runtime event.
// Nothing is invented: before the first snapshot, and wherever a value does not exist, the layer says "—".

const tc = (sec) => {
  if (!Number.isFinite(sec)) return "—";
  const neg = sec < 0;
  const ms = Math.floor(Math.abs(sec) * 1000);
  const m = Math.floor(ms / 60000), s = Math.floor((ms % 60000) / 1000), r = ms % 1000;
  return `${neg ? "−" : ""}${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(r).padStart(3, "0")}`;
};
const tcShort = (sec) => {
  const a = Math.abs(Math.round(sec));
  return `${sec < 0 ? "−" : ""}${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}`;
};
const wordCount = (text) => String(text ?? "").split(/\s+/).filter(Boolean).length;

/**
 * Counters from real runtime events since this page loaded.
 * The page's load moment on the runtime clock is `state.t - performance.now()/1000` (performance.now() counts from
 * navigation start), so a late mount still counts everything since load from `store.history`, then follows the bus.
 * `store.history` holds at most 400 events: when it has already dropped events from after the page load, the
 * numbers are lower bounds and `lowerBound` says so.
 */
export class Tally {
  constructor(ctx) {
    this.ctx = ctx;
    this.n = { accepts: 0, tasks: 0, flinches: 0, rules: 0, words: 0, typed: 0 };
    this.seq = -1;
    this.t0 = null;
    this.lowerBound = false;
    const { store, bus } = ctx;
    if (store.state) {
      this.t0 = store.state.t - performance.now() / 1000;
      const hist = store.history;
      for (const e of hist) if (e.t >= this.t0) this.add(e);
      this.lowerBound = hist.length >= 400 && hist[0].t > this.t0 + 0.5;
    }
    // No snapshot yet: the first frame is history from before this page loaded and is never counted.
    this.off = bus.on("event", (e) => this.add(e));
  }

  add(e) {
    if (e.seq <= this.seq) return;
    this.seq = e.seq;
    const n = this.n;
    switch (e.kind) {
      case "selection.accept": if (e.ok === true) n.accepts++; break;
      case "task.completed": n.tasks++; break;
      case "emg.brace": n.flinches++; break;
      case "rule.approved": n.rules++; break;
      case "speech.utterance":
        if (e.source === "typed") n.typed += wordCount(e.text);
        else n.words += wordCount(e.text); // heard by the microphone
        break;
      default:
    }
  }

  dispose() { this.off?.(); }
}

const CONF = { likely: "likely", maybe: "maybe", unsure: "unsure" };

export class Film {
  constructor(stg, ctx, { onToggle } = {}) {
    this.ctx = ctx;
    this.stg = stg;
    this.on = false;
    this.onToggle = onToggle;
    this.t0 = null;       // runtime clock (state.t) when film mode started
    this.recvT = performance.now();
    this.s = null;
    this.tally = new Tally(ctx);
    const view = stg.querySelector(".stg-view");
    const main = stg.querySelector(".stg-main");
    view.insertAdjacentHTML("beforeend", `
      <div class="fm-card" data-f="card" hidden></div>
      <div class="fm-cap" data-f="cap" hidden></div>`);
    main.insertAdjacentHTML("beforeend", `
      <section class="fm" data-f="band" hidden aria-label="Film proof layer: EMG traces, timecode and counters">
        <div class="fm-tc">
          <span class="fm-k">timecode</span>
          <b class="fm-tc-big num" data-f="tc">—</b>
          <span class="fm-sub num" data-f="rt">runtime t —</span>
          <div class="fm-tags" data-f="tags"></div>
        </div>
        <div class="fm-tr"><canvas data-f="cv" aria-label="Flexor and extensor EMG traces with brace overlay"></canvas></div>
        <div class="fm-ct">
          <div class="fm-c"><b class="num" data-c="selections">—</b><span>selections</span><em data-c-sub="selections"></em></div>
          <div class="fm-c brace"><b class="num" data-c="flinches">—</b><span>flinches</span><em data-c-sub="flinches"></em></div>
          <div class="fm-c muse"><b class="num" data-c="rules">—</b><span>rules learned</span><em data-c-sub="rules"></em></div>
          <div class="fm-c"><b class="num" data-c="words">—</b><span>words spoken to the robot</span><em data-c-sub="words"></em></div>
          <p class="fm-def" data-f="def" hidden><b>Words spoken to the robot</b> = words addressed to the arm: what the microphone heard as requests, counted as words. Typed requests are listed separately.</p>
          <p class="fm-note" data-f="note">since this page loaded</p>
        </div>
      </section>`);
    this.f = {};
    stg.querySelectorAll("[data-f]").forEach((n) => (this.f[n.dataset.f] = n));
    this.c = {};
    stg.querySelectorAll("[data-c]").forEach((n) => (this.c[n.dataset.c] = n));
    this.csub = {};
    stg.querySelectorAll("[data-c-sub]").forEach((n) => (this.csub[n.dataset.cSub] = n));
    const css = getComputedStyle(stg);
    this.col = (n, fb) => css.getPropertyValue(n).trim() || fb;
    this.mono = css.getPropertyValue("--font-mono") || "monospace";
    this.h = {};
  }

  set(on) {
    on = !!on;
    if (on === this.on) return;
    this.on = on;
    this.stg.dataset.film = on ? "1" : "0";
    this.f.band.hidden = !on;
    this.f.cap.hidden = !on;
    this.f.card.hidden = !on;
    this.t0 = null; // the timecode starts at the first snapshot after this moment
    if (on) this.showDefinition();
    this.onToggle?.(on);
    if (on && this.s) this.update(this.s, this.sentence);
  }
  toggle() { this.set(!this.on); }

  /** The definition of "words spoken to the robot" is shown the first time the counters appear, then folds away. */
  showDefinition() {
    let seen = false;
    try { seen = sessionStorage.getItem("h2m.film.def") === "1"; } catch { /* storage blocked: show it every time */ }
    if (seen || this.defShown) return;
    this.defShown = true;
    this.f.def.hidden = false;
    setTimeout(() => {
      this.f.def.hidden = true;
      try { sessionStorage.setItem("h2m.film.def", "1"); } catch { /* ignore */ }
    }, 12000);
  }

  /** Only touch the DOM when the text changed. */
  put(el, html) {
    if (this.h[el.dataset.f || el.dataset.c || el.dataset.cSub] === html) return;
    this.h[el.dataset.f || el.dataset.c || el.dataset.cSub] = html;
    el.innerHTML = html;
  }

  /** Per snapshot (~10 Hz). `sentence` is the arm's current sentence from Stage. */
  update(s, sentence) {
    this.s = s;
    this.sentence = sentence;
    this.recvT = performance.now();
    // state.reply is the robot's last sentence and stays set until the next one. The page can only tell how old it
    // is from when it saw the text change, so a reply it has not seen change is never presented as current.
    const said = s.reply ? String(s.reply) : "";
    if (this.lastReply === undefined) { this.lastReply = said; this.replyAt = -Infinity; }
    else if (said !== this.lastReply) { this.lastReply = said; this.replyAt = this.recvT; }
    if (!this.on) return;
    const { esc } = this.ctx;
    if (this.t0 == null && Number.isFinite(s.t)) this.t0 = s.t;

    // tags: what is synthetic / virtual / simulated, wherever it drives the visual
    const tags = [];
    if (this.ctx.mode === "replay") tags.push(["SIMULATION", s.label && s.label !== "SIMULATION" ? ` · ${s.label}` : ""]);
    if (s.emg && (s.emg.source === "synthetic" || s.emg.hardware === false)) tags.push(["SYNTHETIC EMG", ""]);
    if (s.arm && s.arm.hardware === false) tags.push(["VIRTUAL ARM", ""]);
    if (s.vision?.virtual) tags.push(["VIRTUAL TABLE", ""]);
    this.put(this.f.tags, tags.map(([t, x]) => `<span class="fm-tag">${esc(t)}${esc(x)}</span>`).join(""));

    // counters
    const n = this.tally.n;
    const lb = this.tally.lowerBound ? "≥ " : "";
    const sel = s.selection;
    this.setC("selections", `${lb}${n.accepts}`, sel && !sel.enabled ? "cards off" : `${lb}${n.tasks} task${n.tasks === 1 ? "" : "s"} completed`);
    this.setC("flinches", `${lb}${n.flinches}`, "brace events");
    this.setC("rules", `${lb}${n.rules}`, "rules approved");
    this.setC("words", `${lb}${n.words}`, n.typed ? `voice only · ${n.typed} typed` : "voice only");
    this.put(this.f.note, this.tally.lowerBound ? "since this page loaded · lower bounds (the event window overflowed)" : "since this page loaded");

    this.renderCard(s);
    this.renderCaption(s, sentence);
  }

  /** Blank layer for a replay server with no simulation: nothing to prove. */
  updateEmpty() {
    this.s = null;
    if (!this.on) return;
    for (const k of Object.keys(this.c)) this.setC(k, "—", "");
    this.put(this.f.tags, "");
    this.put(this.f.card, '<span class="fm-k">card</span><em class="dim">—</em>');
    this.put(this.f.cap, '<span class="fm-k">robot</span><strong class="dim">—</strong>');
    this.f.tc.textContent = "—";
    this.f.rt.textContent = "runtime t —";
  }

  setC(k, v, sub) {
    if (this.c[k].textContent !== v) this.c[k].textContent = v;
    if (this.csub[k].textContent !== sub) this.csub[k].textContent = sub;
  }

  renderCard(s) {
    const { esc } = this.ctx;
    const sel = s.selection;
    let html;
    if (!sel) html = `<span class="fm-k">card</span><em class="dim">—</em>`;
    else if (!sel.enabled) html = `<span class="fm-k">card</span><em class="dim">selection is off</em>`;
    else if (!(sel.cards || []).length) {
      html = `<span class="fm-k">card · ${esc(sel.mode || "—")}</span><em class="dim">${esc(sel.blocked || "no cards showing")}</em>`;
    } else {
      const hi = sel.cards.find((c) => c.highlighted) || sel.cards[0];
      const rest = sel.cards.filter((c) => c !== hi);
      const conf = CONF[hi.confidence] || hi.confidence;
      html = `<span class="fm-k">card · ${esc(sel.mode || "—")}${sel.degraded ? " · degraded" : ""}</span>
        <strong>${esc(hi.label)}</strong>
        <span class="fm-conf">${esc(conf || "—")}${sel.confirming === hi.id ? " · confirm?" : ""}</span>
        ${rest.length ? `<ul>${rest.map((c) => `<li>${esc(c.label)}</li>`).join("")}</ul>` : ""}`;
    }
    this.put(this.f.card, html);
  }

  renderCaption(s, sentence) {
    const { esc } = this.ctx;
    const state = s.arm?.state || "—";
    const said = s.reply ? String(s.reply) : "";
    const fresh = said && performance.now() - this.replyAt < 9000;
    // idle prompts ("Say what you need.") are the console's help text, not the robot speaking: leave them out of the film
    const line = String(sentence || "").replace(/\s*(Say what you need\.|Arm it to allow motion\.)$/, "");
    const html = fresh
      ? `<span class="fm-k">robot says · ${esc(state)}</span><strong${said.length > 56 ? ' class="long"' : ""}>“${esc(said)}”</strong>`
      : `<span class="fm-k">${esc(state)}</span><strong${line.length > 56 ? ' class="long"' : ""}>${esc(line || "—")}</strong>`;
    this.put(this.f.cap, html);
  }

  // ── per frame: timecode + traces ───────────────────────────────────────────
  frame() {
    if (!this.on) return;
    const s = this.s;
    if (!s) { this.draw(null, 0); return; } // no snapshot yet, or a replay with no simulation: empty lanes, nothing drawn into them
    if (this.t0 == null) return;
    if (this.s) this.renderCaption(this.s, this.sentence); // memoised; ages a reply out of the caption without waiting for a snapshot
    const sinceRecv = Math.min(0.25, (performance.now() - this.recvT) / 1000);
    const nowT = s.t + sinceRecv;
    const el = tc(nowT - this.t0);
    if (this.f.tc.textContent !== el) this.f.tc.textContent = el;
    const rt = `runtime t ${nowT.toFixed(3)} s`;
    if (this.f.rt.textContent !== rt) this.f.rt.textContent = rt;
    this.draw(s, nowT);
  }

  draw(s, nowT) {
    const c = this.f.cv;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const w = Math.round(c.clientWidth * dpr), h = Math.round(c.clientHeight * dpr);
    if (!w || !h) return;
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    const g = c.getContext("2d");
    g.clearRect(0, 0, w, h);
    const e = s?.emg;
    const grip = this.col("--grip", "#4fd8ff"), open = this.col("--open", "#ffc15a"), brace = this.col("--brace", "#ff4d5e");
    const ink3 = "rgba(170,182,198,.7)";
    const px = (v) => v * dpr;
    const padL = px(8), padR = px(8), top = px(4), axisH = px(16), gap = px(8);
    const braceH = px(40);
    const laneH = (h - top - axisH - braceH - gap * 2) / 2;
    const lanes = [
      { y: top, h: laneH, col: grip, label: "FLEXOR · GRIP", thr: e?.confirm_effort, thrLabel: "confirm" },
      { y: top + laneH + gap, h: laneH, col: open, label: "EXTENSOR · OPEN", thr: e?.stop_effort, thrLabel: "stop" },
      { y: top + laneH * 2 + gap * 2, h: braceH, col: brace, label: "BRACE", thr: e?.brace_effort, thrLabel: "brace" },
    ];
    // lane backgrounds + gridlines
    for (const L of lanes) {
      g.fillStyle = "rgba(0,0,0,.28)"; g.fillRect(padL, L.y, w - padL - padR, L.h);
      g.strokeStyle = "rgba(160,190,230,.12)"; g.lineWidth = dpr;
      for (const f of [0.5, 1]) { const yy = L.y + L.h - f * L.h; g.beginPath(); g.moveTo(padL, yy); g.lineTo(w - padR, yy); g.stroke(); }
    }
    const tr = e?.trace || [], bt = e?.brace_trace || [];
    if (!e || (!tr.length && !bt.length)) {
      g.fillStyle = ink3; g.font = `${px(13)}px ${this.mono}`; g.textAlign = "center";
      g.fillText(e ? "waiting for EMG samples" : s ? "no EMG source" : "no simulation running", w / 2, h / 2);
      g.textAlign = "left";
      return;
    }
    const tEndData = Math.max(tr.length ? tr[tr.length - 1][0] : 0, bt.length ? bt[bt.length - 1][0] : 0);
    const tStartData = Math.min(tr.length ? tr[0][0] : tEndData, bt.length ? bt[0][0] : tEndData);
    const span = Math.max(1, tEndData - tStartData);
    // the window slides with wall time between snapshots (clamped to a quarter second)
    const tEnd = Math.min(nowT, tEndData + 0.25);
    const X = (tt) => padL + (w - padL - padR) - ((tEnd - tt) / span) * (w - padL - padR);
    const Y = (L, v) => L.y + L.h - 1 - Math.max(0, Math.min(1, v)) * (L.h - 2);

    // brace-active band across the two EMG lanes (the overlay), where the brace trace is over its threshold
    const bThr = e.brace_effort;
    if (bt.length > 1 && Number.isFinite(bThr)) {
      g.fillStyle = "rgba(255,77,94,.17)";
      let a = null;
      for (let i = 0; i < bt.length; i++) {
        const on = bt[i][1] >= bThr;
        if (on && a == null) a = bt[i][0];
        if ((!on || i === bt.length - 1) && a != null) {
          const x0 = X(a), x1 = X(bt[i][0]);
          g.fillRect(x0, lanes[0].y, Math.max(px(2), x1 - x0), lanes[1].y + lanes[1].h - lanes[0].y);
          a = null;
        }
      }
    }
    // thresholds
    g.setLineDash([px(4), px(5)]);
    for (const L of lanes) {
      if (!Number.isFinite(L.thr)) continue;
      g.strokeStyle = L.col; g.globalAlpha = 0.45; g.beginPath(); g.moveTo(padL, Y(L, L.thr)); g.lineTo(w - padR, Y(L, L.thr)); g.stroke(); g.globalAlpha = 1;
    }
    g.setLineDash([]);
    // traces
    const line = (L, rows, idx, width, fill) => {
      if (rows.length < 2) return;
      g.beginPath();
      rows.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(L, p[idx])) : g.moveTo(X(p[0]), Y(L, p[idx]))));
      if (fill) {
        g.save();
        g.lineTo(X(rows[rows.length - 1][0]), L.y + L.h); g.lineTo(X(rows[0][0]), L.y + L.h); g.closePath();
        const gr = g.createLinearGradient(0, L.y, 0, L.y + L.h); gr.addColorStop(0, L.col + "66"); gr.addColorStop(1, L.col + "00");
        g.fillStyle = gr; g.fill(); g.restore();
        g.beginPath();
        rows.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(L, p[idx])) : g.moveTo(X(p[0]), Y(L, p[idx]))));
      }
      g.strokeStyle = L.col; g.lineWidth = px(width); g.lineJoin = "round"; g.stroke();
    };
    line(lanes[0], tr, 1, 2.4, true);
    line(lanes[1], tr, 2, 2.4, true);
    line(lanes[2], bt, 1, 2.2, true);

    // event markers (real events only): the person's inputs, in the colour of the signal that made them
    const MARK = { "emg.twitch": ["TWITCH", grip], "emg.double_twitch": ["2× TWITCH", grip], "emg.brace": ["BRACE", brace], "emg.open_hand": ["OPEN", open], "emg.squeeze": ["SQUEEZE", grip] };
    g.font = `600 ${px(10)}px ${this.mono}`;
    const lo = tEnd - span;
    let lastX = -1e9, row = 0;
    for (const ev of this.ctx.store.history) {
      const m = MARK[ev.kind];
      if (!m || ev.t < lo || ev.t > tEnd) continue;
      const x = X(ev.t);
      row = x - lastX < px(80) ? (row + 1) % 3 : 0; // close markers stack their labels instead of overprinting
      lastX = x;
      g.strokeStyle = m[1]; g.globalAlpha = 0.85; g.lineWidth = px(1.5);
      g.beginPath(); g.moveTo(x, lanes[0].y); g.lineTo(x, lanes[2].y + lanes[2].h); g.stroke();
      g.globalAlpha = 1; g.fillStyle = m[1];
      const left = x > w - px(90);
      g.textAlign = left ? "right" : "left";
      g.fillText(m[0], x + (left ? -px(4) : px(4)), lanes[0].y + px(12) + row * px(12));
    }
    g.textAlign = "left";

    // lane labels, burned in
    g.font = `700 ${px(12)}px ${this.mono}`;
    for (const L of lanes) {
      g.fillStyle = L.col; g.globalAlpha = 0.95;
      g.fillText(L.label, padL + px(8), L.y + px(L === lanes[2] ? 14 : 16));
      g.globalAlpha = 1;
    }
    // honest tag on the traces themselves
    const synth = e.source === "synthetic" || e.hardware === false;
    g.textAlign = "right";
    g.font = `700 ${px(12)}px ${this.mono}`;
    g.fillStyle = synth ? this.col("--warn", "#ffc15a") : ink3;
    g.fillText(synth ? "SYNTHETIC EMG" : `EMG · ${e.profile || "profile —"}`, w - padR - px(8), lanes[0].y + px(16));
    g.textAlign = "left";

    // time axis in the shared timecode (whole seconds since film mode started)
    g.font = `${px(10)}px ${this.mono}`; g.fillStyle = ink3;
    g.strokeStyle = "rgba(170,182,198,.35)"; g.lineWidth = dpr;
    const axisY = h - px(3);
    const first = Math.ceil(lo - this.t0), last = Math.floor(tEnd - this.t0);
    g.textAlign = "center";
    for (let k = first; k <= last; k++) {
      const x = X(this.t0 + k);
      if (x < padL + px(18) || x > w - padR - px(18)) continue;
      g.beginPath(); g.moveTo(x, lanes[2].y + lanes[2].h); g.lineTo(x, lanes[2].y + lanes[2].h + px(4)); g.stroke();
      g.fillText(tcShort(k), x, axisY);
    }
    g.textAlign = "left";
  }
}
