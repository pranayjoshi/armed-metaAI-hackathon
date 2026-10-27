// Human2Machine operator UI. Renders the 10 Hz state stream from /api/live.
const $ = (id) => document.getElementById(id);
const words = (v, fb = "—") => (v ? String(v).replaceAll("_", " ") : fb);
const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
let S = null;
let objectNames = {};

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) toast(data.detail || res.statusText);
  return data;
}

function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (el.hidden = true), 2600);
}

// ── tabs ──────────────────────────────────────────────────────────────────
document.querySelectorAll("[data-view]").forEach((tab) =>
  tab.addEventListener("click", () => {
    document.querySelectorAll("[data-view]").forEach((t) => t.setAttribute("aria-selected", t === tab));
    document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${tab.dataset.view}`));
  }),
);

// ── controls ──────────────────────────────────────────────────────────────
$("stop").onclick = () => api("/api/stop");
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && document.activeElement.tagName !== "INPUT") {
    e.preventDefault();
    api("/api/stop");
  }
});
$("say-form").onsubmit = (e) => {
  e.preventDefault();
  const text = $("say").value.trim();
  if (text) api("/api/say", { text });
  $("say").value = "";
};
document.querySelectorAll("[data-arm]").forEach((b) => (b.onclick = () => api(`/api/arm/${b.dataset.arm}`)));
document.querySelectorAll("[data-cal]").forEach(
  (b) =>
    (b.onclick = async () => {
      const phase = b.dataset.cal;
      const out = $(`cal-${phase}`);
      out.className = "";
      out.textContent = phase === "finish" ? "checking…" : "recording 3 s…";
      document.querySelectorAll("[data-cal]").forEach((x) => (x.disabled = true));
      const res = await api(`/api/emg/calibrate/${phase}?seconds=3`);
      document.querySelectorAll("[data-cal]").forEach((x) => (x.disabled = false));
      out.className = res.ok ? "ok" : "bad";
      out.textContent = res.ok ? (phase === "finish" ? "saved" : `ok · rms ${res.rms?.join(" / ")}`) : res.detail || "failed";
    }),
);
for (const [id, key] of [["syn-flexor", "flexor"], ["syn-extensor", "extensor"]]) {
  $(id).oninput = (e) => api("/api/emg/synthetic", { [key]: Number(e.target.value) });
}
$("session-form").onsubmit = async (e) => {
  e.preventDefault();
  if (S?.session?.session_id) await api("/api/session/stop");
  else await api("/api/session/start", { participant: $("participant").value || "anon", notes: "" });
};
$("trial-form").onsubmit = (e) => {
  e.preventDefault();
  const object = $("t-object").value || null;
  api("/api/trial/start", {
    expected_action: $("t-action").value,
    expected_object: object,
    condition: $("t-condition").value,
    instruction: $("t-instruction").value,
  });
};
document.querySelectorAll("[data-outcome]").forEach(
  (b) => (b.onclick = () => api("/api/trial/end", { outcome: b.dataset.outcome })),
);

// ── rendering ─────────────────────────────────────────────────────────────
function chip(label, tone) {
  return `<span class="chip ${tone}"><i></i>${label}</span>`;
}

function renderChips(s) {
  const arm = s.arm;
  const armTone = { ready: "ok", moving: "ok", disarmed: "warn", stopped: "bad", fault: "bad" }[arm.state] || "";
  const out = [chip(`${arm.hardware ? "SO-101" : "Virtual arm"} · ${arm.state}`, armTone)];
  if (s.emg) {
    const tone = s.emg.error ? "bad" : !s.emg.live ? "warn" : s.emg.hardware ? "ok" : "warn";
    out.push(chip(s.emg.hardware ? `EMG ${s.emg.calibrated ? "" : "· uncalibrated"}` : "EMG synthetic", tone));
  }
  out.push(chip(s.vision.virtual ? "Virtual table" : `Camera ${s.vision.fps || 0} fps`, s.vision.virtual ? "warn" : s.vision.live ? "ok" : "bad"));
  if (s.speech) out.push(chip(s.speech.error ? "Mic error" : `${s.speech.asr || "ASR loading"}`, s.speech.error ? "bad" : s.speech.listening ? "ok" : "warn"));
  out.push(chip(s.llm.enabled ? `Muse ${s.llm.last_ms ? s.llm.last_ms + " ms" : "ready"}` : "Grammar only", s.llm.error ? "bad" : s.llm.enabled ? "ok" : ""));
  if (s.session.session_id) out.push(chip(`REC ${s.session.trials_done} trials`, "bad"));
  $("chips").innerHTML = out.join("");
}

let imgTimer = null;
function renderScene(s) {
  const v = s.vision;
  const svg = $("overlay");
  const [W, H] = v.frame_size || [1280, 720];
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  $("stage-empty").hidden = !v.virtual;
  $("scene-img").hidden = v.virtual;
  if (!v.virtual && !imgTimer) {
    imgTimer = setInterval(() => ($("scene-img").src = `/api/camera/scene.jpg?t=${Date.now()}`), 150);
  }
  const contract = s.pending?.contract || s.last?.contract;
  const target = contract?.object_id;
  const cands = new Set((s.pending?.decision?.clarify ? contract.candidates : []).map((c) => c.object_id));
  const parts = [];
  if (v.virtual) {
    parts.push(`<rect class="table" x="${W * 0.06}" y="${H * 0.18}" width="${W * 0.88}" height="${H * 0.7}" rx="18"/>`);
  }
  for (const o of v.objects || []) {
    let box = o.bbox;
    if (!box) {
      const [x, y] = o.table_xy;
      box = [x * W - 70, y * H - 60, x * W + 70, y * H + 60];
    }
    const cls = o.id === target ? "target" : cands.has(o.id) ? "cand" : "";
    parts.push(`<rect class="box ${cls}" x="${box[0]}" y="${box[1]}" width="${box[2] - box[0]}" height="${box[3] - box[1]}" rx="8"/>`);
    parts.push(`<text class="label" x="${box[0] + 8}" y="${box[1] - 10}">${o.name} ${o.virtual ? "" : pct(o.confidence)}</text>`);
  }
  const ray = v.pointing?.ray;
  if (ray && v.pointing.active > 0.3) parts.push(`<line class="ray" x1="${ray[0]}" y1="${ray[1]}" x2="${ray[2]}" y2="${ray[3]}"/>`);
  svg.innerHTML = parts.join("");
  $("scene-meta").textContent = v.virtual
    ? "no camera attached"
    : `${(v.objects || []).length} objects · ${v.latency_ms ?? "—"} ms${v.pointing?.active > 0.3 ? " · pointing" : ""}`;
  const mic = $("mic");
  mic.className = "mic" + (s.speech?.speaking ? " speaking" : s.speech?.listening ? " on" : "");
  mic.style.transform = s.speech ? `scale(${1 + Math.min(1.5, (s.speech.level || 0) / 3)})` : "";
}

function gripText(s, contract) {
  if (contract.verb !== "pick_place") return "—";
  const cap = s.objects.find((o) => o.id === contract.object_id)?.max_grip ?? 1;
  if (contract.grip_effort == null) return `default · ${contract.object_name || "object"} max ${pct(cap)}`;
  return contract.grip_effort > cap
    ? `${pct(contract.grip_effort)} requested → ${pct(cap)} (${contract.object_name} max)`
    : pct(contract.grip_effort);
}

function renderRequest(s) {
  const pending = s.pending;
  const contract = pending?.contract || s.last?.contract;
  const decision = pending?.decision || s.last?.decision;
  const v = $("verdict");
  if (!contract) {
    v.textContent = "";
    v.className = "verdict";
    $("contract").innerHTML = "";
    $("ask").hidden = true;
    return;
  }
  $("heard").textContent = contract.utterance ? `“${contract.utterance}”` : "Request";
  v.textContent = decision.verdict;
  v.className = `verdict ${decision.verdict}`;
  const name = contract.object_name || (contract.unresolved.includes("target") ? "unresolved" : "—");
  const evidence = Object.entries(contract.evidence)
    .map(([k, val]) => `<span>${words(k)} ${val.toFixed ? val.toFixed(2) : val}</span>`)
    .join("");
  $("contract").innerHTML = `
    <dt>Action</dt><dd>${words(contract.verb)}</dd>
    <dt>Object</dt><dd>${name}</dd>
    <dt>Grip</dt><dd>${gripText(s, contract)}</dd>
    <dt>Confidence</dt><dd class="conf"><span class="track"><span style="width:${pct(contract.confidence)}"></span></span>${pct(contract.confidence)}</dd>
    <dt>Evidence</dt><dd class="evidence">${evidence || "—"}</dd>
    <dt>Compiled by</dt><dd>${contract.source}${s.reply ? ` · “${s.reply}”` : ""}</dd>
    <dt>Why</dt><dd>${decision.reason}</dd>`;
  const ask = $("ask");
  if (pending && decision.verdict === "ask") {
    ask.hidden = false;
    if (decision.clarify) {
      ask.innerHTML = `<p>Which one? <span class="count">${pending.expires_in_s}s</span></p><div class="btn-row">${contract.candidates
        .filter((c) => c.score > 0)
        .map((c) => `<button class="btn" data-choose="${c.object_id}">${c.name} · ${pct(c.score)}</button>`)
        .join("")}</div>`;
    } else {
      ask.innerHTML = `<p>Squeeze to confirm <span class="count">${pending.expires_in_s}s</span></p><div class="btn-row"><button class="btn primary" id="confirm-btn">Confirm</button><button class="btn" id="cancel-btn">Cancel</button></div>`;
    }
    ask.querySelectorAll("[data-choose]").forEach((b) => (b.onclick = () => api(`/api/choose/${b.dataset.choose}`)));
    $("confirm-btn") && ($("confirm-btn").onclick = () => api("/api/confirm"));
    $("cancel-btn") && ($("cancel-btn").onclick = () => api("/api/cancel"));
  } else {
    ask.hidden = true;
  }
}

function drawTrace(trace) {
  const c = $("emg-trace");
  const w = (c.width = c.clientWidth * devicePixelRatio);
  const h = (c.height = 70 * devicePixelRatio);
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  if (!trace?.length) return;
  const css = getComputedStyle(document.documentElement);
  for (const [idx, color] of [[1, css.getPropertyValue("--grip")], [2, css.getPropertyValue("--open")]]) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2 * devicePixelRatio;
    ctx.beginPath();
    trace.forEach((p, i) => {
      const x = (i / Math.max(1, trace.length - 1)) * w;
      const y = h - p[idx] * (h - 4) - 2;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
  }
}

function renderEmg(s) {
  const e = s.emg;
  if (!e) {
    $("emg-meta").textContent = "off";
    return;
  }
  $("grip-bar").style.width = pct(e.effort);
  $("open-bar").style.width = pct(e.extensor);
  $("grip-val").textContent = pct(e.effort);
  $("open-val").textContent = pct(e.extensor);
  $("grip-confirm").style.left = pct(e.confirm_effort);
  $("open-stop").style.left = pct(e.stop_effort);
  $("emg-meta").textContent = e.error || `${e.source} · ${e.calibrated ? "profile " + e.profile : "uncalibrated"} · ${e.age_ms ?? "—"} ms`;
  drawTrace(e.trace);
  $("synthetic").hidden = e.source !== "synthetic";
  $("cal-state").textContent = e.calibrated ? `profile ${e.profile}` : "not calibrated";
  $("cal-state").className = `pill ${e.calibrated ? "ok" : "warn"}`;
}

function renderArm(s) {
  const a = s.arm;
  const el = $("arm-state");
  el.textContent = a.state;
  el.className = `pill ${{ ready: "ok", moving: "ok", stopped: "bad", fault: "bad", disarmed: "warn" }[a.state] || ""}`;
  $("arm-progress").style.width = pct(a.progress || 0);
  $("arm-detail").textContent = a.reason
    ? a.reason
    : a.skill
      ? `${words(a.skill)} · ${a.stage}${a.target ? " · " + (objectNames[a.target] || a.target) : ""}`
      : a.state === "disarmed"
        ? "Arm to allow motion."
        : "Idle.";
  $("load-bar").style.width = pct((a.gripper_load || 0) / 0.5);
  $("load-val").textContent = a.gripper_load == null ? "—" : pct(a.gripper_load);
}

const KIND = {
  "speech.utterance": ["heard", ""], intent: ["decision", ""], "arm.started": ["arm", ""],
  "arm.completed": ["done", "done"], "arm.stopped": ["stopped", "stop"], "arm.fault": ["fault", "fault"],
  "emg.squeeze": ["squeeze", "emg"], "emg.open_hand": ["open hand", "emg"], "intent.cancelled": ["cancelled", ""],
  "intent.expired": ["expired", ""], "intent.blocked": ["blocked", "fault"], "compiler.llm": ["muse", ""],
  "trial.started": ["trial", ""], "trial.ended": ["trial", "done"],
};
function describe(e) {
  switch (e.kind) {
    case "speech.utterance": return `“${e.text}”${e.asr_ms ? ` · ASR ${e.asr_ms} ms` : ""}`;
    case "intent": return `${words(e.contract.verb)} ${e.contract.object_name || ""} → ${e.decision.verdict}: ${e.decision.reason}`;
    case "arm.started": return `${words(e.skill)} ${objectNames[e.object_id] || ""}${e.grip ? ` · grip ${pct(e.grip.effort)}` : ""} · by ${e.confirmed_by}`;
    case "arm.completed": return `${words(e.skill)} in ${e.duration_s}s · peak load ${pct(e.peak_load)}`;
    case "arm.stopped": return e.reason;
    case "arm.fault": return e.reason;
    case "emg.squeeze": return `effort ${pct(e.effort)} held ${e.held_ms} ms`;
    case "emg.open_hand": return `extensor ${pct(e.extensor)}`;
    case "compiler.llm": return e.ok ? `interpreted in ${e.ms} ms` : `failed: ${e.error}`;
    case "trial.started": return `#${e.trial} expect ${words(e.expected_action)} ${e.expected_object || ""} (${e.condition})`;
    case "trial.ended": return `#${e.trial} ${words(e.outcome)}`;
    default: return e.reason || e.detail || "";
  }
}
function renderActivity(s) {
  const items = [...s.events].reverse().filter((e) => KIND[e.kind]);
  $("activity").innerHTML = items
    .slice(0, 30)
    .map((e) => {
      const [label, cls] = KIND[e.kind];
      const ago = Math.max(0, s.t - e.t);
      return `<li><time>${ago < 60 ? ago.toFixed(1) + "s" : Math.round(ago / 60) + "m"} ago</time><span class="k ${cls}">${label}</span><span>${describe(e)}</span></li>`;
    })
    .join("");
  const stopped = s.events.filter((e) => e.kind === "arm.stopped").at(-1);
  const trigger = stopped && s.events.filter((e) => (e.kind === "emg.open_hand") && e.t <= stopped.t).at(-1);
  $("latency").textContent = trigger ? `last open-hand → stop: ${Math.round((stopped.t - trigger.t) * 1000)} ms` : "";
}

function renderSetup(s) {
  const rows = [
    ["Arm", `${s.arm.backend}${s.arm.hardware ? " (SO-101)" : ""} · ${s.arm.state}`],
    ["EMG", s.emg ? `${s.emg.source} · ${s.emg.live ? "streaming" : "no data"} · rms ${s.emg.rms.join(" / ")}` : "off"],
    ["Camera", s.vision.virtual ? "off (virtual table)" : `${s.vision.live ? "live" : "no frames"} · detector ${s.vision.detector || "none"} · hands ${s.vision.hand_tracking ? "on" : "off"}`],
    ["Speech", s.speech ? `${s.speech.asr || "loading"}${s.speech.error ? " · " + s.speech.error : ""}` : "off (typed only)"],
    ["Interpreter", s.llm.enabled ? `${s.llm.model}${s.llm.error ? " · " + s.llm.error : ""}` : "grammar only (set MODEL_API_KEY for Muse Spark)"],
  ];
  if (s.vision.errors?.length) rows.push(["Vision issues", s.vision.errors.join("; ")]);
  $("devices").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
  $("poses").textContent = s.arm.hardware
    ? s.arm.untaught.length ? `Untaught: ${s.arm.untaught.join(", ")} — run h2m arm teach <pose>` : "All poses taught."
    : "Virtual arm: placeholder poses are fine.";
  $("objects").innerHTML = s.objects.map((o) => `<li>${o.name} <span class="muted">(max grip ${pct(o.max_grip)})</span></li>`).join("");
}

function renderSessions(s) {
  const sess = s.session;
  $("session-state").textContent = sess.session_id ? `recording · ${sess.session_id}` : "not recording";
  $("session-state").className = `pill ${sess.session_id ? "bad" : ""}`;
  $("session-btn").textContent = sess.session_id ? "Stop session" : "Start session";
  if ($("t-object").options.length !== s.objects.length + 1) {
    $("t-object").innerHTML = `<option value="">(none)</option>` + s.objects.map((o) => `<option value="${o.id}">${o.name}</option>`).join("");
  }
  $("trial-now").textContent = sess.trial
    ? `Trial #${sess.trial.trial}: expect ${words(sess.trial.expected_action)} ${objectNames[sess.trial.expected_object] || ""} · ${sess.trial.condition}`
    : "No trial running.";
  const counts = {};
  sess.outcomes.forEach((o) => (counts[o] = (counts[o] || 0) + 1));
  $("tally").innerHTML = Object.entries(counts).map(([k, n]) => `<span>${words(k)} ${n}</span>`).join("") || "";
}

function render(s) {
  S = s;
  objectNames = Object.fromEntries(s.objects.map((o) => [o.id, o.name]));
  renderChips(s);
  renderScene(s);
  renderRequest(s);
  renderEmg(s);
  renderArm(s);
  renderActivity(s);
  renderSetup(s);
  renderSessions(s);
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/live`);
  ws.onmessage = (m) => render(JSON.parse(m.data));
  ws.onclose = () => {
    $("chips").innerHTML = chip("Runtime offline — reconnecting", "bad");
    setTimeout(connect, 1000);
  };
}
connect();
