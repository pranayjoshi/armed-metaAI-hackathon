# Hunch: idea and demo plan

Written November 3, 2026, for the Meta Global AI Developer Hackathon, indie track ("creativity, technical ambition, and whether it works"), theme Push the Frontier ("give a machine a new capability ... unlock a better way to understand and control a physical system"). Supersedes the earlier demo outline in this file. Concept background: `REFLEX_CONCEPT.md`; code state: `REFLEX_BUILD_PLAN.md`; judging evidence: `WIN_ANALYSIS.md`.

The name is pitch-level. The package stays `h2m`; the UI title and README change in one commit by the Face and Proof streams.

## Name and tagline

**Hunch** — *autocomplete for the body.*

> It has a hunch what you want. One muscle says yes. Your flinch says no, and it learns.

Why the name: the product is a guess you confirm, the way a keyboard guesses the next word. It's short, ownable, friendly on a desk and serious in a hospital bed. Alternatives considered: *Flinch* (names only the safety half), *Volition* (strong for the population, cold on a desk).

## The idea in five lines

1. Most robots wait to be told. Hunch watches your hands, your eyes, your muscles, the clock and your habits, and proposes the three things you most likely want next.
2. You answer with the smallest signal a body can make: one muscle twitch for yes, two to see the next option.
3. If anything goes wrong, you don't press a button. Your body flinches before you've decided, and Hunch stops in under half a second with no model and no network in the path.
4. Every flinch becomes a rule. Muse Wonder investigates the clip, proposes a restriction, a checker proves it can only make the robot more careful, the virtual arm rehearses it, and you accept with a twitch. It's the only robot that gets safer the longer you use it.
5. On a desk it's a third hand for anyone whose hands are busy. Clamped to a headboard, for someone who can't move, it's the whole channel.

## Three claims, each with a beat and a number

| Claim | The beat that shows it | The number that backs it |
|---|---|---|
| **One bit is enough.** With a good predictor, a single yes/no muscle is a complete control channel for a physical system. | Typing with both hands, the phone buzzes, a card appears, one squeeze, the arm holds the phone at eye level. | Selections per task and seconds per task, Hunch vs the scanning menu, n stated. Top-1 accuracy rising across a session. |
| **Your nervous system is the safety layer.** The robot reads the flinch that fires before you've decided, and turns it into a verified rule. | Water comes toward your face, you tense, it freezes centimetres away, says "sorry", and Wonder proposes the gentler approach, proven and rehearsed before you approve. | Flinch-to-motionless latency on 60 fps video. Missed stops: 0 of n. Rules that passed the verifier. |
| **It's a body-language protocol, not an arm.** Twitch, double twitch, brace, flinch and consent are an interface any actuator can implement. | A double twitch sends "I'm okay" to a phone. A carer's voice gets a card but no motion until *you* twitch. The clamp moves from desk to headboard. | Second actuator working through the same gate. Second person calibrated and completing tasks in under five minutes. |

Every bold line in the pitch maps to one row. A claim that loses its beat by shoot time comes out of the pitch.

## Where Meta's models do something only they can

| Model | Job in Hunch | Why this one |
|---|---|---|
| Muse Spark | Ranks the three cards from the scene image, head direction, time, the last days of tasks, rules and recent speech; drives the tool loop; composes gestures from taught blocks | Image in, tools out, under two seconds |
| Muse Wonder | Investigates an incident clip with tools (`track`, `measure`, `arm_trace`, `propose_rule`), holds the day's history, answers "why did you stop" | Video in, long context, parallel tools |
| Muse Voice Transcribe | Separates your voice from a carer's so a carer's request needs your twitch | Diarization in streaming ASR |
| SAM 3.1 | Highlights the object involved in an incident on the replay | Segmentation on the clip |
| Muse Image | Draws the pictogram on each card so the options read from across a room | Styled images from a short description |
| Glimmer (stretch) | Narrates the scene on-device so the bedroom camera never streams by default | Open weights, local |

None of them sits on the freeze path. No model output ever becomes a joint value.

## The demo

### The 45-second cut (vertical, captions, no narration)

| s | On screen | Caption |
|---|---|---|
| 0–5 | Both hands typing. Phone buzzes face-down. A card slides in: *show phone*. | *My hands are busy. It had a hunch.* |
| 5–10 | Forearm squeezes, fingers never leave the keys. Arm picks the phone up and holds it at eye level. | *One muscle said yes.* |
| 10–16 | Empty mug held out to the side without looking. Squeeze. Arm takes it from the hand, waits for the release, puts it away. | *It takes things too.* |
| 16–24 | Water with a straw comes toward the face, slowly. Tension. | — |
| 24–28 | Flinch. Freeze, centimetres away. Silence. | *No model in that stop. Muscle to motor in 300 ms.* |
| 28–34 | Arm backs off, says "sorry". A rule card: *approach the face from below, slower*. Squeeze. | *It just learned a rule from my flinch.* |
| 34–40 | Network cable pulled. Cards stay. Squeeze works. Flinch stops it. | *Even with the cloud gone.* |
| 40–45 | Cost card and end card. | *$500. Hunch: autocomplete for the body.* |

### The 3-minute judge cut (landscape, you on camera, hands visible and still except when typing)

| Time | Beat | Claim |
|---|---|---|
| 0:00 | A scanning menu stepping to "water": 25 s on a timer. Cut. Hunch: one squeeze, 3 s. Counter on screen. | 1 |
| 0:15 | "I'm Pranay. This is Hunch." The arm straightens, waves, looks at you. The line. | — |
| 0:25 | **Phone while typing.** Card, squeeze, phone held at eye level, you keep typing. | 1 |
| 0:45 | **Take this.** Mug out to the side, squeeze, it takes it and puts it away. | 1, 3 |
| 1:00 | **It notices.** The pen cup gets bumped to the wrong spot. A card: *pen cup looks out of place, put it back?* with Muse's reasons underneath. Squeeze. | 1 |
| 1:15 | **The flinch.** Straw to the face, freeze, "sorry". | 2 |
| 1:30 | **The rule.** Wonder's investigation on screen: tracked cup, measured approach, proposed restriction, verifier pass, virtual rehearsal, your squeeze. The next approach is gentler. | 2 |
| 1:55 | **Consent.** A carer's voice: "give him the block". Card appears, robot says "waiting for Pranay", nothing moves. You squeeze. | 3 |
| 2:10 | **Headboard.** Lights low, clamp on the bed. 8:00 on screen, no input: *water + pills*, "you did this yesterday". Squeeze. Then *tell Priya you're okay*: double squeeze, a message lands on a phone. "When you're sick, you can't reach. Some people are sick every day. For them this is the whole channel." | 1, 3 |
| 2:35 | **Cloud gone.** Unplug on camera. Cards stay, squeeze and flinch unchanged. | 2 |
| 2:45 | **Numbers.** Selections and seconds vs scanning with n; top-1 first-10 vs last-10; stop latency; cost. | all |
| 2:55 | "Make it yours": Muse composes a 'well done' from moves it knows, virtual preview, checker pass, squeeze, the real arm does it. Bow. Disclosure card. | stretch |

Optional, anywhere after 0:25: twenty seconds of a real user or occupational therapist, with permission to quote.

## What to build, ranked

Build in this order; cut from the bottom. R marks a reviewed relaxation that ships on the virtual arm until `safety-reviewer` approves it for the real one.

| # | Item | Claim | Stream |
|---|---|---|---|
| 1 | Full loop on hardware: camera, Muse, EMG, cards, twitch, pick | works | A |
| 2 | Show-phone pose pair (pick from a taught spot, hold at eye level, return) — R for the hold near the face | 1 | A, D |
| 3 | Take-this: fixed hand spot, close on squeeze, release detected by gripper load, hand watchdog from `place_near_user` — R | 1, 3 | D, A |
| 4 | Straw-to-mouth pair with a measured stop distance — R | 2 | A, D |
| 5 | "Sorry" retreat: reverse the last commanded segment after the after-stop window, then speech — R | 2 | D |
| 6 | Evidence line under the top card | 1 | B, C |
| 7 | Routine memory: time of day and the last days of tasks in the ranking; the no-input 8am card | 1 | B |
| 8 | Out-of-place card: object seen off its slot and untouched for N s → proposal; moves are slot-to-slot | 1 | B |
| 9 | Wonder investigation tools, restrict-only verifier, virtual rehearsal before the approval card | 2 | B |
| 10 | Consent gate with diarization; "waiting for <name>" | 3 | B, D |
| 11 | Network-pull fallback, tested and filmed | 2 | D, A |
| 12 | `send_message` from a taught list through the same gate, shown on a phone | 3 | D |
| 13 | Card screen: three big cards, Muse Image pictograms, muscle meter, evidence line, counters; readable from 3 m | works | C |
| 14 | Speech out, short lines only | — | D |
| 15 | Hosted replay demo, no arm, no key | works | C |
| 16 | Bench additions: scanning condition, accepted-card index, typing false-accept test, brace stop latency, first-10 vs last-10 | works | E |
| 17 | Headboard scene: re-teach 3–4 poses on the bed; 1.5 h of hardware on day 2 | 1, 3 | A |
| 18 | Wave, look-at-user, thinking, bow in a performance envelope — R | — | D, A |
| 19 | Muse-composed gesture: program from taught blocks and bounded repeats, fixed checker, virtual preview, twitch to perform — R | stretch | D |
| 20 | Glimmer local narration | stretch | B |

Items 1–11 are the demo. 12–16 are what a judge who stays scores. 17–20 are the flourish.

## The "works" rules

1. Two clean runs before anything is in a take.
2. Record every hardware run: both cameras and the event log. The video is cut from real takes.
3. Replay mode is the understudy for every beat.
4. The freeze path never changes.
5. New motion (items marked R) gets its own review and lives on the virtual arm until approved.
6. One writer per file set; the hardware stream owns `configs/poses.yaml` and the EMG profiles.
7. Submit with a two-hour buffer. Check the submission form fields in the Official Rules tonight.
8. For every bold line in the pitch: one filmed beat and one number, or the line comes out.

## Streams

| Stream | Who | Owns | Delivers |
|---|---|---|---|
| A. Hardware | user + hardware session | `configs/poses.yaml`, `data/profiles`, thresholds, takes | 1, 2, 3, 4 (teach), 11 (film), 17, every recording |
| B. Mind | `muse-integrator`, `rules-engineer` | `h2m/intent.py`, `h2m/incident.py`, `h2m/learning.py`, `h2m/rules.py`, `h2m/compiler/`, `h2m/world.py` | 6, 7, 8, 9, 10, 20 |
| C. Face | `ui-builder` | `web/` | 13, 15, title and copy |
| D. Body | `runtime-builder`, `embodied-engineer` | `h2m/app.py`, `h2m/embodied.py`, `h2m/robot/`, `h2m/speech_out.py`, `h2m/sensors/speech.py` | 2, 3, 5, 10 (gate), 11, 12, 14, 18, 19 |
| E. Proof | `bench-tester`, `submission-writer` | `h2m/bench.py`, `docs/RESULTS.md`, `README.md`, script, shot list | 16, numbers, submission text |
| R. Review | `safety-reviewer` | read-only | Verdicts on 2, 3, 4, 5, 18, 19 |

## Schedule (hours from now)

| Hours | A (hardware) | B–E (code) |
|---|---|---|
| 0–3 | Empty-gripper pick with the tolerance fix, then a real block. Teach slot_2, slot_3, nod, the phone spot and eye-level hold, the hand spot, the straw pair (taught, not run). | Start 6, 7, 13, 14, 15 in parallel; 9 starts in B. |
| 3–9 | Sleep. | 8, 9, 10, 12, 16 continue. |
| 9–14 | Camera, Muse, EMG on. Full loop. Film every run. Then show-phone and take-this once reviews are back. | 2, 3, 5 code and reviews. Card screen in use. |
| 14–20 | Scanning baseline, Hunch condition (the learning curve), safety block with 60 fps video, flinch-to-rule ×5, network off ×10, message to phone. | Fixes from the loop. 18, 19 if reviews allow. |
| 20–26 | Headboard: re-teach, film the sick-day scene. Two full rehearsals of the judge cut. | Hosted replay live. `RESULTS.md` filled. |
| 26–32 | Shoot both cuts. Optional user/OT call. | README, submission text, end card, title rename. |
| 32–38 | Edit, captions, export. | Final tests, commit, tag. |
| 38–40 | Submit. Buffer. | — |

## Shot list

- Two angles always: the top camera's own feed and a phone on a tripod at 3/4 showing you, the arm and the screen.
- Hands visible in every take: typing in the phone beat, still in your lap otherwise.
- A stopwatch overlay for the opener; a clock for the 8am beat.
- Side phone close on the face for the straw stop, with a ruler in frame once.
- 60 fps with the muscle meter and the arm in one frame for stop latency.
- Robot speech through a small speaker next to the arm; a lav or close phone mic on you.
- Network pull on camera with the status chip changing.
- The clamp moving from desk to headboard, in one continuous shot if possible.

## Disclosure (end card and README)

Able-bodied demonstrator. Thresholds tuned on one person; a second person's calibration time is reported. n stated for every number, with intervals. Taught poses only; no model output becomes a joint value. Failure clips linked.
