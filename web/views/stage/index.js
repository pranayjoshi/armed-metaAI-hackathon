// Stage: operate the arm. A three.js digital twin on a table, with the command deck around it.
import { Deck, SKELETON } from "./deck.js";
import { createTwin2D } from "./twin2d.js";
import { tableToWorld } from "./kin.js";
import { Film } from "./film.js";
import { Extras } from "./extras.js";

let ctx, root, deck, twin, el, film, extras, T = null;
let active = false, offLoop = null, offKey = null, camTimer = null, camBusy = false;
let rules = { zones: null, routes: null };
let memTarget = null, lastPulse = -9, lastState = null, lastStatus = "";

const words = (v, fb = "—") => (v ? String(v).replaceAll("_", " ") : fb);
const typing = () => /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;

// ── what the arm is doing, in a sentence (BUILD_PLAN §6) ─────────────────────
const PHRASE = { pick_place: "pick up", point: "point at", home: "go home", gesture: "gesture", place_near_user: "set down" };
const DOING = { pick_place: "Picking up", point: "Pointing at", home: "Going home", gesture: "Gesturing", place_near_user: "Setting down" };
const STAGE = { approach: "reaching", grasp: "gripping", carry: "carrying", release: "letting go", return: "returning", home: "homing", point: "pointing" };
function sentenceFor(s) {
  const a = s.arm, p = s.pending;
  const name = (id) => (s.objects || []).find((o) => o.id === id)?.name || id;
  const ro = ctx.mode === "replay"; // read-only: nothing here can be armed, said or confirmed from this page
  if (a.state === "stopped") return `Frozen: ${a.reason || "stopped"}`;
  if (a.state === "fault") return `Fault: ${a.reason || "arm fault"}`;
  if (a.skill) {
    const who = a.target ? ` the ${name(a.target)}` : "";
    return `${DOING[a.skill] || words(a.skill)}${a.skill === "home" ? "" : who}${a.stage ? `, ${STAGE[a.stage] || a.stage}` : ""}`;
  }
  if (p) {
    const c = p.contract;
    if (p.decision.clarify) {
      const names = (c.candidates || []).filter((x) => x.score > 0).map((x) => x.name);
      return `Which one: ${names.join(" or ") || "pick an object"}?`;
    }
    return `${ro ? "Awaiting confirmation" : "Squeeze to confirm"}: ${PHRASE[c.verb] || words(c.verb)}${c.object_name ? ` the ${c.object_name}` : ""}`;
  }
  if (a.state === "disarmed") return ro ? "Disarmed." : "Disarmed. Arm it to allow motion.";
  if (a.state === "ready") return ro ? "Ready." : "Ready. Say what you need.";
  return words(a.state, "Idle");
}

// ── snapshot -> twin data ───────────────────────────────────────────────────
function twinData(s) {
  const a = s.arm, v = s.vision || {}, p = s.pending;
  const stopped = a.state === "stopped" || a.state === "fault";
  if (a.skill && a.target) memTarget = a.target;
  else if (p?.contract?.object_id) memTarget = p.contract.object_id;
  else if (!stopped) memTarget = null;
  const clarify = p?.decision?.clarify;
  const candidates = clarify ? (p.contract.candidates || []).filter((c) => c.score > 0).map((c) => c.object_id) : [];
  // held object: the runtime's own gripper tracker wins, else infer from the running plan
  let heldId = null;
  const g = s.embodied?.gripper;
  const grip = a.joints?.gripper ?? 100;
  if (g?.status === "holding") heldId = g.object_id;
  else if ((a.skill === "pick_place" || a.skill === "place_near_user") && a.target) {
    if (a.stage === "carry" || (a.stage === "grasp" && (a.gripper_load || 0) > 0.04) || (a.stage === "release" && grip < 35)) heldId = a.target;
  }
  let rayWorld = null;
  const ray = v.pointing?.ray, fs = v.frame_size;
  if (ray && fs) {
    const [x0, z0] = tableToWorld(ray[0] / fs[0], ray[1] / fs[1]);
    const [x1, z1] = tableToWorld(ray[2] / fs[0], ray[3] / fs[1]);
    rayWorld = [x0, z0, x1, z1];
  }
  return {
    joints: a.joints, load: a.gripper_load, moving: a.state === "moving", stopped,
    objects: v.objects || [], targetId: memTarget, candidates, heldId,
    rayWorld, rayActive: v.pointing?.active ?? 0,
    zones: rules.zones, routes: rules.routes, routeName: (p?.contract || s.last?.contract)?.route || null,
  };
}

// ── overlays on the view ────────────────────────────────────────────────────
function renderOverlays(s) {
  const { esc } = ctx;
  const a = s.arm, v = s.vision || {};
  const replay = ctx.mode === "replay";
  const tone = { ready: "ok", moving: "ok", stopped: "bad", fault: "bad", disarmed: "warn" }[a.state] || "";
  const tag = `<span class="tw-hw ${a.hardware ? "real" : "virt"}"><i></i>${a.hardware ? "SO-101" : "VIRTUAL ARM"}</span><span class="tw-state ${tone}">${esc(a.state)}</span>`;
  if (el.tag.dataset.h !== tag) { el.tag.dataset.h = tag; el.tag.innerHTML = tag; }

  // legend: only what is on screen right now
  const chips = [];
  if (replay) chips.push(roTag(s));
  if (v.virtual) chips.push('<span class="lg warn" title="No camera: object positions come from the configured table, not from detection"><i></i>VIRTUAL TABLE</span>');
  if (twinCache.targetId) chips.push('<span class="lg tgt"><i></i>target</span>');
  if (twinCache.candidates.length) chips.push('<span class="lg cand"><i></i>candidates</span>');
  if (twinCache.heldId) chips.push('<span class="lg held"><i></i>held</span>');
  if ((v.pointing?.active ?? 0) > 0.3) chips.push('<span class="lg ray"><i></i>pointing</span>');
  const zn = Array.isArray(rules.zones) ? rules.zones : rules.zones ? Object.keys(rules.zones) : [];
  for (const z of zn) chips.push(`<span class="lg zone" title="${rules.zones && !Array.isArray(rules.zones) ? "avoid zone" : "Avoid zone. The API publishes its name but not its geometry, so it is not drawn."}"><i></i>avoid · ${esc(z)}</span>`);
  const lg = chips.join("");
  if (el.legend.dataset.h !== lg) { el.legend.dataset.h = lg; el.legend.innerHTML = lg; }

  // freeze
  const frozen = a.state === "stopped" || a.state === "fault";
  const fz = el.freeze;
  if (frozen) {
    const lat = deck.el.lat.textContent;
    const html = `<span class="fz-k">${a.state === "fault" ? "ARM FAULT" : "ARM FROZEN"}</span><strong class="fz-reason">${esc(a.reason || "stopped")}</strong><span class="fz-sub num">${esc(lat.startsWith("stop latency") ? "" : lat)}</span>${replay ? "" : '<button class="btn" data-arm="reset">Reset arm</button>'}`;
    if (fz.dataset.h !== html) { fz.dataset.h = html; fz.innerHTML = html; }
    if (fz.hidden) { fz.hidden = false; fz.classList.remove("pop"); void fz.offsetWidth; fz.classList.add("pop"); }
  } else if (!fz.hidden) { fz.hidden = true; fz.dataset.h = ""; }

  // waiting for the operator
  const p = s.pending;
  const asking = p && p.decision.verdict === "ask";
  el.prompt.hidden = !asking || frozen;
  el.view.classList.toggle("asking", !!asking);
  el.view.classList.toggle("frozen", frozen);
  if (asking) {
    const cands = (p.contract.candidates || []).filter((c) => c.score > 0);
    const acts = replay ? "" : p.decision.clarify
      ? cands.map((c) => `<button class="btn" data-choose="${esc(c.object_id)}">${esc(c.name)}</button>`).join("") + '<button class="btn danger" data-act="cancel">Cancel</button>'
      : '<button class="btn primary" data-act="confirm">Yes</button><button class="btn danger" data-act="cancel">No</button>';
    const html = `<span class="pr-k">${p.decision.clarify ? "Choose" : replay ? "Awaiting the script" : "Waiting for you"}</span><strong>${esc(sentenceFor(s))}</strong><span class="pr-acts">${acts}</span>`;
    if (el.prompt.dataset.h !== html) { el.prompt.dataset.h = html; el.prompt.innerHTML = html; }
  }
  const sent = sentenceFor(s);
  if (el.sentence.dataset.h !== sent) { el.sentence.dataset.h = sent; el.sentence.innerHTML = `<span class="kicker">${esc(a.state)}</span><strong>${esc(sent)}</strong>`; }

  // camera picture-in-picture: a real camera live; in replay the simulation's schematic table view, labelled as such
  if (replay) {
    el.cam.hidden = !camLoaded || !v.virtual;
    el.camsvg.innerHTML = "";
    el.camcap.textContent = "SIMULATED VIEW · not a camera";
  } else el.cam.hidden = v.virtual;
  if (!replay && !v.virtual) {
    const [W, H] = v.frame_size || [1280, 720];
    el.camsvg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const tgt = twinCache.targetId, cands = new Set(twinCache.candidates);
    const parts = [];
    for (const o of v.objects || []) {
      const b = o.bbox || [o.table_xy[0] * W - 60, o.table_xy[1] * H - 50, o.table_xy[0] * W + 60, o.table_xy[1] * H + 50];
      const cls = o.id === tgt ? "t" : cands.has(o.id) ? "c" : "";
      parts.push(`<rect class="${cls}" x="${b[0]}" y="${b[1]}" width="${b[2] - b[0]}" height="${b[3] - b[1]}" rx="6"/><text x="${b[0] + 6}" y="${Math.max(18, b[1] - 8)}">${esc(o.name)} ${Math.round((o.confidence ?? 0) * 100)}%</text>`);
    }
    const ray = v.pointing?.ray;
    if (ray && v.pointing.active > 0.3) parts.push(`<line class="r" x1="${ray[0]}" y1="${ray[1]}" x2="${ray[2]}" y2="${ray[3]}"/>`);
    el.camsvg.innerHTML = parts.join("");
    el.camcap.textContent = `CAMERA · ${v.fps || 0} fps · ${(v.objects || []).length} obj · ${v.latency_ms ?? "—"} ms${v.pointing?.active > 0.3 ? " · pointing" : ""}`;
  }
}

const roTag = (s) => `<span class="lg ro" title="${ctx.esc(s.note || "Nothing here moves an arm.")}">READ-ONLY · ${ctx.esc(s.label || "REPLAY")}</span>`;
let camLoaded = false;
let twinCache = { targetId: null, candidates: [], heldId: null };

// ── controls ────────────────────────────────────────────────────────────────
function setFocus(on) {
  const stg = root.querySelector(".stg");
  stg.dataset.focus = on ? "1" : "0";
  el.focusbtn.classList.toggle("on", on);
}
function onKey(e) {
  if (!active || e.metaKey || e.ctrlKey || e.altKey) return;
  const stg = root.querySelector(".stg");
  if (e.key === "Escape" && stg.dataset.focus === "1") {
    e.stopImmediatePropagation();
    setFocus(false);
    return;
  }
  if (typing()) return;
  if (e.key === "f" || e.key === "F") { e.preventDefault(); e.stopImmediatePropagation(); setFocus(stg.dataset.focus !== "1"); }
  else if (e.key === "m" || e.key === "M") { e.preventDefault(); e.stopImmediatePropagation(); film.toggle(); }
  else if (e.key === "/" && ctx.mode !== "replay") { e.preventDefault(); deck.el.say.focus(); }
}
function markPreset(name) {
  el.presets.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.preset === name ? "true" : "false"));
}
async function loadRules() {
  const res = await ctx.api.get("/api/rules").catch(() => null);
  if (res && !res.detail) rules = { zones: res.zones ?? null, routes: res.routes ?? null };
}

// Replay without `h2m demo`: the snapshot has no arm, EMG, vision or events at all.
function renderEmpty(s) {
  const { esc } = ctx;
  deck.updateEmpty(s);
  const tag = `<span class="tw-hw virt"><i></i>NO ARM</span>`;
  if (el.tag.dataset.h !== tag) { el.tag.dataset.h = tag; el.tag.innerHTML = tag; }
  const lg = roTag(s);
  if (el.legend.dataset.h !== lg) { el.legend.dataset.h = lg; el.legend.innerHTML = lg; }
  el.freeze.hidden = true;
  el.prompt.hidden = true;
  el.cam.hidden = true;
  el.sentence.dataset.h = "";
  el.sentence.innerHTML = "";
  el.view.classList.add("no-sim");
  el.empty.hidden = false;
  const n = s.replay?.incidents ?? 0;
  const html = `<b>No simulation running</b><span>${esc(s.note || "Replay of saved recordings. Nothing here moves an arm.")}</span><span class="num">${n} incident${n === 1 ? "" : "s"} recorded</span><button class="btn" data-go>Open the replay</button>`;
  if (el.empty.dataset.h !== html) { el.empty.dataset.h = html; el.empty.innerHTML = html; }
}

export default {
  async mount(r, c) {
    ctx = c; root = r;
    ctx.css(import.meta.url, "style.css");
    root.innerHTML = SKELETON;
    deck = new Deck(root, ctx);
    el = deck.el;
    try {
      T = await ctx.loadThree();
      const { createTwin3D } = await import("./twin3d.js");
      twin = createTwin3D(T, el.view, { reduced: ctx.reduced, onPreset: markPreset });
    } catch (err) {
      console.warn("stage: 2D fallback", err.message);
      twin = createTwin2D(el.view, { reduced: ctx.reduced });
      el.presets.hidden = true;
      root.querySelector(".stg").classList.add("no-webgl");
    }
    el.presets.addEventListener("click", (e) => {
      const b = e.target.closest("[data-preset]");
      if (!b) return;
      twin.setPreset(b.dataset.preset);
      markPreset(b.dataset.preset);
    });
    el.focusbtn.onclick = () => setFocus(root.querySelector(".stg").dataset.focus !== "1");
    film = new Film(root.querySelector(".stg"), ctx, {
      onToggle: (on) => {
        el.filmbtn.classList.toggle("on", on);
        el.filmbtn.setAttribute("aria-pressed", String(on));
        twin?.resize?.(); // the twin's box changes size when the film band opens
        requestAnimationFrame(() => twin?.resize?.());
      },
    });
    extras = new Extras(root.querySelector(".stg"), ctx, { getRoutes: () => rules.routes });
    el.filmbtn.onclick = () => film.toggle();
    el.filmbtn.insertAdjacentHTML("beforebegin", '<button class="btn tw-archbtn" data-el="archbtn" aria-pressed="false" title="Architecture frame: what ran today">Architecture</button>');
    const archbtn = root.querySelector(".tw-archbtn");
    archbtn.onclick = () => {
      const on = root.querySelector(".stg").dataset.frame !== "architecture";
      extras.setFrame(on ? "architecture" : "");
      archbtn.classList.toggle("on", on);
      archbtn.setAttribute("aria-pressed", String(on));
      if (ctx.store.state) extras.update(ctx.store.state, film.on);
    };
    const qs = new URLSearchParams(location.search);
    if (qs.get("film") === "1") film.set(true); // ?film=1 turns the proof layer on at load
    if (qs.get("frame") === "architecture") { extras.setFrame("architecture"); archbtn.classList.add("on"); archbtn.setAttribute("aria-pressed", "true"); } // ?film=1&frame=architecture exports the 2:05 frame
    if (ctx.mode !== "replay") el.freeze.addEventListener("click", (e) => { if (e.target.closest("[data-arm]")) ctx.api("/api/arm/reset"); });
    root.querySelector(".stg").dataset.mode = ctx.mode;
    el.empty.addEventListener("click", (e) => { if (e.target.closest("[data-go]")) ctx.router.go("replay", [e.clientX, e.clientY]); });
    el.cam.addEventListener("click", () => el.cam.classList.toggle("big"));
    ctx.bus.on("event", (e) => {
      if (e.kind === "rule.approved" || e.kind === "rule.expired") loadRules();
      if (!active || !twin?.pulse) return;
      if (e.kind === "arm.stopped" || e.kind === "arm.fault") {
        const t = performance.now();
        if (t - lastPulse > 600) { lastPulse = t; twin.pulse(e.kind === "arm.fault" ? "fault" : "stop"); }
      }
    });
    addEventListener("pagehide", () => twin?.dispose?.());
    loadRules();
    if (ctx.store.state) this.update(ctx.store.state, ctx);
  },

  enter() {
    active = true;
    root.querySelectorAll(".stg-view, .say, .stg-under > *, .stg-side > *").forEach((n, i) => {
      n.classList.remove("rise");
      void n.offsetWidth;
      n.style.setProperty("--i", i);
      n.classList.add("rise");
    });
    offLoop = ctx.loop.add((dt, t) => {
      twin.frame?.(dt, t);
      deck.frame(dt, t);
      film.frame();
    });
    addEventListener("keydown", onKey, true);
    offKey = () => removeEventListener("keydown", onKey, true);
    twin.resize?.();
    loadRules();
    const replay = ctx.mode === "replay";
    camTimer = setInterval(() => {
      const s = ctx.store.state;
      if (!s?.vision || (s.vision.virtual && !replay) || camBusy) return;
      camBusy = true;
      el.camimg.onload = () => { camBusy = false; camLoaded = true; };
      el.camimg.onerror = () => { camBusy = false; camLoaded = false; };
      el.camimg.src = `/api/camera/scene.jpg?t=${Date.now()}`;
    }, replay ? 500 : 150);
  },

  leave() {
    active = false;
    offLoop?.(); offLoop = null;
    offKey?.(); offKey = null;
    clearInterval(camTimer); camTimer = null;
  },

  update(s) {
    if (!deck) return;
    if (!s.arm) { renderEmpty(s); film.updateEmpty(); extras.update(s, film.on); return; } // replay with no running simulation: only recordings exist
    el.empty.hidden = true;
    el.view.classList.remove("no-sim");
    const d = twinData(s);
    twinCache = d;
    twin?.setData(d);
    const sent = sentenceFor(s);
    deck.update(s, sent);
    film.update(s, sent);
    extras.update(s, film.on);
    renderOverlays(s);
    lastState = s;
  },
};
