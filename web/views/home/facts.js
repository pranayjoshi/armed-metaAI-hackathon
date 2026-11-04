// Home: turns the live snapshot into the one-line fact each portal carries, and the system-health strip.
// Pure functions. Every string is derived from state or events; nothing is invented.

const words = (v, fb = "") => (v ? String(v).replaceAll("_", " ") : fb);
const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);

/** "just now", "12 s ago", "4 m ago" from seconds (coarse on purpose: the label is read, not timed). */
export function agoText(sec) {
  if (sec == null || !Number.isFinite(sec)) return "";
  if (sec < 2) return "just now";
  if (sec < 60) return `${Math.round(sec)} s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)} m ago`;
  return `${Math.round(sec / 3600)} h ago`;
}

/** @returns {{text:string, tone:string}} tone in "", ok, warn, bad, muse */
export function portalFacts(s, history, rulesCount, rulesError) {
  const out = {};
  out.replay = replayFact(s);
  if (!s.arm) {
    // Replay server with no running simulation (`h2m replay <dir>`): the snapshot has no arm, EMG, vision or events.
    const quiet = { text: "no simulation running", tone: "" };
    return { ...out, stage: quiet, flow: quiet, signals: quiet, learn: quiet, lab: quiet };
  }

  // Stage: arm state + what is on the table
  const a = s.arm;
  const objs = s.vision?.objects || [];
  const objText = objs.length ? `${objs[0].name}${objs.length > 1 ? ` +${objs.length - 1}` : ""}` : "table empty";
  if (a.state === "moving") out.stage = { text: `arm moving · ${words(a.skill, "task")}${a.target ? ` ${s.objects?.find((o) => o.id === a.target)?.name || a.target}` : ""}`, tone: "ok" };
  else if (a.state === "ready") out.stage = { text: `arm ready · ${objText}`, tone: "ok" };
  else if (a.state === "disarmed") out.stage = { text: "arm disarmed", tone: "warn" };
  else out.stage = { text: `arm ${a.state}${a.reason ? ` · ${a.reason}` : ""}`, tone: "bad" };

  // Workflow: pending request, else last decision
  if (s.pending) {
    const c = s.pending.contract;
    out.flow = { text: `waiting · ${words(c.verb)} ${c.object_name || ""}`.trim(), tone: "warn" };
  } else {
    let last = null;
    for (let i = history.length - 1; i >= 0; i--) if (history[i].kind === "intent") { last = history[i]; break; }
    if (!last && s.last) last = null;
    out.flow = last
      ? { text: `last decision: ${last.decision.verdict} ${agoText(s.t - last.t)}`, tone: "" }
      : { text: "no decision yet", tone: "" };
  }

  // Signals: live grip / brace
  const e = s.emg;
  if (!e) out.signals = { text: "EMG off", tone: "warn" };
  else if (e.error) out.signals = { text: "EMG error", tone: "bad" };
  else if (!e.live) out.signals = { text: "EMG no signal", tone: "warn" };
  else out.signals = { text: `grip ${pct(e.effort)} · brace ${pct(e.brace)}`, tone: e.brace >= (e.brace_effort || 0.35) ? "bad" : "" };

  // Learning: waiting proposal beats saved rules
  const L = s.learning || {};
  if (L.proposal) out.learn = { text: L.proposal.rule ? "rule waiting for approval" : "incident waiting for approval", tone: "muse" };
  else if (L.error) out.learn = { text: "learning error", tone: "bad" };
  else if (rulesError) out.learn = { text: "rules unreadable", tone: "bad" };
  else if (L.incident) out.learn = { text: "incident captured", tone: "muse" };
  else if (rulesCount == null) out.learn = { text: "—", tone: "" };
  else out.learn = { text: rulesCount === 0 ? "no rules saved" : `${rulesCount} rule${rulesCount === 1 ? "" : "s"} saved`, tone: rulesCount ? "ok" : "" };

  // Lab: recording beats calibration
  if (s.session?.session_id) out.lab = { text: `REC ${s.session.trials_done} trial${s.session.trials_done === 1 ? "" : "s"}`, tone: "bad" };
  else if (!e) out.lab = { text: "EMG off", tone: "warn" };
  else out.lab = { text: `${e.calibrated ? "calibrated" : "uncalibrated"} · profile ${e.profile || "default"}`, tone: e.calibrated ? "ok" : "warn" };

  return out;
}

/** The Replay portal's fact, only from `state.scenario` / `state.replay`. */
function replayFact(s) {
  const sc = s.scenario;
  if (!sc) {
    const n = s.replay?.incidents ?? 0;
    return { text: `${n} incident${n === 1 ? "" : "s"} recorded`, tone: n ? "muse" : "" };
  }
  const label = sc.label || s.label || "SIMULATION";
  const steps = sc.steps || [];
  const idx = steps.findIndex((x) => x.status === "active");
  if (sc.state === "running") return { text: idx >= 0 ? `${label} · step ${idx + 1} of ${steps.length}` : `${label} · running`, tone: "ok" };
  if (sc.state === "done") return { text: `${label} · finished`, tone: "muse" };
  if (sc.state === "aborted") return { text: `${label} · stopped`, tone: "bad" };
  if (sc.state === "failed") return { text: `${label} · failed`, tone: "bad" };
  return { text: `${label} · ready`, tone: "" };
}

/** System-health strip: arm, EMG, vision, speech, Muse. */
export function healthItems(s) {
  const a = s.arm;
  const items = [];
  if (s.mode === "replay") items.push({ key: "Mode", value: s.label || "REPLAY", tag: "READ-ONLY", tone: "warn" });
  if (!a) {
    // Replay with no simulation: only the recordings exist.
    items.push({ key: "Incidents", value: String(s.replay?.incidents ?? 0), tag: "", tone: "" });
    items.push({ key: "Sessions", value: String(s.replay?.sessions ?? 0), tag: "", tone: "" });
    return items;
  }
  const armTone = { ready: "ok", moving: "ok", disarmed: "warn", stopped: "bad", fault: "bad" }[a.state] || "";
  items.push({ key: "Arm", value: a.state, tag: a.hardware ? "" : "VIRTUAL", tone: armTone });

  const e = s.emg;
  if (!e) items.push({ key: "EMG", value: "off", tag: "", tone: "warn" });
  else {
    const tone = e.error ? "bad" : !e.live ? "warn" : e.hardware ? "ok" : "warn";
    items.push({ key: "EMG", value: e.error ? "error" : e.live ? (e.calibrated ? "live" : "uncalibrated") : "no signal", tag: e.hardware ? "" : "SYNTHETIC", tone });
  }

  const v = s.vision || {};
  if (v.virtual) items.push({ key: "Vision", value: `${(v.objects || []).length} objects`, tag: "VIRTUAL", tone: "warn" });
  else items.push({ key: "Vision", value: v.live ? `${v.fps || 0} fps` : "no camera", tag: "", tone: v.live ? "ok" : "bad" });

  const sp = s.speech;
  if (!sp && s.mode === "replay") { /* the simulation has no microphone; the strip stays short */ }
  else if (!sp) items.push({ key: "Speech", value: "off", tag: "", tone: "" });
  else items.push({ key: "Speech", value: sp.error ? "mic error" : sp.listening ? "listening" : words(sp.asr, "loading"), tag: "", tone: sp.error ? "bad" : sp.listening ? "ok" : "warn" });

  const l = s.llm || {};
  const sim = s.mode === "replay" && s.label === "SIMULATION"; // the explanation is canned: no model, no network
  if (sim) items.push({ key: "Muse", value: "canned", tag: "SIMULATED", tone: "" });
  else items.push({
    key: "Muse",
    value: l.error ? "error" : l.enabled ? (l.last_ms ? `${l.last_ms} ms` : "ready") : "grammar only",
    tag: "",
    tone: l.error ? "bad" : l.enabled ? "muse" : "",
  });

  // Cards: straight from state.selection (absent in snapshots that predate it, so no item then)
  const c = s.selection;
  if (c) {
    if (!c.enabled) items.push({ key: "Cards", value: "off", tag: "", tone: "", title: c.blocked || "selection.enabled is false" });
    else if (c.degraded) items.push({ key: "Cards", value: "scanning", tag: "DEGRADED", tone: "warn", title: String(c.degraded) });
    else if (c.blocked) items.push({ key: "Cards", value: `${words(c.mode, "—")} · held`, tag: "", tone: "warn", title: String(c.blocked) });
    else items.push({ key: "Cards", value: words(c.mode, "on"), tag: "", tone: "ok", title: c.cards?.length ? `${c.cards.length} card${c.cards.length === 1 ? "" : "s"} showing` : "selection on, no cards showing" });
  }
  return items;
}
