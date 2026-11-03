// Topology of the Reflex pipeline: nodes, edges, and the plain-words role of each node.
// Every node and edge here corresponds to code in h2m/ (see the `src` field); nothing is decorative.
// Grid: col 0..5 left to right, row 0 at the bottom (inputs) .. row 7 at the top (the arm).

export const ROWS = 8;
export const COLS = 6;

/** hue keys map to the shared design tokens (see css/tokens.css); `spine` is the neutral control colour. */
export const NODES = [
  // ── speech & text lane ────────────────────────────────────────────────────────────
  { id: "mic", title: "Microphone", col: 0, row: 0, hue: "spine", lane: "speech", icon: "mic", src: "sensors/speech.py",
    role: "Picks up your voice and cuts it into utterances with a voice-activity detector. If the runtime was started with speech off, this node is dark and typed commands still work." },
  { id: "typed", title: "Typed / UI", col: 1, row: 1, hue: "spine", lane: "speech", icon: "keys", src: "server.py /api/say",
    role: "Text typed in the UI, the CLI or the API. It skips speech recognition and goes straight to the parser." },
  { id: "cam", title: "Camera · vision", col: 2, row: 0, hue: "spine", lane: "speech", icon: "eye", src: "sensors/vision.py",
    role: "Detects the objects on the table and where your hand points, so a request can name a real target. In virtual mode the catalog objects are placed on the taught slots instead." },
  { id: "asr", title: "ASR", col: 0, row: 1, hue: "spine", lane: "speech", icon: "wave", src: "sensors/speech.py",
    role: "Turns each utterance into text on this machine (Parakeet, or Whisper if Parakeet is unavailable). The delay shown is the measured transcription time." },
  { id: "gram", title: "Grammar", col: 0, row: 2, hue: "spine", lane: "speech", icon: "parse", src: "compiler/grammar.py",
    role: "A fixed grammar turns the words into a verb and a target. It is instant and works offline. Whatever it cannot decide is passed to Muse Spark, if Muse is enabled." },
  { id: "stop", title: "Stop words", col: 1, row: 2, hue: "brace", lane: "speech", icon: "bolt", src: "app.on_utterance",
    role: "\"Stop\" is recognised locally, before anything else. It never waits for a model and never leaves the machine: it goes straight to the freeze latch." },
  { id: "muse", title: "Muse Spark", col: 1, row: 3, hue: "muse", lane: "speech", icon: "spark", src: "compiler/llm.py",
    role: "When the grammar is unsure (unknown verb, or no clear target), Muse Spark reads the request and the scene and answers in the same verb-and-object form. If Muse is off the system is grammar-only." },

  // ── control spine ─────────────────────────────────────────────────────────────────
  { id: "contract", title: "Step contract", col: 2, row: 4, hue: "spine", lane: "spine", icon: "doc", src: "contract.py",
    role: "Every input becomes one step contract: what to do (verb), to what (object and where it is), grip, speed, how sure the system is, and the route chosen by saved rules." },
  { id: "gate", title: "Safety gate", col: 2, row: 5, hue: "spine", lane: "spine", icon: "shield", src: "safety.py",
    role: "A deterministic gate with no model inside. It approves, asks (waits for a squeeze, a yes or a click), holds, or rejects. Saved rules can only make it stricter." },
  { id: "exec", title: "Executor", col: 2, row: 6, hue: "spine", lane: "spine", icon: "cog", src: "app._execute_plan",
    role: "Turns an approved contract into a taught plan (reach, grasp, carry, release) with the chosen grip strength and route, then starts the arm." },
  { id: "arm", title: "SO-101 arm", col: 2, row: 7, span: 3, hue: "spine", lane: "spine", icon: "arm", wide: true, src: "robot/arm.py",
    role: "The follower arm (or the virtual arm). It moves only a plan the gate approved, and freezes in place on stop, open hand or brace." },

  // ── body lane ─────────────────────────────────────────────────────────────────────
  { id: "gang", title: "Ganglion EMG", col: 4, row: 0, hue: "spine", lane: "body", icon: "pulse", spark: "emg", src: "sensors/emg.py",
    role: "Forearm electrodes sampled at 200 Hz: flexor channels for squeezing, extensor channels for opening. If it says SYNTHETIC, the signal is generated for testing, not read from a person." },
  { id: "squeeze", title: "Squeeze", col: 3, row: 1, hue: "grip", lane: "body", icon: "fist", spark: "effort", src: "emg._detect",
    role: "Flexors held above threshold: a deliberate squeeze. It confirms a waiting request and sets how hard the gripper closes, and can approve a proposed rule." },
  { id: "open", title: "Open hand", col: 4, row: 1, hue: "open", lane: "body", icon: "palm", spark: "extensor", src: "emg._detect",
    role: "Extensors burst above threshold: an open hand. It freezes the arm." },
  { id: "brace", title: "Brace detector", col: 5, row: 1, hue: "brace", lane: "body", icon: "alert", spark: "brace", src: "emg._detect_brace",
    role: "A fast detector for a brace: flexors and extensors fire together, reached suddenly from calm. It freezes the arm before any model is asked." },
  { id: "freeze", title: "Freeze latch", col: 4, row: 3, hue: "brace", lane: "body", icon: "stop", src: "arm.stop",
    role: "Brace, open hand, the word \"stop\" and the STOP button all land here and freeze the arm in place. No model and no network call sit on this path." },

  // ── learning lane ─────────────────────────────────────────────────────────────────
  { id: "incident", title: "Incident clip", col: 5, row: 3, hue: "muse", lane: "learn", icon: "film", src: "learning.py / incident.py",
    role: "After a freeze while the arm was moving, the last seconds of frames, joint positions and EMG are saved as a clip." },
  { id: "wonder", title: "Muse Wonder", col: 5, row: 4, hue: "muse", lane: "learn", icon: "spark", src: "incident.explain",
    role: "Looks at the clip and explains in plain words what probably went wrong, and proposes one rule. Without a Muse key a fallback only asks what bothered you." },
  { id: "proposal", title: "Rule proposal", col: 5, row: 5, hue: "muse", lane: "learn", icon: "doc", src: "learning.RuleProposal",
    role: "A candidate rule, held until you answer. Rules can only restrict: take another route, slow down, lift higher, grip gentler." },
  { id: "approval", title: "Approval", col: 5, row: 6, hue: "ok", lane: "learn", icon: "check", src: "learning.approve",
    role: "A squeeze, a spoken yes, or a click saves the rule. Silence lets it expire. Nothing is saved without a person." },
  { id: "memory", title: "Rule memory", col: 3, row: 5, hue: "muse", lane: "learn", icon: "db", src: "rules.py",
    role: "Saved rules. On later carries they remove routes or lower speed and grip, and they can never relax a safety check." },
];

/**
 * Edges. Default: leaves the top of `from`, enters the bottom of `to`.
 * fromSide / toSide: top|bottom|left|right. fromAt / toAt: absolute column position of the port (for the wide arm).
 * via: waypoints as [col, row] floats. hue: edge colour override (default: the source node's hue).
 */
export const EDGES = [
  { from: "mic", to: "asr" },
  { from: "asr", to: "gram" },
  { from: "typed", to: "gram", toAt: 0.3 },
  { from: "gram", to: "stop", fromSide: "right", toSide: "left", hue: "brace" },
  { from: "gram", to: "muse", fromAt: 0.2, toSide: "left", hue: "muse" },
  { from: "muse", to: "contract", toSide: "left", hue: "muse" },
  { from: "gram", to: "contract", fromAt: -0.2, toSide: "left", via: [[-0.2, 3.7], [0.55, 4]], label: "grammar path", labelAt: 0.62 },
  { from: "cam", to: "contract" },
  { from: "contract", to: "gate" },
  { from: "gate", to: "exec" },
  { from: "exec", to: "arm", toAt: 2 },
  { from: "memory", to: "gate", fromSide: "left", toSide: "right", hue: "muse", loop: true },

  { from: "gang", to: "squeeze", hue: "grip" },
  { from: "gang", to: "open", hue: "open" },
  { from: "gang", to: "brace", hue: "brace" },
  { from: "squeeze", to: "gate", toSide: "right", via: [[2.62, 3.1], [2.52, 4.55]], hue: "grip", label: "confirm + grip", labelAt: 0.4 },
  { from: "open", to: "freeze", toAt: 3.85, hue: "open" },
  { from: "brace", to: "freeze", toAt: 4.2, hue: "brace" },
  { from: "stop", to: "freeze", fromSide: "right", toSide: "left", via: [[3.4, 2.45]], hue: "brace" },
  { from: "freeze", to: "arm", toAt: 4, hue: "brace", bypass: true, label: "no model on the freeze path", labelAt: 0.3 },

  { from: "freeze", to: "incident", fromSide: "right", toSide: "left", hue: "muse" },
  { from: "incident", to: "wonder" },
  { from: "wonder", to: "proposal" },
  { from: "proposal", to: "approval" },
  { from: "approval", to: "memory", fromSide: "left", toSide: "right", hue: "ok", loop: true, label: "saved rules loop back", labelAt: 0.3 },
].map((e) => ({ ...e, id: `${e.from}-${e.to}` }));

export const NODE_BY_ID = Object.fromEntries(NODES.map((n) => [n.id, n]));
export const EDGE_BY_ID = Object.fromEntries(EDGES.map((e) => [e.id, e]));

/** Lane labels drawn into the world, positioned in grid units. */
export const LANE_LABELS = [
  { text: "SPEECH & TEXT", col: -0.42, row: -0.62, hue: "spine", anchor: "left" },
  { text: "BODY · EMG", col: 5.42, row: -0.62, hue: "grip", anchor: "right" },
  { text: "LEARNING LOOP", col: 5.42, row: 7, hue: "muse", anchor: "right" },
];

export const LEGEND = [
  { hue: "spine", label: "Speech & control" },
  { hue: "grip", label: "Squeeze · confirm" },
  { hue: "open", label: "Open hand" },
  { hue: "brace", label: "Brace · stop" },
  { hue: "muse", label: "Learning · Muse" },
  { hue: "ok", label: "Approved" },
];

/** Ancestors and descendants of a node, for the "trace run" highlight. */
export function reach(id) {
  const fwd = new Set([id]);
  const back = new Set([id]);
  const walk = (set, next) => {
    const q = [id];
    while (q.length) {
      const n = q.pop();
      for (const e of EDGES) {
        const [a, b] = next(e);
        if (a === n && !set.has(b)) {
          set.add(b);
          q.push(b);
        }
      }
    }
  };
  walk(fwd, (e) => [e.from, e.to]);
  walk(back, (e) => [e.to, e.from]);
  const edges = new Set(EDGES.filter((e) => (fwd.has(e.from) && fwd.has(e.to)) || (back.has(e.from) && back.has(e.to))).map((e) => e.id));
  return { nodes: new Set([...fwd, ...back]), edges };
}
