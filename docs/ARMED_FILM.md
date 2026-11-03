# Armed: the idea, and a film where every second earns its place

Written November 3, 2026. This is the curated version of the idea and the demo. It builds on `DEMO_PLAN.md` (beats, streams, works rules), `REFLEX_CONCEPT.md` (why one muscle) and `WIN_ANALYSIS.md` (what wins). Where this file and `DEMO_PLAN.md` disagree, this file is the story and `DEMO_PLAN.md` is the build.

---

## 1. The idea in one breath

**Armed is a third arm.** It clamps to your desk or your headboard and does what a third hand would: lifts, holds, takes, brings, puts away. What makes it a third arm rather than a robot you operate is the predictor behind it. It watches the clock, the room, who is talking, where you look, what your hands are doing and one muscle in your forearm, and it guesses what you want next. It shows you the guess. One twitch says yes. A brace says no. If something goes wrong your body flinches before you've decided, and Armed stops with no model in the path, then learns a rule that can only make it more careful. It can lift things, take things from your hand, bring water to your mouth, speak to the people in the room on your behalf, send a message, turn a light off, and ask a person who isn't you to wait until you've agreed.

**Short description:** *Armed — a third arm that autocompletes you.*

**Sub-description:** *It watches the time, your room, your voice and your muscles, works out what you need, then acts, speaks, or checks with the people around you.*

**The line for the film:** *You never said a word to it. It knew. One muscle did the rest.*

Third arms have been promised for a decade; Apple and Lenovo are building desk arms now with a screen on the end. Nobody has shipped one that acts like a hand, because nobody solved how you'd tell it what you want when your own hands and voice are busy, or when you can't move at all. Armed is that arm, and the one-muscle predictor is how you talk to it. The film should make a viewer feel both: an arm that is useful, and an arm that needed no instructions.

---

## 2. What it senses, what it does, and when it is allowed to

### Inputs the predictor fuses

| Signal | What it contributes | Shown on screen as |
|---|---|---|
| Clock and history | "08:00, you did water then pills yesterday" | an evidence line under the card |
| Scene (overhead and wrist camera, YOLOE and SAM 3.1) | what is where, what moved, what is out of place, who is in the room | masks on the replay, the out-of-place card |
| Voice (Muse Voice Transcribe, diarized) | who said what; a request from someone else becomes a card addressed to you | *Priya asked: …* |
| Head direction and hands (MediaPipe) | where you glanced; a hand held out; hands on the keyboard | *you glanced at it* · *hand out* |
| Forearm EMG (Ganglion) | twitch = yes, double twitch = next or send, brace = no, brace during motion = freeze, sustained effort = grip | the muscle meter |
| Sounds in the room (mic) | phone buzz, knock, a kettle | *heard a knock* |

### Outputs

Pick and place · hold at eye level · take from your hand · place near your hand · straw to mouth · look, nod, shake, tilt, wave, bow · speak a short line to you · speak a short line to someone else for you · send a message from a taught list · switch a lamp · ask and wait.

### The trust ladder (this is what makes it safe to be bold)

| Rung | Armed may | Example |
|---|---|---|
| **Acts alone** | only when nothing can be harmed: no motion, no message | lamp on when you sit down; look at you when you speak |
| **Proposes** | anything that moves or leaves the room, as a card; nothing happens without a twitch | show phone, take the mug, text Priya |
| **Asks the named person** | when the request came from someone else | *Priya asked: phone. Waiting for you.* |
| **Never** | free text from a model to a motor, a message, or a stranger; any rule that makes it less careful | — |

The freeze path never touches a model or the network. Every motion is a taught pose. Every rule passes a checker that proves it only restricts.

---

## 3. What is new here, filed under what the judges score

The indie track is judged on "creativity, technical ambition, and whether it works," and the reported rubric weights impact, use of Meta's tools, the demo and technical depth. Every item below names the thing we do that we could not find elsewhere, the beat that shows it, and the prior work it stands on, so no claim is wider than the evidence.

### Creativity: the interaction is the invention

| What we do | Why it is new | Stands on |
|---|---|---|
| **Prediction as the interface.** The robot proposes the next action from the clock, your history, the scene, who is speaking, where you look and what your hands are doing; you only answer. It is autocomplete for physical help. | VLM intent inference exists for teleoperation with a keyboard confirm (CASPER) and ranked action menus exist for joysticks (Naughton–Hauser). Nobody fuses time, routine, diarized speech and body pose into proposals for a person whose only reply channel is one muscle. | CASPER (CoRL 2025), Naughton & Hauser (RA-L 2022), SpeakFaster for AAC text |
| **One muscle, five meanings.** On a single EMG channel: a twitch is yes, a double twitch is next or send, a sustained brace is no, a brace during motion is a freeze, and sustained effort is grip strength. The same contraction means different things depending on timing and on what the robot is doing. | Co-contraction is used as a mode switch in prostheses and as a stop in one patent. A graded, time-structured vocabulary where the veto and the reflex are the same signal under a proposal menu is not in the literature we found. | Myoelectric co-contraction switching; US 10817058; DLR EDAN for EMG-controlled assistive arms |
| **The body is the safety layer.** Bracing during motion freezes the arm with no model and no network in the path, and the freeze is then investigated and turned into a rule. | Implicit feedback for robots has used faces and EEG error potentials, offline or in binary tasks. We found no assistive arm that uses a muscle brace during motion as a live stop, nor any that turns the reaction into a verified rule. | EMPATHIC (facial), Salazar-Gomez (EEG ErrP), Kirschner's Expectable Motion Unit (predicts startle rather than sensing it) |
| **Rules that can only restrict.** Muse Wonder proposes a rule in a four-verb language with no word for "allow"; a checker proves the permitted set only shrinks and replays the incident against it; the arm rehearses before you approve. | FEAST whitelists what an LLM may edit; the preference-learning work uses an LLM judge. The asymmetry "the model can only make the robot more careful, proven, from video, after approval" is ours. | FEAST (2025), Shankar et al. (RO-MAN 2026) |
| **Learning from a refusal.** A brace on a proposal becomes a restrict-only "don't propose that" rule, so the robot gets quieter where it was wrong. | Rejection is usually discarded; here it is the same rule machinery pointed at the proposer. | — |
| **Consent by name.** Anyone can ask; only the person wearing the band can say yes, and the robot says who it is waiting for. | Diarized ASR as a consent mechanism for a shared robot is, as far as we found, unused. | Muse Voice Transcribe diarization |
| **A third arm that reaches past its gripper.** The same arm, gate and twitch also speak to a visitor for you, text your roommate, and turn a lamp off. | Assistive and supernumerary arms stop at the end of the gripper. Armed is the arm plus the channel, so the arm's reach extends to the room. | Supernumerary-limb research, which notes the control interface is the unsolved part |
| **A whole day without a word.** The counter that never leaves zero is the argument. | Framing, not technology, but it is what a judge will remember. | — |

### Technical ambition: the hard parts, each with a proof artefact

| Hard part | What we built | How a judge can check it |
|---|---|---|
| Multimodal fusion into ranked proposals under a two-second budget | Muse Spark 1.3 tool loop over scene, head direction, diarized speech, routine history and rules; unreachable candidates dropped before display; scanning order as the fallback | Top-1 and top-3 accuracy over the day, first ten vs last ten proposals; the evidence line under every card |
| A model-free freeze path with measured latency | Causal EMG pipeline, co-contraction onset rule, stop latched in the executor; no lock, sleep, disk or socket between onset and the servo write | Software p50/p95 from logs; physical stop time from 60 fps video with a ruler; missed stops 0 of n |
| Grounded explanation, not a caption | Wonder runs tools (`track`, `measure`, `arm_trace`, `body_trace`, `propose_rule`) over the actual clip; SAM 3.1 tracks the cup, the face and the hand; every distance in the explanation has a mask and a frame behind it | The on-screen tool loop; the ruler in frame agreeing with the measured distance |
| Verified learning | Four-verb restrict-only DSL; monotonicity checker; counterfactual replay of the incident; empty-gripper rehearsal | A rejected proposal shown on screen; the rule set only ever shrinking the permitted routes and speeds |
| A trust ladder enforced in one deterministic gate | Acts-alone rung limited in config to non-motion, non-message actions; proposes for motion; asks the named person for third-party requests; models never produce joint values | The gate is a pure function with tests; the lamp beat vs the Priya beat |
| Graceful degradation | Offline: scanning menu, local stop words, twitch and brace unchanged | The router pull on camera |
| Per-person setup in minutes | Named EMG profiles, a four-prompt calibration | The second person's stopwatch |
| A real arm near a face | Taught `face_near` as a hard distance cap, flinch stops the approach, reviewed relaxation with its own envelope | The straw beat, the ruler, the safety review record in the repo |

### Whether it works: what we will have measured

Selections per task and seconds per task against a single-switch scanning menu, same person, same task, with the published scanning baseline beside it (row–column scanning averaged 4.9 selections per minute at 2.2 switch hits each in a CHI 2022 study). Stop latency, software and physical. Proposal accuracy across the day. False twitches during ten minutes of typing. Second-person setup time. API cost for the day. Every number with n, and the failure clips linked from the README.

### Impact: who this is for

A third arm for everyone whose hands are busy: the desk product Apple and Lenovo are circling with a screen on an arm, done as a hand instead. The same arm, clamped to a headboard, for the millions who cannot move or speak well, for whom a predictor plus one muscle is not a convenience but the whole channel; Meta's own accessibility research says forearm muscle signals persist in people paralyzed for years. Under a thousand dollars of open hardware today. The arm is the product; the one-muscle channel is why it works, and it carries to any arm.

### Use of Meta's models: nothing swappable

Spark for the proposal loop under latency; Wonder for the investigation over video with parallel tools and the day's memory; SAM 3.1 as the measuring instrument; Voice Transcribe's diarization as the consent mechanism; Muse Image for the pictogram on each card; Glimmer on-device if it ships. Remove any one and a beat in the film stops working.

### What we do not claim

Not the first VLM to propose actions (CASPER). Not the first EMG-controlled assistive arm (EDAN). Not the first ranked action menu (Naughton–Hauser). Not the first third arm. Not "reads your mind", not "involuntary" until a twenty-stimulus flinch test passes, not tested with the population it is designed for. Those sentences go in the README as written.

## 4. The story device

**One day. Zero words.** A clock in the corner, and two counters that only ever go up: *selections* and *words spoken to the robot*. The second counter never leaves zero. The day starts badly (a sick morning in bed), gets ordinary (a desk, busy hands, a call), fills with other people (a roommate, a friend, a knock at the door), loses the internet, and ends with the lights going out. Every beat opens on a situation, shows the guess, shows the twitch, and ends on the thing done. No beat is longer than fifteen seconds. The robot speaks maybe six short lines all day, and each one lands.

Cast: you; a roommate (call her Priya in the script, use the real name on the day); a friend who has never worn the band; one voice at the door. Locations: the bed with the clamp on the headboard, the desk. Props: phone in a rubber case, lidded cup with a straw, pill tray, a mug, the pen cup, a lamp on a smart plug, a second phone to receive messages.

---

## 5. The film, second by second

Captions are burned in; the narration is your own voice, calm, used only where marked. Speed ramps on carries are allowed and labelled *2×* once. Everything is a real take; replay mode is the understudy for any beat that fails on the day.

| Time | Clock | What happens | On screen | Why it hooks |
|---|---|---|---|---|
| 0:00 | — | Black. Keyboard sound. A phone buzzes. Cut to overhead: two hands typing, a phone face-down. A card slides in: **Show you the phone?** with the line *buzzed 2 s ago · you glanced at it · 11:02, you usually look*. | The card, nothing else | A machine just explained a guess about you, and you haven't been introduced |
| 0:06 | — | The muscle meter spikes once. The fingers never stop typing. The arm lifts the phone to eye level. | *One muscle said yes.* | The hands never moved; that's the whole thesis in four seconds |
| 0:11 | — | A double twitch. The phone goes back. The counters appear: *Selections 2 · Words spoken to the robot 0.* Title: **ARMED** *a third arm that autocompletes you* | Title | The second counter is the promise for the next three minutes |
| 0:15 | — | Narration: "It guesses what I want from the time, the room, who's talking, where I'm looking, and one muscle in my arm. It never moves without asking. Here's one day. It started badly." Clock snaps to **07:58**. | Clock | A story is starting, not a feature list |
| 0:24 | 08:00 | Bed. Low light. The clamp on the headboard. You under a blanket, hands still. No input at all. A card appears on its own: **Water, then pills? Like yesterday.** *08:00 · you did this yesterday · tray is here.* Twitch. The straw comes to your mouth (slow, from below). You drink. | *It remembers your mornings.* | Nothing was asked. It proposed from the clock and the past |
| 0:36 | 08:04 | The pills. The arm approaches again; this time your shoulders jump, the forearm trace spikes on both channels, and the arm freezes mid-air. Robot: "Sorry." Hard cut to slow motion: the two traces, the freeze, a ruler in frame, *stopped at 9 cm*. | *Stopped in [X] ms. No model in the path.* | The first thing it did wrong, and the body caught it before the person could |
| 0:48 | 08:05 | The screen: Wonder's investigation runs as a visible tool loop. *track(cup, face) · measure → 9 cm, 14 cm/s · propose: approach face slower, stop at 12 cm · verifier: restricts only ✓ · rehearsal on the virtual arm.* A card: **Approach slower from now on?** Twitch. The next approach is visibly gentler. | *Every flinch becomes a rule. Rules can only make it more careful.* | A robot that investigates its own mistake and proves the fix before asking |
| 1:02 | 08:10 | Priya in the doorway: "Can you give him his phone?" A card: **Priya asked: phone.** Robot, to her: "Waiting for Pranay." The arm does not move. A beat. Twitch. The phone comes. | *Others can ask. Only you can say yes.* | Consent, shown with a second person, in ten seconds |
| 1:12 | 08:12 | Priya has left. A card: **Tell Priya: "I'm okay, going back to sleep"?** Double twitch. Cut to Priya's phone buzzing in the kitchen with that message. | *It speaks for you.* | The arm's reach just left the room |
| 1:21 | 11:00 | Desk. You sit down. The lamp comes on by itself; the arm looks up and nods. No card, no twitch. | *When nothing can be harmed, it just does it.* | Shows the trust ladder without explaining it |
| 1:29 | 11:20 | You're on a video call, talking. Hands typing. You hold an empty mug out to the side without looking. Card: **Take the mug?** *hand out · mug empty.* Twitch mid-sentence. The arm takes it from your hand, waits for your fingers to let go, puts it away. | *Hands busy. Voice busy. One muscle free.* | Voice-controlled robots can't do this beat; that's the point of it |
| 1:43 | 14:00 | Overhead. The pen cup gets knocked to the wrong spot by your elbow. Thirty seconds later (clock jumps) a card: **Pen cup looks out of place. Put it back?** with a SAM mask on the replay. You brace. The card dismisses. The next day's card would not come: *learned: pen cup can live there.* | *It guesses. You veto. It learns that too.* | A wrong guess, corrected, and the correction is itself a rule |
| 1:56 | 16:00 | A knock. The screen: *heard a knock*. Card: **Say you're coming?** Double twitch. The speaker, toward the door: "He's coming, one minute." | *It hears the room.* | It just talked to a stranger for you |
| 2:05 | 16:30 | You unplug the router on camera. The status chip flips to *offline*. The cards stay (now in scanning order). Twitch: an action still runs. A brace: it still stops. | *No internet. Still yours.* | Pre-empts the question every judge has |
| 2:15 | 18:00 | Your friend sits down, has never worn the band. Timelapse: pads on, four prompts, the first card, a twitch, the arm hands him the block. A stopwatch stops. | *Second person: [m:ss] to first task.* | Proof it is not tuned to one body |
| 2:27 | 22:30 | Bed again. Lights low. A card: **Lights off?** Twitch. Darkness. Robot, quietly: "Night, Pranay." | — | The day closes on the smallest, kindest thing it does |
| 2:34 | — | Numbers card, on black: selections vs scanning menu with n; stop latency p50 and physical; top-1 rising across the day; second-person setup time; cost per day of API calls. Then the counters, final: *Selections [N] · Flinches 1 · Rules learned 2 · Words spoken to the robot 0.* | Numbers | The counters pay off |
| 2:46 | — | Narration: "People have promised a third arm for a decade. This is one. Nobody solved how you'd tell it what you want when your hands are busy, or when you can't move. Your body already knows. We just listened." End card: **Armed** · repo · *Able-bodied demonstrator. Taught poses only. n stated. Failure clips linked.* | End card | The claim, then the honesty, then silence |
| 2:58 | — | Black. The phone buzzes once more. | — | A smile on the way out |

Optional insert, anywhere after 1:00, twenty seconds: a real user or occupational therapist, on a phone call, quoted with permission. If it exists it goes before the numbers.

### The 45-second vertical cut (for the community vote)

0:00 phone-while-typing (6 s) · 0:06 title (3 s) · 0:09 straw, flinch, freeze, "Sorry" in slow motion (10 s) · 0:19 the rule card and the gentler approach (8 s) · 0:27 Priya asks, "Waiting for Pranay", twitch (8 s) · 0:35 lights off, "Night, Pranay" (5 s) · 0:40 counters: *Words spoken to the robot: 0* (5 s). No narration. Captions only.

---

## 6. What makes every second work

- **Show the guess before the action, every time.** The card with its evidence line is the intelligence made visible. Without it, the arm is a remote-controlled toy.
- **The hands are always in frame and always busy or still.** Typing, under a blanket, holding a mug, on a call. The viewer must never be able to suspect a hidden button.
- **The muscle meter is in every beat where a twitch happens**, small, same corner, so by the third beat the viewer reads it without thinking.
- **The robot has a voice and uses it sparingly.** "Sorry." "Waiting for Pranay." "He's coming, one minute." "Night, Pranay." Four lines carry the character.
- **The arm is always doing something a hand would do.** Lift, hold, take, bring, put away, switch off. Never a gesture for its own sake, never a screen on a stick.
- **Other people make it real.** A roommate, a friend, a stranger's knock. Assistive demos are usually one person and a robot; a household is a different story.
- **Mistakes are in the film on purpose.** The flinch, the wrong pen-cup guess. A system that is seen to be wrong and corrected is trusted more than one that is never wrong.
- **The counters are the spine.** Selections go up; words stay at zero. The payoff is a number.
- **Nothing is faked and the film says so.** Speed ramps labelled, re-enactments labelled, a disclosure card at the end, raw takes in the repo.

---

## 7. What each beat needs

Status against the code as of commit `1d99c31`: **have** (exists, needs a take), **build** (software work), **teach** (a pose on the arm), **review** (a reviewed relaxation that lives on the virtual arm until `safety-reviewer` approves), **cast** (another person).

| Beat | Needs |
|---|---|
| Phone while typing | have (cards, twitch, double twitch); teach `phone_hold`; review (hold near face); phone on `slot_2` |
| Morning card from clock and history | build: routine memory in the ranker (time of day, last days of tasks) and the no-input proposal; teach straw pair; review (face approach) |
| Flinch and freeze at the face | have (brace, freeze, after-stop window); the taught `face_near` pose is the hard distance cap, the flinch stops the approach; 60 fps side camera with a ruler |
| Wonder investigation and rule | have (incident capture, Wonder client, rules, routes); build: visible tool loop (`track`, `measure`, `propose_rule`), restrict-only verifier shown passing, virtual rehearsal before the approval card |
| Priya asks, waiting for you | build: Muse Voice Transcribe with diarization, the "asked by" card, the named-wait gate rule; cast |
| Message to Priya | build: `send_message` from a taught list through the gate (Messages on the Mac via AppleScript, or a Twilio number); cast's phone |
| Lamp on when you sit | build: `set_light` on a smart plug through the gate; the "acts alone" rung limited to non-motion, non-message actions, in config |
| Take the mug mid-call | have (take-this two-stage plan, gripper-load release); review (hand spot); teach `handover_above/release` |
| Pen cup out of place, vetoed, learned | build: out-of-place proposal from the world tracker; refusal becomes a restrict-only "don't propose" rule |
| Knock, "He's coming" | build: a local sound-event detector (energy spike plus Spark's label from a 2 s clip, or Wonder audio) → card; `say_to_room` tool with taught lines |
| Offline | have (scanning fallback, local stop); film the router pull |
| Second person | cast; the calibration flow timed on camera |
| Lights off, "Night" | `set_light`; `say` |
| Numbers | build: bench additions (scanning condition, top-1 by hour, second-person setup time, cost) |

Hardware time the film needs: teach six to eight more poses (about an hour), the headboard re-teach (ninety minutes), and two clean runs of each of fourteen beats before a take. Protect that time above everything else; it is the only resource that cannot be bought back.

---

## 8. The minimum film, if beats fall

If only five beats survive, these five keep the story and all three claims: phone while typing · flinch and rule · Priya asks, waiting for you · offline · lights off and the counters. Everything else is addition, not foundation.

---

## 9. Claims and the words to avoid

- Say *involuntary* only if a real flinch test with at least twenty stimuli passes. Until then the straw beat is a *brace*, and it is still the best beat in the film.
- Say "we couldn't find prior work on" the one-muscle vocabulary, restrict-only learning from video review, and the selections-per-task measurement. Credit CASPER, EDAN, Argall's lab and Naughton–Hauser on screen in the writeup.
- Never say "reads your mind", "first ever", "understands emotion", or anything about EEG.
- The demonstrator is able-bodied; the end card says so. The bedside scene is a design target, shown honestly as one.
