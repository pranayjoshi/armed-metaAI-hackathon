# Reflex: a robot arm for people who have one muscle to spare

Written November 1, 2026. This replaces the flinch as the *headline*; the flinch stays as the safety layer. `IDEA_REVIEW.md` and `PITCH.md` still apply to the parts they cover and will need their framing updated if this direction is chosen.

---

## 1. The problem with the flinch as the headline

A flinch is a brake. Brakes are necessary and nobody buys a car for them. The judging criteria are creativity, ambition and whether it works, under themes that ask you to "solve a problem you know firsthand" or "give a machine a new capability." A robot that stops when you flinch is a capability, but the problem it solves (a desk robot occasionally carrying water over a laptop) is small, and the judges will feel that even if they can't say why.

The hardware you have is a tabletop arm, four channels of muscle signal, two cameras and a frontier multimodal model with memory. The question is: for whom is *that* combination life-changing, rather than neat?

---

## 2. The answer: people who cannot use their hands or their voice

Millions of people live with severe upper-limb impairment: ALS, high spinal cord injury, late-stage MS, stroke, cerebral palsy. Many retain a few reliably controllable muscles somewhere: a forearm, a jaw, a shoulder. Today those muscles drive a wheelchair joystick, a scanning keyboard, or a single switch that steps through menus at one option every second or two. Fetching a glass of water means asking another person.

Assistive robot arms exist (Kinova JACO on wheelchairs, the Obi feeding robot). They are controlled by joysticks or sip-and-puff, and their users spend most of the time steering, joint by joint. The bandwidth problem is the whole problem: a human with one or two signals cannot steer six joints.

**The idea:** stop making the person steer. Let Muse watch the scene, know the person's routines, and *propose* the most likely next action. The person answers with one muscle: a twitch to accept, a brace to refuse. The robot does the rest, and every proposal, grasp and route goes through the same deterministic gate the project already has.

In one line: **autocomplete for physical help.** The way predictive text turned a 12-key phone into something you could write a letter on, intent completion turns one controllable muscle into a robot arm you can direct.

---

## 3. Why this is the right shape for this hackathon

| Criterion | How Reflex meets it |
|---|---|
| **Impact** | A clear population, a daily problem, a measurable gain: fewer selections and seconds per task than the scanning menus people use today. |
| **Ambition** | Decoding intent from one or two bits per second plus a scene, with a safety gate, in a closed loop on real hardware. Harder than any voice-to-arm demo. |
| **Creativity** | The novelty is not "LLM controls arm." It is the inversion: the robot proposes, the person disposes, and the person with the least bandwidth keeps the last word. |
| **Meta models used beautifully** | Each model does something only it does here (see §6). Wonder's memory and video input, SAM's tracking, Voice Transcribe's diarization, Glimmer on-device for privacy. |
| **Works** | Every piece runs on the kit you have. No clinical population is required for the demo; see §8 for how to be honest about that. |
| **Theme** | Both. "Build for Your World" if anyone on the team has lived near this; "Push the Frontier" in any case, since the machine gains a capability it did not have. |

---

## 4. How it works

```text
                    ┌──────────────── scene ────────────────┐
 overhead + wrist   │ YOLOE / SAM 3.1: objects, hands, faces │
 cams ──────────────┤ MediaPipe: head direction, gaze proxy  ├──┐
                    └───────────────────────────────────────┘  │
 mic ─► Muse Voice Transcribe (diarized) ─► who said what ─────┤
 Ganglion ─► twitch / brace / effort (model-free) ─────────────┤
 routines + rules (Wonder memory) ─────────────────────────────┤
                                                               ▼
                               INTENT COMPLETION (Muse Spark 1.3, tools)
                               ranks 3 candidate step contracts with confidence
                                                               │
                              Stage view: three cards, top one highlighted
                                                               │
                      twitch = accept top · double twitch = next · brace = refuse
                                                               │
                                 SAFETY GATE (deterministic, unchanged)
                                                               │
                                     EXECUTOR → SO-101 (taught poses only)
                                                               │
                        brace during motion → FREEZE → Wonder investigates → rule
```

**The interaction, from the person's chair.** You look toward the cup. A card appears: "Water?" You twitch. The arm brings the cup to the spot by your hand. Later your phone buzzes; a card: "Phone?" You don't want it; you brace once and the card goes. A caregiver walks in and says "give her the pills." The arm does nothing until *you* twitch. It is your arm, not theirs.

**The three channels, and why each is cheap**

| Channel | Signal | Cost to the person |
|---|---|---|
| Twitch | a short flexor burst above threshold (existing squeeze detector, shorter hold) | one muscle, 200 ms |
| Brace | the existing co-contraction detector | one muscle, involuntary or deliberate |
| Effort | sustained flexor level, sets grip (existing) | only when holding something |
| Head direction | MediaPipe face landmarks, coarse (left / centre / right third of the table) | nothing; you were going to look anyway |
| Voice | Muse Voice Transcribe, if the person has speech; diarization separates them from carers | optional |

Nothing requires fine motor control, reading a screen quickly, or clear speech.

**Intent completion.** Spark 1.3 gets: the object list with table positions, head direction, time of day, the last ten actions, the person's rules, and any diarized speech in the last 20 s. It returns three ranked step contracts (`pick_place`, `place_near_user`, `look_at`, `say`) with confidence. The compiler and resolver from `h2m/compiler/` already produce contracts; this is a ranking layer in front of them. Candidates with no visible target are dropped before display, so the model can never propose what the gate would reject.

**Scanning fallback.** If Muse is slow or wrong, the three cards are still there and the person can step through them with double twitches, exactly like the scanning menus they use today. The system degrades to the baseline, never below it.

---

## 5. The flinch, re-homed

Everything the forks built stays, and it matters more here than it did before:

- **Brace as veto** is the only non-verbal "no" available to someone with one muscle. In a voice-arm demo it is a nicety; here it is the person's authority.
- **Freeze → Wonder investigation → rule** becomes *personalization without configuration*. A person who cannot type preferences teaches the robot by reacting. "Don't bring the pills before food" is learned from a brace, not a settings screen.
- **Comfort coupling** (arm slows as the trace rises) is a continuous, nonverbal way to say "closer, slower" to a robot approaching your body. For a person who cannot pull back, that is not a demo trick.
- **Diarized consent** is now the ethical core of the entry: a carer can speak, but the arm acts only on the user's twitch.

---

## 6. Meta models, each doing something only it does

| Model | Role in Reflex | Why not a generic substitute |
|---|---|---|
| **Muse Spark 1.3** | Intent completion: ranks candidate actions from scene, head direction, history and speech in under two seconds; drives the embodied tool loop | Needs image input, tool calling and low latency together |
| **Muse Wonder** | The long-horizon layer: holds the person's whole history (1M context), investigates incidents from the video clip with tools, proposes restrict-only rules, answers "why did you do that" | Video input plus parallel tool calling plus memory across the session |
| **SAM 3.1** | Finds objects by phrase ("the blue pill bottle"), gives the grasp box, tracks the cup, the hand and the person across incident clips, and measures near misses | Text-prompted, pixel-accurate, tracks across video frames |
| **Muse Voice Transcribe** | Realtime ASR with speaker diarization and keyword biasing on object names; separates the user's voice (possibly dysarthric) from carers'; the reason a carer's request needs the user's twitch | Diarization in a streaming ASR is the feature, and it is a stated capability |
| **Muse Glimmer** (open-weight) | On-device scene narrator and the privacy story: a camera in a disabled person's bedroom should not stream to a cloud by default. Glimmer describes the scene locally; only the incident clip, with consent, goes to Wonder | Runs on your own hardware; nothing else in the lineup does |
| **Muse Image** | Optional, and the one creative use that is not padding: generate pictogram cards for novel objects so the three options can be read by someone who cannot read text quickly. This is what AAC boards do, by hand, today | Image generation that follows a style and a short description |

---

## 7. The demo

Three minutes. The person on camera sits with both hands resting on the armrests and does not move them for the whole video. That is the rule, and the viewer is told it in the first title card.

| Time | Beat | What the viewer sees |
|---|---|---|
| 0:00 | Cold open | Hands at rest. The person glances at the cup. A card: *Water?* One visible forearm twitch on the EMG trace. The arm brings the cup to the hand. No narration. |
| 0:12 | Title | *REFLEX. Hands never moved. One muscle, one twitch.* |
| 0:15 | The problem | Thirty seconds of a scanning switch menu, the baseline, with a counter: *14 selections, 41 s* for the same cup. Then Reflex: *1 twitch, 6 s.* |
| 0:40 | How | Architecture card: propose, dispose, gate, taught poses only. |
| 0:55 | It proposes from the world | Phone buzzes on the table. Wonder's audio understanding notes it; a card: *Phone?* A brace: card dismissed. "It heard the phone. I didn't want it." |
| 1:10 | Whose arm is it | Carer enters: "Give her the pills." Diarization labels *speaker 2: carer*. Card: *Pills? (asked by carer)*. Nothing moves until the twitch. |
| 1:25 | The veto that teaches | The arm carries water near the laptop; a brace; freeze in [X] ms. Wonder investigates: SAM tracks the cup and laptop, *3 cm*. Rule proposed in the restrict-only language, checker proves it, arm rehearses empty-handed, twitch approves. |
| 2:05 | It remembers, and explains | Same request, long route. "Why?" The arm looks at the laptop: "Yesterday you braced when I carried water over it." |
| 2:20 | Grip without hands | "Something to write with": looks, nods, picks the marker; sustained effort sets the grip; the paper cup is picked later without crushing. |
| 2:35 | Numbers and failures | Twitches per task and seconds per task, Reflex vs scanning, n stated; top-1 and top-3 proposal accuracy; false twitch rate; one failure where it proposed the wrong thing and the brace fixed it. |
| 2:55 | End card | *Tested with able-bodied participants only. Built to be tested with the people it is for.* Repo. |

**The headline metric is twitches per task.** It is the keystrokes-saved of assistive robotics, it is easy to understand, and the baseline (row-column scanning at a fixed rate) is the real-world standard, so the comparison is fair and the number will be large.

---

## 8. How to be honest, and why it helps

- **Say who tested it.** Able-bodied participants with hands deliberately at rest are a proxy, not the population. Put it on the end card and in the README. Judges trust teams that say this; they distrust teams that imply otherwise.
- **Do not claim clinical readiness, medical benefit or FDA anything.** Claim: a working prototype of a control paradigm, with measurements, on hardware anyone can buy.
- **If you can, talk to one person who lives this, or one occupational therapist, before the deadline**, even for twenty minutes. Quote them with permission. It changes the entry from "we imagined users" to "we listened to one." If you cannot, say that too.
- **Keep the safety story loud.** An arm near a person who cannot move away is a hazard. The deterministic gate, taught poses, the brace veto, comfort coupling and latching stops are not boilerplate here; they are the reason the idea is allowed to exist.

---

## 9. What changes in the build

Most of the code already exists. The new work is a ranking layer, a head-direction channel, a twitch detector variant, and the Stage view cards.

| Piece | State | Work |
|---|---|---|
| Brace detector, freeze, incident capture, rules, routes, embodied tools, speech out | Built by the forks | Keep |
| Twitch | New variant of the squeeze detector: short hold, no effort payload; double-twitch within 600 ms | Half a day |
| Head direction | MediaPipe face mesh already a dependency; yaw → table third | Half a day |
| Intent completion | Spark 1.3 tool loop returning three ranked contracts; drop unreachable; cache scene between calls | One and a half days |
| Three-card Stage view with scanning fallback | UI | One day |
| Scanning baseline mode | Fixed-order menu stepped by double twitch; needed for the comparison | Half a day |
| Voice Transcribe with diarization; carer-request-needs-user-twitch rule in the gate | New ASR client; one gate rule | One day |
| Wonder investigator with SAM tracking, rule DSL, verifier, rehearsal | From `IDEA_REVIEW.md` §2.2–2.3 | Two days |
| Glimmer local narrator, Muse Image pictograms | Stretch | Cut first |
| Bench: twitches/task, seconds/task, top-k accuracy, false twitch rate | `h2m bench` additions | Half a day |

Cut order if behind: pictograms → Glimmer → audio-triggered proposals → the "why" beat → diarization. **Never cut:** intent completion with cards, twitch/brace, the scanning baseline comparison, the freeze.

---

## 10. Alternatives considered, in case this one does not sit right

| Direction | Impact story | Why it is second |
|---|---|---|
| **Reflex (this doc)**: intent completion for people with one controllable muscle | Daily independence for a clear population; a fair baseline to beat | — |
| **Teach by feel**: wear the band while *you* do a task; the robot learns how hard to hold each object class from your effort trace, then does it for fragile goods (eggs, glassware, produce) | Robots crush things; human grip knowledge transferred without programming | Closer to ForceBand's published work; the demo is "robot holds egg", which judges have seen |
| **Reflex library**: many users' braces and rules pooled into a shared, verified safety rulebook that any robot can load | Scales the flinch idea to a fleet | Hard to demo with one arm and two people; the impact is a promise, not a measurement |

Recommendation: Reflex. It is the only one where the measurement in the video *is* the impact.

---

## 11. Name and line

**Reflex.** It is the involuntary signal, and it is the robot reflecting your intent.

*Hands never moved. One muscle, one twitch.*

---

## 12. Scoring across a wider rubric, and the fixes that lift the low cells

Scores are 1–10, for Reflex as described in §1–11, then after the fixes in §13. Hackathon judges rarely publish their full rubric; this one is the union of what the event page says, what hackathon judges habitually reward, and what a robotics judge would check.

| # | Criterion | Before | After | What was weak |
|---|---|---|---|---|
| 1 | Real-world impact | 9 | 9 | Already the strength |
| 2 | Clarity in ten seconds | 8 | 9 | "Hands never moved" does the work; the jaw beat removes the last doubt |
| 3 | Creativity and originality | 7 | 8 | Shared autonomy is a known field; the generalization beyond the arm is the fresh claim |
| 4 | Technical difficulty | 8 | 8 | Already high; don't add more |
| 5 | Depth of Meta model use | 8 | 8 | Each model has a reason; fine |
| 6 | Breadth of Meta model use | 7 | 8 | Glimmer and Muse Image were stretch; one of them must ship |
| 7 | Demo wow factor | 6 | 8 | A slow small arm; the jaw electrodes and the non-arm action fix this |
| 8 | Evidence and measurability | 9 | 9 | Twitches per task vs scanning is the best metric in the field |
| 9 | Works reliably, twice in a row | ? | ? | Unknowable until the arm runs; the dominant risk |
| 10 | Feasibility in the time left | 5 | 7 | Too many beats for one builder; the cut list in §13 is mandatory, not optional |
| 11 | Generalizability beyond the demo | 6 | 9 | It reads as "an arm"; it should read as "a control layer for one-muscle users, the arm is one actuator" |
| 12 | Scalability and business plausibility | 6 | 8 | Commodity hardware plus open software; say the prices |
| 13 | Safety and ethics | 9 | 9 | Gate, veto, consent, honest population statement |
| 14 | Storytelling and emotional pull | 8 | 9 | One real voice from the population changes everything |
| 15 | Polish and UX | 5 | 7 | The Stage view is an operator UI, not a product; three cards and large type |
| 16 | Open-source and community value | 7 | 8 | Already open; add a "bring your own arm" backend note |
| 17 | Comprehensible to a non-robotics judge | 8 | 9 | The baseline counter is what a software judge understands |
| 18 | Fit to the stated themes | 9 | 9 | Both themes |
| 19 | Why Meta specifically | 7 | 8 | Diarization, video-in, open weights on device: make the "why not another provider" slide explicit |
| 20 | Honesty and trust | 9 | 9 | End-card disclosure, failure clips, n stated |

The cells that matter are 7, 10, 11, 12 and 15. Everything else is already near its ceiling or is decided by execution.

---

## 13. The five fixes

**Fix 1: one beat from the jaw (lifts 2, 7, 11).** For one demo beat, move two electrodes to the masseter. A jaw clench is a twitch; a long clench is a brace. Hands and forearms are fully visible and bare, so nobody can suspect a hidden hand movement, and it is the muscle real ALS users commonly keep longest. The detector is the same threshold code with a new calibration profile. Cost: an hour of calibration, and electrodes on your face for one take.

**Fix 2: one action that is not the arm (lifts 3, 11, 12).** Add a single non-arm tool to the intent completion: `send_message("Sam", text)` or `set_light(on)`. One card in the demo reads *Text Sam "come in 5 min"?* and a twitch sends it. The arm stops being the product; the product is intent completion for people with one muscle, and the arm is the actuator that happens to be on the desk. Cost: one tool, one card, half a day. This is the single highest-value change on the list.

**Fix 3: say the price (lifts 12, 16).** SO-101 follower arm about $300 in parts, Ganglion about $500, one webcam, a laptop. A card in the video and a line in the README: "under $1,000 of hardware, all open." Assistive arms today cost tens of thousands. Cost: nothing.

**Fix 4: three cards, large type, nothing else (lifts 15, 17).** The person's view is not the operator UI. Build a separate full-screen page: three cards, the top one highlighted, a thin EMG bar, a confidence word ("likely", "maybe"), and the current spoken sentence. No panels, no traces, no logs. The operator keeps the existing UI on a second screen. Cost: one day for the UI agent, and it replaces the Stage view work already planned.

**Fix 5: the mandatory cut list (lifts 10).** One builder, three days. Ship in this order and stop when time runs out:

1. Real arm taught and running a confirmed pick (nothing else matters first)
2. Twitch and brace, three cards, scanning baseline mode, and the counter
3. Intent completion ranking from scene and head direction
4. Fix 2 (non-arm tool) and Fix 1 (jaw beat)
5. Freeze → investigate → rule, reusing what the forks built
6. Diarized carer request
7. Glimmer local narrator **or** Muse Image pictograms, whichever is faster on the day; one of them ships
8. "Why" recall, audio-triggered proposals

Beats 1–4 make a winning video on their own. Beats 5–8 make it unbeatable if they work and cost nothing if cut.

---

## 14. What this cannot fix

- **Criterion 9.** No idea change makes an arm that has never moved reliable by Tuesday. Protect arm time above everything.
- **A one-person team against polished startup-style indie entries.** Reflex wins on substance and honesty, not finish. Don't try to out-polish; out-measure.
- **Judges who dislike assistive framing by non-disabled teams.** The end-card disclosure and one conversation with a real user or therapist are the only honest answers, and they are good ones.
