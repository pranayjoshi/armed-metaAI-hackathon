// 2D fallback for the signal terrain (no WebGL): a ridge plot of the same SignalBuffer.
// One band per lane, time flows right to left, newest at the right edge.
import { WINDOW_S } from "./buffer.js";

const LANES = [
  { key: "g", hue: "#4fd8ff", title: "GRIP", thr: "confirm", thrName: "CONFIRM" },
  { key: "o", hue: "#ffc15a", title: "OPEN HAND", thr: "stop", thrName: "STOP" },
  { key: "b", hue: "#ff4d5e", title: "BRACE", thr: "brace", thrName: "BRACE" },
];
const EVENT_STYLE = {
  "emg.squeeze": ["SQUEEZE", "#4fd8ff"],
  "emg.open_hand": ["OPEN HAND", "#ffc15a"],
  "emg.brace": ["BRACE", "#ff4d5e"],
  "arm.stopped": ["ARM STOPPED", "#eaf0f8"],
};

export function createTerrain2D(host, ctx, buf, getEvents) {
  const { loop } = ctx;
  const canvas = document.createElement("canvas");
  canvas.className = "terrain-canvas";
  host.append(canvas);
  const g = canvas.getContext("2d");
  let W = 1;
  let H = 1;
  let dpr = 1;
  let remove = null;

  function resize() {
    const r = host.getBoundingClientRect();
    dpr = Math.min(devicePixelRatio || 1, 2);
    W = Math.max(2, Math.round(r.width));
    H = Math.max(2, Math.round(r.height));
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = `${W}px`;
    canvas.style.height = `${H}px`;
  }
  const ro = new ResizeObserver(resize);
  ro.observe(host);
  resize();

  function frame() {
    const now = buf.clock();
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const padL = 54;
    const padR = 14;
    const padT = 56;
    const padB = 26;
    const bandH = (H - padT - padB) / LANES.length;
    const x = (t) => padL + (1 - (now - t) / WINDOW_S) * (W - padL - padR);
    g.font = '500 11px "JetBrains Mono", ui-monospace, monospace';
    g.textBaseline = "middle";

    // time axis
    g.strokeStyle = "rgba(160,190,230,.1)";
    g.fillStyle = "#6c7a8d";
    g.textAlign = "center";
    for (const s of [0, 10, 20, 30]) {
      const xx = x(now - s);
      g.beginPath();
      g.moveTo(xx, padT - 6);
      g.lineTo(xx, H - padB);
      g.stroke();
      g.fillText(s === 0 ? "now" : `-${s} s`, Math.min(W - 18, Math.max(padL, xx)), H - 10);
    }

    LANES.forEach((L, li) => {
      const y0 = padT + (li + 1) * bandH - 8; // baseline
      const hh = bandH - 22;
      const thr = buf.thr[L.thr];
      // gridlines at 0 / 50 / 100 %
      g.strokeStyle = "rgba(160,190,230,.1)";
      g.fillStyle = "#6c7a8d";
      g.textAlign = "right";
      for (const lv of [0, 0.5, 1]) {
        const yy = y0 - lv * hh;
        g.beginPath();
        g.moveTo(padL, yy);
        g.lineTo(W - padR, yy);
        g.stroke();
        g.fillText(`${lv * 100}%`, padL - 8, yy);
      }
      g.textAlign = "left";
      g.fillStyle = L.hue;
      g.font = '600 11px "JetBrains Mono", ui-monospace, monospace';
      g.fillText(L.title, padL + 6, y0 - hh - 2);
      g.font = '500 11px "JetBrains Mono", ui-monospace, monospace';

      // area + line
      const rows = buf.rows;
      if (rows.length > 1) {
        const grad = g.createLinearGradient(0, y0 - hh, 0, y0);
        grad.addColorStop(0, `${L.hue}aa`);
        grad.addColorStop(1, `${L.hue}00`);
        g.beginPath();
        g.moveTo(x(rows[0].t), y0);
        for (const r of rows) g.lineTo(x(r.t), y0 - Math.min(r[L.key], 1.3) * hh);
        g.lineTo(x(rows[rows.length - 1].t), y0);
        g.closePath();
        g.fillStyle = grad;
        g.fill();
        g.beginPath();
        rows.forEach((r, i) => {
          const px = x(r.t);
          const py = y0 - Math.min(r[L.key], 1.3) * hh;
          i ? g.lineTo(px, py) : g.moveTo(px, py);
        });
        g.strokeStyle = L.hue;
        g.lineWidth = 1.6;
        g.stroke();
        g.lineWidth = 1;
      }
      // threshold
      const ty = y0 - thr * hh;
      const isOver = buf.over[L.key];
      g.setLineDash([5, 4]);
      g.strokeStyle = isOver ? L.hue : `${L.hue}88`;
      g.beginPath();
      g.moveTo(padL, ty);
      g.lineTo(W - padR, ty);
      g.stroke();
      g.setLineDash([]);
      g.fillStyle = L.hue;
      g.textAlign = "right";
      g.fillText(`${L.thrName} ${Math.round(thr * 100)}%`, W - padR - 4, ty - 8);
    });

    // crossings
    for (const c of buf.crossings) {
      const li = LANES.findIndex((l) => l.key === c.lane);
      const L = LANES[li];
      const y0 = padT + (li + 1) * bandH - 8;
      const ty = y0 - buf.thr[L.thr] * (bandH - 22);
      const k = Math.min(1, (now - c.t) / 1.2);
      g.strokeStyle = L.hue;
      g.globalAlpha = 1 - k * 0.7;
      g.beginPath();
      g.arc(x(c.t), ty, 4 + k * 14 * (ctx.reduced ? 0 : 1), 0, Math.PI * 2);
      g.stroke();
      g.globalAlpha = 1;
    }
    // events
    g.textAlign = "left";
    let lastLabelX = -99;
    for (const e of getEvents().sort((a, b) => a.t - b.t)) {
      const st = EVENT_STYLE[e.kind];
      if (!st) continue;
      const xx = x(e.t);
      if (xx < padL || xx > W - padR) continue;
      g.strokeStyle = st[1];
      g.globalAlpha = 0.65;
      g.beginPath();
      g.moveTo(xx, padT - 6);
      g.lineTo(xx, H - padB);
      g.stroke();
      g.globalAlpha = 1;
      if (xx - lastLabelX < 78) continue; // line only: its label would sit on the previous one
      lastLabelX = xx;
      g.fillStyle = st[1];
      g.fillText(st[0], xx + 5, padT - 14);
    }
  }

  return {
    syncEvents() {},
    enter() {
      resize();
      remove ||= loop.add(frame);
    },
    leave() {
      remove?.();
      remove = null;
    },
    resize,
    dispose() {
      remove?.();
      ro.disconnect();
      canvas.remove();
    },
  };
}
