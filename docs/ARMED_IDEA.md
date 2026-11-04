# Armed: the idea

Written November 3, 2026. The one current description of what Armed is, how it is executed, why it uses the Meta models it uses, and what it claims. The write-up, the README and the submission text are built from this file. The demo that shows it is `DEMO_STORY.md`; the build state is `ACTIONS.md`, `PROACTIVE.md`, `UI.md`, `COMPLIANCE.md`.

---

## 1. What it is

**Armed — a third arm that autocompletes you.**

*It watches the time, your room, your voice and your muscles, works out what you need, then acts, speaks, or checks with the people around you.*

Armed is a robot arm that clamps to a desk or a headboard and does what a third hand does: lifts, holds, takes, brings, puts away. What makes it a third arm rather than a robot you operate is that you almost never tell it anything. It reads the context, proposes what you probably want next as a card, and you answer with one muscle: a twitch for yes, a double twitch for the next option, a brace for no. If something goes wrong while it moves, the same brace freezes it with no model and no network in the path. Then Muse Wonder investigates what happened from the video, proposes one rule, a checker proves the rule can only make the arm more careful, and you approve it with a twitch. From then on the arm behaves differently, and it can tell anyone in the room why.

Built in nine days, by one person, during the Meta Global AI Developer Hackathon.

## 2. Who it is for

Anyone whose hands are busy: the desk product that Apple and Lenovo are circling with a screen on an arm, done as a hand instead. And the same arm, clamped to a headboard, for people who cannot move or speak well, for whom a predictor plus one muscle is not a convenience but the whole channel. Meta's own accessibility research says forearm muscle signals persist in people paralyzed for years. Under a thousand dollars of open hardware.

The demonstrator is able-bodied, with hands deliberately at rest or busy. The bedside use is a design target shown honestly as one; the submission says so.

## 3. The claim, and the three pieces of evidence

The claim underneath the hook: **an arm that anticipates useful help and lets you decide with minimal effort, while you keep control.**

1. **It anticipates appropriately.** A proposal is seen emerging from real context, and a change in context is seen changing the proposal (a phone buzz makes one card; a jar held out replaces it).
2. **It reduces effort.** A task completes with fewer inputs (selections plus corrections) and less time than a one-switch scanning menu, the standard today for people with one controllable signal. Reported with n.
3. **Feedback changes behaviour.** An interruption, the approval of one specific restriction, and the next execution visibly different; the rule generalises (learned on water, applied to tea) and can be stated to someone else.

## 4. How it is executed

```text
 room ──► SAM 3.1 (phrase → masks: objects, hand, face; on change, events, 10 s) ─┐
 voices ─► Muse Voice Transcribe (realtime, diarized) ───────────────────────────┼─► context packet
 clock, routines, history, rules ────────────────────────────────────────────────┤
 forearm EMG ─► Armed decoder (filters, RMS, thresholds; no weights) ─► twitch / brace / effort
                                                                                  │
                                 Muse Spark 1.3: ranks ≤3 proposals from the packet (≤2 s)
                                                                                  │
                      the Card: proposal + the inputs that made it + the muscle meter
                                                                                  │
                      twitch = accept · double twitch = next · brace = refuse
                                                                                  │
                 Armed gate (pure function): trust ladder, consent by name, rules, reachability
                                                                                  │
                 Armed executor: taught poses only, speed coupled to muscle level ─► SO-101
                                                                                  │
   brace during motion ─► Armed stop controller (no model, no network) ─► freeze, latched
                                                                                  │
   incident clip ─► Muse Wonder 1.0 with tools (track, measure, propose_rule) ─► one restriction
                                                                                  │
   Armed checker: removes options only ✓ · replay: incident prevented ✓ · twin rehearses ─► card ─► twitch ─► rule memory
```

What runs where, in one sentence each:

- **Perception** is SAM 3.1 by phrase, on scene change, on events and on a slow cadence; objects, the hand and the face are masks with the age of their last scan. Conventional code does the scene diff and the hand-in-zone timing.
- **Hearing** is Muse Voice Transcribe over the realtime socket, with speaker labels, so a request from someone else becomes a card addressed to the wearer.
- **The body signal** is a four-channel OpenBCI Ganglion on the forearm and Armed's own decoder: notch, band-pass, RMS, per-user calibration, threshold and onset rules. Signal processing, no learned weights. One channel yields five meanings by timing and context: twitch, double twitch, brace, brace-during-motion, sustained effort.
- **Proposals** come from Muse Spark 1.3 over the context packet, through a deterministic ranker that falls back to a fixed scanning order if the model is slow or absent.
- **Permission** is a trust ladder enforced in one pure function: non-harmful things happen on their own (a lamp, a look); anything that moves is proposed and needs a twitch; a routine the person pre-approved announces itself and gives a five-second veto; a request from another voice waits for the wearer by name; model text never reaches a motor, a message or a stranger.
- **Motion** is taught poses only, interpolated at a capped speed, with one vision-derived pan angle for objects along the taught arc. No learned policy is in the build. The carry speed scales down as the forearm tenses before the brace threshold.
- **The stop** is the brace, the open hand and a physical e-stop, reaching the servo with nothing fallible in the path. It latches until reset.
- **Learning** is a restrict-only rule language (forbid, slow, lift, loosen; there is no word for allow), proposed by Wonder from the clip, proven monotone by a checker, replayed against the incident, rehearsed by the 3D twin, saved only on a twitch. A refusal becomes a "don't propose that" rule by the same machinery. Rules generalise through object tags.
- **Judgement** is Muse Spark 1.3, always on: on every change and a slow heartbeat it sees the frame, the conversation, the timeline, the arm and the setting (desk or bed), and may say one useful thing or put one action from the closed list to the person as a single question; one yes, squeeze or click confirms. Ambient speech is context, never a command; only the wearer can arm it by voice, anyone can stand it down. What runs and what was measured: `LIVE_RUNTIME.md`.

## 5. Why these Meta models, and nothing swappable

| Model | Job | Why this one, and what breaks without it |
|---|---|---|
| **Muse Spark 1.3** | Ranks the three cards from the context packet; drives the embodied tool loop | Image in, tool calling out, under two seconds, which is the budget a card has before a person stops waiting. Without it: the fixed scanning order, which is the baseline being beaten |
| **Muse Wonder 1.0** | Investigates an interrupted action from the actual clip, calling tools in parallel; proposes the restriction; holds the day in context so the arm can answer "why" | Video input plus parallel tool calls plus a million-token window. Without it: a canned "something bothered you" and no rule |
| **SAM 3.1** | Finds every object, the hand and the face by phrase; tracks the cup, the laptop and the hand across the incident clip; is the ruler behind every centimetre in an explanation | Phrase-prompted, pixel-accurate, tracks across frames. Without it: no perception at all in a Meta-only build, and no grounded measurement |
| **Muse Voice Transcribe 1.0** | All speech, with speaker labels; the reason a carer can ask and only the wearer can say yes | Diarization in a streaming ASR. Without it: no consent by name |

Not used, on purpose: Muse Image (a pictogram per card would be padding), Glimmer (an on-device narrator is a fine idea with no beat to justify it this week). Not in the build: YOLOE, MediaPipe, Parakeet, any trained policy.

## 6. What is new, and what is not

**New, as far as a search could find:** the one-muscle vocabulary where the veto and the reflex are the same signal under a proposal menu; a model that may only make a robot more conservative, proven, from video review, after approval; a refusal becoming a rule; consent by diarized name; and the first selections-per-task measurement for a one-switch user on an arm task against scanning.

**Not new, and credited:** VLM intent inference with a confirm key (CASPER, CoRL 2025); EMG-controlled assistive arms for severe impairment (DLR EDAN); ranked next-action menus (Naughton & Hauser, RA-L 2022); interface-aware shared control with spinal-cord-injury users (Argall's lab); the third arm itself (supernumerary-limb research since 2014, and the desk arms Apple and Lenovo are building). The scanning baseline numbers come from Simpson et al. 2011 and Bonaker et al., CHI 2022.

**Not claimed:** "reads your mind"; "first ever"; "understands emotion"; "involuntary" unless a twenty-stimulus flinch test passes; any result about the population it is designed for; any learned motion.

## 7. What was built during the event

Everything in the submitted build and the film, with dated commits: the EMG decoder and its calibration, the twitch and brace detectors, the card system and its selection state machine, the context packet and ranker, the Muse Spark, Wonder, SAM 3.1 and Voice Transcribe clients, the trust ladder in the gate, the incident buffer and investigation tools, the rule language, checker and route chooser, the taught-skill layer, the executor's speed coupling, the stop path and its reviews, the Card and the bedside screen, the console with its stage, context and replay views, replay mode and the hosted software demo, the bench and its metrics, the SO-101 bring-up, 31 taught poses, and this documentation. Reused and credited: the SO-101 design and LeRobot's driver and calibration, OpenBCI's board and BrainFlow, Three.js, Electron. The README's "what runs where" table lists every component with a Meta / conventional column.

## 8. The numbers it will report

Inputs per task (selections plus corrections) and seconds per task, Armed versus a scanning menu, same person and task, with n and the published baseline beside it · stop latency, software and physical from 60 fps video · proposals accepted first time, by hour · second-person setup time · false twitches during ten minutes of typing · API cost for the day. Every number with its n; failure clips linked from the README. Two people do not establish a general result, and the write-up says so.
