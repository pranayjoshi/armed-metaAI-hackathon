// Snapshot -> what each node shows right now (its status ring, state line, honesty tag).
// Only fields that exist in /api/live are read. `level` drives the ring fill (0..1) for always-on nodes.

const fix = (v, d = 2) => (Number.isFinite(v) ? v.toFixed(d) : "—");
const clamp01 = (v) => Math.min(1, Math.max(0, Number.isFinite(v) ? v : 0));
const words = (v, fb = "—") => (v ? String(v).replaceAll("_", " ") : fb);

/**
 * @returns {{stat:string, status:string, level:number|null, tag:string|null, mark:number|null}}
 * status: off | idle | live | ok | warn | hold | bad
 * mark: threshold position on the ring (0..1), when the node has one.
 */
export function live(id, s, extra) {
  const none = { stat: "waiting for runtime", status: "off", level: null, tag: null, mark: null };
  if (!s) return none;
  // Replay server with no running simulation: the snapshot has no arm, EMG, vision or events. Dim everything, say why.
  if (!s.arm) return { ...none, stat: s.mode === "replay" ? "no simulation running" : "waiting for runtime" };
  const sim = s.mode === "replay" && s.label === "SIMULATION"; // virtual arm, synthetic EMG, canned explanation
  const { emg, arm, vision = {}, speech, llm = {}, learning, pending, last } = s;
  switch (id) {
    case "mic":
      if (!speech) return { stat: "off · typed input only", status: "off", level: null, tag: "SPEECH OFF", mark: null };
      if (speech.error) return { stat: "mic error", status: "bad", level: null, tag: null, mark: null };
      return {
        stat: speech.speaking ? "hearing speech" : speech.listening ? "listening" : "muted",
        status: speech.speaking ? "live" : speech.listening ? "idle" : "warn",
        level: clamp01((speech.level || 0) / 2), tag: null, mark: 0.5,
      };
    case "typed":
      if (sim) return { stat: "scripted request", status: "idle", level: null, tag: "SIMULATED", mark: null };
      return { stat: "API · UI · CLI", status: "idle", level: null, tag: null, mark: null };
    case "asr":
      if (!speech) return { stat: "off", status: "off", level: null, tag: "SPEECH OFF", mark: null };
      return {
        stat: speech.error ? "unavailable" : speech.asr ? `${speech.asr} · local` : "loading model…",
        status: speech.error ? "bad" : speech.asr ? "idle" : "warn", level: null, tag: null, mark: null,
      };
    case "gram":
      return { stat: "local · offline", status: "idle", level: null, tag: null, mark: null };
    case "stop":
      return { stat: "local · no network", status: "idle", level: null, tag: null, mark: null };
    case "muse":
      if (sim) return { stat: "canned · no model", status: "idle", level: null, tag: "SIMULATED", mark: null };
      if (!llm.enabled) return { stat: "grammar only", status: "off", level: null, tag: "MUSE OFF", mark: null };
      if (llm.error) return { stat: "last call failed", status: "bad", level: null, tag: null, mark: null };
      return { stat: llm.model ? `ready · ${String(llm.model).slice(0, 18)}` : "ready", status: "idle", level: null, tag: null, mark: null };
    case "cam": {
      if (vision.virtual) return { stat: `virtual table · ${vision.objects?.length ?? 0} objects`, status: "warn", level: null, tag: "VIRTUAL", mark: null };
      const err = vision.errors && vision.errors.length;
      return {
        stat: vision.live ? `${vision.fps || 0} fps · ${vision.objects?.length ?? 0} objects` : "no frames",
        status: err ? "bad" : vision.live ? "live" : "warn", level: null, tag: null, mark: null,
      };
    }
    case "contract": {
      const c = last?.contract;
      return { stat: c ? `${words(c.verb)} · conf ${fix(c.confidence)}` : "no request yet", status: c ? "idle" : "off", level: null, tag: null, mark: null };
    }
    case "gate": {
      if (pending) {
        const sec = pending.expires_in_s;
        return {
          stat: pending.decision?.clarify ? `which one? · ${fix(sec, 1)} s` : `awaiting yes · ${fix(sec, 1)} s`,
          status: "warn", level: clamp01((sec || 0) / 8), tag: null, mark: null,
        };
      }
      const v = last?.decision?.verdict;
      return { stat: v ? `last: ${v}` : "no decision yet", status: v === "approve" ? "ok" : v === "reject" ? "bad" : v === "hold" ? "hold" : v === "ask" ? "warn" : "off", level: null, tag: null, mark: null };
    }
    case "exec":
      return { stat: arm.skill ? `${words(arm.skill)} · ${words(arm.stage, "…")}` : "idle", status: arm.skill ? "live" : "idle", level: null, tag: null, mark: null };
    case "arm": {
      const stateTone = { ready: "ok", moving: "live", disarmed: "warn", stopped: "bad", fault: "bad" }[arm.state] || "idle";
      return {
        stat: `${arm.state}${arm.skill ? ` · ${words(arm.skill)}` : ""}`,
        status: stateTone, level: arm.progress ?? null, tag: arm.hardware ? null : "VIRTUAL ARM", mark: null,
      };
    }
    case "gang":
      if (!emg) return { stat: "EMG off", status: "off", level: null, tag: "EMG OFF", mark: null };
      return {
        stat: emg.error ? "error" : emg.live ? `${emg.source} · ${emg.age_ms ?? "—"} ms old` : "no signal",
        status: emg.error ? "bad" : emg.live ? "live" : "warn",
        level: clamp01(Math.max(emg.effort, emg.extensor, emg.brace)),
        tag: emg.hardware ? (emg.calibrated ? null : "UNCALIBRATED") : "SYNTHETIC EMG", mark: null,
      };
    case "squeeze":
      if (!emg) return { ...none, stat: "EMG off" };
      return { stat: `effort ${fix(emg.effort)} / ${fix(emg.confirm_effort)}`, status: emg.effort >= emg.confirm_effort ? "live" : "idle", level: clamp01(emg.effort), mark: emg.confirm_effort, tag: emg.hardware ? null : "SYNTHETIC" };
    case "open":
      if (!emg) return { ...none, stat: "EMG off" };
      return { stat: `extensor ${fix(emg.extensor)} / ${fix(emg.stop_effort)}`, status: emg.extensor >= emg.stop_effort ? "live" : "idle", level: clamp01(emg.extensor), mark: emg.stop_effort, tag: emg.hardware ? null : "SYNTHETIC" };
    case "brace":
      if (!emg) return { ...none, stat: "EMG off" };
      return {
        stat: `brace ${fix(emg.brace)} / ${fix(emg.brace_effort)}`, status: emg.brace >= emg.brace_effort ? "live" : "idle",
        level: clamp01(emg.brace), mark: emg.brace_effort, tag: emg.hardware ? (emg.brace_calibrated ? null : "BRACE UNCALIBRATED") : "SYNTHETIC",
      };
    case "freeze": {
      const stopped = arm.state === "stopped";
      return { stat: stopped ? `latched · ${(arm.reason || "").slice(0, 22)}` : "armed to freeze · no model", status: stopped ? "bad" : "idle", level: null, tag: null, mark: null };
    }
    case "incident": {
      const i = learning?.incident;
      return { stat: i ? i.state : "no incident yet", status: !i ? "off" : i.state === "explained" ? "ok" : i.state === "capturing" || i.state === "explaining" ? "live" : "idle", level: null, tag: null, mark: null };
    }
    case "wonder":
      if (sim) return { stat: "canned explanation", status: "idle", level: null, tag: "SIMULATED", mark: null };
      return {
        stat: learning?.muse ? "Muse Wonder ready" : "no Muse key · fallback",
        status: learning?.muse ? "idle" : "off", level: null, tag: learning?.muse ? null : "FALLBACK", mark: null,
      };
    case "proposal": {
      const p = learning?.proposal;
      return { stat: p ? `waiting · ${fix(p.expires_in_s, 0)} s left` : "none waiting", status: p ? "warn" : "off", level: p ? clamp01(p.expires_in_s / 30) : null, tag: null, mark: null };
    }
    case "approval":
      if (sim) return { stat: "scripted approval", status: learning?.proposal ? "warn" : "idle", level: null, tag: "SIMULATED", mark: null };
      return { stat: "squeeze · yes · click", status: learning?.proposal ? "warn" : "idle", level: null, tag: null, mark: null };
    case "memory": {
      const n = extra?.rules;
      return { stat: n == null ? "rules: …" : n === -1 ? "rules unreadable" : `${n} saved rule${n === 1 ? "" : "s"}`, status: n === -1 ? "bad" : n ? "ok" : "idle", level: null, tag: null, mark: null };
    }
    default:
      return none;
  }
}

/** Sparkline series for the always-on EMG nodes: [[value]...] normalised 0..1, newest last. */
export function series(kind, s) {
  const emg = s?.emg;
  if (!emg) return null;
  switch (kind) {
    case "effort": return { a: (emg.trace || []).map((r) => r[1]) };
    case "extensor": return { a: (emg.trace || []).map((r) => r[2]) };
    case "brace": return { a: (emg.brace_trace || []).map((r) => r[1]) };
    case "emg": return { a: (emg.trace || []).map((r) => r[1]), b: (emg.trace || []).map((r) => r[2]) };
    default: return null;
  }
}
