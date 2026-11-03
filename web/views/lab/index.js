// Lab: calibrate, test, record. One section, three stages behind a step rail (no page scroll).
import { createCalibrate } from "./calibrate.js";
import { createDevices } from "./devices.js";
import { createSessions } from "./sessions.js";

const STAGES = [
  { id: "calibrate", n: 1, label: "Calibrate", create: createCalibrate },
  { id: "devices", n: 2, label: "Devices", create: createDevices },
  { id: "sessions", n: 3, label: "Sessions", create: createSessions },
];

const clock = (s) => (s == null ? "00:00" : `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`);

let C = null; // ctx
let el = null;
let stages = null; // id -> { pane, mod }
let current = "calibrate";
let visible = false;
let latest = null;
let busy = false;
const subs = {};

export default {
  async mount(root, ctx) {
    C = ctx;
    ctx.css(import.meta.url, "style.css");
    root.innerHTML = `
      <div class="lab">
        <nav class="lab-rail" aria-label="Lab stages">
          <div class="rail-head"><span class="kicker">Lab</span><h2>Calibrate, test, record</h2></div>
          <div class="rail-steps" role="tablist" aria-orientation="vertical">
            <i class="rail-marker" aria-hidden="true"></i>
            ${STAGES.map(
              (s) => `<button class="rail-step" role="tab" data-id="${s.id}" aria-selected="false">
                <span class="n num">${s.n}</span>
                <span class="t"><b>${s.label}</b><small data-sub="${s.id}">—</small></span>
                <i class="flag" data-flag="${s.id}" hidden></i>
              </button>`,
            ).join("")}
          </div>
          <p class="rail-foot">Stop is always on the top bar. Space also stops the arm.</p>
        </nav>
        <div class="lab-stage">
          ${STAGES.map((s) => `<section class="lab-pane" data-pane="${s.id}" role="tabpanel" hidden></section>`).join("")}
        </div>
      </div>`;
    el = root;
    stages = {};
    for (const s of STAGES) {
      const pane = root.querySelector(`[data-pane="${s.id}"]`);
      stages[s.id] = { pane, mod: s.create(pane, ctx) };
      subs[s.id] = root.querySelector(`[data-sub="${s.id}"]`);
    }
    root.querySelector(".rail-steps").addEventListener("click", (e) => {
      const b = e.target.closest(".rail-step");
      if (b) show(b.dataset.id);
    });
    root.querySelector(".rail-steps").addEventListener("keydown", (e) => {
      const i = STAGES.findIndex((s) => s.id === current);
      if (e.key === "ArrowDown" || e.key === "ArrowRight") show(STAGES[(i + 1) % STAGES.length].id);
      else if (e.key === "ArrowUp" || e.key === "ArrowLeft") show(STAGES[(i + STAGES.length - 1) % STAGES.length].id);
      else return;
      e.preventDefault();
      root.querySelector(`.rail-step[data-id="${current}"]`).focus();
    });
    const want = new URLSearchParams(location.search).get("lab"); // deep link: /?lab=devices#/lab
    if (STAGES.some((s) => s.id === want)) current = want;
    place(current, true);
    stages[current].pane.hidden = false;
    new ResizeObserver(() => place(current, true)).observe(root);
  },

  enter() {
    visible = true;
    place(current, true);
    stages[current].mod.activate?.();
    rise(stages[current].pane);
  },

  leave() {
    visible = false;
    for (const s of STAGES) stages[s.id].mod.deactivate?.();
  },

  update(state) {
    latest = state;
    railSubs(state);
    if (visible) stages[current].mod.update?.(state);
  },
};

function place(id, instant) {
  const rail = el.querySelector(".rail-steps");
  const btn = rail.querySelector(`[data-id="${id}"]`);
  const mk = rail.querySelector(".rail-marker");
  rail.querySelectorAll(".rail-step").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.id === id)));
  if (!btn.offsetWidth) return;
  const horizontal = getComputedStyle(rail).flexDirection === "row";
  mk.style.transition = instant ? "none" : "";
  mk.style.width = `${btn.offsetWidth}px`;
  mk.style.height = `${btn.offsetHeight}px`;
  mk.style.transform = `translate(${horizontal ? btn.offsetLeft : 0}px, ${horizontal ? 0 : btn.offsetTop}px)`;
}

function rise(pane) {
  if (C.reduced) return;
  pane.querySelectorAll(".panel").forEach((p, i) => {
    p.style.setProperty("--i", i);
    p.classList.remove("rise");
    void p.offsetWidth;
    p.classList.add("rise");
  });
}

async function show(id) {
  if (id === current || busy) return;
  busy = true;
  const from = stages[current];
  const to = stages[id];
  const dir = STAGES.findIndex((s) => s.id === id) > STAGES.findIndex((s) => s.id === current) ? 1 : -1;
  from.mod.deactivate?.();
  current = id;
  place(id);
  to.pane.hidden = false;
  if (visible) {
    to.mod.activate?.();
    if (latest) to.mod.update?.(latest);
    rise(to.pane);
  }
  const vertical = matchMedia("(min-width: 761px)").matches;
  const off = (n) => (vertical ? `translateY(${n * dir}px)` : `translateX(${n * dir}px)`);
  if (C.reduced || !to.pane.animate) {
    from.pane.hidden = true;
  } else {
    from.pane.animate(
      [{ opacity: 1, transform: "none", filter: "blur(0)" }, { opacity: 0, transform: `${off(-34)} scale(.985)`, filter: "blur(8px)" }],
      { duration: 320, easing: "cubic-bezier(.7,0,.2,1)", fill: "forwards" },
    ).onfinish = () => {
      from.pane.hidden = true;
      from.pane.getAnimations().forEach((a) => a.cancel());
    };
    to.pane.animate(
      [{ opacity: 0, transform: `${off(40)} scale(.985)`, filter: "blur(10px)" }, { opacity: 1, transform: "none", filter: "blur(0)" }],
      { duration: 560, easing: "cubic-bezier(.16,1,.3,1)", delay: 140, fill: "backwards" },
    );
  }
  setTimeout(() => (busy = false), 360);
}

function railSubs(s) {
  const e = s.emg;
  subs.calibrate.textContent = !e ? "EMG off" : e.calibrated ? `${e.profile}${e.brace_calibrated ? " · brace set" : " · no brace scale"}` : "not calibrated";
  subs.calibrate.dataset.tone = !e ? "" : e.error ? "bad" : e.calibrated ? "ok" : "warn";
  const errs = [s.arm.state === "fault" ? s.arm.reason : "", e?.error, ...(s.vision.errors || []), s.speech?.error, s.llm.error].filter(Boolean).length;
  subs.devices.textContent = errs ? `${errs} error${errs > 1 ? "s" : ""} reported` : "no errors reported";
  subs.devices.dataset.tone = errs ? "bad" : "";
  const rec = !!s.session.session_id;
  subs.sessions.textContent = rec ? `REC ${clock(s.session.elapsed_s)} · ${s.session.trials_done} trials` : "not recording";
  subs.sessions.dataset.tone = rec ? "bad" : "";
  el.querySelector('[data-flag="sessions"]').hidden = !rec;
  el.querySelector('[data-flag="devices"]').hidden = !errs;
}
