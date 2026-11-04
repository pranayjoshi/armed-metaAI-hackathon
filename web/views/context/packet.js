// The packet: what the runtime knows about the desk, row by row, from live data only.
// No posture row and no gaze row, ever.
import { esc } from "../../js/core.js";

export const ROWS = [
  ["clock", "Clock"],
  ["objects", "Objects"],
  ["present", "Present"],
  ["hand", "Hand"],
  ["sounds", "Sounds"],
  ["actions", "Last actions"],
  ["routines", "Routines due"],
  ["rules", "Rules in force"],
];

const sec = (s) => (Number.isFinite(+s) ? `${Math.round(+s)} s` : "");
const txt = (v) => (v == null ? "" : typeof v === "string" ? v : v.text || v.label || v.name || v.phrase || "");
const list = (v) => (Array.isArray(v) ? v : v == null || v === "" ? [] : [v]);
const join = (...a) => a.filter(Boolean).join(" · ");
const tidy = (v) => ({ text: join(txt(v), v?.age_s != null ? sec(v.age_s) : ""), key: txt(v) });

const ACTION_KIND = /^(task\.completed|task\.abandoned|arm\.(started|completed|stopped)|selection\.accept)/;

/** -> { id: { items: [{text, key}], dash: bool } } using packet fields first, then what the runtime already exposes. */
export function buildRows(state, ctx) {
  const scene = state.scene || {};
  const p = scene.packet || state.context || {};
  const rows = {};

  // clock: a string, or epoch seconds
  let clock = p.clock;
  if (typeof clock === "number") clock = new Date(clock * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
  rows.clock = clock ? [{ text: String(clock), key: "clock" }] : [];

  // objects: packet.objects, else scan phrases, else what vision sees today
  let objs = list(p.objects).map((o) => {
    const name = typeof o === "string" ? o : o.name || o.phrase || o.label;
    return { text: join(name, o.slot != null ? `slot ${o.slot}` : "", o.age_s != null ? sec(o.age_s) : scene.scan_age_s != null ? sec(scene.scan_age_s) : ""), key: name };
  });
  if (!objs.length && state.vision?.objects?.length) {
    objs = state.vision.objects.map((o) => ({ text: join(o.name || o.id, o.slot != null ? `slot ${o.slot}` : "", o.virtual ? "virtual" : ""), key: o.name || o.id }));
  }
  rows.objects = objs;

  rows.present = list(p.present).map(tidy);

  const h = p.hand;
  rows.hand = h
    ? [{ text: typeof h === "string" ? h : join(`hand at ${h.where || h.at || h.zone || "?"}`, h.s != null ? sec(h.s) : h.age_s != null ? sec(h.age_s) : ""), key: "hand" }]
    : [];

  rows.sounds = list(p.sounds).map(tidy);

  let acts = list(p.last_actions).map(tidy);
  if (!acts.length && ctx?.store?.history?.length) {
    acts = ctx.store.history.filter((e) => ACTION_KIND.test(e.kind)).slice(-3).reverse().map((e) => ({ text: ctx.describeEvent(e), key: e.kind, ev: true }));
  }
  rows.actions = acts;

  rows.routines = list(p.routines_due).map(tidy);

  let rules = list(p.rules).map(tidy);
  if (!rules.length && ctx?.rules) rules = ctx.rules.map((r) => ({ text: join(`holding ${(r.when_holding || []).join(", ")}`, `${(r.avoid || "").trim() ? "avoid " + r.avoid : ""}`, r.response), key: r.avoid }));
  rows.rules = rules;
  return rows;
}

/** Evidence of the highlighted card -> which rows produced it. Items may be {row, text} or plain strings. */
export function litRows(state, rows) {
  const cards = state.selection?.cards || [];
  const card = cards.find((c) => c.highlighted) || cards[0];
  if (!card) return { lit: new Set(), color: null };
  let ev = card.evidence;
  ev = Array.isArray(ev) ? ev : typeof ev === "string" && ev ? [ev] : [];
  const lit = new Set();
  for (const e of ev) {
    if (e && typeof e === "object" && e.row) {
      lit.add(e.row);
      continue;
    }
    const s = String(typeof e === "object" ? txt(e) : e).toLowerCase();
    if (!s) continue;
    for (const [id] of ROWS) {
      if ((rows[id] || []).some((it) => it.key && (s.includes(String(it.key).toLowerCase()) || String(it.text).toLowerCase().includes(s)))) lit.add(id);
    }
    if (/\bhand\b/.test(s)) lit.add("hand");
    if (/\b(sound|heard|said|voice)\b/.test(s)) lit.add("sounds");
    if (/\b(routine|every day|days running)\b/.test(s)) lit.add("routines");
  }
  return { lit, color: card.color || card.colour || null, label: card.label };
}

export function rowHtml(id, label, items, { lit, litColor, calls, noScan }) {
  const body = items.length
    ? items.map((it) => `<li>${esc(it.text)}</li>`).join("")
    : `<li class="none">${noScan ? "—" : "nothing reported"}</li>`;
  return `<div class="cx-row${lit ? " lit" : ""}${items.length ? "" : " empty"}" data-row="${id}" style="${lit ? `--lit:${litColor}` : ""}">
    <h4>${esc(label)}</h4><ul>${body}</ul><div class="cx-calls">${calls || ""}</div></div>`;
}
