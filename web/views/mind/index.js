// Mind: what Armed is processing right now, as a live flow. Senses on the left, Muse Spark
// thinking in the middle, then what it says, what it suggests, the gate, your one yes and the arm.
// Every value is read from the snapshot (state.think, state.calls, state.context, state.scene,
// state.pending, state.arm, state.learning) or from a runtime event; pulses run along an edge
// only when an event really crossed it. Nothing here is simulated.

const NODES = [
  // id, grid area, title, model chip (Meta model id, or an Armed component), hue
  { id: "cam", area: "1 / 1", title: "Camera", chip: "sam-3.1", hue: "muse" },
  { id: "mic", area: "2 / 1", title: "Microphone", chip: "muse-voice-transcribe-1.0", hue: "muse" },
  { id: "emg", area: "3 / 1", title: "Forearm", chip: "EMG decoder", hue: "grip" },
  { id: "clock", area: "4 / 1", title: "Clock · routines", chip: "Armed routines", hue: "spine" },
  { id: "ctx", area: "1 / 2 / 5 / 3", title: "What it knows", chip: "Armed context", hue: "spine", tall: true },
  { id: "think", area: "1 / 3 / 3 / 4", title: "Thinking", chip: "muse-spark-1.3", hue: "muse", big: true },
  { id: "rank", area: "4 / 3", title: "Ranking cards", chip: "muse-spark-1.3", hue: "muse" },
  { id: "remark", area: "1 / 4", title: "Says", chip: "voice out", hue: "spine" },
  { id: "suggest", area: "2 / 4", title: "Suggests", chip: "closed list", hue: "muse" },
  { id: "cards", area: "4 / 4", title: "Cards", chip: "twitch to pick", hue: "grip" },
  { id: "gate", area: "2 / 5", title: "Safety gate", chip: "Armed gate · no model", hue: "spine" },
  { id: "confirm", area: "3 / 5", title: "You confirm once", chip: "yes · squeeze · click", hue: "ok" },
  { id: "arm", area: "4 / 5", title: "Arm", chip: "taught poses", hue: "spine" },
  { id: "brace", area: "5 / 1", title: "Brace", chip: "EMG decoder", hue: "brace" },
  { id: "freeze", area: "5 / 2", title: "Freeze", chip: "no model · no network", hue: "brace" },
  { id: "wonder", area: "5 / 3", title: "Investigates", chip: "muse-wonder-1.0", hue: "muse" },
  { id: "rule", area: "5 / 4", title: "Rule", chip: "Armed checker", hue: "muse" },
];
const EDGES = [
  ["cam", "ctx"], ["mic", "ctx"], ["clock", "ctx"], ["ctx", "think"], ["ctx", "rank"],
  ["think", "remark"], ["think", "suggest"], ["suggest", "gate"], ["rank", "cards"], ["cards", "gate"],
  ["gate", "confirm"], ["emg", "confirm"], ["confirm", "arm"], ["emg", "brace"], ["brace", "freeze"],
  ["freeze", "arm"], ["freeze", "wonder"], ["wonder", "rule"], ["rule", "ctx"],
];
// runtime event -> the edges it crosses, in order
const ROUTES = {
  "scene.event": [["cam", "ctx"]],
  "vision.scan": [["cam", "ctx"]],
  "speech.utterance": [["mic", "ctx"]],
  "sound.event": [["mic", "ctx"]],
  "routine.proposed": [["clock", "ctx"]],
  "think.started": [["ctx", "think"]],
  "think.said": [["think", "remark"]],
  "think.suggested": [["think", "suggest"], ["suggest", "gate"]],
  "selection.shown": [["ctx", "rank"], ["rank", "cards"]],
  "selection.accept": [["cards", "gate"]],
  "emg.twitch": [["emg", "confirm"]],
  "emg.squeeze": [["emg", "confirm"]],
  "intent.confirmed": [["gate", "confirm"], ["confirm", "arm"]],
  "arm.started": [["confirm", "arm"]],
  "emg.brace": [["emg", "brace"], ["brace", "freeze"], ["freeze", "arm"]],
  "arm.stopped": [["freeze", "arm"]],
  "incident.triggered": [["freeze", "wonder"]],
  "incident.explained": [["wonder", "rule"]],
  "rule.saved": [["rule", "ctx"]],
};

const ago = (s) => (s == null ? "" : s < 1 ? "now" : s < 60 ? `${Math.round(s)} s ago` : `${Math.round(s / 60)} min ago`);
const ms = (v) => (v == null ? "" : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`);

export default {
  async mount(root, ctx) {
    ctx.css(import.meta.url, "style.css");
    const { esc } = ctx;
    this.esc = esc;
    root.innerHTML = `
      <div class="mind">
        <header class="mind-head">
          <div><span class="kicker">What Armed is processing right now</span>
          <p class="mind-line" id="mind-line">Waiting for the runtime…</p></div>
          <div class="mind-legend"><span class="lg muse">Meta model</span><span class="lg spine">Armed code</span><span class="lg brace">stop path</span></div>
        </header>
        <div class="mind-grid" id="mind-grid">
          <svg class="mind-wires" id="mind-wires" aria-hidden="true"></svg>
          ${NODES.map((n) => `
            <section class="mn ${n.big ? "big" : ""} ${n.tall ? "tall" : ""}" data-id="${n.id}" data-hue="${n.hue}" style="grid-area:${n.area}">
              <div class="mn-top"><h3>${esc(n.title)}</h3><span class="mn-chip">${esc(n.chip)}</span></div>
              <div class="mn-status" data-k="status"></div>
              <div class="mn-body" data-k="body"></div>
            </section>`).join("")}
        </div>
      </div>`;
    this.root = root;
    this.ctx = ctx;
    this.last = {}; // per-node memory from events (last utterance, last gesture, ...)
    this.flash = new Map(); // node id -> until (ms)
    this.ro = new ResizeObserver(() => this.wire());
    this.ro.observe(root.querySelector("#mind-grid"));
    ctx.bus.on("event", (e) => this.onEvent(e));
  },

  enter(ctx) {
    this.active = true;
    requestAnimationFrame(() => this.wire());
    this.stopLoop = ctx.loop.add(() => this.tick());
  },

  leave() {
    this.active = false;
    this.stopLoop?.();
  },

  node(id) {
    return this.root.querySelector(`.mn[data-id="${id}"]`);
  },

  // SVG wires between node edges, recomputed on resize.
  wire() {
    const grid = this.root.querySelector("#mind-grid");
    const svg = this.root.querySelector("#mind-wires");
    const g = grid.getBoundingClientRect();
    if (!g.width) return;
    svg.setAttribute("viewBox", `0 0 ${g.width} ${g.height}`);
    this.paths = new Map();
    svg.innerHTML = EDGES.map(([a, b]) => {
      const ra = this.node(a).getBoundingClientRect();
      const rb = this.node(b).getBoundingClientRect();
      const ax = ra.left - g.left, ay = ra.top - g.top, bx = rb.left - g.left, by = rb.top - g.top;
      let p0, p1, horiz;
      if (rb.left >= ra.right - 4) {
        p0 = [ax + ra.width, ay + ra.height / 2]; p1 = [bx, by + rb.height / 2]; horiz = true;
      } else if (rb.right <= ra.left + 4) {
        p0 = [ax, ay + ra.height / 2]; p1 = [bx + rb.width, by + rb.height / 2]; horiz = true;
      } else if (rb.top >= ra.bottom - 4) {
        p0 = [ax + ra.width / 2, ay + ra.height]; p1 = [bx + rb.width / 2, by]; horiz = false;
      } else {
        p0 = [ax + ra.width / 2, ay]; p1 = [bx + rb.width / 2, by + rb.height]; horiz = false;
      }
      const k = horiz ? (p1[0] - p0[0]) * 0.5 : (p1[1] - p0[1]) * 0.5;
      const d = horiz
        ? `M${p0[0]} ${p0[1]} C${p0[0] + k} ${p0[1]} ${p1[0] - k} ${p1[1]} ${p1[0]} ${p1[1]}`
        : `M${p0[0]} ${p0[1]} C${p0[0]} ${p0[1] + k} ${p1[0]} ${p1[1] - k} ${p1[0]} ${p1[1]}`;
      const hue = NODES.find((n) => n.id === a).hue;
      return `<path class="wire" data-e="${a}-${b}" data-hue="${hue}" d="${d}"/>`;
    }).join("");
    svg.querySelectorAll("path").forEach((p) => this.paths.set(p.dataset.e, p));
  },

  // A dot runs along each edge of the route, one after the other.
  pulse(route) {
    if (!this.active || !this.paths) return;
    const svg = this.root.querySelector("#mind-wires");
    route.forEach(([a, b], i) => {
      const path = this.paths.get(`${a}-${b}`);
      if (!path) return;
      setTimeout(() => {
        this.lit(a);
        path.classList.add("hot");
        const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        dot.setAttribute("r", "5");
        dot.setAttribute("class", `dot ${path.dataset.hue}`);
        svg.append(dot);
        const len = path.getTotalLength();
        const t0 = performance.now();
        const run = (t) => {
          const f = Math.min(1, (t - t0) / 650);
          const pt = path.getPointAtLength(len * f);
          dot.setAttribute("cx", pt.x);
          dot.setAttribute("cy", pt.y);
          if (f < 1) requestAnimationFrame(run);
          else {
            dot.remove();
            path.classList.remove("hot");
            this.lit(b);
          }
        };
        requestAnimationFrame(run);
      }, i * 520);
    });
  },

  lit(id) {
    this.flash.set(id, performance.now() + 1400);
  },

  onEvent(e) {
    const d = e.data || {};
    const L = this.last;
    if (e.kind === "speech.utterance" && d.echo !== true) L.heard = { who: d.asked_by || "you", text: d.text, t: e.t };
    if (e.kind === "sound.event") L.sound = { label: d.label, t: e.t };
    if (e.kind.startsWith("emg.") && ["emg.twitch", "emg.double_twitch", "emg.squeeze", "emg.brace", "emg.open_hand"].includes(e.kind))
      L.gesture = { name: e.kind.slice(4).replace("_", " "), mock: d.mock, t: e.t };
    if (e.kind === "emg.brace") L.brace = e.t;
    if (e.kind === "embodied.tool" && d.decision) L.gate = { verdict: d.decision.verdict, reason: d.decision.reason, tool: d.tool, t: e.t };
    if (e.kind === "scene.event") L.scene = { what: d.what, t: e.t };
    if (e.kind === "arm.stopped") L.stop = { reason: d.reason, t: e.t };
    if (e.kind === "intent.confirmed") L.confirmed = { by: d.by || d.confirmed_by, t: e.t };
    const route = e.kind === "speech.utterance" && d.echo === true ? null : ROUTES[e.kind];
    if (route) this.pulse(route);
  },

  set(id, key, html) {
    const el = this.node(id)?.querySelector(`[data-k="${key}"]`);
    if (el && el._h !== html) el.innerHTML = el._h = html;
  },

  update(s) {
    this.s = s;
    const esc = this.esc;
    const t = s.t;
    const calls = Array.isArray(s.calls) ? s.calls : [];
    const inflight = (model, pred = () => true) => calls.find((c) => c.model === model && c.ms == null && pred(c));
    const L = this.last;
    const think = s.think || {};

    // headline
    const busy = think.thinking ? `Muse Spark is thinking (${esc(think.thinking.reason)})` : null;
    this.root.querySelector("#mind-line").textContent = busy || (think.enabled ? "Watching, listening, judging." : "The thinker is off (think.enabled).");

    // camera / SAM
    const sam = inflight("sam-3.1");
    const sc = s.scene || {};
    const phrases = [...new Set((sc.masks || []).map((m) => m.phrase))];
    this.set("cam", "status", sam ? `<b class="live">calling sam-3.1 · ${ms(sam.elapsed_ms)}</b>` : sc.scan_ms != null ? `scan ${ago(sc.scan_age_s)} · ${ms(sc.scan_ms)}` : s.vision?.virtual ? "virtual table" : "no scan yet");
    this.set("cam", "body", (phrases.length ? phrases.map((p) => `<span class="tag">${esc(p)}</span>`).join("") : `<span class="muted">nothing found</span>`) + (L.scene ? `<p class="ev">${esc(L.scene.what)} · ${ago(t - L.scene.t)}</p>` : ""));

    // microphone / Voice Transcribe
    const sp = s.speech;
    this.set("mic", "status", !sp ? "mic off" : sp.error ? `<b class="bad">${esc(sp.error)}</b>` : sp.connected ? "listening" : "connecting");
    this.set("mic", "body", L.heard ? `<p class="quote"><b>${esc(L.heard.who)}:</b> ${esc(L.heard.text)}</p><p class="ev">${ago(t - L.heard.t)}</p>` : `<span class="muted">nothing heard yet</span>`);

    // forearm
    const emg = s.emg;
    this.set("emg", "status", !emg ? "off" : emg.hardware ? (emg.live ? "band live" : "band silent") : "mock buttons (T D Q B)");
    this.set("emg", "body", L.gesture ? `<p><b>${esc(L.gesture.name)}</b>${L.gesture.mock ? " <span class='muted'>mock</span>" : ""}</p><p class="ev">${ago(t - L.gesture.t)}</p>` : `<span class="muted">no gesture yet</span>`);

    // clock
    const cx = s.context || {};
    const due = (cx.routines_due || [])[0];
    this.set("clock", "status", esc(cx.clock || ""));
    this.set("clock", "body", due ? `<p>${esc(due.text || due)}</p>` : `<span class="muted">no routine due</span>`);

    // context packet
    const rows = [
      ["objects", (cx.objects || []).map((o) => o.name || o).join(", ")],
      ["present", (cx.present || []).join(", ")],
      ["hand", cx.hand?.where],
      ["sounds", (cx.sounds || []).map((x) => x.text || x).join(", ")],
      ["did", (cx.last_actions || []).slice(-2).map((x) => x.text || x).join(" · ")],
      ["rules", (cx.rules || []).length ? `${cx.rules.length} in force` : ""],
      ["setting", s.routines?.profile || ""],
    ];
    this.set("ctx", "status", `refreshed every second`);
    this.set("ctx", "body", rows.map(([k, v]) => `<div class="row ${v ? "" : "empty"}"><span>${k}</span><b>${esc(v || "—")}</b></div>`).join(""));

    // thinking
    const notes = think.notes || [];
    const note = notes[notes.length - 1];
    this.set("think", "status", think.thinking ? `<b class="live">thinking · ${ms(think.thinking.elapsed_ms)} · ${esc(think.thinking.reason)}</b>` : think.last_ms != null ? `last thought ${ms(think.last_ms)} · ${think.count} so far` : think.enabled ? "waiting for something to change" : "off");
    this.set("think", "body", notes.length
      ? notes.slice(-4).reverse().map((n, i) => `<div class="thought ${i ? "old" : ""}"><span class="why">${esc(n.reason)} · ${ago(n.age_s)}</span>${n.notice ? `<p>${esc(n.notice)}</p>` : `<p class="muted">nothing worth acting on</p>`}</div>`).join("")
      : `<span class="muted">no thoughts yet</span>`);

    // ranking cards
    const rankCall = inflight("muse-spark-1.3", (c) => c.for !== "think");
    this.set("rank", "status", rankCall ? `<b class="live">ranking · ${ms(rankCall.elapsed_ms)}</b>` : s.selection?.source ? `last order: ${esc(s.selection.source)}` : "no menu");
    this.set("rank", "body", "");

    // says / suggests
    const said = [...notes].reverse().find((n) => n.said);
    this.set("remark", "status", said ? ago(said.age_s) : "");
    this.set("remark", "body", said ? `<p class="quote">“${esc(said.said)}”</p>` : `<span class="muted">silent</span>`);
    const sug = [...notes].reverse().find((n) => n.suggest);
    this.set("suggest", "status", sug ? `${ago(sug.age_s)} · ${sug.suggest.queued ? "asked you" : "held"}` : "");
    this.set("suggest", "body", sug ? `<p class="quote">“${esc(sug.suggest.ask)}”</p>${sug.suggest.why ? `<p class="ev">because ${esc(sug.suggest.why)}</p>` : ""}` : `<span class="muted">nothing to suggest</span>`);

    // cards
    const cards = s.selection?.cards || [];
    this.set("cards", "status", cards.length ? `${cards.length} up` : "");
    this.set("cards", "body", cards.length ? cards.map((c, i) => `<p class="${i === (s.selection.highlight ?? 0) ? "hl" : ""}">${esc(c.label)}</p>`).join("") : `<span class="muted">none</span>`);

    // gate, confirm, arm
    const g = L.gate;
    this.set("gate", "status", g ? `${esc(g.verdict)} · ${ago(t - g.t)}` : "");
    this.set("gate", "body", g ? `<p>${esc(g.reason || "")}</p>` : `<span class="muted">nothing asked yet</span>`);
    const p = s.pending;
    this.set("confirm", "status", p ? `<b class="live">waiting · ${p.expires_in_s} s left</b>` : L.confirmed ? `confirmed by ${esc(L.confirmed.by || "you")} · ${ago(t - L.confirmed.t)}` : "");
    this.set("confirm", "body", p ? `<p>${esc(p.contract?.utterance || p.plan || p.decision?.reason || "")}</p>` : `<span class="muted">nothing waiting</span>`);
    const arm = s.arm || {};
    this.set("arm", "status", `${arm.hardware ? "SO-101" : "virtual"} · <b>${esc(arm.state || "")}</b>`);
    const grip = s.embodied?.gripper;
    this.set("arm", "body", grip ? `<p>gripper ${esc(grip.status)}${grip.object_id ? ` · ${esc(grip.object_id)}` : ""}</p>` : "");

    // stop lane
    this.set("brace", "status", L.brace ? ago(t - L.brace) : "");
    this.set("brace", "body", `<span class="muted">freezes the arm with no model in the path</span>`);
    this.set("freeze", "status", L.stop ? `${ago(t - L.stop.t)}` : "");
    this.set("freeze", "body", L.stop ? `<p>${esc(L.stop.reason)}</p>` : `<span class="muted">no stop</span>`);
    const inc = s.learning?.incident;
    const wonderCall = inflight("muse-wonder-1.0");
    this.set("wonder", "status", wonderCall ? `<b class="live">investigating · ${ms(wonderCall.elapsed_ms)}</b>` : inc?.state ? esc(inc.state) : "");
    this.set("wonder", "body", inc?.explanation ? `<p>${esc(inc.explanation)}</p>` : `<span class="muted">runs on every stop</span>`);
    const prop = s.learning?.proposal;
    this.set("rule", "status", prop ? "waiting for your yes" : "");
    this.set("rule", "body", prop ? `<p>${esc(prop.text || prop.rule?.text || "")}</p>` : `<span class="muted">restrict-only rules</span>`);

    // live highlight: a model in flight glows
    const thinking = !!think.thinking;
    this.node("think").classList.toggle("busy", thinking);
    this.node("cam").classList.toggle("busy", !!sam);
    this.node("rank").classList.toggle("busy", !!rankCall);
    this.node("wonder").classList.toggle("busy", !!wonderCall);
    this.node("confirm").classList.toggle("busy", !!p);
    this.node("arm").classList.toggle("busy", arm.state === "moving");
  },

  tick() {
    const now = performance.now();
    for (const n of NODES) {
      const until = this.flash.get(n.id) || 0;
      this.node(n.id).classList.toggle("lit", until > now);
    }
  },
};
