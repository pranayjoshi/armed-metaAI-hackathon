// Stop latency: every EMG trigger (emg.brace / emg.open_hand) paired with the arm.stopped it caused.
// latency = arm.stopped.t - trigger.t, both from the runtime clock. Only stops whose reason names
// the same EMG gesture are paired, so a manual STOP is never counted as a reflex.
import { esc } from "../../js/core.js";

const TRIGGERS = { "emg.brace": "brace", "emg.open_hand": "open hand" };

export function pairStops(history) {
  const out = [];
  let trig = null;
  for (const e of history) {
    if (TRIGGERS[e.kind]) trig = e;
    else if (e.kind === "arm.stopped" && trig) {
      const word = TRIGGERS[trig.kind];
      const stopT = e.latency_ref ?? e.t; // arm.stopped carries a finer stamp of the freeze itself
      const dt = Math.max(0, stopT - trig.t);
      if (String(e.reason || "").startsWith(word) && dt < 2) {
        // from the first moment the detector saw the muscle rise, when the event carries it (brace does)
        const fromOnset = trig.onset_t != null ? Math.max(0, (stopT - trig.onset_t) * 1000) : null;
        out.push({
          seq: e.seq,
          kind: trig.kind,
          ev: dt * 1000, // trigger event -> arm.stopped
          ms: fromOnset ?? dt * 1000, // what the strip plots: the longest honest span we have
          fromOnset,
          detect: trig.detect_ms ?? null,
        });
      }
      trig = null;
    }
  }
  return out;
}

const median = (a) => {
  const s = [...a].sort((x, y) => x - y);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
const fmt = (ms) => (ms < 10 ? ms.toFixed(1) : String(Math.round(ms)));
const niceMax = (v) => {
  for (const s of [20, 50, 100, 200, 300, 500, 1000, 2000]) if (v * 1.15 <= s) return s;
  return Math.ceil(v / 1000) * 1000;
};

export function createLatency(host) {
  const lane = (kind, hue, label) => `
    <div class="lat-lane" data-k="${kind}" style="--h:${hue}">
      <div class="lat-lhead"><span class="lat-lbl">${label}</span><span class="lat-stat num"></span></div>
      <div class="lat-track"></div>
    </div>`;
  host.innerHTML = `
    <div class="lat-nums">
      <span class="kicker lat-what">brace</span>
      <div class="lat-big">
        <div><span class="kicker">median</span><b class="lat-med num">—</b></div>
        <div><span class="kicker">worst</span><b class="lat-worst num">—</b></div>
        <div><span class="kicker">stops</span><b class="lat-n num">0</b></div>
      </div>
    </div>
    <div class="lat-plot">
      <div class="lat-empty">No stops yet — squeeze or brace to measure</div>
      <div class="lat-lanes">
        ${lane("emg.brace", "var(--brace)", "brace · muscle onset → stopped")}
        ${lane("emg.open_hand", "var(--open)", "open hand · event → stopped")}
        <div class="lat-axis num"></div>
      </div>
    </div>
    <p class="lat-foot num"></p>`;
  const q = (s) => host.querySelector(s);
  const med = q(".lat-med");
  const worst = q(".lat-worst");
  const nEl = q(".lat-n");
  const what = q(".lat-what");
  const plot = q(".lat-plot");
  const axis = q(".lat-axis");
  const foot = q(".lat-foot");
  const lanes = {};
  for (const k of Object.keys(TRIGGERS)) {
    const el = q(`[data-k="${k}"]`);
    lanes[k] = { track: el.querySelector(".lat-track"), stat: el.querySelector(".lat-stat") };
  }
  let key = null;
  const known = new Set();

  return {
    update(history) {
      const pairs = pairStops(history);
      const k = pairs.map((p) => p.seq).join(",");
      if (k === key) return;
      key = k;
      const has = pairs.length > 0;
      plot.classList.toggle("empty", !has);
      if (!has) {
        med.textContent = worst.textContent = "—";
        nEl.textContent = "0";
        foot.textContent = "";
        for (const l of Object.values(lanes)) l.track.innerHTML = l.stat.textContent = "";
        return;
      }
      const by = (kind) => pairs.filter((p) => p.kind === kind);
      // the big numerals follow the lane that has data, brace first (its span starts at muscle onset)
      const main = by("emg.brace").length ? "emg.brace" : "emg.open_hand";
      const mp = by(main).map((p) => p.ms);
      what.textContent = main === "emg.brace" ? "brace" : "open hand";
      what.style.color = main === "emg.brace" ? "var(--brace)" : "var(--open)";
      med.innerHTML = `${fmt(median(mp))}<small>ms</small>`;
      worst.innerHTML = `${fmt(Math.max(...mp))}<small>ms</small>`;
      nEl.textContent = String(mp.length);

      const max = niceMax(Math.max(...pairs.map((p) => p.ms)));
      axis.innerHTML = [0, 0.25, 0.5, 0.75, 1].map((f) => `<span style="left:${f * 100}%">${Math.round(max * f)}${f === 1 ? " ms" : ""}</span>`).join("");
      for (const [kind, l] of Object.entries(lanes)) {
        const mine = by(kind);
        const cols = new Map();
        const ms = mine.map((p) => p.ms);
        l.stat.textContent = mine.length ? `median ${fmt(median(ms))} · worst ${fmt(Math.max(...ms))} ms · n ${mine.length}` : "none yet";
        const tick = mine.length ? `<b class="lat-med-tick" style="left:${(median(ms) / max) * 100}%"></b>` : "";
        l.track.innerHTML =
          tick +
          mine
            .map((p) => {
              const x = (p.ms / max) * 100;
              const bucket = Math.round(x / 2.2);
              const row = cols.get(bucket) ?? 0;
              cols.set(bucket, row + 1);
              const fresh = known.has(p.seq) ? "" : " fresh";
              const tip = [
                p.fromOnset != null ? `${fmt(p.fromOnset)} ms from detected muscle onset to arm.stopped` : `${fmt(p.ev)} ms from the ${TRIGGERS[kind]} event to arm.stopped`,
                p.detect != null ? `detector took ${p.detect} ms` : "",
                `${fmt(p.ev)} ms event to stop`,
              ].filter(Boolean).join(" · ");
              return `<i class="lat-dot${fresh}" style="left:${x}%;--r:${row}" title="${esc(tip)}"></i>`;
            })
            .join("");
      }
      pairs.forEach((p) => known.add(p.seq));
      const ev = pairs.map((p) => p.ev);
      const det = pairs.filter((p) => p.detect != null).map((p) => p.detect);
      foot.textContent = `event → stopped, all stops: median ${fmt(median(ev))} ms · worst ${fmt(Math.max(...ev))} ms${det.length ? ` · detector onset → event: median ${fmt(median(det))} ms` : ""} · software time, not the physical stop`;
    },
  };
}
