// Event -> animation plan. Pure: turns one runtime event into an ordered list of actions
// (packets to send, nodes to pulse, payload previews and measured latencies to record).
// Nothing here is invented: every number is read from the event or is a difference of two event timestamps `t`.

import { words } from "../../js/core.js";

const clip = (s, n) => {
  s = String(s ?? "");
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
};
const fix = (v, d = 2) => (Number.isFinite(v) ? v.toFixed(d) : "—");
const pct = (v) => (Number.isFinite(v) ? `${Math.round(v * 100)}%` : "—");

export const hop = (edge, o = {}) => ({ k: "hop", edge, ...o });
export const pulse = (node, o = {}) => ({ k: "pulse", node, ...o });
export const inject = (node, o = {}) => ({ k: "inject", node, ...o });

/** A record of "what this node just saw": preview line, raw payload, optional measured latency. */
export function S(node, line, raw, o = {}) {
  return { node, line, raw, ...o };
}

export const verdictTone = (v) => ({ approve: "ok", ask: "warn", hold: "hold", reject: "bad" })[v] || null;

/** Latest earlier event of a kind (optionally filtered), from the shared history. */
function before(env, kind, e, pred) {
  const h = env.hist;
  for (let i = h.length - 1; i >= 0; i--) {
    const x = h[i];
    if (x.seq >= e.seq) continue;
    if (x.kind === kind && (!pred || pred(x))) return x;
  }
  return null;
}
/** Did the arm actually stop because of this detector event? (A detector firing while idle only logs.) */
const stoppedAfter = (env, e) => env.hist.some((x) => x.kind === "arm.stopped" && x.seq > e.seq && x.t - e.t < 1);
const dtMs = (a, b) => (a && b ? Math.max(0, (b.t - a.t) * 1000) : null);

export function ruleText(rule) {
  if (!rule) return "—";
  const when = (rule.when_holding || []).join("+");
  return `${when} → ${words(rule.response)} · avoid ${rule.avoid}`;
}

export function fmtMs(v) {
  if (!Number.isFinite(v)) return "—";
  if (v < 0.01) return "<0.01 ms";
  if (v < 1) return `${v.toFixed(2)} ms`;
  if (v < 10) return `${v.toFixed(1)} ms`;
  if (v < 1000) return `${Math.round(v)} ms`;
  return `${(v / 1000).toFixed(v < 10000 ? 2 : 1)} s`;
}

/** Short one-line text for the trace ribbon. */
export function short(e, names = {}) {
  switch (e.kind) {
    case "speech.utterance": return `“${clip(e.text, 40)}”${e.asr_ms ? ` · ASR ${e.asr_ms} ms` : e.source === "typed" ? " · typed" : ""}`;
    case "intent": {
      const c = e.contract || {};
      const d = e.decision || {};
      const tag = e.confirmed_by ? ` · by ${e.confirmed_by}` : "";
      return `${d.verdict} · ${words(c.verb)}${c.object_name ? ` ${c.object_name}` : ""} · ${fix(c.confidence)}${tag}`;
    }
    case "compiler.llm": return e.ok ? `Muse answered in ${e.ms} ms` : `Muse failed: ${clip(e.error, 40)}`;
    case "arm.started": return `${words(e.skill)} ${names[e.object_id] || e.object_id || ""} · by ${e.confirmed_by}`;
    case "arm.completed": return `${words(e.skill)} done in ${e.duration_s}s`;
    case "arm.stopped": return `stopped · ${clip(e.reason, 36)}`;
    case "arm.fault": return `fault · ${clip(e.reason, 40)}`;
    case "emg.squeeze": return `squeeze ${pct(e.effort)} held ${e.held_ms} ms`;
    case "emg.squeeze_rejected": return `squeeze ignored · ${clip(e.reason, 34)}`;
    case "emg.open_hand": return `open hand ${pct(e.extensor)}`;
    case "emg.brace": return `brace · detected in ${e.detect_ms} ms · peak ${fix(e.peak_brace)}`;
    case "intent.cancelled": return `cancelled · ${clip(e.reason, 34)}`;
    case "intent.expired": return "request expired, no answer";
    case "intent.blocked": return `blocked · ${clip(e.reason, 40)}`;
    case "incident.triggered": return `incident from ${e.source}`;
    case "incident.saved": return `clip saved${e.held ? ` · held ${clip(e.held, 24)}` : ""}`;
    case "incident.explained": return e.proposal ? `Wonder proposes: ${ruleText(e.proposal.rule)}` : `Wonder: ${clip(e.say, 46)}`;
    case "incident.error": return `incident error · ${clip(e.detail, 40)}`;
    case "rule.approved": return `rule saved · ${ruleText(e.rule)} · by ${e.by}`;
    case "rule.rejected": return `rule declined · by ${e.by}`;
    case "rule.expired": return "rule proposal expired";
    case "rule.error": return `rule error · ${clip(e.detail, 40)}`;
    case "speech.ambiguous": return clip(e.reason, 50);
    case "emg.error": case "speech.error": case "vision.error": return clip(e.detail, 50);
    default: return e.reason || e.detail || "";
  }
}

/** Kinds that appear in the trace ribbon and can drive the graph. */
export const TRACE_KINDS = new Set([
  "speech.utterance", "compiler.llm", "intent", "intent.cancelled", "intent.expired", "intent.blocked",
  "arm.started", "arm.completed", "arm.stopped", "arm.fault",
  "emg.squeeze", "emg.squeeze_rejected", "emg.open_hand", "emg.brace",
  "incident.triggered", "incident.saved", "incident.explained", "incident.error",
  "rule.approved", "rule.rejected", "rule.expired", "rule.error",
  "speech.ambiguous", "speech.error", "vision.error", "emg.error",
]);

/**
 * plan(e, env) -> actions. env = { state, hist, names }.
 * action.src is applied when the packet leaves; action.set when it arrives (or at once for pulses/seeding).
 */
export function plan(e, env) {
  const A = [];
  const st = env.state || {};
  switch (e.kind) {
    case "speech.utterance": {
      const voice = e.source === "voice" || e.asr_ms != null;
      const line = `“${clip(e.text, 44)}”`;
      const verb = words(e.verb, "?");
      const gramSet = S("gram", `${verb} ← ${line}`, e);
      if (voice) {
        A.push(hop("mic-asr", {
          src: S("mic", line, e),
          set: S("asr", `${line}`, e, { lat: e.asr_ms, latLabel: "transcription" }),
        }));
        A.push(hop("asr-gram", { set: gramSet }));
      } else {
        A.push(hop("typed-gram", { src: S("typed", line, e), set: gramSet }));
      }
      if (e.verb === "stop") {
        A.push(hop("gram-stop", { set: S("stop", `“${clip(e.text, 30)}” → stop`, e) }));
        A.push(hop("stop-freeze", { set: S("freeze", `stop word · “${clip(e.text, 24)}”`, e) }));
      }
      break;
    }
    case "compiler.llm":
      A.push(hop("gram-muse", {
        set: S("muse", e.ok ? "interpreted the request" : `failed · ${clip(e.error, 30)}`, e, {
          lat: e.ms, latLabel: "Muse call", tone: e.ok ? null : "bad",
        }),
      }));
      break;
    case "intent": {
      const c = e.contract || {};
      const d = e.decision || {};
      const obj = c.object_name || c.object_id;
      const sContract = S("contract", `${words(c.verb)}${obj ? ` · ${obj}` : ""}${c.route ? ` · ${c.route}` : ""}`, e, {
        lat: e.compile_ms, latLabel: "utterance → decision",
      });
      const sGate = S("gate", `${d.verdict} · ${fix(c.confidence)} · ${obj || words(c.verb)}`, e, { tone: verdictTone(d.verdict) });
      if (e.confirmed_by) {
        if (e.confirmed_by === "emg") A.push(hop("squeeze-gate", { set: sGate }));
        else A.push(inject("gate", { label: e.confirmed_by, hue: "ok", set: sGate }));
        break;
      }
      if (e.chosen_by) {
        A.push(inject("contract", { label: e.chosen_by, hue: "spine", set: sContract }));
      } else if (e.resolved_by) {
        A.push(hop("cam-contract", { set: sContract }));
      } else {
        const viaMuse = c.source === "muse" || c.source === "llm";
        A.push(hop(viaMuse ? "muse-contract" : "gram-contract", { set: sContract }));
        if (c.object_id || (c.candidates || []).length) A.push(hop("cam-contract", { set: sContract }));
      }
      if ((c.applied_rules || []).length) {
        A.push(hop("memory-gate", { src: S("memory", `applied ${c.applied_rules.join(", ")}`, e) }));
      }
      A.push(hop("contract-gate", { set: sGate }));
      break;
    }
    case "arm.started": {
      const intent = before(env, "intent", e);
      const lat = dtMs(intent, e);
      const obj = env.names?.[e.object_id] || e.object_id || "";
      const grip = e.grip ? ` · grip ${pct(e.grip.effort)}` : "";
      A.push(hop("gate-exec", {
        set: S("exec", `${words(e.skill)} ${obj}${e.route ? ` · ${e.route}` : ""}${grip}`, e, { lat, latLabel: "decision → start" }),
      }));
      A.push(hop("exec-arm", { set: S("arm", `${words(e.skill)} · by ${e.confirmed_by}`, e) }));
      break;
    }
    case "arm.completed":
      A.push(pulse("arm", { set: S("arm", `done ${e.duration_s}s · load ${pct(e.peak_load)}`, e, { tone: "ok" }) }));
      A.push(pulse("exec", { set: S("exec", `finished ${words(e.skill)}`, e, { tone: "ok" }) }));
      break;
    case "arm.stopped": {
      const r = String(e.reason || "");
      const kind = /brace/i.test(r) ? "brace" : /open hand/i.test(r) ? "open" : /^(voice|typed):/.test(r) ? "word" : "ui";
      const origin =
        kind === "brace" ? before(env, "emg.brace", e)
        : kind === "open" ? before(env, "emg.open_hand", e)
        : kind === "word" ? before(env, "speech.utterance", e, (x) => x.verb === "stop")
        : null;
      const lat = origin && e.t - origin.t < 2 ? dtMs(origin, e) : null;
      const from = { brace: "brace", open: "open hand", word: "stop word" }[kind];
      const fz = S("freeze", `${clip(r, 26)}${e.stage ? ` · ${e.stage}` : ""}`, e, lat != null ? { lat, latLabel: `${from} → arm stopped` } : {});
      if (kind === "ui") A.push(inject("freeze", { label: "stop", hue: "brace", set: fz }));
      A.push(hop("freeze-arm", { hue: "brace", src: fz, set: S("arm", `stopped · ${clip(r, 24)}`, e, { tone: "bad" }) }));
      break;
    }
    case "arm.fault":
      A.push(pulse("arm", { set: S("arm", `fault · ${clip(e.reason, 30)}`, e, { tone: "bad" }) }));
      break;
    case "emg.squeeze":
      A.push(hop("gang-squeeze", {
        src: S("gang", `squeeze ${pct(e.effort)}`, e),
        set: S("squeeze", `${pct(e.effort)} held ${e.held_ms} ms`, e, { lat: e.held_ms, latLabel: "hold time" }),
      }));
      break;
    case "emg.squeeze_rejected":
      A.push(pulse("squeeze", { set: S("squeeze", `ignored · ${clip(e.reason, 28)}`, e, { tone: "warn" }) }));
      break;
    case "emg.open_hand":
      A.push(hop("gang-open", { src: S("gang", `open hand ${pct(e.extensor)}`, e), set: S("open", `extensor ${pct(e.extensor)}`, e) }));
      A.push(hop("open-freeze", { set: S("freeze", `open hand ${pct(e.extensor)}${stoppedAfter(env, e) ? "" : " · idle, logged"}`, e) }));
      break;
    case "emg.brace":
      A.push(hop("gang-brace", {
        src: S("gang", `brace ${fix(e.peak_brace)}`, e),
        set: S("brace", `peak ${fix(e.peak_brace)} · ${e.trigger || "fast"}`, e, { lat: e.detect_ms, latLabel: "onset → detected" }),
      }));
      A.push(hop("brace-freeze", { set: S("freeze", `brace ${fix(e.peak_brace)}${stoppedAfter(env, e) ? "" : " · idle, logged"}`, e) }));
      break;
    case "intent.cancelled":
      A.push(pulse("gate", { set: S("gate", `cancelled · ${clip(e.reason, 26)}`, e, { tone: "warn" }) }));
      break;
    case "intent.expired":
      A.push(pulse("gate", { set: S("gate", "expired · nobody answered", e, { tone: "warn" }) }));
      break;
    case "intent.blocked":
      A.push(pulse("gate", { set: S("gate", `blocked · ${clip(e.reason, 28)}`, e, { tone: "bad" }) }));
      break;
    case "incident.triggered":
      A.push(hop("freeze-incident", { set: S("incident", `capturing · from ${e.source}`, e) }));
      break;
    case "incident.saved": {
      const trig = before(env, "incident.triggered", e);
      A.push(pulse("incident", {
        set: S("incident", `saved · ${e.held ? `held ${clip(e.held, 18)}` : "nothing held"}`, e, { tone: "ok", lat: dtMs(trig, e), latLabel: "freeze → clip saved" }),
      }));
      break;
    }
    case "incident.explained": {
      const saved = before(env, "incident.saved", e);
      const x = e.explanation || {};
      const wline = x.source === "fallback" ? "fallback · asks what bothered you" : `${x.cause_object || "?"} · ${x.relation ? words(x.relation) : "—"} · ${fix(x.confidence)}`;
      A.push(hop("incident-wonder", { set: S("wonder", wline, e, { lat: dtMs(saved, e), latLabel: "clip saved → explained" }) }));
      if (e.proposal) A.push(hop("wonder-proposal", { set: S("proposal", ruleText(e.proposal.rule), e) }));
      else A.push(pulse("wonder", { set: S("wonder", `no rule proposed · ${wline}`, e, { tone: "warn" }) }));
      break;
    }
    case "incident.error":
      A.push(pulse("incident", { set: S("incident", `error · ${clip(e.detail, 28)}`, e, { tone: "bad" }) }));
      break;
    case "rule.approved": {
      const ex = before(env, "incident.explained", e);
      A.push(hop("proposal-approval", {
        set: S("approval", `approved · by ${e.by}`, e, { tone: "ok", lat: dtMs(ex, e), latLabel: "question → answer" }),
      }));
      if (e.by === "emg") A.push(inject("approval", { label: "squeeze", hue: "grip" }));
      else if (e.by) A.push(inject("approval", { label: e.by, hue: "ok" }));
      A.push(hop("approval-memory", { hue: "ok", set: S("memory", ruleText(e.rule), e, { tone: "ok" }) }));
      break;
    }
    case "rule.rejected":
    case "rule.expired": {
      const why = e.kind === "rule.expired" ? "expired · no answer" : `declined · ${clip(Array.isArray(e.reason) ? e.reason.join("; ") : e.reason || e.by, 24)}`;
      A.push(hop("proposal-approval", { set: S("approval", why, e, { tone: "warn" }) }));
      break;
    }
    case "rule.error":
      A.push(pulse("approval", { set: S("approval", `error · ${clip(e.detail, 28)}`, e, { tone: "bad" }) }));
      break;
    case "speech.ambiguous":
      A.push(pulse("gate", { set: S("gate", clip(e.reason, 34), e, { tone: "warn" }) }));
      break;
    case "speech.error":
      A.push(pulse("asr", { set: S("asr", clip(e.detail, 34), e, { tone: "bad" }) }));
      break;
    case "vision.error":
      A.push(pulse("cam", { set: S("cam", clip(e.detail, 34), e, { tone: "bad" }) }));
      break;
    case "emg.error":
      A.push(pulse("gang", { set: S("gang", clip(e.detail, 34), e, { tone: "bad" }) }));
      break;
    default:
      break;
  }
  void st;
  return A;
}
