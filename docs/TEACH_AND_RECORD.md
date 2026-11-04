# Armed: teach, set up, shoot

Written November 3, 2026. The one operational document for the submission: what to teach, how the desk and the bed are laid out, what each beat of the final cut needs, how every run is recorded, and the order of the next thirty hours. It follows `DEMO_STORY.md` (fourth revision) with the Meta-only edits from the Demo plan session, and the rule that nothing learned runs in the build (`COMPLIANCE.md`). Teaching commands and pose placement are from `docs/hardware/calibrate.md`; everything that was dropped (staging poses, torch, thermometer, policy episodes) is left out here on purpose.

---

## 1. Layouts, props, cast

### Desk
You sit at the front edge. The arm is clamped at the back right. Three object spots in a row in front of the arm, 10–12 cm apart: `slot_1` left, `slot_2` middle, `slot_3` right. The tray at the far left. **The dummy laptop between `slot_1` and the tray**, so the direct carry crosses it. The hand spot at the front edge by your right hand. The bin at the right edge. The lamp on a plug, or a large rocker switch within reach of the arm. Mark every spot with tape; the overhead camera is mounted and taped, exposure locked, a reference frame saved in `data/reference/desk.jpg`.

### Bed
The arm clamped to the headboard on the side of your dominant hand. A bedside table within reach with three spots: cup, pill cup, marker. The hand spot on the blanket by your hand. The headboard screen (tablet or small display) in far mode where you can see it lying down. Pillow height fixed for the night and the morning. Overhead camera on the headboard, taped, reference frame `data/reference/bed.jpg`.

### Props
Wooden block · small plastic water bottle (about 250 ml) with a sealed straw cap (the only water in the film) · phone in a rubber case · small pill cup · thick marker · a jar with a 2–3 cm lid the jaws can hold · pen cup · a wrapper and a used cup for the desk check · the dummy laptop (empty box or dead unit) · a bin · a lamp. Test every object in the jaws by hand before building a beat on it: the taught opening is 30%, about 2–3 cm.

### Cast and cameras
You (band on the right forearm). X (one voice, on camera twice, and on the leader arm for nothing now). A friend who has never worn the band, 30 minutes, any time after 16:00. Cameras: the overhead feed (the robot's own); a phone on a tripod at three-quarter showing you, the arm and the screen; a side phone at 60 fps with a ruler on the dummy laptop for the stop; a first-person phone at the pillow for the bed preview; a screen recording of your laptop for the Card cutaways; the stage view recorded to a file. A small speaker by the arm; a lav on you.

---

## 2. Teach

`h2m arm teach <name>`: torque drops, support the arm with one hand, place it, Enter, torque returns pinned. Leader arm unplugged. Gesture steps as `gesture.<gesture>.<step>`. A skill with an untaught pose is refused by the real arm with the pose named.

### Block A: the loop (taught)
| Pose | Placement |
|---|---|
| `home` | taught; leave it |
| `slot_1/2/3 .above .grasp .point` | taught; re-teach `above` 2 cm higher if the jaws came near the table |
| `tray_above`, `tray_release` | taught; the jaw clears the rim at 30 open |
| `via_high`, `via_left` | taught; above home height, clear of the laptop |
| `handover_above`, `handover_release` | taught; 8 cm and 2 cm over the hand spot, gripper 30; `handover.spot` set in `routes.yaml` |
| `sorry_back`, `sorry_low`; `wave_up/left/right` | taught |

### Block B: today at the desk, about 40 minutes
| Pose | Placement | For |
|---|---|---|
| `hold_up` | arm up, gripper toward your face, about 40 cm away, as if holding a phone to read | the 0:00 preview (review) |
| `jar_hold` | at the hand spot, jaws open, at the height your hand holds a jar out | the jar (review) |
| `bin_above`, `bin_release` | over the bin, gripper closed then 30 | the desk check |
| `face_above`, `face_near` | 20 cm in front of and above your mouth; then 6 cm from your lips, straw level, from below (the desk arm can reach; the bed arm cannot) | the straw clip for the README (the twitch at the final approach) |
| `press_above`, `press_down` | closed jaws 3 cm over the switch, then pressing it | lights, if the lamp is a physical switch; skip with a plug |
| `gesture.look_at_user.*`, `gesture.nod.*` | pan toward where you sit, wrist up a little; wrist flex ±10° | the clamp beat, the pills nod |

### Block C: tonight on the bed, about 90 minutes
| Pose | Placement | For |
|---|---|---|
| bed `home` | rest pose clear of your head | every bed beat |
| `cup_slot.*` (the water bottle), `pill_slot.*`, `marker_slot.*` | above / grasp / point for the three bedside spots; `point` is the jaws just touching the object (the close goes there plus a slight squeeze) | pills, water, X asks |
| bed `handover_above`, `handover_release` | over your open hand on the blanket, wrist roll about -26 (the pill box lies flat) | pills |
| bed `handover_bottle_above`, `handover_bottle_release` | the same hand spot, wrist roll about 77 (the bottle stays upright) | water |
| bed `x_hand_above`, `x_hand_release` | over X's outstretched hand on the far side of the bed, roll about -26 | the marker |
| bed `look_at_user` | pan toward X | X asks |

Revised November 4: the bed arm cannot reach your mouth, so on the bed it hands over the pills and then the water bottle to your hand, and the marker to X's hand; the straw to the mouth moves to the desk (a README clip). The cup spot holds the water bottle.

Bed poses go in a second poses file (`docs/hardware/calibrate.md`), loaded with the bed profile.

### Calibrations, after Block B, about 20 minutes
- **Table homography** (`h2m calibrate-table`): SAM 3.1 boxes to table coordinates. Without it, nothing maps to a slot.
- **Slot arc**: confirm the three slots sit at one radius from the base, so a box anywhere on the arc gives a `shoulder_pan` for `slot_1`'s height and wrist. This is "pick from anywhere" without a policy.
- **Laptop avoid zone** in `routes.yaml`, measured from the overhead frame; the direct route must cross it and `via_high` must not.
- **EMG**: rest, squeeze, open, brace, twitch on the forearm; a named profile. Re-do it on the bed tonight after the pads have been off.
- **Jaw calibration** (20 min): measure the jaw opening in mm at 30%, 60% and 100% with a ruler and save the three points. This is what lets a SAM 3.1 width become an opening percentage (`COMPLIANCE.md` §7): at the `above` pose one SAM call measures the target's width through the homography, the jaws open to width + 10–15 mm clamped to the slot's safe range, the wrist roll follows the mask's axis within ±25° of the taught roll, and the close still stops on gripper load and the catalog cap. Expect width within ±5 mm about 80–90% of the time on opaque objects; transparent or thin objects keep the catalog value. Reviewed before it runs on the real arm; the fixed 30% is the fallback.

### Skills and gestures in the cut
`hold_up` (phone) · `take_this` grasp reused for the jar hold · `sip` (straw) · `press_button` (switch) · `sorry` (after the stop) · `look_at_user`, `nod` · `pick_place`, `move`, `place_near_user`. Not in the cut, not taught: `celebrate`, `bow`, `wave` beyond the hello, thinking, shake.

---

## 3. The demo sequence: every beat, what it needs

Clock is story time at the desk, real time in bed. Every beat: two clean runs before a take; the stage view recording; a trial started and ended in the console.

| Time | Beat | The arm | Poses and skills | Trigger and models (chips) | Record |
|---|---|---|---|---|---|
| 0:00 | *Built Oct 26–Nov 4* card, 2 s | — | — | — | — |
| 0:02 | Phone while typing (preview) | pick the phone from `slot_2`, hold at eye level, back | `slot_2.*`, `hold_up` | phone buzz (mic) → card; `sam-3.1 · objects`, `muse-spark-1.3 · proposed`, `EMG decoder` | overhead tight on hands; laptop screen recording |
| 0:08 | Pills from the bed (preview) | pill cup from `pill_slot` to your open hand, let go | `pill_slot.*`, `handover_*` | 08:00 routine card *from your routine*; twitch at the approach | first-person phone; far-mode screen in shot |
| 0:14 | Title, montage, counters | — | — | — | four dated stills |
| 0:20 | Water after pills | the bottle from `cup_slot` to the same hand, upright, let go; a nod | `cup_slot.*`, `handover_bottle_*`, `nod` | routine *after pills*, *like yesterday* (yesterday's water run must exist); `muse-spark-1.3` | side |
| 0:25 | X asks: marker | turn toward X, wait, marker to X's hand | `look_at_user`, `marker_slot.*`, `x_hand_*` | X's voice → *X asked* card; `muse-voice-transcribe-1.0 · speaker 2 = X`; the arm turns 1–2 s after X speaks (SAM face scan on new voice) | wide; transcript line |
| 0:35 | Clamp move | look up | `look_at_user` | — | one unbroken shot per move |
| 0:38 | Context, then the jar | grip the jar at the hand spot, hold while you open it, release on twitch | `jar_hold` (via `take_this` grasp), hold stage | packet rows: *phone buzz*, *phone on desk · last scan 4 s*, *11:02, usually looks*; then *hand out · jar in hand* from adjacent masks; `calling sam-3.1 · [ms]` while the scan runs; the jar card arrives 1–2 s after the hand settles | live desk; laptop screen recording for the Card cutaway; gripper load bar |
| 0:58 | Water over the laptop: slow-down, brace, stop | carry the sealed cup on the direct route; speed drops with the trace; freeze; "Sorry" | `slot_1.*` → direct route → `handover_*`; `sorry` | 14:00 routine card; comfort coupling (reviewed); `Armed stop controller · no model` | side phone at 60 fps, ruler on the laptop; meter and arm in one frame |
| 1:12 | Investigation | still | — | `muse-wonder-1.0 · investigating`, `sam-3.1 · masks and tracks`, `Armed measure`, `Armed checker`; the rule card; twitch | desk screen full frame |
| 1:32 | High route | same carry on `via_high` | `via_high` | rule applied; route overlay | overhead |
| 1:37 | Explain to X | turn toward X, speak the rule | `look_at_user`, `say` | X's voice; `recall`; `muse-voice-transcribe-1.0` | wide |
| 1:44 | Offline, mid-carry | a carry in progress; router pulled; brace; stop; then a say card answered by a twitch | a carry; `say` | chip *offline · scene frozen at last seen · stop local · cards fixed order* | router pull in one unbroken shot; Card cutaway |
| 1:52 | Desk check | look at the desk; clear two things to bin and tray after the 5 s ring; reach for the pen cup; stop on the brace; "Noted" | `look_at_user`, `move` chain, `bin_*`, `tray_*` | 30-minute routine; Spark over a SAM scan classifies *wrapper · not yours to keep*, *used cup · not on a slot*; the auto rung (reviewed); refusal → rule | wide; the ring and 1/2 · 2/2 on the Card |
| 2:10 | The friend | block to the friend's hand spot | `slot_1.*`, `handover_*` | fresh profile; a stopwatch | time-lapse |
| 2:17 | Results | — | — | from `h2m bench` | — |
| 2:26 | Lights off, "Night" | press the switch, or the plug | `press_*` or `set_light`; `say` | 22:30 card | bed, lights low |
| 2:29 | End card | — | — | — | — |

Cut if not clean by 18:30, in order: the investigation (labelled replay), the desk check's auto rung (ask mode), diarization (*a voice asked*; explain-to-X cut), the slow-down caption, the jar (the mug).

---

## 4. Record

The runtime records itself; nothing extra to build.

1. **Start a session** in the console before the first run of each block: participant, notes (which beats, which scene). The HUD clock is the session's clock.
2. **One trial per attempt**: expected action, condition `armed` or `scanning`, outcome at the end. `h2m bench` turns trials into inputs per task, time per task, accepted-first-time, false twitches, stop latency.
3. **Cameras to file** for every take: overhead, three-quarter, the side 60 fps when a stop is in the beat, the laptop screen recording for desk beats, the stage view to a file; all started within the same minute.
4. **Takes are trials** with outcome `success` in a session tagged `take`; an *earlier take* substitution is a lookup in `trials.jsonl`.
5. **Comparison runs first**: ten tasks `scanning`, ten `armed`, same objects and placements, same person, one session, before any desk take.
6. **Incidents** save themselves on every brace during motion: clip, joint path, EMG, then the investigation's tool log and the approval.
7. **The friend's calibration** is its own session, timed by its first and last events.
8. **Jaw-opening accuracy** (20 min, once): twenty objects × five placements, SAM width against a caliper, reported as *within 5 mm: n/100* in `RESULTS.md`. Chip on screen during picks: `sam-3.1 · width 24 mm · open 48%`.
9. **Yesterday's water**: run the water routine on the bed tonight so tomorrow's 08:00 card can say *like yesterday* and mean it.

`data/` is git-ignored; `docs/RESULTS.md` is generated from the sessions; raw takes and failure clips are linked from a release or a drive folder.

---

## 5. The order

| When (CST) | Do |
|---|---|
| Nov 3, 13:00–14:00 | Block B. Homography, arc, laptop zone, jaw calibration. Enable `selection`. Three-card loop with a real twitch: the first "works". Commit. |
| 14:00–16:00 | Forks replace YOLOE, MediaPipe and Parakeet (`COMPLIANCE.md`). You: comparison runs (ten and ten). Time ten SAM calls when the client lands. |
| 16:00–18:30 | Desk takes that work at story time: phone preview once `hold_up` is reviewed; offline mid-carry; the laptop carry and stop; the high route; the jar once reviewed; the friend when free. |
| 18:30 | Cut list. |
| 18:30–22:00 | Block C on the bed. EMG re-calibration. The water routine (for *yesterday*). Rehearse the four bed beats. |
| 22:30 | The night take. |
| 23:00–06:30 | Sleep. |
| Nov 4, 07:00–07:50 | Bed setup: X, first-person phone, lav, speaker, stage view recording. |
| 08:00–08:20 | Pills preview first, then the water, then X asks. |
| 08:30–11:30 | Clamp to the desk. Desk check, explain-to-X, investigation if not in the can; retakes. |
| 11:30–15:30 | Edit. |
| 15:30–16:45 | README, results, commit, tag, the form. |
| 17:00 | Submit. |
