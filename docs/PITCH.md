# Flinch: how to present and market it at the Meta Global AI Developer Hackathon

Written November 1, 2026. Pairs with `IDEA_REVIEW.md` (why these choices) and `H2M_BUILD_PLAN.md` (how to build them). Everything here assumes gate G1 passed. If it did not, see §9 before writing a word.

The event page says the indie track is "judged on creativity, technical ambition, and whether it works." No video length, format or rubric is published, and Official Rules exist that we have not read. **Read the Official Rules before submitting** and adjust §8 to them.

---

## 1. The one-liner, the paragraph, the page

**Name:** Flinch
**Tagline:** You don't have to tell it. Your body already did.

**One sentence (for the submission title field):** A desk robot that stops when you flinch, investigates its own mistake with Muse Wonder and SAM 3.1, proves the fix is safe, and remembers it as a rule you can read.

**One paragraph (for the summary field):**
> Robots in homes will have to read the people around them, and speech is too slow: by the time you say "stop", the water is already over the laptop. Flinch gives an SO-101 arm a reflex channel. Forearm EMG catches the involuntary bracing that precedes any word, and freezes the arm in [X] ms with no model in the loop. Then Muse Wonder takes over as an incident investigator: it watches the last six seconds of video, uses SAM 3.1 to track the cup, the laptop and your hand frame by frame, measures the near miss, and proposes a rule in a small language that can only make the robot more cautious. A checker proves the rule is restrictive, the arm rehearses the new route with an empty gripper, and you approve with a squeeze. Next time, it takes the long way without being asked, and if you ask why, it tells you which flinch taught it. Measured on [n] people: [detection]% of flinches caught, [false] false stops per 10 minutes, [p50] ms software stop. Known failures are in the results.

**The page (README top) answers, in this order:** what it does in one sentence · the 20-second GIF of the freeze · the numbers table · how Meta's models are used (one line each) · how to run it · what is honestly not solved yet.

---

## 2. The story the judges should walk away with

Three sentences, in this order, repeated in the video, the README and the writeup:

1. **Speech is after the fact. The body is before.** Your muscles brace 100–200 ms before you can say anything.
2. **A flinch is a stop today and a rule tomorrow.** The robot investigates its own incidents and learns from them, under proof that it can only get safer.
3. **Nothing the model says can move a motor.** Taught poses only; the model chooses, the checker proves, the human approves.

If a judge remembers only one line, make it the tagline. If they remember a second, make it "the model proposes, the checker proves, the arm rehearses, the human approves."

---

## 3. The first ten seconds

Judges watch dozens of videos. The entry is decided before the title card.

**0:00–0:10, no narration, natural sound.** Overhead and side cameras. The arm is carrying the lidded cup. The person is typing, not watching. The arm's route bends toward the laptop. The person's shoulders jump. The arm freezes mid-air. Hard cut to black.

**0:10–0:13, title card:** *FLINCH. Stopped in 212 ms. No words spoken.* (use the measured number).

Then the narration starts. Everything before the title card has to be real footage with the proof overlays described in §4, or the rest of the video is not believed.

---

## 4. Proof: making an invisible reflex visible

A viewer cannot tell a flinch from acting, so the video has to carry its own evidence.

| Proof element | How |
|---|---|
| **The trigger was unpredictable** | The software randomizes which run takes the bad route and when. Show the config and the random seed on screen in the replay beat. Say out loud: "I did not know which run it would be." |
| **The EMG was live** | Burn the raw flexor and extensor traces into the frame with a shared timecode, not added in editing. Record the Stage view and the cameras to one timeline. |
| **The stop was fast** | A 240 fps phone on a tripod aimed at the gripper. In the replay, show the slow-motion stop with the EMG onset frame marked, and read the number off it. |
| **The person was not watching the arm** | Keep the person typing or talking to camera when the bad route begins, so the reaction is to peripheral motion, not anticipation. |
| **Nothing is faked** | A fixed lower-third on any re-enactment: *RE-ENACTED*. Any virtual-arm footage: *SIMULATION*. Never mix them with real takes without the label. |
| **Numbers have an n** | Every rate on screen carries its denominator: "17/20 flinches, 2 people". |

Make the raw footage downloadable from the repo. Judges who doubt can check.

---

## 5. The three-minute video, beat by beat

Record in beats, never one live take. Each beat is a real run; cut between them.

| Time | Beat | Picture | Words (narration, plain voice) |
|---|---|---|---|
| 0:00 | Cold open | Carry, flinch, freeze, cut to black | none |
| 0:10 | Title | *FLINCH. Stopped in [X] ms. No words spoken.* | none |
| 0:13 | Why | Slow-motion replay, EMG onset marked, 240 fps stop | "By the time you say stop, it's too late. Your forearm braces before you know you're going to. Flinch listens to that." |
| 0:35 | The freeze is dumb on purpose | Architecture card: EMG → freeze, no model, no network | "No model sits in this path. Four electrodes, a threshold, a servo write." |
| 0:45 | Comfort coupling | Second run: arm slows as the trace rises, then stops | "It isn't binary. Tense a little and it slows. Brace and it stops." |
| 0:58 | The investigator | Stage view: Wonder's tool calls appear one by one; SAM masks paint the cup, laptop, hand; a distance appears: *3 cm* | "Then Muse Wonder opens the case. It watches the clip, asks SAM 3.1 to track the cup, the laptop and my hand, and measures what happened." |
| 1:20 | The explanation | Arm looks at the laptop, speaks | Robot: "You braced as I carried water three centimetres over your laptop. Should I keep liquids away from it?" |
| 1:30 | Propose, prove, rehearse | Rule DSL on screen; checker: *restrictive ✓, prevents incident ✓*; arm dry-runs the new route, old path red, new path green | "It writes a rule in a language that can only say no. A checker proves the rule can't make it less careful, and replays the incident against it. Then it rehearses, empty-handed." |
| 1:52 | Approve | Squeeze; rule card saved with the clip thumbnail | "I squeeze. Now it's a rule, and it remembers which flinch taught it." |
| 2:00 | It remembers, it generalizes | Same request, new route, *rule 1 applied*; then a bottle takes the same detour | "Same job, long way round. And it knows a bottle is a liquid too." |
| 2:15 | Ask why | "Why the long way?" The arm looks at the laptop, answers, the rule card shows the clip | Robot: "Yesterday you braced when I carried water over it." |
| 2:25 | Who can teach it | Bystander says "stop": it stops. Bystander says "yes" to a rule: ignored, *speaker 2 is not the wearer* | "Anyone can stop it. Only the person wearing the band can teach it. Muse Voice Transcribe tells us who spoke." |
| 2:38 | It is still a companion | "Pass me something to write with": looks, nods, picks the marker; squeeze sets the grip; paper cup survives | "And it's still an assistant. Squeeze sets how hard it holds." |
| 2:48 | Numbers and failures | Results table with n; one failure clip: a sneeze stops it, and the corroboration says *no recoil, not learning* | "Two people, forty flinches. Here's what it caught, here's what it didn't, and here's a sneeze it stopped for but refused to learn from." |
| 2:58 | End card | Flinch · repo · *We couldn't find prior work on this. If you know some, tell us.* | none |

Cut order if the video runs long: companion beat → who-can-teach → generalization. Never cut: cold open, investigator, propose/prove/rehearse, numbers.

---

## 6. The writeup

Judges read after they watch. Structure it so a skimmer gets the same story.

1. **What it does** (the paragraph from §1).
2. **The numbers** (the table from §7, first, before any architecture).
3. **How Meta's models are used**, one line each, with what breaks without it:
   - *Muse Wonder*: the incident investigator. Takes the video, calls tools, proposes the rule. Without it: a canned "what bothered you?"
   - *SAM 3.1*: the measuring instrument. Tracks cup, laptop and hand across the clip; every distance in an explanation has a mask behind it. Without it: no grounded explanations, no recoil corroboration.
   - *Muse Spark 1.3*: the low-latency tool loop for requests, gestures and recall.
   - *Muse Voice Transcribe*: realtime ASR with diarization and keyword biasing; the reason only the wearer can approve a rule.
   - *Muse Glimmer* (if shipped): on-device narrator, no network.
4. **The safety argument**, in one diagram: freeze path with no model; models choose from taught options; rules verified monotone-restrictive; human approval; stops latch.
5. **What is new**, stated carefully: implicit feedback for robots has used faces and EEG error signals; we could not find prior work using muscle bracing as a live stop, nor any turning a reaction into a verified rule through an LLM. Cite the two prior-work papers.
6. **Known failures and limits**, with clips: habituation, false stops from large arm movements, Wonder latency, two participants only.
7. **What was built when**: a dated list of what was built during the event, so the contribution is unambiguous.
8. **Credits**: ForceBand, EMPATHIC, EEG error-potential work, ELEGNT, LeRobot, OpenBCI.

---

## 7. The results table

Fill every cell from logs. Leave a visible "not measured" rather than a target.

| Claim | Metric | Value |
|---|---|---|
| Catches flinches | caught / stimuli, 95% CI, per person, first vs second half | |
| Doesn't stop for nothing | false stops per 10 min of normal activity | |
| Stops fast | software p50 / p95 ms; physical stop ms from 240 fps | |
| Explanations are grounded | correct cause / incidents; distances within 1 cm of ruler | |
| Rules only restrict | proposals accepted / rejected by the verifier, with one rejected example | |
| Learns | repeat-mistake rate before vs after the rule, same task | |
| Generalizes | rule applied to an object not in the incident: yes / no | |
| Learns from reflexes, not noise | stops that became rules vs stops rejected by corroboration | |
| Gentle | crush / drop rate, fixed grip vs squeeze grip | |

Two or three people does not establish a general result. Say so, in the table caption.

---

## 8. Submission checklist

- [ ] Re-read the Official Rules; note any limit on video length, team size, or required Meta model use, and comply.
- [ ] Video: under three minutes, 1080p, captions burned in, natural sound under the narration, every re-enactment labelled.
- [ ] Repo: `README.md` with the §1 page; `docs/RESULTS.md` with §7 filled; raw footage and EMG recordings linked; pinned dependency versions; one-command launch for the virtual-arm demo so a judge without hardware can run the software story.
- [ ] A 20-second GIF of the freeze at the top of the README.
- [ ] One architecture image (the §6 diagram), exported from the Stage view, not drawn separately.
- [ ] The failure clips are in the repo, not only in the video.
- [ ] `MODEL_API_KEY` is not in the repo. Check the history.
- [ ] Submit by early afternoon on Nov 4. The deadline is 9:00 PM ET; do not use the margin.

---

## 9. If G1 failed

Do not pretend. The entry changes shape and is still worth submitting.

- The word "involuntary" leaves every document. The signal becomes "a deliberate brace", a gesture that needs no hand, no words and no line of sight.
- The tagline becomes: *Tense up, and it stops. Then it learns why.*
- The story leads with the investigator and the proof chain, which do not depend on the reflex. The cold open becomes the arm slowing as the trace rises, then freezing on a brace.
- The accessibility angle gets stronger, not weaker: a brace is a "no" for someone who cannot speak or point.
- Report the G1 numbers anyway, as a negative result with n. A judge who sees an honest failed experiment next to a working system trusts the working system more.

---

## 10. Marketing outside the judging room

Cheap, and it can reach a judge before the video does.

- **One post on X and one on LinkedIn**, the day before the deadline: the 20-second freeze GIF, the tagline, one measured number, the repo link. No thread. Tag nobody who did not ask to be tagged.
- **A single Hacker News "Show HN"** after judging ends on Nov 8, not before, so it cannot look like vote-gathering.
- **The repo is the landing page.** Most people who hear about it will land there, not on the submission portal.
- **Say what it is not.** "This does not read minds, decode EEG, or train a policy. It reads four electrodes and keeps a rulebook." Modesty is rare in hackathon posts and it reads as confidence.

---

## 11. Judge questions and the answers to have ready

| Question | Answer |
|---|---|
| How do I know the flinch wasn't acted? | Randomized trigger, burned-in live EMG, 240 fps, raw footage in the repo, and the person was typing. |
| Isn't this just an EMG stop button? | The stop is the cheap part. The new part is what happens after: investigation over video with SAM as a ruler, a rule language that can only restrict, a proof, a rehearsal, and consent tied to who is wearing the band. |
| What stops the model from learning something dangerous? | The grammar has no word for "allow", and the checker rejects any rule that does not shrink the permitted set. Here is one it rejected. |
| Why Muse Wonder rather than any VLM? | It takes the video itself, calls six tools in a loop, and holds the whole session in context so the robot can later answer "why". The explanation's numbers come from SAM, not from the model. |
| What's the latency of the explanation? | [measured] seconds. The arm is already stopped; the gestures cover it; the fallback is a spoken question. |
| Does it generalize across people? | Two or three people, re-calibrated per session. We say so. The study after the hackathon is in the plan. |
| What about habituation? | First-half vs second-half detection is in the table. The demo uses a fresh trigger. |
| Could this work on a bigger robot? | The freeze path is a few milliseconds of signal processing and a servo write; the rule machinery is robot-agnostic. We have only shown it on an SO-101. |
