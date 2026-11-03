// Radial gauges (grip / open hand / brace) and the four channel scopes.
import { clamp, damp, esc } from "../../js/core.js";

const SWEEP = 270;
const A0 = 135; // degrees, SVG orientation (0 = +x, clockwise); the dial opens at the bottom
const CX = 80;
const CY = 78;
const R = 58;
const pt = (deg, r = R) => {
  const a = (deg * Math.PI) / 180;
  return [CX + r * Math.cos(a), CY + r * Math.sin(a)];
};
const f2 = (n) => n.toFixed(2);
const ARC = (() => {
  const [x0, y0] = pt(A0);
  const [x1, y1] = pt(A0 + SWEEP);
  return `M${f2(x0)} ${f2(y0)} A${R} ${R} 0 1 1 ${f2(x1)} ${f2(y1)}`;
})();

const DEFS = [
  { id: "grip", title: "Grip", sub: "flexors · squeeze", hue: "var(--grip)", thr: "confirm_effort", thrName: "confirm", read: (e) => e.effort, idle: "idle", over: "confirming", held: "confirmed" },
  { id: "open", title: "Open hand", sub: "extensors", hue: "var(--open)", thr: "stop_effort", thrName: "stop", read: (e) => e.extensor, idle: "idle", over: "stop", held: "stopped" },
  { id: "brace", title: "Brace", sub: "both muscles at once", hue: "var(--brace)", thr: "brace_effort", thrName: "brace", read: (e) => e.brace, idle: "idle", over: "BRACE!", held: "BRACE!" },
];

export function createGauges(host) {
  host.innerHTML = DEFS.map(
    (d) => `
    <div class="gauge" data-g="${d.id}" style="--h:${d.hue}">
      <svg viewBox="0 0 160 134" role="img" aria-label="${d.title} level">
        <path class="g-track" d="${ARC}" pathLength="1"/>
        <path class="g-over" d="${ARC}" pathLength="1"/>
        <path class="g-val" d="${ARC}" pathLength="1"/>
        <g class="g-ticks"></g>
        <text class="g-num num" x="${CX}" y="${CY + 12}" text-anchor="middle"><tspan class="g-n">0</tspan><tspan class="g-pct" dx="2" dy="-12">%</tspan></text>
      </svg>
      <div class="g-meta">
        <span class="kicker">${d.title}</span>
        <b class="g-state">${d.idle}</b>
        <span class="g-sub">${d.sub}</span>
        <span class="g-thr num">${d.thrName} —</span>
      </div>
    </div>`,
  ).join("");

  const gs = DEFS.map((d) => {
    const el = host.querySelector(`[data-g="${d.id}"]`);
    return {
      d,
      el,
      val: el.querySelector(".g-val"),
      over: el.querySelector(".g-over"),
      ticks: el.querySelector(".g-ticks"),
      num: el.querySelector(".g-n"),
      state: el.querySelector(".g-state"),
      thr: el.querySelector(".g-thr"),
      shown: 0,
      target: 0,
      thrV: null,
      heldUntil: 0,
    };
  });

  function setTick(g, thr) {
    if (g.thrV === thr) return;
    g.thrV = thr;
    const deg = A0 + SWEEP * clamp(thr);
    const [x0, y0] = pt(deg, R - 9);
    const [x1, y1] = pt(deg, R + 8);
    g.ticks.innerHTML = `<line x1="${f2(x0)}" y1="${f2(y0)}" x2="${f2(x1)}" y2="${f2(y1)}"/>`;
    g.thr.textContent = `${g.d.thrName} ≥ ${Math.round(thr * 100)}%`;
    // the threshold-to-end segment glows when the level is past it
    g.over.style.strokeDasharray = `${f2(1 - thr)} 2`;
    g.over.style.strokeDashoffset = `${f2(-thr)}`;
  }

  return {
    /** snapshot -> targets (called at ~10 Hz) */
    update(emg, now = performance.now()) {
      for (const g of gs) {
        const v = clamp(g.d.read(emg) ?? 0, 0, 1);
        g.target = v;
        const thr = emg[g.d.thr];
        setTick(g, thr);
        const isOver = v >= thr;
        const held = now < g.heldUntil;
        g.el.classList.toggle("is-over", isOver);
        g.el.classList.toggle("is-held", held && !isOver);
        g.el.classList.toggle("alarm", g.d.id === "brace" && (isOver || held));
        g.state.textContent = isOver ? g.d.over : held ? g.d.held : g.d.idle;
      }
    },
    /** a real runtime event keeps the state word up long enough to read */
    hold(kind) {
      const map = { "emg.squeeze": "grip", "emg.open_hand": "open", "emg.brace": "brace" };
      const id = map[kind];
      const g = gs.find((x) => x.d.id === id);
      if (g) g.heldUntil = performance.now() + (id === "brace" ? 2200 : 1600);
    },
    /** per frame: damped arcs and numerals */
    tick(dt) {
      for (const g of gs) {
        g.shown = damp(g.shown, g.target, 16, dt);
        if (Math.abs(g.shown - g.target) < 0.0004) g.shown = g.target;
        g.val.style.strokeDasharray = `${f2(Math.max(0.0001, g.shown))} 2`;
        g.val.style.opacity = g.shown < 0.006 ? 0 : 1;
        g.num.textContent = Math.round(g.shown * 100);
      }
    },
    snap() {
      for (const g of gs) g.shown = g.target;
    },
  };
}

// ── channels: four mini scopes with the current RMS ─────────────────────────
const CH = [
  { label: "flexor 1", hue: "var(--grip)" },
  { label: "flexor 2", hue: "var(--grip)" },
  { label: "extensor 3", hue: "var(--open)" },
  { label: "extensor 4", hue: "var(--open)" },
];

export function createChannels(host) {
  host.innerHTML = `${CH.map(
    (c, i) => `
    <div class="ch" style="--h:${c.hue}" data-ch="${i}">
      <span class="ch-name">${esc(c.label)} <i class="num">ch${i + 1}</i></span>
      <svg class="ch-scope" viewBox="0 0 120 30" preserveAspectRatio="none" aria-hidden="true">
        <path class="ch-area" d=""/><path class="ch-line" d=""/>
      </svg>
      <b class="ch-val num">—</b>
      <span class="ch-bar"><i></i></span>
    </div>`,
  ).join("")}<div class="ch-foot num"><span class="ch-unit"></span><span class="ch-scale"></span></div>`;
  const rows = [...host.querySelectorAll(".ch")].map((el) => ({
    el,
    line: el.querySelector(".ch-line"),
    area: el.querySelector(".ch-area"),
    val: el.querySelector(".ch-val"),
    bar: el.querySelector(".ch-bar i"),
    shown: 0,
    target: 0,
  }));
  const names = rows.map((r) => r.el.querySelector(".ch-name"));
  let roleKey = "";
  const unit = host.querySelector(".ch-unit");
  const scaleEl = host.querySelector(".ch-scale");
  let scale = 60;
  let scaleT = 60;

  return {
    update(emg, buf) {
      const rms = emg.rms || [];
      // Roles: the brief says ch1-2 flexors, ch3-4 extensors. If the runtime ever reports its own
      // flexor_channels / extensor_channels (0-based), those win.
      const fx = emg.flexor_channels ?? [0, 1];
      const ex = emg.extensor_channels ?? [2, 3];
      const rk = `${fx}|${ex}`;
      if (rk !== roleKey) {
        roleKey = rk;
        rows.forEach((r, i) => {
          const role = fx.includes(i) ? "flexor" : ex.includes(i) ? "extensor" : "channel";
          r.el.style.setProperty("--h", role === "flexor" ? "var(--grip)" : role === "extensor" ? "var(--open)" : "var(--ink-3)");
          names[i].innerHTML = `${role} ${i + 1}`;
        });
      }
      // honest, slowly adapting scale: floor 50, never clips the visible window
      let peak = 0;
      for (const a of buf.rms) for (const p of a) peak = Math.max(peak, p[1]);
      scaleT = Math.max(50, Math.ceil((peak * 1.1) / 10) * 10);
      unit.textContent = emg.hardware ? "µV RMS" : "RMS · synthetic units";
      scaleEl.textContent = `scale 0–${Math.round(scale)}`;
      rows.forEach((r, i) => {
        const v = rms[i];
        r.target = v ?? 0;
        r.val.textContent = v == null ? "—" : v.toFixed(1);
        const a = buf.rms[i] || [];
        if (a.length > 1) {
          const t1 = a[a.length - 1][0];
          const pts = a.map(([t, y]) => `${((1 - (t1 - t) / 6) * 120).toFixed(1)} ${(30 - clamp(y / scale) * 28 - 1).toFixed(1)}`);
          const d = `M${pts.join(" L")}`;
          r.line.setAttribute("d", d);
          const x0 = pts[0].split(" ")[0];
          r.area.setAttribute("d", `${d} L120 30 L${x0} 30 Z`);
        }
      });
    },
    tick(dt) {
      scale = damp(scale, scaleT, 5, dt);
      for (const r of rows) {
        r.shown = damp(r.shown, r.target, 14, dt);
        r.bar.style.transform = `scaleX(${clamp(r.shown / scale).toFixed(4)})`;
      }
    },
  };
}
