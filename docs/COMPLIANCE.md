# Meta-only runtime: what has to change, and by when

Written November 3, 2026 at 12:40 CST. Source: the organizer guidance relayed from the Cerebral Valley Discord, that every AI model the submitted project invokes at runtime must be a Meta model, with replies saying this covers a separately trained RL policy and even a small network trained from scratch during the event; that conventional software (drivers, deterministic controllers, waypoint executors, simulators) is fine; that fine-tuning an eligible Meta model is supported; and that the video should show live Meta calls. **Verify the exact wording in the Discord yourself before the submission is written; this document assumes the relay is accurate.**

---

## 1. What the rule does to the plan, in one paragraph

Every learned motion policy (ACT, SmolVLA, π0.5, GR00T) is out of the submitted build. That is the smaller change, because the film was already going to run on taught poses and geometry. The larger change is that three models already in the runtime are not Meta models, and the video's own chips name two of them. They have to be replaced before the takes, which moves this work above every new beat in `DEMO_STORY.md`.

---

## 2. The audit: everything that runs when `h2m run` runs

| Component | What it is | Meta? | Verdict |
|---|---|---|---|
| Object detection: was a local open-vocabulary detector, every frame | learned vision model | **No** | **Replaced** (Nov 3) by SAM 3.1 phrase scans, `h2m/sensors/sam.py` and `h2m/sensors/vision.py` |
| Hand landmarks and pointing: was a local hand-landmark model | learned vision model | **No** | **Replaced** (Nov 3) by the SAM 3.1 `hand` mask: outline points for the spot check, major axis for coarse pointing |
| Head direction: was a local face-mesh model behind `h2m/head.py` | learned vision model | **No** | **Replaced** (Nov 3) by the SAM 3.1 `face` mask's horizontal position (left / centre / right); still a tie-breaker only |
| Speech to text: Muse Voice Transcribe 1.0 (`h2m/sensors/voice.py`, realtime WebSocket, diarized, keyword-biased) | Meta Model API | Yes | **replaced** (Nov 3): the local ASR models and their extras are gone from the runtime |
| Card ranking and the free-form interpreter: Muse Spark 1.3 | Meta Model API | Yes | keep |
| Incident investigation: Muse Wonder 1.0 | Meta Model API | Yes | keep |
| Masks for the highlight: SAM 3.1 | Meta Model API | Yes | keep, and widen its job |
| EMG decoder: notch, band-pass, RMS, thresholds, onset rule (`h2m/sensors/emg.py`) | signal processing, no learned weights | conventional | keep; say so in the README |
| Deterministic ranker, safety gate, rule checker, route chooser, scene diff | code | conventional | keep |
| Executor, taught poses, LeRobot driver | conventional robotics software | conventional | keep |
| Scene-change detection (pixel difference, world tracker timers) | conventional | conventional | keep |
| Three.js, the web UI, Electron | rendering | not AI | keep |
| ACT / SmolVLA / π0.5 checkpoints and `learned_grasp` wrapper | learned policies | **No** | **Not in the submitted build**; code stays off the main branch; the film never shows them |

The stop story survives untouched: the brace, the open hand and the physical e-stop reach the servo with no model of any kind in the path. The *voice* "stop" moves to Voice Transcribe and becomes the slow stop, which it already was in the narration.

---

## 3. The replacements

### 3.1 Detection: SAM 3.1 instead of YOLOE

SAM 3.1 takes a short noun phrase and returns every match as a box and a mask, which is exactly what YOLOE did locally. The difference is latency and cost, since it is an API call, so it is not run every frame:

- **When:** on a scene change (pixel difference over the table region, conventional), on an event (a sound, a card, a stop), and on a slow cadence (every 10 s) otherwise. Objects on a desk rarely move; the world tracker already keeps the last known positions.
- **What:** one call with the catalog phrases (`wooden block`, `paper cup`, `phone`, `pill cup`, `jar`, `laptop`), plus `hand` and `face` when the person is present. Boxes go through the existing table homography into slots as before.
- **Fallback:** if the call fails or is slow, the tracker keeps the last positions and the card says *scene stale*; nothing moves toward a stale target (the gate's `target_visible` already fails closed).

**Built (Nov 3):** `h2m/sensors/sam.py` (client, one request per phrase in parallel, the one_bit mask decoder, the scan scheduler) and `h2m/sensors/vision.py` on it; `scripts/time_sam.py` measures it. Measured on a 1920x1080 frame uploaded at 960 px, five phrases in parallel: **p50 1278 ms, p95 2072 ms** per scan, which is the "scan on change, events and the 10 s cadence" case above. The snapshot carries `scene` = {masks, scan_age_s, scan_ms, stale, limit_s} and `calls` = [{model: "sam-3.1", for: "scan", ms}]; a stale scene (older than `vision.stale_s`) flags every object and the gate's `target_visible` fails closed on it.

### 3.2 Hands, hand-out and head direction: SAM 3.1 masks instead of MediaPipe

- *Hand held out with an object*: the `hand` mask inside the handover zone with an object mask adjacent, on the event cadence. This is what the world tracker needs for *Take this* and the jar.
- *Pointing*: dropped for the film; it was never in a beat.
- *Head direction*: the `face` mask's horizontal position in the overhead or side frame gives left / centre / right coarsely, which is all `h2m/head.py` ever produced. If it is not ready, drop it: it is a tie-breaker in the ranker, never a trigger.
- *Hands under the desk*: not in the cut.

### 3.3 Speech: Muse Voice Transcribe instead of Parakeet

The realtime WebSocket with native endpointing and diarization. It was already planned for the *X asked* beat; now it carries all speech. The local stop-word spotter goes with Parakeet; the brace and the e-stop are the fast stops and were always the ones the film shows.

### 3.4 Learned motion: none

Taught poses, and taught poses plus one vision-derived angle from a SAM 3.1 box. No checkpoint is loaded by the submitted build. The only Meta-compliant path to a learned controller would be fine-tuning an eligible Meta robotics checkpoint (V-JEPA 2-AC, or whatever FAIR has released since), whose eligibility is unverified and whose setup does not fit before the deadline. That is the post-hackathon question, not this week's.

---

## 4. Cost and time

| Work | Owner | Time |
|---|---|---|
| SAM 3.1 detection on change, event and cadence; catalog phrases; tracker wiring; `scene stale` state | `muse-integrator`, `runtime-builder` | 5 h agent |
| Hand-out and face position from masks; remove MediaPipe | `muse-integrator` | 2 h agent |
| Muse Voice Transcribe client, realtime, diarized; remove Parakeet and Whisper; `fetch-models` updated | `muse-integrator` | 4 h agent (already in the plan for diarization) |
| Chips and README: `sam-3.1 · objects`, `sam-3.1 · hands`, `muse-voice-transcribe-1.0 · speech`; the YOLOE and MediaPipe chips removed from `DEMO_STORY.md` | docs, `ui-builder` | 1 h |
| Remove `ultralytics`, `mediapipe`, `parakeet-mlx`, `mlx-whisper` from the default extras; learned-policy code to a branch | `runtime-builder` | 1 h |
| A live run with the camera, the mic and the API, end to end | user | 1.5 h at the arm |

About 13 agent-hours, which two forks clear this afternoon, and ninety minutes of the person's time to prove it live. It goes **first**, before the jar, the desk check or the investigation loop, because every take depends on it.

SAM 3.1 at the cadence above is a few hundred calls per filming day; check the price page, but it is small next to the Vultr credit, which is now unneeded for the submission.

---

## 5. What the submission says

- README, one section, *What runs where*: the table in §2 with the Meta model ids, the conventional components named as such, and the EMG decoder described as signal processing with no learned weights.
- The video's chips name only Meta models and Armed's own components. No YOLOE, no MediaPipe, no Parakeet appear anywhere in the cut.
- The learned-policy work is not mentioned in the submission. It is a branch, and a plan for afterwards.
- If a ruling is wanted on anything, ask the organizers in the Discord before submitting, with the exact component named. The relay's example of another entrant running a third-party policy is not a ruling.

---

## 6. How far each replacement goes, and what it does to the product

| Was | Becomes | Replaceable | What changes in the running product | What the film shows differently |
|---|---|---|---|---|
| YOLOE, every frame, local | SAM 3.1 by phrase, on scene change, events and a 10 s cadence | **Fully** for named objects; masks are better than YOLOE's | Detection goes from 10 Hz to about 0.1–1 Hz and needs the network. On a desk nothing moves between calls, so cards, slots and the out-of-place timer behave the same; the overlay shows *seen 4 s ago* instead of live boxes. Offline, the scene is frozen at last-seen and the gate acts only on it. Unknowns to test in the first hour: SAM 3.1 call latency and price | Masks instead of boxes; the chip reads `sam-3.1 · objects`; the offline caption gains four words: *works from what it last saw* |
| MediaPipe hands and face mesh | SAM 3.1 `hand` and `face` masks on the same cadence | **Mostly.** Hand-out-with-object: yes. Face position left / centre / right: yes. Fingertip pointing and fine head yaw: no | Pointing is gone (it was in no beat). Head direction becomes coarse, which is all the ranker ever used | Nothing; the chip reads `sam-3.1 · hands` |
| Parakeet / Whisper, local | Muse Voice Transcribe, realtime WebSocket | **Fully**, and it adds diarization and keyword biasing | Speech needs the network; the voice "stop" gains the round trip and is the slow stop (the brace and the e-stop are the fast ones, unchanged). Offline: voice off, cards and twitch and brace unchanged. Price from the docs: about $3 per 1,000 minutes | Nothing; the *X asked* beat gets the real speaker label it was promised |
| ACT / π0.5 learned grasp | Nothing | **Not at all.** Meta offers no motor policy on the API; V-JEPA 2-AC is a research path, not a week's work | Grasps are taught, or taught plus a SAM-derived pan angle along the arc. "Pick from anywhere on the desk" shrinks to "anywhere on the taught arc" | The bonus clip is gone; no beat in the cut used it |

Net effect on scoring: Meta tooling goes up, since SAM and Voice Transcribe now do visible work every minute instead of once per incident; technical depth is unchanged; the demo is the same demo with one new dependency, the network, which the offline beat already addresses.

The one test that decides the cadence: time ten SAM 3.1 calls on an overhead frame with six phrases. Under 1.5 s, run on change and events as above; over 3 s, run on events only and let the tracker carry the rest.

---

## 7. Meta-measured grasp parameters: jaw opening, wrist roll, grip profile

Today every pick opens the jaws to a fixed 30% and closes to a catalog value. Three parameters of the taught pick can instead come from Meta models, each through a bounded, reviewable mapping, so the rule "no model output becomes a joint value" holds in the way it always has: the model measures or classifies; code clamps and chooses.

| Parameter | Meta model | Conventional step | Bound |
|---|---|---|---|
| **Jaw opening before the descent** | SAM 3.1 mask of the target from the overhead frame at the `above` pose (or the wrist frame, whose scale at `above` is fixed) → object width perpendicular to the jaw axis, in mm, through the homography | `open_pct = clamp(mm_to_pct(width + margin), open_min, open_max)` with a one-time jaw calibration (percent → mm at three openings) | never below the widest safe opening for the slot; a width the mask cannot give falls back to 30% |
| **Wrist roll at the grasp** | the same mask's principal axis (PCA on the mask, conventional) | roll = axis angle, clamped to ±25° around the taught `grasp` roll | the envelope is a config value; outside it, the taught roll |
| **Grip profile on closing** | Muse Spark classifies the target (`rigid`, `fragile`, `liquid`, `unknown`) from the frame, cached per object | selects the catalog profile (`max_grip`, close margin, torque cap); the EMG effort still scales inside it; closing stops on gripper-load readback | `unknown` → the softest profile; the model can only lower the cap, never raise it |

What it buys: a pick that opens the right amount for a jar lid, a phone or a pill cup without a catalog entry per object, and a *take this* that fits the jaws to whatever is held out. On screen: a chip `sam-3.1 · width 24 mm · open 48%` at the `above` pose, one more place a Meta call is visibly doing work.

**Built (Nov 3), off by default:** `h2m/grasp.py` has the three mappings as pure functions: `JawCalibration` (three-point percent to mm), `width_from_mask`, `roll_from_mask` (PCA on the evenly resampled mask outline), `roll_for_axis` and `grasp_params`. The opening is clamped to `open_min_pct..open_max_pct`. The roll stays within `roll_envelope_deg` of the taught roll, with a hard ceiling of 45° in code. The close cap is `min(catalog, profile)`, with `unknown` forced to the softest cap. Each mapping falls back to the taught opening and roll. `plan_pick_place` takes optional `grasp_open_pct` / `grasp_roll_deg` (keyword-only). It clamps the roll again, and the tray release opens to at least the grasp opening. Tests: `tests/test_grasp.py`. Wired, still off (Nov 3): with `grasp.sam_width` true a pick runs in two stages like the handover (`H2M._continue_grasp`). The approach goes to `slot.above` only. Then, off the control thread and outside the arm lock, one `MuseClient.segment_mask` call (its own `sam_timeout_s`) and a Muse Spark profile classification (cached per object, `unknown` takes the softest cap) feed `grasp_params`, and the pick is rebuilt and run only if the request epoch, `arm_count` and the gate decision are unchanged. Every failure runs the taught plan. The mask is decoded to a polygon when the reply carries a PNG mask record (token layout unverified), otherwise the roll stays taught; the roll also needs `roll_sign_verified`. `h2m arm jaw` records the three jaw points. The chip is `snapshot()["grasp"]["chip"]`. Needs the safety-reviewer's approval before either switch goes on. `grasp.sam_width: false` and `jaw_calibration.calibrated: false` ship; the steps are in `docs/hardware/calibrate.md`.

Cost: jaw calibration (20 min at the arm: measure the opening at 30%, 60%, 100%); SAM call at `above` (one call, 1–2 s, before the descent); the three mappings with tests (about 3 h agent); a reviewed relaxation because a vision measurement now sets a gripper value and a wrist angle. Fits before the jar beat if it starts this afternoon; otherwise after the deadline, with the fixed 30% in the film.
