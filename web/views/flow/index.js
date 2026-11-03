// Workflow: the multi-agent pipeline, alive. Inputs at the bottom, the robot at the top, data flows upward.
// Everything that moves is a response to a runtime event (bus "event"); idle is a dim network with a very slow drift.
import { LANE_LABELS, LEGEND, NODES, EDGES, NODE_BY_ID, EDGE_BY_ID, reach } from "./graph.js";
import { WORLD_W, WORLD_H, NODE_W, NODE_H, boxOf, colX, rowY, allGeometry } from "./layout.js";
import { Packets } from "./packets.js";
import { plan, TRACE_KINDS, fmtMs } from "./events.js";
import { live, series } from "./live.js";
import { ICONS } from "./icons.js";
import { SidePanel } from "./panel.js";

const NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};

const SUB = {
  mic: "voice activity", typed: "skips ASR", cam: "objects · hand", asr: "speech to text", gram: "offline parse",
  stop: "no model", muse: "intent compiler", contract: "verb · object", gate: "no model inside", exec: "plan + route",
  arm: "follower arm", gang: "electrodes", squeeze: "confirm + grip", open: "stop gesture", brace: "reflex detector",
  freeze: "no model", incident: "saved clip", wonder: "explains why", proposal: "held for you",
  approval: "you decide", memory: "restrict only",
};
const JOINTS = [["shoulder_pan", "pan"], ["shoulder_lift", "lift"], ["elbow_flex", "elbow"], ["wrist_flex", "wrist"], ["wrist_roll", "roll"], ["gripper", "grip"]];
// where an injected packet (a click, a typed "yes", a squeeze that has no edge of its own) enters a node
const STUB = { gate: "left", contract: "right", freeze: "bottom", approval: "right" };
const MIN_K = 0.18;
const MAX_K = 2.4;

export default {
  async mount(root, ctx) {
    ctx.css(import.meta.url, "style.css");
    const { store, bus, esc, api, reduced } = ctx;
    const V = (this.v = { ctx, root, reduced, visible: false, ready: new Map(), data: new Map(), rules: null, recorded: new WeakSet() });

    root.innerHTML = `
      <div class="wf">
        <div class="wf-stage" tabindex="-1">
          <div class="wf-world">
            <svg class="wf-svg" width="${WORLD_W}" height="${WORLD_H}" viewBox="0 0 ${WORLD_W} ${WORLD_H}" aria-hidden="true">
              <defs></defs><g class="lanes"></g><g class="edges"></g><g class="stubs"></g><g class="packets"></g><g class="floaters"></g>
            </svg>
            <div class="wf-nodes"></div>
          </div>
          <div class="wf-hud wf-tl">
            <div class="wf-title"><span class="kicker">Workflow</span><span class="wf-live num"><i></i><b data-k="count">0</b> events since load</span><span class="wf-ro" data-k="ro" hidden></span></div>
            <ul class="wf-legend"></ul>
            <svg class="wf-mini" style="aspect-ratio:${WORLD_W}/${WORLD_H}" viewBox="0 0 ${WORLD_W} ${WORLD_H}" aria-label="Minimap"></svg>
            <div class="wf-ctl">
              <button data-z="out" aria-label="Zoom out">−</button>
              <button data-z="fit" aria-label="Fit to screen">Fit</button>
              <button data-z="in" aria-label="Zoom in">+</button>
              <button data-z="map" class="map" aria-label="Show legend and minimap">Map</button>
            </div>
          </div>
        </div>
        <aside class="wf-side" aria-label="Trace and inspector"></aside>
      </div>`;
    const $ = (s) => root.querySelector(s);
    V.stage = $(".wf-stage");
    V.world = $(".wf-world");
    V.svg = $(".wf-svg");
    V.nodesHost = $(".wf-nodes");
    V.mini = $(".wf-mini");
    V.countEl = $('[data-k="count"]');
    V.roEl = $('[data-k="ro"]');
    V.count = 0;

    this._legend(V, $(".wf-legend"));
    this._lanes(V);
    this._edges(V);
    this._nodes(V);
    this._mini(V);

    V.panel = new SidePanel($(".wf-side"), {
      mode: ctx.mode,
      data: (id) => V.data.get(id),
      live: (id) => live(id, store.state, { rules: V.rules }),
      names: () => store.objectNames,
      now: () => store.state?.t,
      hueOfEvent: (e) => this._hueOfEvent(V, e),
      onPick: (e) => this._pick(V, e),
      onSelect: (id) => this._select(V, id),
      onClose: () => this._select(V, null),
    });

    // view transform
    V.view = { x: 0, y: 0, k: 1 };
    V.target = { x: 0, y: 0, k: 1 };
    V.userMoved = false;
    this._input(V);
    new ResizeObserver(() => {
      V.size = null;
      if (V.visible && !V.userMoved) this._fit(V, true);
    }).observe(V.stage);

    // seed "last seen" from history that happened before this page was mounted; no animation
    const env = () => ({ state: store.state, hist: store.history, names: store.objectNames });
    V.env = env;
    for (const e of store.history) {
      if (!TRACE_KINDS.has(e.kind)) continue;
      this._applyAll(V, plan(e, env()));
      V.panel.add(e, { animate: false });
    }
    V.count = store.history.filter((e) => TRACE_KINDS.has(e.kind)).length;
    V.countEl.textContent = V.count;

    // all motion comes from here, subscribed once
    bus.on("event", (e) => {
      if (!TRACE_KINDS.has(e.kind)) return;
      V.count += 1;
      V.countEl.textContent = V.count;
      const actions = plan(e, env());
      if (V.visible) this._run(V, actions);
      else this._applyAll(V, actions);
      V.panel.add(e, { animate: V.visible, state: store.state });
      if (e.kind.startsWith("rule.")) this._rules(V);
    });
    this._rules(V);
    this.api = api;
  },

  enter(ctx) {
    const V = this.v;
    V.visible = true;
    V.size = null;
    if (!V.measured) this._measure(V);
    this._fit(V, true);
    V.stage.classList.remove("intro");
    void V.stage.offsetWidth;
    V.stage.classList.add("intro");
    this._rules(V);
    V.remove = ctx.loop.add((dt) => this._frame(V, dt));
  },

  leave() {
    const V = this.v;
    V.visible = false;
    V.remove?.();
    V.remove = null;
    V.packets?.clear();
    V.ready.clear();
  },

  update(state) {
    const V = this.v;
    if (V.ctx.mode === "replay") {
      const t = `READ-ONLY · ${state.label || "REPLAY"}`;
      V.roEl.hidden = false;
      if (V.roEl.textContent !== t) V.roEl.textContent = t;
      V.roEl.title = state.note || "Nothing here moves an arm.";
    }
    if (!V.measured) return;
    this._refresh(V, state);
  },

  // ───────────────────────────────────────────────────────────────────────────────────
  // build
  _legend(V, ul) {
    ul.innerHTML = LEGEND.map((l) => `<li style="--h: var(--${l.hue})"><i></i>${l.label}</li>`).join("");
  },

  _lanes(V) {
    const g = V.svg.querySelector(".lanes");
    for (const l of LANE_LABELS) {
      const t = svgEl("text", { class: "lane-label", x: colX(l.col), y: rowY(l.row), "text-anchor": l.anchor === "right" ? "end" : "start" });
      t.style.setProperty("--h", `var(--${l.hue})`);
      t.textContent = l.text;
      g.append(t);
    }
  },

  _edges(V) {
    const defs = V.svg.querySelector("defs");
    const host = V.svg.querySelector(".edges");
    V.edges = new Map();
    const geo = allGeometry();
    for (const e of EDGES) {
      const gm = geo.get(e.id);
      const hue = e.hue || NODE_BY_ID[e.from].hue;
      const hue1 = e.hue || NODE_BY_ID[e.to].hue;
      const grad = svgEl("linearGradient", { id: `g-${e.id}`, gradientUnits: "userSpaceOnUse", x1: gm.p0.x, y1: gm.p0.y, x2: gm.p1.x, y2: gm.p1.y });
      // a perfectly vertical/horizontal edge still gets a gradient because the units are user space
      grad.innerHTML = `<stop offset="0" style="stop-color:var(--${hue})"/><stop offset="1" style="stop-color:var(--${hue1})"/>`;
      defs.append(grad);
      const g = svgEl("g", { class: `edge${e.bypass ? " bypass" : ""}${e.loop ? " loop" : ""}`, "data-id": e.id });
      g.style.setProperty("--heat", "0");
      const stroke = `url(#g-${e.id})`;
      const cable = svgEl("path", { class: "cable", d: gm.d, stroke });
      const glow = svgEl("path", { class: "glow", d: gm.d, stroke });
      const flow = svgEl("path", { class: "flow", d: gm.d, stroke });
      const [dx, dy] = gm.into;
      const tip = { x: gm.p1.x, y: gm.p1.y };
      const nx = -dy;
      const ny = dx;
      const bx = tip.x - dx * 9;
      const by = tip.y - dy * 9;
      const arrow = svgEl("path", { class: "arrow", d: `M${tip.x} ${tip.y}L${bx + nx * 4.6} ${by + ny * 4.6}L${bx - nx * 4.6} ${by - ny * 4.6}Z`, fill: `var(--${hue1})` });
      g.append(cable, glow, flow, arrow);
      host.append(g);
      V.edges.set(e.id, { e, g, path: flow, d: gm.d, len: 0, heat: 0, hue, geo: gm });
    }
  },

  _nodes(V) {
    V.nodes = new Map();
    for (const n of NODES) {
      const b = boxOf(n);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `node${n.wide ? " wide" : ""}${n.id === "gram" ? " chip-br" : ""}`;
      btn.dataset.id = n.id;
      btn.dataset.hue = n.hue;
      btn.dataset.status = "off";
      btn.style.cssText = `left:${b.x}px;top:${b.y}px;width:${b.w}px;height:${b.h}px;--h:var(--${n.hue})`;
      btn.style.setProperty("--i", n.row);
      btn.setAttribute("aria-label", `${n.title}. ${n.role}`);
      btn.innerHTML = `
        <span class="n-ring"><svg viewBox="0 0 32 32" aria-hidden="true"><circle class="trk" cx="16" cy="16" r="13"/><circle class="arc" cx="16" cy="16" r="13" pathLength="100" transform="rotate(-90 16 16)"/><line class="mk" x1="16" y1="1.4" x2="16" y2="5.6" opacity="0"/></svg><svg class="ico" viewBox="0 0 24 24" aria-hidden="true">${ICONS[n.icon]}</svg></span>
        <span class="n-head"><b>${n.title}</b><em class="n-sub">${SUB[n.id] || ""}</em></span>
        <span class="n-lat num" hidden></span>
        <span class="n-stat num"></span>
        <span class="n-pay"><span class="n-pt num">—</span><span class="n-pa num"></span></span>
        ${n.spark ? '<svg class="n-spark" viewBox="0 0 160 78" preserveAspectRatio="none" aria-hidden="true"><polyline class="s2" points=""/><polyline class="s1" points=""/></svg>' : ""}
        ${n.wide ? `<span class="n-joints">${JOINTS.map(([, l]) => `<span><i>${l}</i><b class="num" data-j="${l}">—</b></span>`).join("")}</span><i class="n-prog"></i>` : ""}`;
      V.nodesHost.append(btn);
      btn.addEventListener("click", () => this._select(V, V.selected === n.id ? null : n.id));
      btn.addEventListener("keydown", (ev) => this._nodeKey(V, ev, n.id));
      const refs = {
        arc: btn.querySelector(".arc"), mk: btn.querySelector(".mk"), sub: btn.querySelector(".n-sub"), stat: btn.querySelector(".n-stat"),
        pt: btn.querySelector(".n-pt"), pa: btn.querySelector(".n-pa"), lat: btn.querySelector(".n-lat"),
        s1: btn.querySelector(".s1"), s2: btn.querySelector(".s2"), prog: btn.querySelector(".n-prog"),
        joints: n.wide ? Object.fromEntries(JOINTS.map(([, l]) => [l, btn.querySelector(`[data-j="${l}"]`)])) : null,
      };
      V.nodes.set(n.id, { n, el: btn, refs, lit: 0, level: null, shown: 0, cache: {} });
      V.data.set(n.id, { raw: null, line: null, n: 0, lat: [], latLabel: "", tone: null });
    }
  },

  _mini(V) {
    const m = V.mini;
    m.innerHTML = `<g class="mini-edges"></g><g class="mini-nodes"></g><rect class="mini-view" rx="6"/>`;
    const gn = m.querySelector(".mini-nodes");
    V.miniNodes = new Map();
    for (const n of NODES) {
      const b = boxOf(n);
      const r = svgEl("rect", { x: b.x, y: b.y, width: b.w, height: b.h, rx: 14, "data-h": n.hue });
      r.style.setProperty("--h", `var(--${n.hue})`);
      gn.append(r);
      V.miniNodes.set(n.id, r);
    }
    const ge = m.querySelector(".mini-edges");
    for (const e of EDGES) {
      const p = svgEl("path", { d: V.edges.get(e.id).d });
      ge.append(p);
    }
    V.miniView = m.querySelector(".mini-view");
    const jump = (ev) => {
      const r = m.getBoundingClientRect();
      const wx = ((ev.clientX - r.left) / r.width) * WORLD_W;
      const wy = ((ev.clientY - r.top) / r.height) * WORLD_H;
      const { w, h } = this._size(V);
      V.target.x = w / 2 - wx * V.target.k;
      V.target.y = h / 2 - wy * V.target.k;
      V.userMoved = true;
    };
    m.addEventListener("pointerdown", (ev) => {
      m.setPointerCapture(ev.pointerId);
      jump(ev);
      m.onpointermove = jump;
    });
    m.addEventListener("pointerup", () => (m.onpointermove = null));
  },

  _measure(V) {
    // path lengths need real geometry; done on first visit when the section is displayed
    const paths = new Map();
    for (const [id, ed] of V.edges) {
      ed.len = ed.path.getTotalLength();
      paths.set(id, { path: ed.path, len: ed.len, d: ed.d });
    }
    // stub paths for injected packets (clicks, typed answers)
    const stubs = V.svg.querySelector(".stubs");
    for (const [id, side] of Object.entries(STUB)) {
      const b = boxOf(NODE_BY_ID[id]);
      const L = 78;
      const pts = {
        left: [b.x - L, b.cy, b.x, b.cy],
        right: [b.x + b.w + L, b.cy, b.x + b.w, b.cy],
        bottom: [b.cx, b.y + b.h + L, b.cx, b.y + b.h],
        top: [b.cx, b.y - L, b.cx, b.y],
      }[side];
      const d = `M${pts[0]} ${pts[1]}L${pts[2]} ${pts[3]}`;
      const p = svgEl("path", { d, class: "stub", fill: "none" });
      stubs.append(p);
      paths.set(`stub-${id}`, { path: p, len: p.getTotalLength(), d, start: { x: pts[0], y: pts[1] } });
    }
    V.packets = new Packets(V.svg.querySelector(".packets"), paths, V.reduced);
    V.stubPaths = paths;
    // edge labels
    const host = V.svg.querySelector(".edges");
    for (const ed of V.edges.values()) {
      if (!ed.e.label) continue;
      const pt = ed.path.getPointAtLength(ed.len * (ed.e.labelAt ?? 0.5));
      const w = ed.e.label.length * 5.7 + 16;
      const g = svgEl("g", { class: "elabel", transform: `translate(${pt.x.toFixed(1)} ${pt.y.toFixed(1)})` });
      g.style.setProperty("--h", `var(--${ed.hue})`);
      g.append(svgEl("rect", { x: -w / 2, y: -9, width: w, height: 18, rx: 9 }));
      const t = svgEl("text", { y: 3.6, "text-anchor": "middle" });
      t.textContent = ed.e.label;
      g.append(t);
      host.append(g);
    }
    V.measured = true;
  },

  // ───────────────────────────────────────────────────────────────────────────────────
  // data
  _apply(V, set, { record = true } = {}) {
    if (!set) return;
    const d = V.data.get(set.node);
    if (!d) return;
    const fresh = !V.recorded.has(set);
    d.raw = set.raw;
    d.line = set.line;
    if (record && fresh) {
      V.recorded.add(set);
      d.n += 1;
      if (Number.isFinite(set.lat)) {
        d.lat.push({ v: set.lat, t: set.raw?.t });
        if (d.lat.length > 60) d.lat.shift();
        d.latLabel = set.latLabel || d.latLabel;
      }
    }
    const node = V.nodes.get(set.node);
    if (node) node.cache.pay = null;
  },

  _applyAll(V, actions) {
    for (const a of actions) {
      this._apply(V, a.src);
      this._apply(V, a.set);
    }
  },

  async _rules(V) {
    const body = await V.ctx.api.get("/api/rules");
    V.rules = Array.isArray(body?.rules) ? body.rules.length : -1;
    V.lastRule = Array.isArray(body?.rules) ? body.rules.at(-1) : null;
  },

  _hueOfEvent(V, e) {
    const a = plan(e, V.env())[0];
    if (!a) return "ink-3";
    if (a.k === "hop") {
      const ed = EDGE_BY_ID[a.edge];
      return a.hue || ed.hue || NODE_BY_ID[ed.from].hue;
    }
    return a.hue || NODE_BY_ID[a.node].hue;
  },

  // ───────────────────────────────────────────────────────────────────────────────────
  // animation
  _run(V, actions, { replay = false } = {}) {
    if (!V.packets) return;
    const now = performance.now();
    const P = V.packets;
    const ready = V.ready;
    for (const a of actions) {
      if (a.k === "hop") {
        const ed = V.edges.get(a.edge);
        if (!ed) continue;
        const { from, to } = ed.e;
        const start = Math.max(now, ready.get(from) || 0);
        const dur = P.duration(a.edge);
        ready.set(to, Math.max(ready.get(to) || 0, start + dur));
        P.send({
          edge: a.edge, hue: a.hue || ed.hue, start, dur,
          onStart: () => {
            if (!replay) this._apply(V, a.src);
            this._heat(V, a.edge, 1);
            this._lit(V, from, 0.55);
          },
          onArrive: () => {
            if (!replay) this._apply(V, a.set);
            this._lit(V, to, 1, a.set?.tone);
          },
        });
      } else if (a.k === "pulse") {
        const start = Math.max(now, ready.get(a.node) || 0);
        ready.set(a.node, start);
        P.at(start, () => {
          if (!replay) this._apply(V, a.set);
          this._lit(V, a.node, 1, a.set?.tone);
        });
      } else if (a.k === "inject") {
        const key = `stub-${a.node}`;
        const stub = V.stubPaths.get(key);
        const start = Math.max(now, ready.get(a.node) || 0);
        const dur = V.reduced ? 160 : 420;
        ready.set(a.node, start + dur);
        if (stub) this._floater(V, stub.start, a.label, a.hue || "spine", start);
        P.send({
          edge: key, hue: a.hue || "spine", start, dur,
          onArrive: () => {
            if (!replay) this._apply(V, a.set);
            this._lit(V, a.node, 1, a.set?.tone);
          },
        });
      }
    }
  },

  _floater(V, pt, text, hue, start) {
    if (!text) return;
    V.packets.at(start, () => {
      const g = svgEl("g", { class: "floater", transform: `translate(${pt.x} ${pt.y})` });
      g.style.setProperty("--h", `var(--${hue})`);
      const w = text.length * 5.8 + 14;
      g.append(svgEl("rect", { x: -w / 2, y: -9, width: w, height: 18, rx: 9 }));
      const t = svgEl("text", { y: 3.6, "text-anchor": "middle" });
      t.textContent = text;
      g.append(t);
      V.svg.querySelector(".floaters").append(g);
      setTimeout(() => g.remove(), 1400);
    });
  },

  _lit(V, id, amount, tone) {
    const nd = V.nodes.get(id);
    if (!nd) return;
    nd.lit = Math.max(nd.lit, amount);
    nd.el.dataset.tone = tone || "";
    if (amount >= 1 && !V.reduced) {
      nd.el.classList.remove("hit");
      void nd.el.offsetWidth;
      nd.el.classList.add("hit");
    }
  },

  _heat(V, id, v) {
    const ed = V.edges.get(id);
    if (ed) ed.heat = Math.max(ed.heat, v);
  },

  _frame(V, dt) {
    const now = performance.now();
    V.packets?.tick(now);
    for (const nd of V.nodes.values()) {
      if (nd.lit > 0) {
        nd.lit = Math.max(0, nd.lit - dt / 1.5);
        nd.el.style.setProperty("--lit", (nd.lit * nd.lit).toFixed(3));
        if (nd.lit === 0) nd.el.dataset.tone = "";
      }
      if (nd.level != null) {
        const next = nd.shown + (nd.level - nd.shown) * (1 - Math.exp(-14 * dt));
        if (Math.abs(next - nd.shown) > 0.002 || nd.level !== nd.shown) {
          nd.shown = Math.abs(nd.level - next) < 0.003 ? nd.level : next;
          nd.refs.arc.style.strokeDasharray = `${(nd.shown * 100).toFixed(1)} 100`;
        }
      }
    }
    for (const ed of V.edges.values()) {
      if (ed.heat > 0) {
        ed.heat = Math.max(0, ed.heat - dt / 2.4);
        ed.g.style.setProperty("--heat", ed.heat.toFixed(3));
        ed.g.classList.toggle("hot", ed.heat > 0.04);
      }
    }
    // view easing
    const v = V.view;
    const t = V.target;
    if (V.reduced) Object.assign(v, t);
    else {
      const a = 1 - Math.exp(-12 * dt);
      v.x += (t.x - v.x) * a;
      v.y += (t.y - v.y) * a;
      v.k += (t.k - v.k) * a;
      if (Math.abs(t.x - v.x) < 0.05 && Math.abs(t.y - v.y) < 0.05 && Math.abs(t.k - v.k) < 0.0004) Object.assign(v, t);
    }
    if (v.x !== V.lx || v.y !== V.ly || v.k !== V.lk) {
      V.lx = v.x;
      V.ly = v.y;
      V.lk = v.k;
      this._applyView(V);
    }
  },

  // ───────────────────────────────────────────────────────────────────────────────────
  // live state from the snapshot (10 Hz)
  _refresh(V, s) {
    const now = s?.t;
    V.nowT = now;
    for (const nd of V.nodes.values()) {
      const { n, el, refs, cache } = nd;
      const L = live(n.id, s, { rules: V.rules });
      const d = V.data.get(n.id);
      if (cache.status !== L.status) el.dataset.status = cache.status = L.status;
      const sub = L.tag || SUB[n.id] || "";
      if (cache.sub !== sub) {
        refs.sub.textContent = cache.sub = sub;
        refs.sub.classList.toggle("tag", !!L.tag);
      }
      if (cache.stat !== L.stat) refs.stat.textContent = cache.stat = L.stat;
      nd.level = L.level;
      if (L.level == null && cache.lv !== "none") {
        refs.arc.style.strokeDasharray = "0 100";
        nd.shown = 0;
        cache.lv = "none";
      } else if (L.level != null) cache.lv = "on";
      if (refs.mk) {
        const m = L.mark;
        if (m !== cache.mark) {
          cache.mark = m;
          if (m == null) refs.mk.setAttribute("opacity", "0");
          else {
            refs.mk.setAttribute("opacity", "1");
            refs.mk.setAttribute("transform", `rotate(${m * 360} 16 16)`);
          }
        }
      }
      const line = d.line ?? "—";
      if (cache.pay !== line) {
        refs.pt.textContent = cache.pay = line;
        el.title = `${n.title}\n${d.line ? d.line : "nothing seen yet"}${d.latLabel && d.lat.length ? `\nlatency: ${d.latLabel}` : ""}`;
      }
      const age = d.raw && now != null ? this._age(now - d.raw.t) : "";
      if (cache.age !== age) refs.pa.textContent = cache.age = age;
      const lat = d.lat.length ? fmtMs(d.lat.at(-1).v) : null;
      if (cache.lat !== lat) {
        cache.lat = lat;
        refs.lat.hidden = lat == null;
        refs.lat.textContent = lat || "";
      }
      if (n.spark && s) {
        const sr = series(n.spark, s);
        if (sr) {
          refs.s1.setAttribute("points", this._poly(sr.a, 160, 78, n.spark === "emg" ? 0.78 : 0.66));
          if (sr.b) refs.s2.setAttribute("points", this._poly(sr.b, 160, 78, 0.78));
        }
      }
      if (n.wide) {
        const j = s?.arm?.joints || {};
        for (const [key, short] of JOINTS) {
          const txt = Number.isFinite(j[key]) ? j[key].toFixed(0) : "—";
          if (refs.joints[short].textContent !== txt) refs.joints[short].textContent = txt;
        }
        refs.prog.style.transform = `scaleX(${s?.arm?.progress ?? 0})`;
      }
      const mr = V.miniNodes.get(n.id);
      if (mr && mr.dataset.st !== L.status) mr.dataset.st = L.status;
    }
    V.panel.tick(s);
    V.panel.render(s);
  },

  _age(sec) {
    if (!Number.isFinite(sec)) return "";
    if (sec < 60) return `${sec.toFixed(sec < 10 ? 1 : 0)}s`;
    if (sec < 3600) return `${Math.round(sec / 60)}m`;
    return ">1h";
  },

  _poly(vals, w, h, amp) {
    const n = vals.length;
    if (n < 2) return "";
    const pts = [];
    const step = Math.max(1, Math.floor(n / 80));
    for (let i = 0; i < n; i += step) {
      const x = (i / (n - 1)) * w;
      const y = h - 3 - Math.min(1, Math.max(0, vals[i])) * h * amp;
      pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    return pts.join(" ");
  },

  // ───────────────────────────────────────────────────────────────────────────────────
  // selection, trace highlight
  _select(V, id) {
    V.selected = id;
    V.nodes.forEach((nd) => nd.el.classList.toggle("sel", nd.n.id === id));
    if (id) {
      V.panel.show(id);
      this._highlight(V, reach(id), id);
    } else {
      V.panel.hide();
      this._highlight(V, null);
    }
    V.panel.clearPick();
  },

  _highlight(V, set, focusId) {
    clearTimeout(V.hlTimer);
    V.stage.classList.toggle("dimmed", !!set);
    for (const nd of V.nodes.values()) {
      nd.el.classList.toggle("in-path", !!set && set.nodes.has(nd.n.id));
      nd.el.classList.toggle("focus-node", focusId === nd.n.id);
    }
    for (const ed of V.edges.values()) ed.g.classList.toggle("in-path", !!set && set.edges.has(ed.e.id));
    V.miniNodes.forEach((r, id) => r.classList.toggle("dim", !!set && !set.nodes.has(id)));
  },

  _pick(V, e) {
    const actions = plan(e, V.env());
    const nodes = new Set();
    const edges = new Set();
    for (const a of actions) {
      if (a.k === "hop") {
        edges.add(a.edge);
        nodes.add(EDGE_BY_ID[a.edge].from);
        nodes.add(EDGE_BY_ID[a.edge].to);
      } else nodes.add(a.node);
    }
    V.selected = null;
    V.nodes.forEach((nd) => nd.el.classList.remove("sel"));
    this._highlight(V, { nodes, edges });
    V.packets?.clear();
    V.ready.clear();
    this._run(V, actions, { replay: true });
    V.hlTimer = setTimeout(() => {
      this._highlight(V, null);
      V.panel.clearPick();
    }, 9000);
  },

  _nodeKey(V, ev, id) {
    const dir = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] }[ev.key];
    if (ev.key === "Escape" && V.selected) {
      ev.stopPropagation();
      ev.preventDefault();
      this._select(V, null);
      return;
    }
    if (!dir) return;
    ev.preventDefault();
    const me = boxOf(NODE_BY_ID[id]);
    let best = null;
    let bestScore = Infinity;
    for (const n of NODES) {
      if (n.id === id) continue;
      const b = boxOf(n);
      const dx = b.cx - me.cx;
      const dy = b.cy - me.cy;
      const along = dx * dir[0] + dy * dir[1];
      if (along <= 4) continue;
      const across = Math.abs(dx * dir[1]) + Math.abs(dy * dir[0]);
      const score = along + across * 2.2;
      if (score < bestScore) {
        bestScore = score;
        best = n;
      }
    }
    if (best) {
      const el = V.nodes.get(best.id).el;
      el.focus();
      this._reveal(V, best.id);
    }
  },

  _reveal(V, id) {
    const b = boxOf(NODE_BY_ID[id]);
    const { w, h } = this._size(V);
    const k = V.target.k;
    const sx = b.cx * k + V.target.x;
    const sy = b.cy * k + V.target.y;
    const m = 110;
    let dx = 0;
    let dy = 0;
    if (sx < m) dx = m - sx;
    else if (sx > w - m) dx = w - m - sx;
    if (sy < m) dy = m - sy;
    else if (sy > h - m) dy = h - m - sy;
    if (dx || dy) {
      V.target.x += dx;
      V.target.y += dy;
      V.userMoved = true;
    }
  },

  // ───────────────────────────────────────────────────────────────────────────────────
  // pan / zoom
  _size(V) {
    if (!V.size) V.size = { w: V.stage.clientWidth, h: V.stage.clientHeight };
    return V.size;
  },

  _fit(V, snap) {
    const { w, h } = this._size(V);
    if (!w || !h) return;
    const pad = w < 760 ? 12 : 28;
    let k = Math.min((w - pad * 2) / WORLD_W, (h - pad * 2) / WORLD_H);
    k = Math.min(1.35, k);
    let x = (w - WORLD_W * k) / 2;
    let y = (h - WORLD_H * k) / 2;
    if (w < 760) {
      // phone: a readable zoom on the spine; the rest is a pan away
      k = Math.max(k, 0.52);
      x = w / 2 - colX(2.3) * k;
      y = h / 2 - rowY(3.6) * k;
    }
    V.target = { x, y, k };
    V.userMoved = false;
    if (snap) {
      V.view = { ...V.target };
      this._applyView(V);
    }
  },

  _applyView(V) {
    const { x, y, k } = V.view;
    V.world.style.transform = `translate(${x.toFixed(2)}px,${y.toFixed(2)}px) scale(${k.toFixed(4)})`;
    V.stage.style.setProperty("--vx", `${x.toFixed(1)}px`);
    V.stage.style.setProperty("--vy", `${y.toFixed(1)}px`);
    V.stage.style.setProperty("--vk", k.toFixed(4));
    const { w, h } = this._size(V);
    if (V.miniView) {
      V.miniView.setAttribute("x", (-x / k).toFixed(1));
      V.miniView.setAttribute("y", (-y / k).toFixed(1));
      V.miniView.setAttribute("width", (w / k).toFixed(1));
      V.miniView.setAttribute("height", (h / k).toFixed(1));
    }
  },

  _zoomAt(V, px, py, factor) {
    const t = V.target;
    const k = Math.min(MAX_K, Math.max(MIN_K, t.k * factor));
    const f = k / t.k;
    t.x = px - (px - t.x) * f;
    t.y = py - (py - t.y) * f;
    t.k = k;
    V.userMoved = true;
  },

  _input(V) {
    const stage = V.stage;
    const ptrs = new Map();
    let drag = null;
    let pinch = null;
    stage.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const r = stage.getBoundingClientRect();
      const f = Math.exp(-ev.deltaY * (ev.ctrlKey ? 0.01 : 0.0016));
      this._zoomAt(V, ev.clientX - r.left, ev.clientY - r.top, f);
    }, { passive: false });
    stage.addEventListener("pointerdown", (ev) => {
      if (ev.target.closest(".node, .wf-hud button, .wf-mini")) return;
      stage.setPointerCapture(ev.pointerId);
      ptrs.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
      if (ptrs.size === 1) drag = { x: ev.clientX, y: ev.clientY, tx: V.target.x, ty: V.target.y, moved: false };
      if (ptrs.size === 2) {
        const [a, b] = [...ptrs.values()];
        pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), k: V.target.k };
        drag = null;
      }
    });
    stage.addEventListener("pointermove", (ev) => {
      if (!ptrs.has(ev.pointerId)) return;
      ptrs.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
      if (pinch && ptrs.size === 2) {
        const [a, b] = [...ptrs.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        const r = stage.getBoundingClientRect();
        this._zoomAt(V, (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top, (pinch.k * (d / pinch.d)) / V.target.k);
      } else if (drag) {
        const dx = ev.clientX - drag.x;
        const dy = ev.clientY - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
        V.target.x = drag.tx + dx;
        V.target.y = drag.ty + dy;
        V.userMoved = true;
        stage.classList.add("panning");
      }
    });
    const end = (ev) => {
      ptrs.delete(ev.pointerId);
      if (ptrs.size < 2) pinch = null;
      if (!ptrs.size) {
        if (drag && !drag.moved && ev.type === "pointerup" && V.selected) this._select(V, null);
        else if (drag && !drag.moved && ev.type === "pointerup" && V.stage.classList.contains("dimmed")) {
          this._highlight(V, null);
          V.panel.clearPick();
        }
        drag = null;
        stage.classList.remove("panning");
      }
    };
    stage.addEventListener("pointerup", end);
    stage.addEventListener("pointercancel", end);
    stage.addEventListener("dblclick", (ev) => {
      if (ev.target.closest(".node, .wf-hud")) return;
      this._fit(V, false);
    });
    V.root.querySelector(".wf-ctl").addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-z]");
      if (!b) return;
      const { w, h } = this._size(V);
      if (b.dataset.z === "map") V.root.querySelector(".wf-tl").classList.toggle("open");
      else if (b.dataset.z === "fit") this._fit(V, false);
      else this._zoomAt(V, w / 2, h / 2, b.dataset.z === "in" ? 1.25 : 0.8);
    });
    V.root.addEventListener("keydown", (ev) => {
      if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
      const { w, h } = this._size(V);
      if (ev.key === "+" || ev.key === "=") this._zoomAt(V, w / 2, h / 2, 1.2);
      else if (ev.key === "-" || ev.key === "_") this._zoomAt(V, w / 2, h / 2, 1 / 1.2);
      else if (ev.key === "0") this._fit(V, false);
      else if (ev.key === "Escape" && V.selected) {
        ev.stopPropagation();
        this._select(V, null);
      }
    });
  },
};
