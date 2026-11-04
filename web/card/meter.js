// The muscle meter (UI.md 2.3): one thin bar. Flexor fills from the left in --grip, extensor from the
// right in --open, the overlap is --brace. A twitch flashes the bar once (a double twitch twice), a
// brace fills it red while it is held. Drawn on a canvas at display rate; levels are eased between
// the 10 Hz snapshots, flashes come from the twitch / brace EVENTS so a 60 ms twitch is never lost.
const clamp = (v, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));

export function createMeter(canvas, { reduced }) {
  const g = canvas.getContext("2d");
  let w = 0;
  let h = 0;
  let dpr = 1;
  const col = {};
  const readColors = () => {
    const cs = getComputedStyle(document.documentElement);
    for (const k of ["grip", "open", "brace", "line-2", "ink-3", "ink"]) col[k] = cs.getPropertyValue(`--${k}`).trim() || "#888";
  };
  readColors();

  const level = { f: 0, x: 0, b: 0 };
  const target = { f: 0, x: 0, b: 0, thrF: 0.3, thrX: 0.55, braceThr: 0.35, has: false };
  const flashes = []; // {t0, dur, color}
  let braceUntil = 0;
  let dirty = true;

  const size = () => {
    const r = canvas.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = Math.max(1, Math.round(r.width));
    h = Math.max(1, Math.round(r.height));
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    dirty = true;
  };
  new ResizeObserver(size).observe(canvas);
  size();

  return {
    /** From a snapshot's `emg` (null when there is no EMG). */
    set(emg) {
      target.has = !!emg;
      if (!emg) {
        target.f = target.x = target.b = 0;
        return;
      }
      target.f = clamp(emg.effort ?? 0);
      target.x = clamp(emg.extensor ?? 0);
      target.b = clamp(emg.brace ?? 0);
      target.thrF = emg.confirm_effort ?? 0.3;
      target.thrX = emg.stop_effort ?? 0.55;
      target.braceThr = emg.brace_effort ?? 0.35;
    },
    twitch(n, now) {
      for (let i = 0; i < n; i++) flashes.push({ t0: now + i * 0.17, dur: 0.22, color: col.grip });
      dirty = true;
    },
    brace(now) {
      braceUntil = Math.max(braceUntil, now + 0.7);
      flashes.push({ t0: now, dur: 0.3, color: col.brace });
      dirty = true;
    },
    get held() {
      return target.b >= target.braceThr;
    },
    /** Called every frame while the panel is visible. */
    draw(dt, now) {
      const k = reduced ? 1 : 1 - Math.exp(-22 * dt);
      for (const key of ["f", "x", "b"]) {
        const d = target[key] - level[key];
        if (Math.abs(d) > 0.002) {
          level[key] += d * k;
          dirty = true;
        } else level[key] = target[key];
      }
      const live = flashes.length > 0 || now < braceUntil;
      if (!dirty && !live) return;
      dirty = false;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, w, h);
      const r = Math.min(3, h / 2);
      const path = () => {
        g.beginPath();
        g.roundRect(0, 0, w, h, r);
      };
      g.save();
      path();
      g.clip();
      g.fillStyle = "rgba(160,190,230,.10)";
      g.fillRect(0, 0, w, h);
      const held = target.b >= target.braceThr || now < braceUntil;
      const fx = level.f * w;
      const xx = w - level.x * w;
      if (held && target.has) {
        g.fillStyle = col.brace;
        g.fillRect(0, 0, w, h);
      } else {
        g.fillStyle = col.grip;
        g.fillRect(0, 0, fx, h);
        g.fillStyle = col.open;
        g.fillRect(xx, 0, w - xx, h);
        if (fx > xx) {
          g.fillStyle = col.brace;
          g.fillRect(xx, 0, fx - xx, h);
        }
      }
      // thresholds: where a squeeze counts (left) and where an open hand stops the arm (right)
      if (target.has) {
        g.fillStyle = "rgba(234,240,248,.55)";
        g.fillRect(Math.round(target.thrF * w) - 0.5, 0, 1, h);
        g.fillRect(Math.round(w - target.thrX * w) - 0.5, 0, 1, h);
      }
      for (let i = flashes.length - 1; i >= 0; i--) {
        const f = flashes[i];
        const p = (now - f.t0) / f.dur;
        if (p >= 1) flashes.splice(i, 1);
        else if (p >= 0) {
          g.globalAlpha = (1 - p) * 0.85;
          g.fillStyle = "#fff";
          g.fillRect(0, 0, w, h);
          g.globalAlpha = 1;
        }
      }
      g.restore();
      dirty = live;
    },
  };
}
