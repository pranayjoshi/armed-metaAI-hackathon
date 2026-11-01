# Reflex build plan (code)

Written November 1, 2026, after the Reflex scope lock. Concept and cut list: `REFLEX_CONCEPT.md` (§9 work table, §12 scorecard, §13 cut order). Evidence-based changes: `WIN_ANALYSIS.md` §3. Investigator and rule design: `IDEA_REVIEW.md` §2.2–2.3. Video beats: `PITCH.md`.

## Constraints that do not change

- The freeze path has no model and no network call.
- Models select from taught options only; no model output reaches a joint value.
- Rules can only restrict.
- `safety-reviewer` approval before anything relaxes a check or runs on the real arm.
- One code writer in this checkout. Docs are edited by the other session.

## State at the start of this plan

Built and in the working tree (safety review round 3 pending, then committed): brace detector, freeze, incident capture, Muse Wonder explanation, SAM call, rule memory and routes, embodied tools, speech out, world tracker, handover two-stage plan, operator gripper release, local API hardening. 336 tests pass on the virtual arm and mocked Muse. Nothing has run on the real arm; every threshold is untuned.

## Build order and who builds it

Hardware-dependent items are the user's. Everything else builds now, in this order, each piece reviewed by `safety-reviewer` when it touches a gate, actuation or a stop path.

| # | Work | Agent | Needs hardware | Notes |
|---|---|---|---|---|
| 0 | Replay mode and hosted software demo | `runtime-builder`, then `ui-builder` (user may take the UI) | No | No arm, no key, no network: recorded incident + EMG trace scrubbable, virtual arm takes the long route. Makes the hardware risk survivable. First. |
| 1 | Real arm taught, confirmed pick | user | Yes | Teach home, slots, via poses, gestures, handover. `hardware-guide` runbook on request. |
| 2a | Twitch detector | `emg-engineer` | No (synthetic) | Short-hold squeeze variant, no effort payload; double twitch within 600 ms; brace stays the veto. A twitch must never be accepted from a brace. |
| 2b | Selection model | `runtime-builder` | No | Three candidate cards; twitch accepts top, double twitch steps, brace refuses. Pending/confirm flow and reaction gates reused. |
| 2c | Scanning baseline mode | `runtime-builder` | No | Fixed-order menu stepped by double twitch. Same executor and gate. Selections and seconds counter in `h2m bench`. |
| 2d | Three-card user screen | user (or `ui-builder`) | No | Separate full-screen page; operator UI stays on a second screen. |
| 3 | Intent completion | `muse-integrator` | No (mocked) | Spark 1.3 returns three ranked contracts from scene, head direction (tie-breaker only), last ten actions, rules, recent speech. Candidates with no visible or reachable target dropped before display. Falls back to scanning order if Muse is slow, wrong or absent. |
| 3b | Head direction | `muse-integrator` | No | Face landmarks to table third; tie-breaker input only, never an intent sensor. |
| 4a | One non-arm tool (`send_message` or `set_light`) | `embodied-engineer` | No | Goes through contract and gate like any tool; message text from a taught list, never free model text. |
| 4b | Jaw-electrode calibration profile | `emg-engineer` | Profile needs a real jaw take | Same detector, new profile. |
| 5 | Freeze, then Wonder investigation, then rule | `muse-integrator`, `rules-engineer` | No (mocked) | Tools over video: `track`, `measure`, `arm_trace`, `body_trace`, `propose_rule`. Restrict-only DSL (forbid, slow, lift, loosen; no allow). Verifier proves monotone restriction and replays the clip. Arm rehearses empty-handed before the approval card. Builds on `incident.py`, `learning.py`, `rules.py`. |
| 6 | Muse Voice Transcribe with diarization | `muse-integrator` | Needs mic for live | Gate rule: a carer request needs the user's twitch. Local stop-word path stays. |
| 7 | Glimmer local narrator or Muse Image pictograms | `muse-integrator` | No | Whichever is faster; one ships. Never on a safety path. |
| 8 | "Why" recall, audio-triggered proposals | `muse-integrator` | No | Cut first. |
| 9 | Bench additions | `bench-tester` | No | Twitches per task, seconds per task, top-1 and top-3 proposal accuracy, false twitch rate, stop latency, with n stated. |

## Open decisions and known gaps carried in

- Operator gripper release needs the reviewer's verdict on whether token plus confirm is enough (round 3).
- `camera_sees_past_arm` ships false, so `place_near_user` is refused until checked at teach time.
- `flexor_spike` brace feature can read a fast deliberate squeeze as a brace; steady squeeze guidance goes in setup. A faster twitch detector (2a) must not inherit that confusion; it needs recorded data from the flinch test.
- Flinch test result and Muse key check are still outstanding from the user and gate every threshold.
- Placeholders needing teach-time capture: gesture and handover poses, via routes, per-slot route polylines, laptop avoid zone.

## Cut order if behind

8, then 7, then diarization (6), then 4b. Never cut: selection with cards, twitch and brace, the scanning baseline comparison, the freeze, replay mode.
