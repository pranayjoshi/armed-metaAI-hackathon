// Signals: the body, visualised. Real EMG only: terrain from emg.trace / emg.brace_trace, gauges from the
// live levels and thresholds, scopes from emg.rms, latency from real emg.* -> arm.stopped event pairs.
import { SignalBuffer, WINDOW_S } from "./buffer.js";
import { createGauges, createChannels } from "./gauges.js";
import { createLatency } from "./latency.js";

const TERRAIN_EVENTS = new Set(["emg.squeeze", "emg.open_hand", "emg.brace", "arm.stopped"]);

const html = `
<div class="sig">
  <header class="sig-head rise" style="--i:0">
    <div class="sig-title">
      <span class="kicker">The body, visualised</span>
      <p class="sig-line" id="sg-line">Waiting for the EMG stream…</p>
    </div>
    <div class="sig-badges" id="sg-badges"></div>
  </header>

  <section class="panel sig-terrain rise" style="--i:1" aria-label="Signal terrain">
    <div class="panel-head">
      <div><h3>Signal terrain</h3><p class="sig-cap">last ${WINDOW_S} s · height = % of calibrated maximum · newest at the front</p></div>
      <ul class="sig-legend">
        <li style="--h:var(--grip)">grip</li><li style="--h:var(--open)">open hand</li><li style="--h:var(--brace)">brace</li>
      </ul>
    </div>
    <div class="terrain-host" id="sg-terrain"></div>
    <div class="terrain-empty" id="sg-empty" hidden></div>
    <div class="terrain-hint" id="sg-hint">drag to orbit · scroll to zoom · double-click to reset</div>
  </section>

  <aside class="sig-side">
    <section class="panel sig-gauges rise" style="--i:2" aria-label="Levels">
      <div class="panel-head"><h3>Live levels</h3><span class="sig-age num" id="sg-age"></span></div>
      <div class="gauges" id="sg-gauges"></div>
    </section>
    <section class="panel sig-channels rise" style="--i:3" aria-label="Channels">
      <div class="panel-head"><h3>Channels</h3></div>
      <div class="channels" id="sg-channels"></div>
    </section>
    <section class="panel sig-drive rise" style="--i:4" id="sg-drive" hidden aria-label="Drive the signal">
      <div class="panel-head"><h3>Drive the signal</h3><span class="chip warn dev"><i></i>dev · synthetic only</span></div>
      <label class="drv"><span style="color:var(--grip)">flexor</span><input type="range" min="0" max="1" step="0.01" value="0" id="sg-flex" /><output class="num" id="sg-flex-o">0.00</output></label>
      <label class="drv"><span style="color:var(--open)">extensor</span><input type="range" min="0" max="1" step="0.01" value="0" id="sg-ext" /><output class="num" id="sg-ext-o">0.00</output></label>
      <div class="btn-row"><button class="btn danger" id="sg-brace">Brace pulse · 250 ms</button><button class="btn" id="sg-zero">Zero</button></div>
    </section>
  </aside>

  <section class="panel sig-latency rise" style="--i:5" aria-label="Stop latency">
    <div class="panel-head"><h3>Stop latency</h3><span class="muted sig-cap">trigger event to arm.stopped</span></div>
    <div id="sg-latency" class="latency"></div>
  </section>
</div>`;

export default {
  async mount(root, ctx) {
    ctx.css(import.meta.url, "style.css");
    root.innerHTML = html;
    const $ = (s) => root.querySelector(s);
    const { store, bus, api } = ctx;

    const buf = new SignalBuffer();
    const gauges = createGauges($("#sg-gauges"));
    const channels = createChannels($("#sg-channels"));
    const latency = createLatency($("#sg-latency"));

    const s = (this.s = { root, ctx, buf, gauges, channels, latency, terrain: null, off: [], removeTick: null, active: false, $ });

    // the history buffer accumulates even while this section is hidden
    if (store.state) buf.ingest(store.state);
    s.off.push(bus.on("state", (st) => buf.ingest(st)));
    s.off.push(
      bus.on("event", (e) => {
        gauges.hold(e.kind);
        if (TERRAIN_EVENTS.has(e.kind)) s.terrain?.syncEvents();
      }),
    );

    // terrain: three.js, or a 2D ridge plot when WebGL is missing / fails
    const getEvents = () => {
      const lo = buf.clock() - WINDOW_S - 1;
      return store.history.filter((e) => TERRAIN_EVENTS.has(e.kind) && e.t >= lo);
    };
    const host = $("#sg-terrain");
    try {
      if (!ctx.webgl) throw new Error("no webgl");
      const { createTerrain } = await import("./terrain.js");
      s.terrain = await createTerrain(host, ctx, buf, getEvents);
    } catch (err) {
      if (ctx.webgl) console.warn("signals: 3D terrain failed, using 2D", err);
      host.replaceChildren();
      const { createTerrain2D } = await import("./terrain2d.js");
      s.terrain = createTerrain2D(host, ctx, buf, getEvents);
      $("#sg-hint").hidden = true;
    }

    // Replay servers have no /api/emg/synthetic: the drive dock is never wired there.
    if (ctx.mode !== "replay") this.bindDrive(s);
    else $("#sg-drive").remove();
  },

  bindDrive(s) {
    const { $, ctx } = s;
    const { api } = ctx;
    const flex = $("#sg-flex");
    const ext = $("#sg-ext");
    const fo = $("#sg-flex-o");
    const eo = $("#sg-ext-o");
    let timer = null;
    const post = () => {
      timer = null;
      api("/api/emg/synthetic", { flexor: +flex.value, extensor: +ext.value });
    };
    const queue = () => {
      fo.textContent = (+flex.value).toFixed(2);
      eo.textContent = (+ext.value).toFixed(2);
      timer ||= setTimeout(post, 60); // throttle: at most ~16 requests/s while dragging
    };
    flex.addEventListener("input", queue);
    ext.addEventListener("input", queue);
    const setBoth = (v) => {
      flex.value = ext.value = v;
      fo.textContent = eo.textContent = v.toFixed(2);
    };
    $("#sg-zero").onclick = () => {
      setBoth(0);
      post();
    };
    let pulse = null;
    $("#sg-brace").onclick = async () => {
      clearTimeout(pulse);
      setBoth(0.9);
      await api("/api/emg/synthetic", { flexor: 0.9, extensor: 0.9 });
      pulse = setTimeout(() => {
        setBoth(0);
        api("/api/emg/synthetic", { flexor: 0, extensor: 0 });
      }, 250);
    };
    s.dispose = () => {
      clearTimeout(timer);
      clearTimeout(pulse);
    };
  },

  enter(ctx) {
    const s = this.s;
    if (!s) return;
    s.active = true;
    s.terrain?.enter();
    s.gauges.snap();
    s.removeTick ||= ctx.loop.add((dt) => {
      s.gauges.tick(dt);
      s.channels.tick(dt);
    });
    if (ctx.store.state) this.update(ctx.store.state, ctx);
  },

  leave() {
    const s = this.s;
    if (!s) return;
    s.active = false;
    s.terrain?.leave();
    s.removeTick?.();
    s.removeTick = null;
  },

  update(state, ctx) {
    const s = this.s;
    if (!s) return;
    const emg = state.emg;
    const { $ } = s;
    const replay = ctx.mode === "replay";
    const ro = replay ? `<span class="chip" title="${ctx.esc(state.note || "Nothing here moves an arm.")}"><i></i>READ-ONLY · ${ctx.esc(state.label || "REPLAY")}</span>` : "";
    $("#sg-empty").hidden = !!emg;
    const drive = $("#sg-drive");
    if (drive) drive.hidden = !(emg && emg.source === "synthetic");
    if (!emg) {
      const msg = replay
        ? "No simulation running. Open Replay to browse the recorded incidents; the live signal only exists while a simulation runs."
        : "EMG is off. Start the runtime with <code>--emg synthetic</code> or the Ganglion to see the signal.";
      if ($("#sg-empty").dataset.m !== msg) { $("#sg-empty").dataset.m = msg; $("#sg-empty").innerHTML = msg; }
      $("#sg-line").textContent = replay ? "No simulation running" : "EMG is off";
      $("#sg-badges").innerHTML = ro;
      return;
    }
    s.gauges.update(emg);
    s.channels.update(emg, s.buf);
    s.latency.update(ctx.store.history);

    // header: honesty first
    const synth = emg.source === "synthetic";
    const badge = (text, tone, extra = "") => `<span class="chip ${tone}"${extra}><i></i>${text}</span>`;
    const b = [];
    if (synth) b.push(badge("SYNTHETIC EMG", "warn", ' title="Signal is generated by the runtime, not read from electrodes"'));
    else if (emg.hardware) b.push(badge(emg.live ? "GANGLION · LIVE" : "GANGLION · NO DATA", emg.live ? "ok" : "bad"));
    if (emg.error) b.push(badge("ERROR", "bad", ` title="${ctx.esc(emg.error)}"`));
    if (!emg.live && !emg.error) b.push(badge("STREAM STALE", "bad"));
    if (replay) b.push(ro);
    else {
      if (!emg.calibrated) b.push(badge("NOT CALIBRATED", "warn"));
      if (!emg.brace_calibrated) b.push(badge("BRACE NOT CALIBRATED", "warn"));
    }
    const need = !emg.calibrated || !emg.brace_calibrated;
    // Replay has no Lab (calibration actuates); the button is not rendered there.
    const html = `${b.join("")}${replay ? "" : `<button class="btn ${need ? "primary" : ""} cal" id="sg-cal">${need ? "Calibrate in Lab" : "Open Lab"}</button>`}`;
    const badges = $("#sg-badges");
    if (badges.dataset.k !== html) {
      badges.dataset.k = html;
      badges.innerHTML = html;
      const cal = badges.querySelector("#sg-cal");
      if (cal) cal.onclick = (e) => {
        const r = e.currentTarget.getBoundingClientRect();
        ctx.router.go("lab", [r.left + r.width / 2, r.top + r.height / 2]);
      };
    }
    $("#sg-line").textContent = `${emg.rms?.length ?? 0} channels · profile “${emg.profile}” · ${emg.live ? "streaming" : "no data"}`;
    $("#sg-age").textContent = emg.age_ms == null ? "—" : `${emg.age_ms} ms old`;
    s.root.firstElementChild.classList.toggle("brace-hot", emg.brace >= emg.brace_effort);
  },

  destroy() {
    const s = this.s;
    if (!s) return;
    s.off.forEach((f) => f());
    s.removeTick?.();
    s.dispose?.();
    s.terrain?.dispose();
  },
};
