// Command deck for Stage: say bar, request card, muscle meters, arm strip, activity ticker.
// Everything shown is read from the /api/live snapshot; nothing is invented.

const I = {
  approve: '<path d="M5 12.5l4.6 4.6L19 7.4"/>',
  ask: '<path d="M9.4 9.2a2.7 2.7 0 1 1 4 2.3c-.9.6-1.4 1.1-1.4 2.4"/><path d="M12 17.4v.2"/>',
  hold: '<path d="M7.5 12h9"/>',
  reject: '<path d="M7.5 7.5l9 9M16.5 7.5l-9 9"/>',
};
const VERDICT = { approve: "ok", ask: "warn", hold: "mute", reject: "bad" };
const TICKER_KINDS = new Set(["speech.utterance", "intent", "arm.started", "arm.completed", "arm.stopped", "arm.fault", "emg.squeeze", "emg.open_hand", "emg.brace", "intent.cancelled", "intent.expired", "intent.blocked", "compiler.llm", "incident.triggered", "incident.explained", "rule.approved", "embodied.tool"]);
const R = 26, CIRC = 2 * Math.PI * R;

export const SKELETON = `
<div class="stg" data-focus="0">
  <section class="stg-main">
    <div class="stg-view" data-el="view">
      <div class="tw-labels"></div>
      <div class="tw-tag" data-el="tag"></div>
      <div class="tw-tools">
        <div class="seg" role="group" aria-label="Camera view" data-el="presets">
          <button data-preset="top" aria-pressed="false">Top</button>
          <button data-preset="angled" aria-pressed="true">Angled</button>
          <button data-preset="follow" aria-pressed="false">Follow</button>
        </div>
        <button class="btn tw-filmbtn" data-el="filmbtn" aria-pressed="false" title="Film mode (M): the proof layer the camera records">Film <kbd>M</kbd></button>
        <button class="btn tw-focusbtn" data-el="focusbtn" title="Focus mode (F)">Focus <kbd>F</kbd></button>
      </div>
      <div class="tw-legend" data-el="legend"></div>
      <div class="tw-empty" data-el="empty" hidden></div>
      <div class="tw-freeze" data-el="freeze" hidden></div>
      <div class="tw-prompt" data-el="prompt" hidden></div>
      <div class="tw-sentence" data-el="sentence"></div>
      <figure class="tw-cam" data-el="cam" hidden>
        <img alt="Overhead camera" data-el="camimg" />
        <svg data-el="camsvg" preserveAspectRatio="none"></svg>
        <figcaption data-el="camcap"></figcaption>
      </figure>
    </div>

    <form class="say" data-el="sayform" autocomplete="off">
      <span class="mic" data-el="mic" title="Microphone level"><i></i></span>
      <input class="say-input" data-el="say" placeholder="Say or type a request: “hand me the cup gently”" aria-label="Request" />
      <span class="say-hint" data-el="sayhint"></span>
      <button class="btn primary say-send" type="submit">Send</button>
    </form>

    <div class="stg-under">
      <div class="panel arm" data-el="armcard">
        <div class="panel-head"><h3>Arm</h3><span class="pill" data-el="armpill">—</span></div>
        <p class="arm-line" data-el="armline">—</p>
        <div class="arm-prog" data-el="prog"><i data-el="progfill"></i></div>
        <div class="arm-load"><span class="kicker">Gripper load</span><div class="lbar"><i data-el="loadfill"></i><b></b></div><span class="num" data-el="loadval">—</span></div>
        <div class="btn-row arm-btns">
          <button class="btn primary" data-arm="arm">Arm</button>
          <button class="btn" data-arm="disarm">Disarm</button>
          <button class="btn" data-arm="reset">Reset</button>
          <button class="btn" data-arm="home">Home</button>
        </div>
        <p class="arm-ro" data-el="armro" hidden></p>
      </div>
      <div class="panel act">
        <div class="panel-head"><h3>Activity</h3><span class="tick-lat num" data-el="lat">stop latency —</span></div>
        <ol class="tick-list" data-el="ticker"><li class="empty">No activity yet</li></ol>
      </div>
    </div>
  </section>

  <aside class="stg-side">
    <div class="panel rq" data-el="rq"></div>
    <div class="panel mu" data-el="mu">
      <div class="panel-head"><h3>Muscle</h3><span class="mu-tag" data-el="mutag"></span></div>
      <div class="mrow" data-m="grip"><label>Grip</label><div class="mbar"><i class="fill"></i><u class="tick"><s></s></u></div><b class="num">—</b></div>
      <div class="mrow" data-m="open"><label>Open hand</label><div class="mbar"><i class="fill"></i><u class="tick"><s></s></u></div><b class="num">—</b></div>
      <div class="mrow" data-m="brace"><label>Brace</label><div class="mbar"><i class="fill"></i><u class="tick"><s></s></u></div><b class="num">—</b></div>
      <canvas class="mtrace" data-el="trace" height="64"></canvas>
      <p class="mu-hint">squeeze = confirm · open hand = stop · brace = freeze</p>
    </div>
  </aside>
</div>`;

const pct = (v) => (v == null || Number.isNaN(v) ? "—" : `${Math.round(v * 100)}%`);
const words = (v, fb = "—") => (v ? String(v).replaceAll("_", " ") : fb);

export class Deck {
  constructor(root, ctx) {
    this.ctx = ctx;
    this.root = root;
    this.replay = ctx.mode === "replay"; // read-only server: no actuation route exists, so no control that calls one is wired
    this.el = {};
    root.querySelectorAll("[data-el]").forEach((n) => (this.el[n.dataset.el] = n));
    this.rows = Object.fromEntries([...root.querySelectorAll("[data-m]")].map((n) => [n.dataset.m, { row: n, fill: n.querySelector(".fill"), tick: n.querySelector(".tick"), val: n.querySelector("b"), s: n.querySelector("s"), v: 0 }]));
    this.sig = "";
    this.total = new Map();   // contract id -> first expires_in_s seen
    this.recvT = performance.now();
    this.s = null;
    this.seen = new Set();
    this.bind();
  }

  bind() {
    const { api } = this.ctx;
    if (this.replay) {
      this.el.sayform.onsubmit = (e) => e.preventDefault();
      this.el.say.disabled = true;
      this.el.say.placeholder = "Read-only. Replay never sends a request to the arm.";
      this.el.sayform.querySelector(".say-send").disabled = true;
      this.el.sayform.classList.add("ro");
      this.root.querySelectorAll("[data-arm]").forEach((b) => (b.hidden = true));
      this.root.querySelector(".arm-btns").hidden = true;
      this.el.armro.hidden = false;
      this.el.armro.textContent = "Read-only. Nothing on this page can move the arm.";
      return; // no click handlers at all: nothing here can reach /api/say, /api/arm/*, /api/confirm, /api/choose/* or /api/cancel
    }
    this.el.sayform.onsubmit = (e) => {
      e.preventDefault();
      const text = this.el.say.value.trim();
      if (text) api("/api/say", { text });
      this.el.say.value = "";
    };
    this.root.querySelectorAll("[data-arm]").forEach((b) => (b.onclick = () => api(`/api/arm/${b.dataset.arm}`)));
    const onAct = (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.say) api("/api/say", { text: b.dataset.say });
      else if (b.dataset.choose) api(`/api/choose/${encodeURIComponent(b.dataset.choose)}`);
      else if (b.dataset.act === "confirm") api("/api/confirm");
      else if (b.dataset.act === "cancel") api("/api/cancel");
    };
    this.el.rq.addEventListener("click", onAct);
    this.el.prompt.addEventListener("click", onAct);
  }

  // ── per-snapshot render ───────────────────────────────────────────────────
  update(s, sentence) {
    this.s = s;
    this.recvT = performance.now();
    this.renderSay(s);
    this.renderRequest(s);
    this.renderMuscle(s);
    this.renderArm(s, sentence);
    this.renderTicker(s);
  }

  /** Replay with no running simulation: the snapshot carries no arm, EMG, vision or events. Say so, change nothing else. */
  updateEmpty(s) {
    this.s = null;
    this.renderSay(s);
    this.emg = null;
    this.renderMuscle({});
    this.sig = "";
    const rq = this.el.rq;
    if (rq.dataset.empty !== "1") {
      rq.dataset.empty = "1";
      rq.className = "panel rq empty";
      rq.innerHTML = `<div class="panel-head"><h3>Request</h3><span class="verdict v-mute idle"><b>idle</b></span></div><p class="rq-heard muted">No simulation running</p><p class="rq-why muted">Recorded incidents and sessions open in Replay. Requests only exist while a simulation runs.</p>`;
    }
    this.armroKey = "";
    this.el.armro.textContent = "Read-only. No simulation is running, so there is no arm to show.";
    this.el.armpill.textContent = "no simulation";
    this.el.armpill.className = "pill";
    this.el.armline.textContent = "No simulation running";
    this.el.prog.classList.remove("on");
    this.loadTarget = 0;
    this.el.loadval.textContent = "—";
    this.tickKey = "empty";
    this.el.ticker.innerHTML = '<li class="empty">No activity</li>';
    this.el.lat.textContent = "stop latency —";
  }

  renderSay(s) {
    const sp = s.speech;
    const mic = this.el.mic;
    const lvl = sp ? Math.min(1, (sp.level || 0) / 2.5) : 0;
    mic.className = `mic ${!sp ? "off" : sp.error ? "err" : sp.speaking ? "hot" : sp.listening ? "on" : "idle"}`;
    mic.style.setProperty("--lvl", lvl.toFixed(2));
    const hint = this.replay ? "read-only" : !sp ? "typed only" : sp.error ? "mic error" : sp.speaking ? "hearing you" : sp.listening ? "listening" : "mic muted";
    if (this.el.sayhint.textContent !== hint) this.el.sayhint.textContent = hint;
  }

  // request card ------------------------------------------------------------
  renderRequest(s) {
    if (this.el.rq.dataset.empty === "1") { this.el.rq.dataset.empty = ""; this.sig = ""; }
    const pending = s.pending;
    const contract = pending?.contract || s.last?.contract;
    const decision = pending?.decision || s.last?.decision;
    const rq = this.el.rq;
    if (!contract) {
      const names = (s.objects || []).map((o) => o.name);
      const esig = "empty:" + names.join("|");
      if (this.sig !== esig) {
        this.sig = esig;
        const tries = this.replay ? [] : [names[0] && `hand me the ${names[0]} gently`, names[1] && `point at the ${names[1]}`, names[2] && `put the ${names[2]} in the tray`, "go home"].filter(Boolean);
        rq.className = "panel rq empty";
        rq.innerHTML = `<div class="panel-head"><h3>Request</h3><span class="verdict v-mute idle"><b>idle</b></span></div>
          <p class="rq-heard muted">${this.replay ? "Waiting for the script’s request" : "Waiting for a request"}</p>
          <div class="rq-main"><div class="conf">${this.confSvg(0, "mute")}<span class="conf-n num">—</span></div>
          <dl class="rq-kv"><dt>Action</dt><dd>—</dd><dt>Object</dt><dd>—</dd><dt>Grip</dt><dd>—</dd><dt>Route</dt><dd>—</dd></dl></div>
          <p class="rq-why muted">${this.replay ? "Read-only. The reason for every decision shows here." : "Say or type a request. The reason for every decision shows here."}</p>
          ${this.replay ? "" : `<div class="rq-try"><span class="kicker">Try saying</span>${tries.map((t) => `<button class="try" data-say="${this.ctx.esc(t)}">${this.ctx.esc(t)}</button>`).join("")}</div>`}`;
      }
      return;
    }
    const verdict = decision.verdict;
    const asking = !!pending && verdict === "ask";
    const sig = [contract.id, verdict, asking, decision.clarify, decision.reason, pending?.plan, JSON.stringify(contract.candidates?.map((c) => c.object_id + c.score))].join("|");
    if (asking) {
      const t = Math.max(this.total.get(contract.id) || 0, pending.expires_in_s || 0);
      this.total.set(contract.id, t);
    }
    if (sig !== this.sig) {
      this.sig = sig;
      this.renderRequestFull(s, contract, decision, pending);
    }
    if (asking) this.tickAsk(contract, pending);
  }

  confSvg(c, tone) {
    return `<svg class="conf-ring" viewBox="0 0 64 64" aria-hidden="true"><circle class="bg" cx="32" cy="32" r="${R}"/><circle class="fg t-${tone}" cx="32" cy="32" r="${R}" stroke-dasharray="${CIRC.toFixed(1)}" stroke-dashoffset="${CIRC.toFixed(1)}" data-c="${c}"/></svg>`;
  }

  gripText(s, contract) {
    if (contract.verb !== "pick_place") return "—";
    const cap = (s.objects || []).find((o) => o.id === contract.object_id)?.max_grip ?? 1;
    if (contract.grip_effort == null) return `default, ${contract.object_name || "object"} max ${pct(cap)}`;
    return contract.grip_effort > cap ? `${pct(contract.grip_effort)} asked, capped at ${pct(cap)}` : pct(contract.grip_effort);
  }

  renderRequestFull(s, contract, decision, pending) {
    const { esc } = this.ctx;
    const verdict = decision.verdict;
    const tone = VERDICT[verdict] || "mute";
    const asking = !!pending && verdict === "ask";
    const evidence = Object.entries(contract.evidence || {}).map(([k, v]) => {
      const num = typeof v === "number";
      return `<span class="ev"><em>${esc(words(k))}</em><b class="num">${num ? v.toFixed(2) : esc(v)}</b>${num && v <= 1 ? `<i style="--w:${Math.max(0.04, v)}"></i>` : ""}</span>`;
    }).join("");
    const name = contract.object_name || (contract.unresolved?.includes("target") ? "unresolved" : "—");
    const rules = contract.applied_rules?.length || 0;
    const route = contract.route ? `${esc(words(contract.route))}${rules ? ` · ${rules} rule${rules > 1 ? "s" : ""}` : ""}` : rules ? `${rules} rule${rules > 1 ? "s" : ""}` : "—";
    const conf = contract.confidence ?? 0;
    let ask = "";
    if (asking) {
      if (decision.clarify) {
        const cands = (contract.candidates || []).filter((c) => c.score > 0);
        const ro = this.replay;
        ask = `<div class="ask-top"><div class="ask-ring"><svg viewBox="0 0 64 64"><circle class="bg" cx="32" cy="32" r="${R}"/><circle class="fg" cx="32" cy="32" r="${R}" stroke-dasharray="${CIRC.toFixed(1)}" data-ask-ring/></svg><span class="num" data-ask-n>—</span></div>
          <div class="ask-txt"><strong class="ask-title">Which one?</strong><span class="ask-sub">${ro ? "Read-only. " : ""}Expires in <span class="num" data-ask-s>—</span> s</span></div>${ro ? "" : '<button class="btn danger ask-cancel" data-act="cancel">Cancel</button>'}</div>
          <div class="ask-opts">${cands.map((c) => `<button class="opt"${ro ? " disabled" : ` data-choose="${esc(c.object_id)}"`}><span>${esc(c.name)}</span><b class="num">${pct(c.score)}</b><i style="--w:${c.score}"></i></button>`).join("")}</div>`;
      } else {
        ask = `<div class="ask-top"><div class="ask-ring"><svg viewBox="0 0 64 64"><circle class="bg" cx="32" cy="32" r="${R}"/><circle class="fg" cx="32" cy="32" r="${R}" stroke-dasharray="${CIRC.toFixed(1)}" data-ask-ring/></svg><span class="num" data-ask-n>—</span></div>
          <div class="ask-txt"><strong class="ask-title">${this.replay ? "Awaiting confirmation" : "Squeeze to confirm"}</strong><span class="ask-sub">${this.replay ? "Read-only. The script confirms." : "or press Confirm."} Expires in <span class="num" data-ask-s>—</span> s</span></div></div>
          ${this.replay ? "" : '<div class="btn-row ask-btns"><button class="btn primary" data-act="confirm">Confirm</button><button class="btn danger" data-act="cancel">Cancel</button></div>'}`;
      }
    }
    this.el.rq.className = `panel rq v-${tone} ${asking ? "asking" : ""}`;
    this.el.rq.innerHTML = `
      <div class="panel-head"><div class="rq-title"><h3>Request</h3><span class="via">${esc(contract.source)}</span></div><span class="verdict v-${tone}"><svg viewBox="0 0 24 24" aria-hidden="true">${I[verdict] || I.hold}</svg><b>${esc(verdict)}</b></span></div>
      <p class="rq-heard">${contract.utterance ? `“${esc(contract.utterance)}”` : "Request"}</p>
      <div class="rq-main">
        <div class="conf">${this.confSvg(conf, tone)}<span class="conf-n num">${Math.round(conf * 100)}</span><small>conf</small></div>
        <dl class="rq-kv">
          <dt>Action</dt><dd>${esc(words(contract.verb))}${pending?.plan ? "" : ""}</dd>
          <dt>Object</dt><dd>${esc(name)}</dd>
          <dt>Grip</dt><dd>${esc(this.gripText(s, contract))}</dd>
          <dt>Route</dt><dd>${route}</dd>
        </dl>
      </div>
      ${evidence ? `<div class="rq-ev">${evidence}</div>` : ""}
      <p class="rq-why"><span class="kicker">Why</span>${esc(decision.reason)}</p>
      ${s.reply ? `<p class="rq-reply">“${esc(s.reply)}”</p>` : ""}
      ${asking ? `<div class="rq-ask">${ask}</div>` : ""}`;
    // animate arcs from zero
    requestAnimationFrame(() => {
      const fg = this.el.rq.querySelector(".conf-ring .fg");
      if (fg) fg.style.strokeDashoffset = (CIRC * (1 - conf)).toFixed(1);
    });
  }

  tickAsk(contract, pending) {
    const total = this.total.get(contract.id) || pending.expires_in_s || 1;
    const left = pending.expires_in_s ?? 0;
    const ring = this.el.rq.querySelector("[data-ask-ring]");
    if (!ring) return;
    ring.style.strokeDashoffset = (CIRC * (1 - Math.max(0, Math.min(1, left / total)))).toFixed(1);
    ring.classList.toggle("low", left <= 3);
    const n = this.el.rq.querySelector("[data-ask-n]");
    const sEl = this.el.rq.querySelector("[data-ask-s]");
    if (n) n.textContent = Math.ceil(left);
    if (sEl) sEl.textContent = left.toFixed(1);
  }

  // muscle ------------------------------------------------------------------
  renderMuscle(s) {
    const e = s.emg;
    const mu = this.el.mu;
    mu.classList.toggle("off", !e);
    if (!e) {
      this.el.mutag.innerHTML = `<span class="chip"><i></i>${this.replay ? "no simulation" : "EMG off"}</span>`;
      for (const r of Object.values(this.rows)) { r.target = 0; r.val.textContent = "—"; }
      return;
    }
    const tag = e.error ? `<span class="chip bad"><i></i>${this.ctx.esc(e.error)}</span>` : e.hardware ? `<span class="chip ${e.live ? "ok" : "warn"}"><i></i>${e.calibrated ? e.profile : "uncalibrated"} · ${e.age_ms ?? "—"} ms</span>` : `<span class="chip warn"><i></i>SYNTHETIC EMG</span>`;
    if (this.tagHtml !== tag) this.el.mutag.innerHTML = this.tagHtml = tag;
    const set = (k, v, thr, label) => {
      const r = this.rows[k];
      r.target = v;
      r.thr = thr;
      r.tick.style.left = `${Math.min(100, thr * 100)}%`;
      r.s.textContent = `${label} ${Math.round(thr * 100)}`;
      r.val.textContent = Math.round(v * 100);
      r.row.classList.toggle("over", v >= thr);
    };
    set("grip", e.effort, e.confirm_effort, "confirm");
    set("open", e.extensor, e.stop_effort, "stop");
    set("brace", e.brace || 0, e.brace_effort, "brace");
    this.emg = e;
  }

  // arm ---------------------------------------------------------------------
  renderArm(s, sentence) {
    const a = s.arm;
    if (this.replay && this.armroKey !== "sim") {
      this.armroKey = "sim";
      this.el.armro.textContent = "Read-only. The simulation script drives the arm; STOP in the top bar still halts it.";
    }
    const tone = { ready: "ok", moving: "ok", stopped: "bad", fault: "bad", disarmed: "warn" }[a.state] || "";
    const pill = this.el.armpill;
    const label = `${a.hardware ? "SO-101" : "virtual"} · ${a.state}`;
    if (pill.textContent !== label) pill.textContent = label;
    pill.className = `pill ${tone}`;
    this.el.armline.textContent = sentence;
    this.el.prog.classList.toggle("on", a.progress != null);
    this.el.progfill.style.transform = `scaleX(${a.progress ?? 0})`;
    const load = a.gripper_load;
    this.loadTarget = load == null ? 0 : Math.min(1, load / 0.5);
    this.el.loadval.textContent = load == null ? "—" : pct(load);
    const st = a.state;
    this.root.querySelector('[data-arm="arm"]').classList.toggle("primary", st === "disarmed");
    this.root.querySelector('[data-arm="reset"]').classList.toggle("primary", st === "stopped" || st === "fault");
    this.root.querySelector('[data-arm="reset"]').classList.toggle("pulse", st === "stopped" || st === "fault");
  }

  // ticker ------------------------------------------------------------------
  renderTicker(s) {
    const { esc, EVENT_KIND, ago } = this.ctx;
    // core's describeEvent has no wording for the learning events; say what the event itself carries
    const describeEvent = (e) => {
      const d = this.ctx.describeEvent(e);
      if (d) return d;
      if (e.kind === "incident.explained") return e.explanation?.say || "explained";
      if (e.kind === "incident.saved") return `clip saved${e.held ? ` · held ${e.held}` : ""}`;
      if (e.kind === "incident.triggered") return `${words(e.source)} · arm freezing`;
      if (e.kind === "rule.approved" && e.rule) return `${(e.rule.when_holding || []).join(", ")} → ${words(e.rule.response)} · avoid ${e.rule.avoid}${e.by ? ` · by ${e.by}` : ""}`;
      return "";
    };
    const items = [...(s.events || [])].reverse().filter((e) => TICKER_KINDS.has(e.kind) && EVENT_KIND[e.kind]).slice(0, 6);
    const html = items.map((e) => {
      const [label, cls] = EVENT_KIND[e.kind];
      const fresh = this.seen.size && !this.seen.has(e.seq) ? " fresh" : "";
      return `<li class="${fresh}"><time class="num">${ago(s, e)}</time><span class="k k-${cls}">${esc(label)}</span><span class="txt" title="${esc(describeEvent(e))}">${esc(describeEvent(e))}</span></li>`;
    }).join("");
    items.forEach((e) => this.seen.add(e.seq));
    const key = items.map((e) => e.seq).join(",") + ":" + (items[0] ? Math.floor(s.t - items[0].t) : 0) + (items[1] ? Math.floor(s.t - items[1].t) : 0);
    if (key !== this.tickKey) {
      this.tickKey = key;
      this.el.ticker.innerHTML = html || '<li class="empty">No activity yet</li>';
    }
    // stop latency: how long after the trigger the arm froze (event timestamps from the runtime)
    const stopped = [...(s.events || [])].reverse().find((e) => e.kind === "arm.stopped");
    let text = "stop latency —";
    if (stopped) {
      const trig = [...(s.events || [])].reverse().find((e) => (e.kind === "emg.brace" || e.kind === "emg.open_hand") && e.t <= stopped.t && stopped.t - e.t < 2);
      if (trig) {
        const lag = Math.round((stopped.t - trig.t) * 1000);
        text = trig.kind === "emg.brace" ? `brace caught in ${trig.detect_ms ?? "—"} ms, frozen +${lag} ms` : `open hand → frozen in ${lag} ms`;
      } else text = `last stop: ${stopped.reason}`;
    }
    if (this.el.lat.textContent !== text) this.el.lat.textContent = text;
  }

  // per-frame: meters ease, load bar eases, trace scrolls ---------------------
  frame(dt, t) {
    const k = 1 - Math.exp(-16 * dt);
    for (const r of Object.values(this.rows)) {
      r.v += ((r.target ?? 0) - r.v) * k;
      r.fill.style.transform = `scaleX(${Math.max(0, Math.min(1, r.v)).toFixed(4)})`;
    }
    this.loadV = (this.loadV ?? 0) + ((this.loadTarget ?? 0) - (this.loadV ?? 0)) * k;
    this.el.loadfill.style.transform = `scaleX(${this.loadV.toFixed(4)})`;
    this.drawTrace(t);
  }

  drawTrace() {
    const c = this.el.trace;
    const e = this.emg;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const w = Math.round(c.clientWidth * dpr), h = Math.round(c.clientHeight * dpr);
    if (!w || !h) return;
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    const g = c.getContext("2d");
    g.clearRect(0, 0, w, h);
    const css = getComputedStyle(this.root);
    const col = (n) => css.getPropertyValue(n).trim() || "#fff";
    g.lineWidth = 1 * dpr;
    g.strokeStyle = "rgba(160,190,230,.10)";
    for (const f of [0, 0.5, 1]) { const y = h - 2 - f * (h - 6); g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); }
    if (!e) return;
    const tr = e.trace || [], bt = e.brace_trace || [];
    const tEnd = Math.max(tr.length ? tr[tr.length - 1][0] : 0, bt.length ? bt[bt.length - 1][0] : 0);
    const tStart = Math.min(tr.length ? tr[0][0] : tEnd, bt.length ? bt[0][0] : tEnd);
    const span = Math.max(1, tEnd - tStart);
    // between snapshots, slide the window by the wall time since the last one (clamped to one frame of data)
    const slide = Math.min(0.12, (performance.now() - this.recvT) / 1000);
    const X = (tt) => (w - 2) - ((tEnd + slide - tt) / span) * (w - 2);
    const Y = (v) => h - 2 - Math.max(0, Math.min(1, v)) * (h - 6);
    g.setLineDash([3 * dpr, 4 * dpr]);
    for (const [v, c2] of [[e.confirm_effort, col("--grip")], [e.stop_effort, col("--open")], [e.brace_effort, col("--brace")]]) {
      g.strokeStyle = c2; g.globalAlpha = 0.32; g.beginPath(); g.moveTo(0, Y(v)); g.lineTo(w, Y(v)); g.stroke();
    }
    g.setLineDash([]);
    g.globalAlpha = 1;
    g.lineJoin = "round";
    const line = (rows, idx, color, width, fill) => {
      if (rows.length < 2) return;
      g.beginPath();
      rows.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(p[idx])) : g.moveTo(X(p[0]), Y(p[idx]))));
      if (fill) {
        g.save();
        g.lineTo(X(rows.at(-1)[0]), h); g.lineTo(X(rows[0][0]), h); g.closePath();
        const grad = g.createLinearGradient(0, 0, 0, h); grad.addColorStop(0, color + "55"); grad.addColorStop(1, color + "00");
        g.fillStyle = grad; g.fill(); g.restore();
        g.beginPath();
        rows.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(p[idx])) : g.moveTo(X(p[0]), Y(p[idx]))));
      }
      g.strokeStyle = color; g.lineWidth = width * dpr; g.stroke();
    };
    line(tr, 2, col("--open"), 1.5, false);
    line(tr, 1, col("--grip"), 1.8, true);
    line(bt, 1, col("--brace"), 1.6, false);
    g.fillStyle = "rgba(170,182,198,.6)";
    g.font = `${9 * dpr}px ${css.getPropertyValue("--font-mono") || "monospace"}`;
    g.fillText(`-${span.toFixed(1)}s`, 3 * dpr, 10 * dpr);
    g.textAlign = "right";
    g.fillText("now", w - 3 * dpr, 10 * dpr);
    g.textAlign = "left";
  }
}
