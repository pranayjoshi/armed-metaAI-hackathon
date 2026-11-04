// Sounds (UI.md 2.4), generated with WebAudio, no assets: a soft tick when cards appear, a click on
// accept, a low tone on refuse. Nothing on a stop (the robot's "Sorry" is the sound), nothing while
// running. Off in far mode and under reduced motion; a mute toggle covers the rest.
let ctx = null;
let master = null;

// Creating an AudioContext before any user gesture only logs a warning and stays silent, so wait for
// one (a click on the card, or the desktop shell, whose autoplay policy is its own).
const mayStart = () => navigator.userActivation?.hasBeenActive || !!window.armedHost;

function ready() {
  if (!ctx) {
    if (!mayStart()) return false;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    try {
      ctx = new AC();
      master = ctx.createGain();
      master.gain.value = 0.9;
      master.connect(ctx.destination);
    } catch {
      return false;
    }
  }
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  return ctx.state === "running";
}

/** A short enveloped tone. */
function tone({ f = 440, f2 = null, dur = 0.08, gain = 0.05, type = "sine", delay = 0 }) {
  if (!ready()) return;
  const t0 = ctx.currentTime + delay;
  const o = ctx.createOscillator();
  const g = ctx.createGain();
  o.type = type;
  o.frequency.setValueAtTime(f, t0);
  if (f2) o.frequency.exponentialRampToValueAtTime(f2, t0 + dur);
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(gain, t0 + 0.006);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  o.connect(g).connect(master);
  o.start(t0);
  o.stop(t0 + dur + 0.02);
}

export const SOUNDS = {
  appear: () => tone({ f: 1320, f2: 1180, dur: 0.05, gain: 0.035 }),
  accept: () => {
    tone({ f: 760, dur: 0.035, gain: 0.05, type: "triangle" });
    tone({ f: 1140, dur: 0.06, gain: 0.04, type: "triangle", delay: 0.04 });
  },
  refuse: () => tone({ f: 190, f2: 140, dur: 0.26, gain: 0.07 }),
};

/** Resume the context on the first pointer or key gesture (autoplay policy). */
export function unlockOnGesture() {
  const go = () => {
    ready();
    removeEventListener("pointerdown", go, true);
  };
  addEventListener("pointerdown", go, true);
}
