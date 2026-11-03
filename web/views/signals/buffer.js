// Client-side history of the muscle signal.
// The runtime only sends the last ~5 s of `emg.trace` / `emg.brace_trace` in every snapshot.
// This buffer accumulates those rows by timestamp (never re-adding, never interpolating) so the
// terrain can show a continuous ~30 s window. Timestamps are the runtime's monotonic clock, the
// same clock as `state.t` and every event's `t`.

export const WINDOW_S = 30;

export class SignalBuffer {
  constructor(windowS = WINDOW_S) {
    this.windowS = windowS;
    this.rows = []; // { t, g (grip), o (open hand), b (brace), gap }
    this.rms = [[], [], [], []]; // last ~6 s of per-channel RMS, [t, value]
    this.lastT = -Infinity;
    this.anchor = { stateT: 0, perf: 0 }; // maps performance.now() to the runtime clock
    this.thr = { confirm: 0.3, stop: 0.55, brace: 0.35 };
    this.crossings = []; // { lane, t, level } threshold crossings seen on live data
    this.seeded = false;
    this.over = { g: false, o: false, b: false };
  }

  /** Feed one /api/live snapshot. Safe to call while the section is hidden. */
  ingest(state) {
    const emg = state?.emg;
    if (!emg || !Array.isArray(emg.trace)) return;
    this.thr = { confirm: emg.confirm_effort ?? 0.3, stop: emg.stop_effort ?? 0.55, brace: emg.brace_effort ?? 0.35 };
    this.anchor = { stateT: state.t, perf: performance.now() };

    const tail = emg.trace.length ? emg.trace[emg.trace.length - 1][0] : null;
    if (tail != null && tail < this.lastT - 1) this.reset(); // runtime restarted: its clock went backwards

    const brace = new Map((emg.brace_trace || []).map((r) => [r[0], r[1]]));
    const live = this.seeded; // the first snapshot is history: no threshold bursts for it
    for (const [t, g, o] of emg.trace) {
      if (t <= this.lastT) continue;
      const b = brace.get(t) ?? 0;
      const prev = this.rows[this.rows.length - 1];
      // A hole in the data (stream paused / reconnected) is drawn as a drop to zero, not bridged.
      if (prev && t - prev.t > 0.35) {
        this.rows.push({ t: prev.t + 0.001, g: 0, o: 0, b: 0, gap: true }, { t: t - 0.001, g: 0, o: 0, b: 0, gap: true });
      }
      this.rows.push({ t, g, o, b, gap: false });
      this.lastT = t;
      this.cross("g", g, this.thr.confirm, t, live);
      this.cross("o", o, this.thr.stop, t, live);
      this.cross("b", b, this.thr.brace, t, live);
    }
    this.seeded = true;

    // trim to the window
    const cut = (this.lastT === -Infinity ? 0 : this.lastT) - this.windowS - 1;
    let k = 0;
    while (k < this.rows.length && this.rows[k].t < cut) k++;
    if (k > 0) this.rows.splice(0, k);
    this.crossings = this.crossings.filter((c) => c.t >= cut);

    // channel RMS: one sample per snapshot, stamped with the runtime time
    if (Array.isArray(emg.rms)) {
      emg.rms.forEach((v, i) => {
        const a = this.rms[i];
        if (!a) return;
        if (a.length && a[a.length - 1][0] === state.t) return;
        a.push([state.t, v]);
        while (a.length && a[0][0] < state.t - 6) a.shift();
      });
    }
  }

  cross(lane, level, thr, t, live) {
    const was = this.over[lane];
    // 0.9 * thr on the way down: hysteresis so noise at the line does not re-fire
    const now = was ? level >= thr * 0.9 : level >= thr;
    if (now && !was && live) this.crossings.push({ lane, t, level });
    this.over[lane] = now;
  }

  reset() {
    this.rows = [];
    this.rms = [[], [], [], []];
    this.lastT = -Infinity;
    this.crossings = [];
    this.seeded = false;
  }

  /** Runtime time "now", advanced between snapshots (capped, so a dead stream stops scrolling). */
  clock(perfNow = performance.now()) {
    const dt = Math.min(1.5, Math.max(0, (perfNow - this.anchor.perf) / 1000));
    return this.anchor.stateT + dt;
  }
}
