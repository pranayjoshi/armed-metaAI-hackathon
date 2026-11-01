# H2M build plan: Muse Embodied + Flinch

Prepared October 27, 2026. **Direction replaced October 31, 2026:** the hackathon entry is now a physical Muse assistant that learns from involuntary body reactions. Submissions for the Meta Global AI Developer Hackathon are due **Nov 4, 9:00 PM ET**. The earlier voice + point + squeeze scope is now a supporting layer. Nothing about flinch detection has been measured yet; every target below is a target.

---

## 0. The entry in one page

**Pitch:** a desk assistant powered by Muse that has a body, sees your desk and reads yours. "You don't have to tell it. Your body already did."

**The loop nobody we found has shown:**
1. The SO-101 carries something past something you care about (water past a laptop).
2. You flinch. Forearm flexors and extensors fire together. **The arm freezes before you say anything.**
3. Muse Wonder reviews the seconds before the flinch and explains what it thinks went wrong, out loud.
4. You approve with a squeeze, and it becomes a saved rule.
5. Next time, the arm takes another route unprompted.

| Decision | Choice | Why |
|---|---|---|
| Core novelty | Involuntary muscle bracing as a live stop and a learning signal | Prior implicit-feedback work uses [facial reactions](https://arxiv.org/abs/2009.13649) and [EEG error signals](https://arxiv.org/html/2507.13171v1). Our searches found none using muscle bracing, and none turning a reaction into a saved rule through an LLM. Say "we couldn't find prior work", never "first ever". |
| Appeal layer | Embodied assistant: looks, gestures, talks, fetches | Voice + LLM arm agents [already exist](https://github.com/Tibe31/so101-mcp), so this is not the novelty. It makes the video watchable. |
| Repo | `pranayjoshi/h2m`; extend the existing runtime | Safety gate, EMG, executor, recorder and UI exist |
| Safety rule | Muse selects from taught options and **learned rules can only restrict** | A bad inference makes the robot more cautious, never less |
| EEG, VLA fine-tuning, show-it-once | Out of the hackathon | Unconvincing or too slow; see §7 |

**The bet that can fail:** a real flinch may not register cleanly on forearm electrodes. Gate G1 (§4.2) tests this first. If it fails, the entry falls back to a deliberate "tense up" signal and drops the word "involuntary".

**Hackathon fit.** Reported weights are impact 30%, creative use of Meta models 25%, demo 25%, technical depth 20% ([secondary source](https://thegenaimagazine.com/meta-global-ai-developer-hackathon-ideas-prizes)). The [event page](https://cerebralvalley.ai/e/meta-ai-developer-hackathon) describes indie judging as creativity, ambition and whether it works, and startup judging as execution and whether it holds up in production. **Read the official rules** for weights, track, and whether code ported from `human2machine` is acceptable: secondary sources say only work from Oct 26–Nov 4 counts, and `h2m` ports earlier code, so disclose it.

---

## 1. Starting point (October 31)

| Piece | State |
|---|---|
| `h2m` runtime: contract, safety gate, executor, EMG squeeze/open-hand, recorder, UI, `h2m bench` | Built Oct 27; exercised with virtual arm and synthetic EMG only |
| SO-101 bring-up | Scripts and docs added Oct 31 in `scripts/` and `docs/hardware/` (ports, motor setup, calibration, cameras, teleop, recording) |
| Runtime on the real arm | Not done. `configs/poses.yaml` has zero taught poses; the `LeRobotArm` torque-limit and load calls are unverified |
| Live camera, mic, Ganglion in the runtime | Not done |
| Muse API | Never called live. `MODEL_API_KEY` status unknown |
| YOLOE | Embeddings cached for placeholder objects only; new objects need CLIP once |

**Hardware use**

| Device | Role | Limit |
|---|---|---|
| OpenBCI Ganglion | 4 channels at 200 Hz: squeeze, open hand, **brace** | No hand pose; re-calibrate each session |
| SO-101 follower | The body: manipulation and gestures | Position servos; slow on purpose; no contact sensing except load readback |
| SO-101 leader | Teach routes and gestures by hand; demos after the hackathon | |
| Overhead camera | Scene, objects, incident replay | Fixed mount, or slot positions break |
| Wrist camera | The robot's "eye": looking at you and at objects | |
| Crown EEG | Not used in the demo | No decoding claim |
| Arduino Tiny ML kit | Not used | No EMG sensor |

---

## 2. Architecture

```text
 mic ─► Parakeet ─► local stop words ───────────────────────────► STOP (never leaves the machine)
          └► Muse Spark (tools) ─┐
 cameras ─► YOLOE / MediaPipe ───┼─► step contract ─► safety gate ─► executor ─► SO-101
 rule memory ───────────────────►┤   + route choice    (+ rules)        ▲
 Ganglion ─► squeeze (confirm + grip)                                   │
          ─► open hand ─────────────────────────────► freeze ───────────┤
          ─► BRACE ─────────────────────────────────► freeze ───────────┘
                │
                ▼
      incident clip: last 5 s of frames, joint path, held object, EMG
                │
                ▼
      Muse Wonder + SAM 3.1 ─► explanation + candidate rule (schema-checked)
                │
                ▼
      spoken explanation ─► squeeze/"yes" approves ─► rule memory
```

The freeze path contains no model and no network call. Muse runs only after the arm has stopped.

The step contract from `ROBODOJO_FINAL_PLAN.md` stays the shared interface. Contracts gain `route` and `applied_rules`. Nothing flinch-related goes into the RoboDojo repo.

---

## 3. Components to build

### 3.1 Brace detector (`h2m/sensors/emg.py`)

What `h2m` already does:

- Causal DC removal, 60 Hz notch and 20–90 Hz band-pass; 250 ms windows every 50 ms; per-channel RMS.
- A rest / squeeze / open calibration normalizes flexor effort and extensor activity to 0–1, with smoothing.
- `emg.squeeze`: flexors held above threshold; carries the grip effort. `emg.open_hand`: extensor burst; freezes the arm.
- Threshold rules only, no trained classifier. A learned decoder waits until after the hackathon.

New in the brace path:
- **Feature:** `brace = min(flexor, extensor)`, both normalized per user. A squeeze is flexor only and an open hand is extensor only, so this separates all three on the existing electrodes.
- **Faster window:** a parallel 100 ms window on the same 50 ms hop, because the 250 ms window plus smoothing is too slow for a reflex. Tune against recorded data.
- **Onset rule:** brace above threshold, reached from a calm baseline within about 150 ms, held at least one or two hops. Slow tensing is not a flinch.
- **Calibration:** add a 3 s "brace your whole forearm" step to the profile.
- **Priority:** brace > open hand > squeeze. A flinch must **never** count as a confirmation, so squeeze detection requires extensors below the co-contraction threshold.
- **When it acts:** freezes only during motion. While idle it just logs, for false-positive counting.
- **Event:** `emg.brace` with onset time, peak values and the recent trace.

**Latency budget (to measure):** muscle onset → Ganglion BLE chunk → window → detection → next 30 Hz control tick → servo stop. Report software and physical stop time separately; physical time comes from slow-motion video.

### 3.2 Incident capture and explanation (`h2m/incident.py`, new)

- **Ring buffer:** 5–8 s of downsampled overhead and wrist frames, joint positions, active contract, EMG.
- **On brace:** freeze the buffer, keep 1 s after the freeze, and save the clip with the session.
- **Muse Wonder input:** 6–8 labeled keyframes, the detected objects and the arm's table position over time, what it held, and how the reaction unfolded.
- **SAM 3.1:** segments the object Muse blames, for the on-screen highlight and the avoid zone. Fall back to the YOLOE box.
- **Output, schema-checked:**

```json
{"cause_object": "laptop", "relation": "carried_over", "held_property": "liquid",
 "say": "You braced as I carried water over your laptop. Should I keep liquids away from it?",
 "confidence": 0.8,
 "rule": {"when_holding": ["liquid"], "avoid": "laptop", "response": "route_around"}}
```

- `response` must be one of `route_around`, `slow_down`, `lift_higher`, `grip_gentler`. Anything else is rejected.
- If Muse is unsure or the API fails, the arm says "Something bothered you. What was it?" and takes a spoken answer. It stays stopped either way.

### 3.3 Rule memory and route choice (`h2m/rules.py`, new)

- Saved per user as JSON with the incident id, clip link and approval. Viewable and deletable in the UI.
- **Nothing is saved without approval** (squeeze, "yes" or click). A wrong guess costs one sentence.
- **Rules can only restrict:** they remove routes, lower speed or raise caution. They can never bypass a safety check.
- **Routes:** taught, named paths between slots and the drop zone (`direct`, `via_left`, `via_high`), each with a table polyline measured at teach time.
- **Selection:** deterministic. Drop routes whose polyline crosses an avoid zone for the object held, and use the first that remains. If none remains, say so and ask.
- **Generalization:** catalog objects carry tags (`liquid`, `fragile`), so a rule learned on a cup also applies to a bottle.

### 3.4 Embodied assistant (`h2m/embodied.py`, new)

- **Muse Spark tools:** `pick_place`, `place_near_user`, `look_at`, `gesture`, `say`. Each returns through the contract and gate. The grammar stays as the offline path.
- **Scene questions:** Muse answers from the wrist or overhead frame and gestures while replying.
- **Gestures:** 4–5 taught motions (nod, tilt, shake, look at user, thinking). They hide API latency, which is most of what makes it feel alive.
- **Speech:** macOS `say` or a local voice; we found no Muse text-to-speech. Speech must cut off on stop or brace.
- **Handover:** place on a marked spot near the hand, never into the hand. Direct handover needs validation we won't have by Nov 4.
- **Gate change for review:** gestures need arming and an empty gripper, stay in a small envelope near home at low speed, and skip per-motion confirmation. Anything that carries or grasps still confirms. This relaxes `always_confirm`, so `safety-reviewer` must approve it.

### 3.5 Existing squeeze-to-grip

Unchanged and kept as beat 5 of the demo. Human EMG is not a motor command, so it passes through a constrained mapping:

- Effort is capped by the object's `max_grip`, then sets the gripper close target and torque limit (never above 50%, LeRobot's burnout cap).
- The arm only proposes within those limits. `Present_Load` readback is displayed; verify it on the real arm.

---

## 4. Build plan (Oct 31 → Nov 4)

### 4.1 Demo (3 minutes, one continuous-looking story)

| # | Beat | On screen |
|---|---|---|
| 1 | **Cold open.** The arm carries water toward a laptop. You flinch. It freezes. | "Stopped in X ms. No words spoken." |
| 2 | **Replay.** The moment again, slowed | EMG trace with both channels spiking |
| 3 | **It understands.** The arm looks at the laptop and speaks its explanation; you squeeze to approve | SAM mask on the laptop; the rule appears |
| 4 | **It remembers.** Same request, new route. Then a different liquid gets the same treatment. | Route overlay; "rule applied" |
| 5 | **It's a companion.** "Pass me something to write with." It looks, nods, picks the marker; squeeze sets the grip, the paper cup survives. | Grip and load traces |
| 6 | **Numbers.** Measured results and known failures | Results table |

**Props:** dummy laptop (an empty box or dead unit), **lidded** cup or small sealed bottle with water, thick marker, paper cup, block. Everything chosen for the SO-101 gripper and placed on taught slots. No real electronics within reach of the water.

**Authenticity:** a rehearsed flinch is acting. The software (not a person) fires the unpredictable trigger for the real take, since you can't surprise yourself; label any re-enactment in the video.

### 4.2 Schedule and gates

| Day | Work | Gate at end of day |
|---|---|---|
| **Oct 31** | Flinch recorder (`h2m flinch record`), run G1. Get the Muse key and make one live call with an image (`h2m muse-check`). Runtime on the real arm in shadow mode, verify torque and load calls, teach `home` and slots. | **G1 flinch** and **G2 API** decided |
| **Nov 1** | Brace detector and freeze; first real confirmed pick; taught routes and gestures. Safety review of motion and gate changes. | **G3:** real pick-and-place, and a brace freezes the real arm |
| **Nov 2** | Incident capture, Muse explanation, SAM mask, rule memory, route selection, speech. | **G4:** the full flinch → explain → approve → reroute loop works once on hardware |
| **Nov 3** | Embodied tools, Stage view, replay. Morning trials, afternoon demo takes. | **G5:** a clean take exists, and the results table is filled |
| **Nov 4** | Buffer, retakes, video edit, README, results. **Submit by early afternoon.** | Submitted |

**G1 protocol (about 17 minutes recorded):**
- Ganglion on, seated. Software plays loud noise bursts at random times (you cannot surprise yourself, so no helper is needed).
- At least 20 surprises and at least 10 minutes of normal activity: typing, reaching, lifting a mug, gesturing, talking, resting.
- Raw EMG is saved for offline scoring; thresholds are tuned on one part of the data and scored on the rest. On ties the tuner prefers the higher threshold to cut false stops.
- **Pass:** about 80% of flinches caught, under 1 false stop per 10 minutes, on data not used to set thresholds.
- **Watch for habituation:** startle responses fade with repetition. Report first-half vs second-half detection, and plan to use a fresh, surprising trigger for the demo take.
- **Limit:** a noise startle is not a reaction to the arm. The demo claim needs its own retest with randomized arm lurches.

**If G1 fails:** move electrodes to the upper arm or shoulder and retest once. If it still fails, use a deliberate "tense up" gesture. The rest of the system is unchanged, but the pitch becomes "a muscle signal that teaches rules" and the word "involuntary" goes.

**Cut order if behind:** scene questions → gestures beyond nod and look → generalization to a second liquid → replay scrubber. **Never cut:** freeze, explain, approve, reroute.

**Fallbacks**

| Failure | Response |
|---|---|
| Muse Wonder unavailable or slow | Muse Spark; then spoken "what bothered you?" with the grammar. Be honest in the video. |
| SAM 3.1 unavailable | YOLOE box for the highlight and zone |
| Real arm fails | Virtual arm for the software story, clearly labeled. This seriously weakens the entry, so protect arm time first. |
| Ganglion BLE unstable | USB dongle if available; shorter takes |

### 4.3 Measurements (`h2m bench` additions)

| Claim | Metric |
|---|---|
| It catches flinches | Detection rate with 95% interval, first vs second half, per person |
| It doesn't stop for nothing | False stops per 10 minutes of normal activity |
| It stops fast | Software p50/p95, physical stop time from video |
| It understands | Muse names the right cause: correct / incidents |
| It learns | Repeat-mistake rate before vs after the rule, on the same task |
| It generalizes | Rule applied correctly to an object not in the incident |
| It's gentle | Crush and drop rate, fixed vs squeeze grip |

Report 2–3 people with n stated. Two or three people does not establish a general result, and the write-up says so.

### 4.4 Submission package
- 3-minute video, repo with one-command launch, pinned versions, `docs/RESULTS.md` including failures.
- Credit prior work: ForceBand for EMG-to-force, EMPATHIC and error-potential work for implicit feedback, ELEGNT for expressive motion.

---

## 5. After the hackathon

Teleop data collection, learned executors and the RoboDojo plan below are unchanged, and the study adds the flinch question: across many people and days, does bracing generalize, and do learned rules reduce repeat mistakes?

### 5.1 Data collection on your arms (week 1–2)
- Leader-arm teleop with the Ganglion on the *operator's* wrist. Each episode = LeRobot v3 dataset + synchronized EMG + effort trace + contract text + anchor (from the overhead detector). This is a force-enriched, language- and point-labeled SO-101 dataset; ForceBand collected 10 h with similar structure.
- Target: 3 tasks (pick-place to tray, handoff to hand, pour-free "move soft object") × 3 objects × ~40 episodes = ~360 episodes. Split by session and by operator.
- EEG recorded, unused.

### 5.2 Executor fine-tuning (week 2–4)
| Candidate | Why | Cost |
|---|---|---|
| **SmolVLA (450M)** | Documented SO-101 path; 50-episode minimum; trains on one consumer GPU | First |
| π0.5 (LoRA) | Stronger; openpi has SO-101 recipes; 3–6 h A100 | Second |
| XR-1-5B | The RoboDojo base; Apache-2.0; no SO-101 adapter yet (custom format) | Only after GSC data tooling exists |

Conditioning = contract text + rendered anchor marker in both cameras (Point-VLA / AnySlot style, same code as the RoboDojo S5 stage) + `grip_effort` as an extra proprio scalar. Train with and without each input for the ablation. The gripper action target is the *torque-limited* command, so the policy learns compliant grasps.

### 5.3 The experiment that proves the product
Held-out users and sessions, same tasks, same executor:
1. language only · 2. + camera · 3. + pointing · 4. + EMG effort · 5. + EMG veto.
Report success, wrong-action rate, crush/drop, time, stop latency, with paired CIs. Robustness: noise 65 dB, lighting, distractors, electrode re-placement, fatigue after 20 min. This is the paper/tech report and the evidence for the pitch.

### 5.4 RoboDojo (parallel, separate repo, `ROBODOJO_FINAL_PLAN.md`)
Unchanged. The contract schema and the marker-rendering code are shared; nothing EMG-related goes into that repo (release requirement). Gate G0 (committee email) first; no GPU spend before G2.

---

## 6. App design

Changes to the existing UI; everything shown comes from live events.

- **Operate:** contract card shows route and applied rules; a brace meter joins grip and open hand.
- **Stage view (`S`):** fullscreen, large type. Camera with masks and route overlay, EMG traces, stop-latency readout, the arm's current sentence.
- **Incident view:** slowed clip synced with the EMG trace, Muse's explanation, approve/reject.
- **Rules page:** every saved rule with its clip; delete with one click.
- **Setup:** brace step in calibration; flinch-test results.

---

## 7. Risks and what was cut

| Risk | Mitigation |
|---|---|
| Flinch doesn't show on the forearm, or habituates | G1 today; fallback defined |
| False stops ruin the take | Tune on G1 data; prefer missing a mild flinch over stopping at random |
| Moving arm/body artifacts look like a brace | Require co-contraction and fast onset; test in G1 |
| Muse names the wrong cause | Approval required; on-camera correction by voice is itself a good moment |
| Arm time eats the schedule | Few slots, two routes, tray or marked spots only |
| Water near electronics | Dummy laptop, sealed container |
| Gesture exemption weakens safety | Small envelope, empty gripper, armed only, reviewed |
| Over-claiming | No "reads your mind", no "first ever"; small n stated; EEG unused |
| Judges have seen voice-arm agents | Lead with the flinch in the first 10 seconds |

**Cut:**
- **EEG decoding:** Brain2Qwerty's [EEG result is 67% character error, offline](https://arxiv.org/html/2502.17480v1).
- **VLA fine-tuning:** no time in the window.
- **"Moves before you do":** the [30–100 ms EMG lead](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11723079/) is used up by our latency.
- **Blindfolded touch:** needs extra hardware and barely uses Meta's models.
- **Silent speech:** [too unreliable](https://arxiv.org/abs/2603.02847v2).
- **Show it once:** strong but less distinctive; revisit after Nov 4.

---

## 8. Decisions (answered Oct 31)
1. **`MODEL_API_KEY`:** user has one. Whether it covers Muse Wonder and SAM 3.1 is checked by `h2m muse-check`. Model names are unconfirmed against the account.
2. **Surprise trigger:** software fires noise bursts at random times for G1 (no helper needed). The demo take needs a randomized arm lurch for the same reason.
3. **Track:** **indie.** Judging favors a striking working demo over production polish.
4. **Props:** confirmed, including a lidded cup the gripper can hold.

## Sources
- Meta Global AI Developer Hackathon — dates, tracks, judging: https://cerebralvalley.ai/e/meta-ai-developer-hackathon
- ForceBand: Learning Forceful Manipulation with sEMG (2026): https://arxiv.org/abs/2606.26093
- Koopman grip-force prediction from one sEMG pair (IEEE TNSRE 2025): https://doi.org/10.1109/tnsre.2025.3576110
- HD-EMG joint position + grip force (2026): https://arxiv.org/pdf/2410.23986
- Brain2Qwerty (MEG 32% / EEG 67% CER): https://arxiv.org/html/2502.17480v1
- Meta emg2pose / emg2qwerty datasets: https://ai.meta.com/blog/open-sourcing-surface-electromyography-datasets-neurips-2024/
- Meta's EMG band beyond AR glasses: https://www.engadget.com/wearables/metas-emg-wristband-is-moving-beyond-its-ar-glasses-120000503.html
- MIT ultrasound wristband robot hand: https://news.mit.edu/2026/wristband-enables-wearers-control-robotic-hand-with-own-movements-0325
- SAM 3.1 via Meta Model API: https://www.mind-verse.de/news/meta-sam-3-1-bild-video-analyse-api · model card: https://huggingface.co/jtreminio/sam3.1
- Muse Spark (vision): https://about.fb.com/news/2026/04/introducing-muse-spark-meta-superintelligence-labs/ · Muse Glimmer 30B (Apache-2.0): https://huggingface.co/meta-models/Muse-Glimmer-30B
- Xiaomi-Robotics-1 (Apache-2.0): https://github.com/XiaomiRobotics/Xiaomi-Robotics-1 · paper: https://arxiv.org/abs/2607.15330
- SmolVLA on SO-101 (50 episodes): https://huggingface.co/docs/lerobot/smolvla · https://openelab.io/blogs/learn/how-to-fine-tune-smolvla-on-so-101-with-lerobot
- π0.5 on SO-101: https://www.ay-robots.com/train/pi0-5-on-so-101
- LeRobot SO-101 gripper torque/current limits: https://huggingface.co/docs/lerobot/so101
- Arduino Tiny ML kit contents: https://store.arduino.cc/en-be/collections/kits/products/arduino-tiny-machine-learning-kit
- RoboDojo benchmark: https://arxiv.org/abs/2607.04434 · leaderboard: robodojo-benchmark.com/leaderboard
- Implicit feedback from facial reactions (EMPATHIC): https://arxiv.org/abs/2009.13649
- Implicit feedback from EEG error potentials: https://arxiv.org/html/2507.13171v1
- Reported judging weights (secondary): https://thegenaimagazine.com/meta-global-ai-developer-hackathon-ideas-prizes
- Reported work-during-event rule (participant repo): https://github.com/sholaola23/meta-hackathon-2026
- Muse Wonder announcement: https://about.fb.com/news/2026/10/introducing-muse-wonder-models/
- Meta Model API docs: https://dev.meta.ai/docs/overview · models: https://ai.developer.meta.com/docs/models/
- Existing SO-101 LLM agents: https://github.com/Tibe31/so101-mcp · https://github.com/reality-opened/artist-agent
- Apple ELEGNT expressive lamp: https://9to5mac.com/2025/02/06/apple-just-built-an-adorable-robot-lamp-a-sneak-peek-into-robotics-work/
- EMG electromechanical delay: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11723079/
- Silent-speech EMG (SilentWear): https://arxiv.org/abs/2603.02847v2
