# Armed: presenting and submitting it

Written November 3, 2026, replacing the October 31 version. How the entry is written up, proven, submitted and talked about. The idea is `ARMED_IDEA.md`; the film is `DEMO_STORY.md`. The indie track is judged on "creativity, technical ambition, and whether it works"; reported weights are impact, use of Meta's tools, demo, technical depth. The signed-in Official Rules decide the submission fields and limits, so §6 is checked against them before anything is sent.

---

## 1. The three sentences

**Short:** Armed — a third arm that autocompletes you.

**Sub:** It watches the time, your room, your voice and your muscles, works out what you need, then acts, speaks, or checks with the people around you.

**One paragraph (the summary field):**
> Armed is a robot arm for a desk or a headboard that you almost never have to instruct. From the clock, the room, who is speaking and one muscle in your forearm, it proposes what you probably want next; a twitch says yes, a brace says no. If it gets something wrong while moving, the same brace freezes it with no model in the path; Muse Wonder then investigates the clip, proposes one restriction, a checker proves the rule can only make the arm more careful, and you approve it with a twitch. Measured against the one-switch scanning menu people with one controllable signal use today: [N] inputs per task instead of [M], with n stated. Built in nine days by one person, on under a thousand dollars of open hardware, with Muse Spark, Muse Wonder, SAM 3.1 and Muse Voice Transcribe doing the seeing, hearing and reasoning, and taught poses doing every motion.

## 2. What the judges should walk away with

Three sentences, repeated in the film, the README and the write-up, in this order:

1. **It proposes; you decide with one muscle.** The person's job is to say yes or no, not to explain.
2. **The body is the brake, and the brake teaches.** A brace stops it with nothing fallible in the path, and the stop becomes a proven, approved rule.
3. **Nothing a model says can move a motor.** Taught poses only; the model chooses, the checker proves, the human approves.

If a judge remembers one line, make it the short description. If a second, *"joint values from a model: zero."*

## 3. Proof: the film carries its own evidence

The film plan is `DEMO_STORY.md`; the proof devices it relies on are these, and each needs to be in the raw takes, not added in editing:

| Proof | Device |
|---|---|
| The hands never touched anything | Hands in frame in every beat: typing, under the blanket, on the jar, in the lap |
| The twitch was real | The muscle meter burned in from the stage view, same corner, every beat |
| The stop was fast and local | A 60 fps side camera on the carry with a ruler on the dummy laptop; the speed readout falling with the trace; the chip *Armed stop controller · no model* |
| The models ran live | Chips with the exact model ids and the measured latency on every call; the 2:05 frame with the day's real call counts |
| The learning is real | The investigation tool loop on screen, the checker's two ticks, the twin rehearsing beside the frozen arm, then the high route, then the tea |
| Nothing is staged | Real takes only; a failed beat is replaced by a labelled earlier take of the same beat or cut; the raw takes and the event log are in the repo |
| The numbers mean something | Every rate with its n and the published scanning baseline beside it; the failure clips linked |

## 4. The write-up

Judges read after they watch. Build it so a skimmer gets the same story.

1. **What it does** (the paragraph from §1).
2. **The numbers first** (the table in §5), before any architecture.
3. **How it is executed**: the diagram and the one-sentence-per-layer list from `ARMED_IDEA.md` §4.
4. **Why these Meta models and nothing swappable**: the table from `ARMED_IDEA.md` §5, with what breaks without each.
5. **What runs where**: the component table from `COMPLIANCE.md` §2, Meta / conventional column, under one sentence: the EMG decoder is signal processing with no learned weights, and no learned policy is in the build.
6. **What is new, and what is not**: `ARMED_IDEA.md` §6 as written, with the citations.
7. **Known failures and limits**, with clips: false twitches while typing, the scan cadence and its waits, Wonder latency, two participants only, able-bodied demonstrator, the straw and the brace described as a brace unless the flinch test passed.
8. **What was built during the event**: the dated component list from `ARMED_IDEA.md` §7, matching the commit history exactly.
9. **Credits**: LeRobot and the SO-101 design, OpenBCI and BrainFlow, CASPER, EDAN, Argall's lab, Naughton & Hauser, the scanning-baseline papers, Three.js, Electron.

First person singular throughout. No "we".

## 5. The results table

Fill every cell from logs; a visible *not measured* beats a target.

| Claim | Metric | Value |
|---|---|---|
| Less effort | inputs per task (selections + corrections) and seconds per task, Armed vs scanning, same person and task, with n; published baseline beside it | |
| Proposals are right | accepted first time / proposals, by hour of the day | |
| Stops fast and locally | software p50/p95 ms; physical stop from 60 fps video | |
| Doesn't stop for nothing | false twitches and false braces in ten minutes of typing | |
| Learns | the rule applied on the next carry; the tea taking the high route; a refusal that stopped a proposal recurring | |
| Transfers | second person: setup time to first task; their first refusal | |
| Costs | API calls and cost for the day | |

Caption: two people, one day; this does not establish a general result.

## 6. Submission checklist

- [ ] Read the signed-in Official Rules: video length, required fields, team size, required links; comply exactly.
- [ ] Video 2:30, 1080p, captions burned in, natural sound under the narration, the stamps and chips from the stage view, no re-enactments (or labelled if any).
- [ ] Repo: README with §1 at the top, the 20-second GIF of the phone beat, the numbers table, the four sections from `ARMED_IDEA.md`, `docs/RESULTS.md`, one-command launch for `h2m demo` (no arm, no key), pinned versions.
- [ ] Raw takes, the event log and the failure clips linked from the README.
- [ ] `MODEL_API_KEY` nowhere in the repo or its history.
- [ ] The "what was built" list checked against `git log` dates.
- [ ] The social cut exported separately (45 s, vertical, captions only).
- [ ] Submit with a two-hour buffer. The deadline is Nov 4, 9:00 PM ET.

## 7. The straw and the brace: words

The stop in the film is a brace. The word *flinch* and the word *involuntary* appear in the narration, the captions and the write-up only if a twenty-stimulus flinch test has passed before the shoot (`DEMO_STORY.md` marks the one line that changes). If it has not, the pitch is *a brace that teaches*, which is true, and still the best beat in the film. Report the flinch-test numbers either way, with n, as a result.

## 8. Marketing outside the judging room

- One post on X and one on LinkedIn the day before the deadline: the 45-second cut, the short description, one measured number, the repo link. No thread, no tagging anyone who did not ask.
- A Show HN after judging ends on Nov 8, not before.
- The repo is the landing page; most people who hear about it land there.
- Say what it is not, plainly: it does not read minds, decode EEG or train a policy. It reads four electrodes and keeps a rulebook that can only shrink what it is allowed to do.

## 9. Judge questions and the answers

| Question | Answer |
|---|---|
| Isn't this an EMG stop button on a pick-and-place? | The stop is the cheap part. The product is the proposal: context makes a card, one muscle answers, and the wearer keeps authority over everyone else's requests. The stop is what makes a proactive arm safe to have. |
| How is this different from CASPER, or EDAN, or a ranked menu? | It builds on all three and says so. New: one channel with five meanings where the veto and the reflex are the same signal; a model that can only make the robot more conservative, proven, from video, after approval; a refusal becoming a rule; consent by diarized name; and the first inputs-per-task number against scanning on an arm task. |
| What stops the model from learning something dangerous? | The rule language has no word for allow, and the checker rejects anything that does not shrink the permitted set. Here is a rejected proposal. And no model output ever becomes a joint value. |
| Why no learned policy? | Meta offers no motor policy on the API, and a taught pick repeats on camera while a 75-episode policy on its first day does not. The intelligence is in what to do and whether to do it, not in how to move. |
| Why Muse Wonder rather than any VLM? | It takes the video itself, calls six tools in a loop, and holds the day in context so the arm can answer "why" to the next person who asks. The numbers in its explanation come from SAM, not from the model. |
| Perception on a ten-second cadence? | Objects on a desk rarely move; a scene change, a sound, a hand entering the zone or a new voice triggers a scan at once. The gate refuses to move toward anything stale. Latency is on screen with every call. |
| Was the stop a flinch? | It was a brace, and the film says so, unless the flinch test passed. Either way the path has no model in it and the latency is measured on 60 fps video. |
| Does it generalise across people? | Two people, re-calibrated per session, four minutes to first task. The write-up says two people establish nothing general; the study after the hackathon is in the plan. |
| Tested with the people it is for? | No, and the end card says so. Able-bodied demonstrator, hands at rest or busy; the bedside scene is a design target. |
| Could this run on a bigger arm? | The stop is signal processing and a servo write; the gate, the rules and the proposals are arm-agnostic. Shown only on an SO-101. |
