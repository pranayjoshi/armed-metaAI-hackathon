// The loop tracker (Brace -> Freeze -> Clip saved -> Muse explains -> You approve -> Rule saved)
// and the "thread": particles that flow along it while the loop is waiting on something, and fly into
// the rules when one is approved.
import { esc } from "/web/js/core.js";

const G = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const X_ICON = G('<path d="M6 6l12 12M18 6L6 18"/>');
export const STEPS = [
  { id: "brace", label: "Brace", icon: G('<path d="M2 12h4l2.4-7 3.8 14 3-9 1.6 2H22"/>') },
  { id: "freeze", label: "Freeze", icon: G('<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>') },
  { id: "clip", label: "Clip saved", icon: G('<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3 9h18M8 5v4M13 5v4M18 5v4"/>') },
  { id: "muse", label: "Muse explains", icon: G('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16l.7 1.8L21.5 18.5l-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7z"/>') },
  { id: "approve", label: "You approve", icon: G('<path d="M5 12.5l4.5 4.5L19 7.5"/>') },
  { id: "rule", label: "Rule saved", icon: G('<path d="M6 3h12v18l-6-4.5L6 21z"/>') },
];

export class Tracker {
  constructor(host) {
    this.host = host;
    host.innerHTML = `<ol class="trk">${STEPS.map(
      (s, i) => `<li class="trk-n pending" data-i="${i}" data-id="${s.id}"><span class="dot">${s.icon}</span><b>${s.label}</b><small>—</small></li>`,
    ).join("")}<i class="trk-line"><i class="trk-prog"></i></i></ol>`;
    this.items = [...host.querySelectorAll(".trk-n")];
    this.line = host.querySelector(".trk-line");
    this.prog = host.querySelector(".trk-prog");
    this.sig = "";
  }
  dot(i) {
    return this.items[i].querySelector(".dot");
  }
  /** stages: [{state: done|active|pending|fail|skip, sub}] */
  render(stages) {
    const sig = JSON.stringify(stages);
    if (sig === this.sig) return;
    const prev = this.states || [];
    this.sig = sig;
    this.states = stages.map((s) => s.state);
    stages.forEach((s, i) => {
      const li = this.items[i];
      li.className = `trk-n ${s.state}`;
      const want = s.state === "fail" ? "x" : "i";
      const dot = li.querySelector(".dot");
      if (dot.dataset.k !== want) {
        dot.dataset.k = want;
        dot.innerHTML = s.state === "fail" ? X_ICON : STEPS[i].icon;
      }
      li.querySelector("small").textContent = s.sub || "—";
      if (prev[i] && prev[i] !== s.state && (s.state === "done" || s.state === "fail")) {
        li.classList.add("pop");
        setTimeout(() => li.classList.remove("pop"), 900);
      }
    });
    let last = -1;
    stages.forEach((s, i) => {
      if (s.state === "done" || s.state === "skip") last = i;
      else if (s.state === "fail") last = Math.max(last, i);
    });
    const active = stages.findIndex((s) => s.state === "active");
    this.activeIdx = active;
    this.lastIdx = last;
    this.prog.style.setProperty("--p", String(Math.max(0, last) / (STEPS.length - 1)));
    this.line.classList.toggle("empty", last < 0);
  }
}

// ── thread particles ─────────────────────────────────────────────────────────
export class Thread {
  constructor(root, canvas) {
    this.root = root;
    this.c = canvas;
    this.g = canvas.getContext("2d");
    this.parts = [];
    this.flowOn = null;
    this.acc = 0;
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(root);
    this.resize();
  }
  resize() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const b = this.root.getBoundingClientRect();
    this.w = b.width;
    this.h = b.height;
    this.c.width = Math.round(b.width * dpr);
    this.c.height = Math.round(b.height * dpr);
    this.dpr = dpr;
  }
  dispose() {
    this.ro.disconnect();
  }
  center(el) {
    const r = el.getBoundingClientRect();
    const b = this.root.getBoundingClientRect();
    return [r.left - b.left + r.width / 2, r.top - b.top + r.height / 2];
  }
  /** Continuous particles on the straight thread between two tracker dots (or off). */
  flow(from, to, color) {
    this.flowOn = from && to ? { from, to, color } : null;
  }
  /** A burst along a curve from one element into another. */
  fly(from, to, { color = "90,240,164", count = 46, dur = 1.5, onArrive } = {}) {
    const a = this.center(from);
    const b = this.center(to);
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len;
    const ny = dx / len;
    const arc = Math.max(70, len * 0.3) * (nx > 0 ? -1 : 1);
    const bez = [a, [a[0] + dx * 0.25 + nx * arc, a[1] + dy * 0.25 + ny * arc], [b[0] - dx * 0.25 + nx * arc * 0.5, b[1] - dy * 0.25 + ny * arc * 0.5], b];
    let arrived = false;
    for (let i = 0; i < count; i++) {
      this.parts.push({ kind: "fly", bez, u: -(i / count) * 0.55, spd: 1 / dur, jit: (Math.random() - 0.5) * 18, r: 1.6 + Math.random() * 2.2, color, onArrive: i === count - 1 ? () => !arrived && ((arrived = true), onArrive?.()) : null });
    }
  }
  frame(dt) {
    const g = this.g;
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    g.clearRect(0, 0, this.w, this.h);
    if (this.flowOn) {
      const a = this.center(this.flowOn.from);
      const b = this.center(this.flowOn.to);
      this.acc += dt;
      while (this.acc > 0.16) {
        this.acc -= 0.16;
        this.parts.push({ kind: "flow", a, b, u: 0, spd: 0.55 + Math.random() * 0.3, r: 1.6 + Math.random() * 1.6, color: this.flowOn.color });
      }
    }
    g.globalCompositeOperation = "lighter";
    this.parts = this.parts.filter((p) => {
      p.u += dt * p.spd;
      if (p.u < 0) return true;
      if (p.u >= 1) {
        p.onArrive?.();
        return false;
      }
      const draw = (u, a, r) => {
        let x;
        let y;
        if (p.kind === "flow") {
          x = p.a[0] + (p.b[0] - p.a[0]) * u;
          y = p.a[1] + (p.b[1] - p.a[1]) * u + Math.sin(u * 14 + p.r * 5) * 2.2;
        } else {
          const e = u * u * (3 - 2 * u);
          const m = 1 - e;
          const B = p.bez;
          x = m * m * m * B[0][0] + 3 * m * m * e * B[1][0] + 3 * m * e * e * B[2][0] + e * e * e * B[3][0];
          y = m * m * m * B[0][1] + 3 * m * m * e * B[1][1] + 3 * m * e * e * B[2][1] + e * e * e * B[3][1];
          const wob = Math.sin(u * Math.PI) * p.jit;
          x += wob;
          y += wob * 0.4;
        }
        const grad = g.createRadialGradient(x, y, 0, x, y, r * 3);
        grad.addColorStop(0, `rgba(${p.color},${a})`);
        grad.addColorStop(1, `rgba(${p.color},0)`);
        g.fillStyle = grad;
        g.beginPath();
        g.arc(x, y, r * 3, 0, 6.283);
        g.fill();
      };
      const fade = p.kind === "flow" ? Math.sin(p.u * Math.PI) : 1 - Math.max(0, p.u - 0.85) / 0.15;
      for (let k = 0; k < 5; k++) draw(Math.max(0, p.u - k * 0.012), fade * (0.9 - k * 0.17), p.r * (1 - k * 0.12));
      return true;
    });
    g.globalCompositeOperation = "source-over";
  }
}

export { esc };
