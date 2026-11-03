// Stage 1: guided EMG calibration around a live 3D forearm.
// Real data only: pad glow is the live emg.rms[i]; bars after a step are what the server returned.
import { createHand3d, createHandSvg, DEFAULT_ROLES } from "./hand3d.js";

const DUR = 3; // seconds of recording per step (matches the old UI and the server default)
const HUE_VAR = { flexor: "var(--grip)", extensor: "var(--open)" };
/** Channel roles from the snapshot when the runtime reports them, else the plan's layout (CH1-2 flexor, CH3-4 extensor). */
function rolesOf(e) {
  const fl = e?.flexor_channels ?? [0, 1];
  const ex = e?.extensor_channels ?? [2, 3];
  return [0, 1, 2, 3].map((i) => (fl.includes(i) ? "flexor" : ex.includes(i) ? "extensor" : null));
}

const STEPS = [
  {
    id: "rest", phase: "rest", label: "Relax", pose: "rest", color: "#aab6c6", drive: [0, 0], optional: false,
    title: "Relax the forearm",
    text: "Rest the arm on the desk, hand limp, fingers loose. Do not squeeze. This is the noise floor everything else is measured against.",
  },
  {
    id: "squeeze", phase: "squeeze", label: "Squeeze", pose: "squeeze", color: "#4fd8ff", drive: [0.8, 0], optional: false,
    title: "Make a firm fist",
    text: "Squeeze as hard as you would to grip a cup. The palm-side flexor pads should rise well above rest.",
  },
  {
    id: "open", phase: "open", label: "Open hand", pose: "open", color: "#ffc15a", drive: [0, 0.8], optional: false,
    title: "Spread the hand, wrist back",
    text: "Open every finger wide and cock the wrist back. The back-of-forearm extensor pads should rise.",
  },
  {
    id: "brace", phase: "brace", label: "Brace", pose: "brace", color: "#ff4d5e", drive: [0.8, 0.8], optional: true,
    title: "Brace the whole forearm",
    text: "Stiffen the wrist and forearm at once, as if catching a fall, without moving the hand. Both pad pairs must rise together. Optional, but without it the brace detector has no personal scale.",
  },
  {
    id: "save", phase: "finish", label: "Save profile", pose: "save", color: "#5af0a4", drive: [0, 0], optional: false,
    title: "Save the profile",
    text: "The server checks every recorded phase for a real rise over rest, then writes the profile. If a channel is weak it tells you which one.",
  },
];

const STEP_NOTE = { rest: "noise floor", squeeze: "flexor max", open: "extensor max", brace: "both muscles", save: "write profile" };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const f1 = (v) => (Number.isFinite(v) ? v.toFixed(1) : "—");

export function createCalibrate(pane, ctx) {
  const { api, esc, clamp, loop, loadThree, webgl } = ctx;

  pane.innerHTML = `
    <div class="cal">
      <div class="cal-hand panel" data-ref="hand">
        <div class="cal-stage" data-ref="stage"></div>
        <div class="cal-copy">
          <div class="kicker" data-ref="stepno">Step 1 of 5</div>
          <h2 class="cal-title" data-ref="title"></h2>
          <p class="cal-text" data-ref="text"></p>
        </div>
        <div class="cal-tags">
          <span class="chip warn" data-ref="syn" hidden><i></i>SYNTHETIC EMG</span>
          <span class="chip" data-ref="status"><i></i>—</span>
        </div>
        <div class="cal-foot">
          <div class="cal-info">
            <div class="cal-legend" data-ref="legend">
              <span><i style="background:var(--grip)"></i>flexor · palm side</span>
              <span><i style="background:var(--open)"></i>extensor · back</span>
              <span><i style="background:var(--ink-2)"></i>reference · elbow bone</span>
              <span class="assume" data-ref="assume" hidden>channel map assumed: the runtime does not report it</span>
            </div>
            <div class="cal-norm">Glow = live RMS per channel: (rms − 30 s low) ÷ max(30 s high − low, 3 × low). The pose is the instruction; the glow is what the electrodes see.</div>
          </div>
          <div class="cal-trace">
            <canvas data-ref="trace"></canvas>
            <span class="kicker"><b style="color:var(--grip)">flexor</b> · <b style="color:var(--open)">extensor</b> effort · <span data-ref="tracemeta">emg.trace</span></span>
          </div>
        </div>
        <div class="cal-off" data-ref="off" hidden>
          <h2>EMG is off in this build</h2>
          <p>Start the runtime with <code>--emg synthetic</code> or <code>--emg ganglion</code> to calibrate.</p>
        </div>
      </div>

      <div class="cal-side">
        <ol class="stepper" data-ref="stepper"></ol>

        <div class="rec panel">
          <div class="rec-top">
            <div class="ring" data-ref="ring">
              <svg viewBox="0 0 132 132" aria-hidden="true"><circle class="trk" cx="66" cy="66" r="56"/><circle class="arc" data-ref="arc" cx="66" cy="66" r="56" pathLength="100"/></svg>
              <b class="num" data-ref="ringnum">3 s</b>
            </div>
            <div class="rec-act">
              <button class="btn primary big" data-ref="go">Record 3 s</button>
              <p class="rec-hint" data-ref="hint"></p>
            </div>
          </div>
          <div class="chcap kicker" data-ref="chcap">live rms</div>
          <div class="chbars" data-ref="chbars"></div>
          <p class="rec-result" data-ref="result" hidden></p>
        </div>

        <details class="drive panel" data-ref="drive" hidden>
          <summary><span>Development drive</span><span class="chip warn"><i></i>synthetic</span></summary>
          <div class="drive-body">
            <label class="check"><input type="checkbox" data-ref="auto" checked /> Drive the synthetic source to match each step</label>
            <label class="slide"><span>Flexor</span><input type="range" min="0" max="1" step="0.01" value="0" data-ref="sf" /><b class="num" data-ref="sfv">0.00</b></label>
            <label class="slide"><span>Extensor</span><input type="range" min="0" max="1" step="0.01" value="0" data-ref="se" /><b class="num" data-ref="sev">0.00</b></label>
            <p class="muted">Sends <code>POST /api/emg/synthetic</code>. There is no electrode here; this only exercises the loop.</p>
          </div>
        </details>
      </div>
    </div>`;

  const q = {};
  pane.querySelectorAll("[data-ref]").forEach((el) => (q[el.dataset.ref] = el));
  const trace2d = q.trace.getContext("2d");

  let state = null;
  let active = 0;
  let busy = false;
  let recT0 = 0;
  let hand = null;
  const handHost = q.stage;
  let ro = null;
  let removeLoop = null;
  let activeFlag = false;
  let advanceTimer = 0;
  let driveT = 0;
  const hist = [[], [], [], []]; // last 30 s of live rms per channel: [ms, rms]
  const results = {}; // step id -> { state: 'ok'|'fail', rms, detail, windows }
  let saved = null; // last finish response

  // ── stepper ────────────────────────────────────────────────────────────────
  q.stepper.innerHTML = STEPS.map(
    (s, i) => `<li><button data-i="${i}" style="--c:${s.color}">
      <span class="dot"><b class="num">${i + 1}</b><svg viewBox="0 0 20 20"><path d="M4 10.5l4 4 8-9"/></svg></span>
      <span class="lbl"><b>${s.label}${s.optional ? ' <em>optional</em>' : ""}</b><small data-s="${s.id}"></small></span>
      <span class="mini" data-m="${s.id}"></span>
    </button></li>`,
  ).join("");
  q.stepper.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-i]");
    if (b && !busy) select(Number(b.dataset.i));
  });
  q.chbars.innerHTML = [0, 1, 2, 3]
    .map((i) => `<div class="chb" data-row="${i}"><span data-name="${i}"></span><div class="trk"><i class="res" data-res="${i}"></i><i class="live" data-live="${i}"></i></div><b class="num" data-v="${i}">—</b></div>`)
    .join("");
  let rolesKey = "";
  function applyRoles(roles) {
    const key = roles.join("|");
    if (key === rolesKey) return;
    rolesKey = key;
    roles.forEach((r, i) => {
      const row = q.chbars.querySelector(`[data-row="${i}"]`);
      row.style.setProperty("--c", HUE_VAR[r] || "var(--ink-3)");
      row.dataset.role = r || "unused";
      row.querySelector("[data-name]").textContent = `CH${i + 1} ${r || "unused"}`;
    });
  }
  applyRoles(DEFAULT_ROLES);
  const resBars = [...pane.querySelectorAll("[data-res]")];
  const liveBars = [...pane.querySelectorAll("[data-live]")];
  const vals = [...pane.querySelectorAll("[data-v]")];

  q.go.addEventListener("click", record);

  // ── synthetic drive ────────────────────────────────────────────────────────
  const postDrive = (flexor, extensor) => api("/api/emg/synthetic", { flexor, extensor });
  function throttledDrive() {
    clearTimeout(driveT);
    driveT = setTimeout(() => postDrive(Number(q.sf.value), Number(q.se.value)), 90);
  }
  q.sf.addEventListener("input", () => {
    q.sfv.textContent = Number(q.sf.value).toFixed(2);
    throttledDrive();
  });
  q.se.addEventListener("input", () => {
    q.sev.textContent = Number(q.se.value).toFixed(2);
    throttledDrive();
  });
  function showDrive(f, e) {
    q.sf.value = f;
    q.se.value = e;
    q.sfv.textContent = f.toFixed(2);
    q.sev.textContent = e.toFixed(2);
  }

  const isSynthetic = () => state?.emg?.source === "synthetic";

  // ── actions ────────────────────────────────────────────────────────────────
  function select(i) {
    clearTimeout(advanceTimer);
    active = Math.max(0, Math.min(STEPS.length - 1, i));
    const s = STEPS[active];
    hand?.setPose(s.pose);
    renderStep();
  }

  async function record() {
    const s = STEPS[active];
    if (busy || !state?.emg) return;
    busy = true;
    clearTimeout(advanceTimer);
    results[s.id] = { state: "rec" };
    renderStep();
    const auto = isSynthetic() && q.auto.checked && s.id !== "save";
    if (auto) {
      showDrive(...s.drive);
      await postDrive(...s.drive);
      await sleep(700); // let the filters settle on the new level
    }
    recT0 = performance.now();
    const isSave = s.id === "save";
    let res;
    try {
      res = isSave ? await api("/api/emg/calibrate/finish") : await api(`/api/emg/calibrate/${s.phase}?seconds=${DUR}`);
    } catch (err) {
      res = { ok: false, detail: String(err.message || err) };
    }
    recT0 = 0;
    if (auto) {
      showDrive(0, 0);
      postDrive(0, 0);
    }
    if (res?.ok) {
      results[s.id] = { state: "ok", rms: res.rms, windows: res.windows };
      if (isSave) saved = res.profile || { name: state?.emg?.profile };
    } else {
      results[s.id] = { state: "fail", detail: res?.detail || "The server did not give a reason." };
    }
    busy = false;
    renderStep();
    if (res?.ok && active < STEPS.length - 1) {
      const from = active;
      advanceTimer = setTimeout(() => active === from && !busy && select(from + 1), 1500);
    }
  }

  // ── render ─────────────────────────────────────────────────────────────────
  function maxScale() {
    let m = 12;
    for (const r of Object.values(results)) if (r.rms) m = Math.max(m, ...r.rms);
    const live = state?.emg?.rms;
    if (live) m = Math.max(m, ...live);
    return m * 1.08;
  }

  function renderStep() {
    const s = STEPS[active];
    q.stepno.textContent = `Step ${active + 1} of ${STEPS.length}${s.optional ? " · optional" : ""}`;
    q.title.textContent = s.title;
    q.text.textContent = s.text;
    q.legend.style.opacity = s.id === "save" ? 0 : 1;
    q.stepper.querySelectorAll("button").forEach((b, i) => {
      const st = STEPS[i];
      const r = results[st.id];
      b.classList.toggle("on", i === active);
      b.dataset.state = r?.state || "idle";
      b.disabled = busy && i !== active;
      const sub = q.stepper.querySelector(`[data-s="${st.id}"]`);
      sub.textContent = r?.state === "ok" ? (st.id === "save" ? "profile saved" : `recorded · ${r.windows ?? "?"} windows`) : r?.state === "fail" ? "failed" : r?.state === "rec" ? (st.id === "save" ? "checking…" : "recording…") : STEP_NOTE[st.id];
      const mini = q.stepper.querySelector(`[data-m="${st.id}"]`);
      mini.innerHTML = r?.rms ? r.rms.map((v) => `<i style="height:${Math.max(6, Math.round((v / maxScale()) * 100))}%"></i>`).join("") : "";
    });
    q.go.textContent = busy ? (s.id === "save" ? "Checking…" : "Recording…") : s.id === "save" ? "Save profile" : results[s.id]?.state === "ok" ? `Re-record ${DUR} s` : `Record ${DUR} s`;
    q.go.disabled = busy || !state?.emg;
    q.hint.textContent = busy
      ? s.id === "save" ? "Validating the three phases and writing the profile." : "Hold the pose until the ring closes."
      : s.id === "save" ? "Needs rest, squeeze and open (brace optional) recorded in this visit, or a stored profile for a brace-only update."
        : "Get into the pose, then press Record. The server averages three seconds of EMG.";
    const r = results[s.id];
    q.result.hidden = !(r && (r.state === "fail" || (r.state === "ok" && s.id === "save")));
    if (r?.state === "fail") {
      q.result.className = "rec-result bad";
      q.result.textContent = r.detail;
    } else if (r?.state === "ok" && s.id === "save") {
      q.result.className = "rec-result ok";
      q.result.textContent = `Saved profile “${saved?.name || state?.emg?.profile || "default"}”${saved?.brace_max ? " with brace scale" : " without a brace scale"}.`;
    }
    q.ring.dataset.state = busy ? "rec" : r?.state === "ok" ? "ok" : r?.state === "fail" ? "fail" : "idle";
    if (!busy) {
      q.ringnum.textContent = r?.state === "ok" ? "done" : r?.state === "fail" ? "failed" : s.id === "save" ? "save" : `${DUR} s`;
      q.arc.style.strokeDashoffset = r?.state === "ok" ? 0 : 100;
      q.ring.style.setProperty("--c", s.color);
    }
    renderBars();
  }

  function renderBars() {
    const s = STEPS[active];
    const r = results[s.id];
    const m = maxScale();
    const live = state?.emg?.rms || [];
    for (let i = 0; i < 4; i++) {
      liveBars[i].style.width = `${clamp((live[i] ?? 0) / m) * 100}%`;
      const rec = r?.state === "ok" && r.rms ? r.rms[i] : null;
      resBars[i].style.width = rec == null ? "0%" : `${clamp(rec / m) * 100}%`;
      vals[i].innerHTML = rec == null ? `${f1(live[i])}` : `<em>${f1(rec)}</em> <small>${f1(live[i])}</small>`;
    }
    const res = r?.state === "ok" && r.rms;
    q.chbars.dataset.mode = res ? "result" : "live";
    q.chcap.textContent = res ? "recorded median (solid) · live (line) · raw rms" : "live rms · raw units · linear";
  }

  function renderStatus(e) {
    const el = q.status;
    if (!e) {
      el.className = "chip";
      el.innerHTML = "<i></i>EMG off";
      return;
    }
    const tone = e.error ? "bad" : e.calibrated ? (e.brace_calibrated ? "ok" : "warn") : "warn";
    el.className = `chip ${tone}`;
    el.innerHTML = `<i></i>${esc(e.error ? e.error : e.calibrated ? `profile ${e.profile}${e.brace_calibrated ? " · brace calibrated" : " · no brace scale"}` : "not calibrated")}`;
  }

  // ── trace + frame ──────────────────────────────────────────────────────────
  function drawTrace() {
    const c = q.trace;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const w = Math.round(c.clientWidth * dpr);
    const h = Math.round(c.clientHeight * dpr);
    if (!w || !h) return;
    if (c.width !== w || c.height !== h) {
      c.width = w;
      c.height = h;
    }
    trace2d.clearRect(0, 0, w, h);
    trace2d.strokeStyle = "rgba(160,190,230,.12)";
    trace2d.lineWidth = 1;
    for (const f of [0.25, 0.5, 0.75]) {
      trace2d.beginPath();
      trace2d.moveTo(0, h * f);
      trace2d.lineTo(w, h * f);
      trace2d.stroke();
    }
    const tr = state?.emg?.trace;
    if (!tr?.length) return;
    for (const [idx, color] of [[1, "#4fd8ff"], [2, "#ffc15a"]]) {
      trace2d.strokeStyle = color;
      trace2d.lineWidth = 2 * dpr;
      trace2d.lineJoin = "round";
      trace2d.beginPath();
      tr.forEach((p, i) => {
        const x = (i / Math.max(1, tr.length - 1)) * w;
        const y = h - clamp(p[idx], 0, 1.05) * (h - 6 * dpr) - 3 * dpr;
        i ? trace2d.lineTo(x, y) : trace2d.moveTo(x, y);
      });
      trace2d.stroke();
    }
  }

  function levels() {
    const out = [0, 0, 0, 0];
    for (let i = 0; i < 4; i++) {
      const h = hist[i];
      if (!h.length) continue;
      let lo = Infinity;
      let hi = -Infinity;
      for (const [, x] of h) {
        lo = Math.min(lo, x);
        hi = Math.max(hi, x);
      }
      const v = h[h.length - 1][1];
      out[i] = clamp((v - lo) / Math.max(hi - lo, 3 * lo, 1e-6));
    }
    return out;
  }

  function onFrame(dt, t) {
    if (!hand) return;
    const p = recT0 ? clamp((performance.now() - recT0) / (DUR * 1000), 0, 0.985) : null;
    const s = STEPS[active];
    if (busy && s.id !== "save") {
      hand.setScan(p ?? 0, s.color);
      const left = Math.max(0, DUR - (p ?? 0) * DUR);
      q.arc.style.strokeDashoffset = String(100 - (p ?? 0) * 100);
      q.ringnum.textContent = p == null ? "arming" : `${left.toFixed(1)}`;
    } else {
      hand.setScan(null);
    }
    hand.frame(dt, t);
  }

  async function ensureHand() {
    if (hand) return;
    let THREE = null;
    if (webgl) {
      try {
        THREE = await loadThree();
      } catch {
        THREE = null;
      }
    }
    if (hand) return;
    try {
      hand = THREE ? createHand3d(THREE, handHost) : createHandSvg(handHost);
    } catch (err) {
      console.error("hand3d", err);
      hand = createHandSvg(handHost);
    }
    hand.setPose(STEPS[active].pose);
    ro = new ResizeObserver(() => hand?.resize());
    ro.observe(handHost);
    hand.resize();
    if (state) pushLevels();
  }

  function pushLevels() {
    const e = state?.emg;
    if (!hand || !e) return;
    hand.setRoles?.(rolesOf(e));
    hand.setLevels(levels(), e.brace || 0);
  }

  // ── lifecycle ──────────────────────────────────────────────────────────────
  renderStep();
  return {
    async activate() {
      activeFlag = true;
      await ensureHand();
      if (!activeFlag || removeLoop) return;
      removeLoop = loop.add(onFrame);
    },
    deactivate() {
      activeFlag = false;
      removeLoop?.();
      removeLoop = null;
    },
    update(s) {
      state = s;
      const e = s.emg;
      q.off.hidden = !!e;
      q.syn.hidden = !(e && e.source === "synthetic");
      q.drive.hidden = !(e && e.source === "synthetic");
      q.assume.hidden = !e || !!e.flexor_channels;
      if (e) applyRoles(rolesOf(e));
      q.tracemeta.textContent = e ? `${e.source}` : "off";
      renderStatus(e);
      if (e) {
        const now = performance.now();
        e.rms.forEach((v, i) => {
          hist[i].push([now, v]);
          while (hist[i].length > 1 && now - hist[i][0][0] > 30000) hist[i].shift();
        });
        pushLevels();
        drawTrace();
        if (!busy) renderBars();
        q.go.disabled = busy;
      } else {
        q.go.disabled = true;
      }
    },
    dispose() {
      this.deactivate();
      clearTimeout(advanceTimer);
      clearTimeout(driveT);
      ro?.disconnect();
      hand?.dispose();
    },
  };
}

