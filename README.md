# Armed

**Armed — a third arm that autocompletes you.**

*It watches the time, your room, your voice and your muscles, works out what you need, then acts, speaks, or checks with the people around you.*

Armed is a robot arm for a desk or a headboard that you almost never have to instruct. From the clock, the room, who is speaking and one muscle in your forearm, it proposes what you probably want next; a twitch says yes, a brace says no. If it gets something wrong while moving, the same brace freezes it with no model in the path; Muse Wonder then investigates the clip, proposes one restriction, a checker proves the rule can only make the arm more careful, and you approve it with a twitch. Measured against the one-switch scanning menu people with one controllable signal use today: [N] inputs per task instead of [M], n = TODO (not measured yet, see the table below). Built in nine days by one person working with AI coding agents, on open hardware (cost: TODO, bill of materials not totalled yet), with Muse Spark, Muse Wonder, SAM 3.1 and Muse Voice Transcribe doing the seeing, hearing and reasoning, and taught poses doing every motion.

> **Nothing a model says can move a motor.** Models rank, describe and propose. Motion is taught poses only, behind a pure-function gate, and the stop path (brace, open hand, physical e-stop) has no model and no network in it.

Film (2:30): TODO (link). GIF of the phone beat (20 s): TODO (`docs/media/phone.gif` is not recorded yet).

Entry for the Meta Global AI Developer Hackathon, solo, indie track. Status as of November 3, 2026: the software runs on the virtual arm (`h2m demo`, `h2m run`), and the SO-101 has been brought up with taught poses; the live Meta model calls are still being verified one by one, and most numbers below are not collected yet.

---

## The numbers

Every cell is a value with its n, or `TODO (not measured)`. A target is never written as a measurement. The full table, generated from the recorded sessions by `h2m bench --results`, is [docs/RESULTS.md](docs/RESULTS.md).

| Claim | Metric | Value |
|---|---|---|
| Less effort | inputs per task (selections + corrections) and seconds per task, Armed vs scanning, same person and task; published baseline beside it | TODO (not measured). Published scanning baseline (Simpson et al. 2011; Bonaker et al., CHI 2022): TODO |
| Proposals are right | accepted first time / proposals, by hour of the day | TODO (not measured) |
| Stops fast and locally | software p50/p95 ms; physical stop from 60 fps video | software: p50 2 ms, p95 2 ms, n=1, **self-test only** (synthetic EMG, virtual arm; not a result). Physical stop: TODO (not measured) |
| Doesn't stop for nothing | false twitches and false braces in ten minutes of typing | TODO (not measured) |
| Learns | the rule applied on the next carry; the tea taking the high route; a refusal that stopped a proposal recurring | TODO (not measured) |
| Transfers | second person: setup time to first task; their first refusal | TODO (not measured) |
| Costs | API calls and cost for the day | TODO (not measured) |
| Stop test (gate G1) | flinch test, caught / stimuli, target 80% | **Failed, two runs of mine.** Run 1: 1/2 and 0/2 caught (n=4 stimuli). Run 2: 2/4 caught (n=8 stimuli). Details under *Known failures and limits*. |

*Two people, one day; this does not establish a general result.* The only sessions recorded so far are one self-test (four trials, synthetic EMG, virtual arm, typed speech) that checks the tooling. Raw takes, the event log and the failure clips: TODO (URL).

---

## How it is executed

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

## Why these Meta models, and nothing swappable

| Model | Job | Why this one, and what breaks without it |
|---|---|---|
| **Muse Spark 1.3** | Ranks the three cards from the context packet; drives the embodied tool loop | Image in, tool calling out, under two seconds, which is the budget a card has before a person stops waiting. Without it: the fixed scanning order, which is the baseline being beaten |
| **Muse Wonder 1.0** | Investigates an interrupted action from the actual clip, calling tools in parallel; proposes the restriction; holds the day in context so the arm can answer "why" | Video input plus parallel tool calls plus a million-token window. Without it: a canned "something bothered you" and no rule |
| **SAM 3.1** | Finds every object, the hand and the face by phrase; tracks the cup, the laptop and the hand across the incident clip; is the ruler behind every centimetre in an explanation | Phrase-prompted, pixel-accurate, tracks across frames. Without it: no perception at all in a Meta-only build, and no grounded measurement |
| **Muse Voice Transcribe 1.0** | All speech, with speaker labels; the reason a carer can ask and only the wearer can say yes | Diarization in a streaming ASR. Without it: no consent by name |

Not used, on purpose: Muse Image (a pictogram per card would be padding), Glimmer (an on-device narrator is a fine idea with no beat to justify it this week). Not in the build: any trained policy. Meta offers no motor policy on the API, and a taught pick repeats on camera.

## What runs where

The target state of the submitted build. Every model the build invokes at runtime is a Meta model; everything else is conventional software. The EMG decoder is signal processing with no learned weights, and no learned policy is in the build.

| Component | What it is | Meta / conventional |
|---|---|---|
| Objects, hand, face (masks, tracking, measurement) | SAM 3.1, Meta Model API | **Meta** |
| Speech to text with speaker labels | Muse Voice Transcribe 1.0, realtime socket | **Meta** |
| Card ranking, free-form interpreter, embodied tool loop | Muse Spark 1.3, Meta Model API | **Meta** |
| Incident investigation, "why" answers | Muse Wonder 1.0, Meta Model API | **Meta** |
| EMG decoder: notch, band-pass, RMS, thresholds, onset rules (`h2m/sensors/emg.py`) | signal processing, no learned weights | conventional |
| Deterministic ranker, safety gate, rule checker, route chooser, scene diff | code | conventional |
| Executor, taught poses, LeRobot driver | robotics software | conventional |
| Scene-change detection (pixel difference, world tracker timers) | code | conventional |
| Three.js, the web UI, Electron | rendering, not AI | conventional |
| Learned motion policy | none | none in the build |

*This table is the target state. It is checked against the code and the dependency list before submission: TODO (not yet checked). Until then, the swap of the older perception and speech clients for SAM 3.1 and Muse Voice Transcribe is the part to verify first.*

## What is new, and what is not

**New, as far as I could find:** I couldn't find prior work with the one-muscle vocabulary where the veto and the reflex are the same signal under a proposal menu; a model that may only make a robot more conservative, proven, from video review, after approval; a refusal becoming a rule; consent by diarized name; or a selections-per-task measurement for a one-switch user on an arm task against scanning. That is a statement about my search, not a survey. Reference list with titles and links: TODO.

**Not new, and credited:**

- VLM intent inference with a confirm key: CASPER (CoRL 2025).
- EMG-controlled assistive arms for severe impairment: DLR EDAN.
- Ranked next-action menus: Naughton & Hauser (RA-L 2022).
- Interface-aware shared control with spinal-cord-injury users: Argall's lab.
- The third arm itself: supernumerary-limb research since 2014, and the desk arms Apple and Lenovo are building.
- The scanning baseline numbers: Simpson et al. 2011 and Bonaker et al., CHI 2022.

**Not claimed:** that it decodes thoughts, EEG or emotion (it reads four electrodes); that it is the first of its kind; that the stop is a reflex (see below); any result about the population it is designed for; any learned motion.

## Known failures and limits

- **The stop is a brace, not a flinch.** The flinch test (gate G1) is specified at 20 stimuli; I ran it on myself twice, with 4 and 8 stimuli, and both runs failed their verdict. The earlier simulated run (12 stimuli) passed, but it only verifies the tooling and says nothing about real responses.

  | Run | Stimuli | Caught (target 80%) | False stops | Median delay |
  |---|---|---|---|---|
  | 2026-11-01 01:31, mine | 4 | 1/2 (fast flexor spike), 0/2 (co-contraction) | 0 in 1.6 min; 1 in 1.6 min (6.2 per 10 min) | 583 ms |
  | 2026-11-01 01:36, mine | 8 | 2/4 (both detectors) | 1 in 1.6 min (6.4 per 10 min) | 601 ms |

  The delays include audio playback. Self-paced runs measure an acoustic startle, which may differ from reacting to the arm. One person, small n. Because the test did not pass, this write-up calls the stop a *brace* everywhere. The recordings are under `data/flinch/` on my machine and are not in the repository (the folder is git-ignored): TODO (publish).
- **False twitches while typing.** Typing uses the same forearm. The rate of false twitches and braces in ten minutes of typing is TODO (not measured).
- **Scan cadence.** SAM 3.1 runs on scene change, on events and every 10 s. An object moved between scans is stale until the next one; the gate refuses to move toward a stale target and the card says so. The call latency is on screen with every call; its distribution is TODO (not measured).
- **Wonder latency.** Muse Wonder takes the clip over the network. Its latency per investigation is TODO (not measured). The stop does not wait for it.
- **Unverified API shapes.** Some Meta API request and response formats (tool-call layout, SAM mask records, model ids) were written from the documentation and are marked UNVERIFIED in the code until a live call confirms them. Which are confirmed: TODO (checked before submission).
- **Two participants at most.** Setup time and first-refusal numbers for a second person are TODO (not measured). Two people establish nothing general.
- **Able-bodied demonstrator.** The demonstrator has hands at rest or busy. The bedside use is a design target, not tested with the people it is for.
- **Simulation is labelled.** `h2m demo` and the replay views run the scripted scenario on a virtual arm with a bundled synthetic sample; the screen says SIMULATION. Footage of the virtual arm in the film is labelled the same way.
- **One arm.** Shown only on an SO-101.

## What was built during the event

The dated component list, generated from `git log` and split into commits and the uncommitted working tree, is [docs/BUILT_DURING_EVENT.md](docs/BUILT_DURING_EVENT.md). Reused and credited below.

---

## Quick start

No arm, no key, no network: the software demo runs a scripted scenario on the virtual arm (SIMULATION).

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
```

```bash
.venv/bin/h2m demo
```

Open http://127.0.0.1:8080. `h2m demo --loop` repeats the scenario; `--port` changes the port. Resolved dependency versions are pinned in `uv.lock`.

The live runtime, still without hardware (virtual arm, synthetic EMG):

```bash
.venv/bin/h2m run --arm virtual --emg synthetic --vision off --speech off --llm off
```

With the Meta Model API, put the key in a git-ignored `.env` file as the environment variable `MODEL_API_KEY`, then check it with one call:

```bash
.venv/bin/h2m muse-check
```

With the real SO-101 and a Ganglion (needs `.[emg,arm]`, taught poses, and the hardware e-stop within reach; the runtime refuses without the flag):

```bash
.venv/bin/h2m run --arm lerobot --emg ganglion --i-have-the-estop
```

The real arm refuses untaught poses, needs the operator to arm it in the UI, and freezes on any fault. Bring-up of the SO-101, cameras and calibration: [docs/hardware](docs/hardware/README.md). The desktop shell that floats the Card over your work is in [desktop/](desktop/README.md). Benchmark report and the results table:

```bash
.venv/bin/h2m bench            # writes data/report.md
.venv/bin/h2m bench --results  # writes docs/RESULTS.md
```

Tests: `.venv/bin/python -m pytest -q`.

## Repository map

```text
h2m/            runtime: contract, gate, selection, rules, EMG decoder, executor, bench
web/            console and the Card (no build step; Three.js vendored)
desktop/        Electron shell for the Card
configs/        h2m.yaml (every threshold), poses.yaml, skills.yaml, routes.yaml
docs/           idea, pitch, compliance audit, film plan, hardware bring-up, results
```

The idea is [docs/ARMED_IDEA.md](docs/ARMED_IDEA.md); the model audit is [docs/COMPLIANCE.md](docs/COMPLIANCE.md); the film is [docs/DEMO_STORY.md](docs/DEMO_STORY.md).

## Credits

- **LeRobot** and the **SO-101** arm design (Hugging Face and TheRobotStudio): the arm, its driver, calibration and teleoperation, which the bring-up scripts wrap.
- **OpenBCI** (the Ganglion board) and **BrainFlow** (the acquisition library).
- **CASPER** (CoRL 2025), **DLR EDAN**, **Argall's lab**, **Naughton & Hauser** (RA-L 2022), and the scanning-baseline papers (Simpson et al. 2011; Bonaker et al., CHI 2022): prior work this builds on, as above.
- **Three.js** for the twin and the console; **Electron** for the desktop shell.
- **Meta**: Muse Spark, Muse Wonder, SAM 3.1 and Muse Voice Transcribe, through the Meta Model API.
- Built with AI coding agents (Claude Code); their definitions are in `.claude/agents/`.

---

## Submission checklist

Mirrors [docs/PITCH.md](docs/PITCH.md) section 6. Nothing is ticked until it is done.

- [ ] Read the signed-in Official Rules: video length, required fields, team size, required links; comply exactly.
- [ ] Video 2:30, 1080p, captions burned in, natural sound under the narration, the stamps and chips from the stage view, no re-enactments (or labelled if any).
- [ ] Repo: README with the summary at the top, the 20-second GIF of the phone beat, the numbers table, the four sections from `ARMED_IDEA.md`, `docs/RESULTS.md`, one-command launch for `h2m demo` (no arm, no key), pinned versions.
- [ ] Raw takes, the event log and the failure clips linked from the README.
- [ ] `MODEL_API_KEY` nowhere in the repo or its history.
- [ ] The "what was built" list checked against `git log` dates.
- [ ] The social cut exported separately (45 s, vertical, captions only).
- [ ] Submit with a two-hour buffer. The deadline is Nov 4, 9:00 PM ET.
