# Idea review: can "Muse Embodied + Flinch" win, and what would make it win

Written November 1, 2026, against the facts on the event page and the Meta Model API docs as of today. Companion to `H2M_BUILD_PLAN.md` (what to build) and `PITCH.md` (how to present it).

---

## 1. The honest verdict

**As scoped today, I would not bet on it winning.** It is a good idea with a thin execution story and bolted-on model use. With the changes below, and a passed G1, it becomes a real contender for a top placing in the indie track. Here is the reasoning, so you can disagree with it.

### What the judges actually score

The event page gives no weights. The indie track is "judged on creativity, technical ambition, and whether it works." Every project must fit one of two themes: "Build for Your World" or "Push the Frontier", and the Frontier text names robotics directly: "give a machine a new capability ... unlock a better way to understand and control a physical system." The 30/25/25/20 weights in the plan came from a secondary source and should not drive decisions.

So the three questions a judge will ask, in order of how easily they can check them:

1. **Does it work?** Can I see a real arm stop on a real flinch, with proof it was not staged?
2. **Is it ambitious?** Is this harder than wiring a VLM to an arm, which many entries will do?
3. **Is it creative, and does it use Meta's models in a way I have not seen?**

### Scorecard of the current idea

| Question | Today | Why |
|---|---|---|
| Works | **Unknown** | G1 has not been run on a person. The only recording is simulated. Zero poses are taught. Nothing has moved the real arm through the runtime. |
| Ambitious | **Medium** | A threshold detector, taught poses and a JSON rule file. Solid engineering, but a judge can describe it as "EMG stop button plus a prompt." |
| Creative use of Meta models | **Weak** | Muse is a one-shot caption call after the stop. SAM 3.1 draws a highlight. Speech uses Parakeet, not Meta. Three of the six Meta model lines are unused, and the ones used are used generically. |
| Theme fit | **Strong** | Robotics, a machine gaining a new capability, a physical system. This is exactly the Frontier text. |
| Story | **Strong** | "Your body already said no" is a line judges will remember. |

### The five things that can sink it

1. **The flinch is invisible on video.** A viewer cannot tell a reflex from acting. If the first ten seconds look staged, nothing after them matters. The fix is proof overlays and an unpredictable trigger, covered in `PITCH.md`.
2. **G1 may fail.** The whole pitch rests on co-contraction showing up on forearm electrodes within ~150 ms. Nobody has measured it. If it fails, "involuntary" leaves the pitch and the entry becomes "a tense-up gesture", which is fine engineering and a weak story.
3. **The arm is slow and small.** An SO-101 at 30 deg/s carrying a lidded cup is not visually dramatic. Drama has to come from the person's reaction, the freeze, and the overlays, not from the robot.
4. **The learning is a lookup.** "It saved a rule" is a JSON write. Judges who build agents will see that. The rule path needs real machinery behind it: a verifier, a counterfactual, a rehearsal.
5. **Meta models look optional.** If a judge can imagine the same demo with any VLM, the "creative use" box is empty. Each model must do something only it does here.

---

## 2. The upgraded idea

Keep the loop. It is the right loop. Change what each stage is made of.

**One sentence:** a desk robot that stops when your body flinches, investigates its own mistake like an engineer, proves the fix is safe before it tries it, and remembers it as a rule you can read.

**The new capability, in the judges' words:** a machine that can be corrected by reflex, and that turns a reflex into a verified rule.

### 2.1 The freeze: unchanged, but graded

The freeze path stays model-free. Add one thing: **comfort coupling.** Below the brace threshold, the arm's speed scales down with sub-threshold co-contraction. You tense a little, it slows. You brace, it stops. This is a few lines on the existing `speed` field, it is honest, and it is the most visible thing in the video: the arm visibly hesitating as the person's trace rises.

### 2.2 The investigation: Muse Wonder as an agent, not a captioner

Today Wonder gets seven keyframes and returns a caption. The docs position Wonder for "long-horizon agentic work" that "calls tools in parallel." Use it that way.

On a freeze, Wonder runs as an **incident investigator with tools**:

| Tool | What it returns | Backed by |
|---|---|---|
| `get_clip()` | the actual 6 s video, both cameras | ring buffer (Wonder accepts video input) |
| `track(phrase)` | per-frame boxes and masks for "the cup", "the laptop", "the person's hand" | SAM 3.1 video tracking |
| `measure(a, b, t)` | table-plane distance between two tracked objects at time t | homography + SAM boxes |
| `arm_trace()` | joint path, held object, speed, route name | executor log |
| `body_trace()` | EMG flexor/extensor/brace at 20 Hz, onset time | EMG service |
| `propose_rule(dsl)` | accepted or rejected, with the verifier's reason | rule verifier (§2.3) |

Wonder's job is to produce a **grounded** explanation: not "you seemed worried about the laptop" but "at t=−0.4 s the cup's mask passed 3 cm above the keyboard while you braced; the laptop is the nearest object to the cup's path at onset." The numbers come from SAM, not from the model's imagination. The model does the reasoning and the tool orchestration, which is exactly the thing the docs say it is for.

Why this is unique: nobody we found uses a frontier model as a post-incident investigator over a robot's own sensor logs, with segmentation as a measuring instrument. It also makes the explanation checkable on screen: every number it says has a mask behind it.

### 2.3 The rule: proposed by Muse, proven by a checker, rehearsed by the arm

Replace "Wonder emits JSON, we save it" with three stages.

1. **Propose.** Wonder writes the rule in a tiny typed language, for example `when holding(liquid): forbid route crossing zone(laptop); max_speed 0.6`. The grammar admits only restrictions: forbid, slow, lift, loosen. There is no syntax for "allow".
2. **Prove.** A deterministic checker shows the rule is monotone-restrictive against the current rule set: the set of permitted routes, speeds and grips can only shrink. It also replays the incident clip against the new rule set and confirms the incident would have been prevented. If either check fails, the proposal is rejected and Wonder gets the reason back as a tool result.
3. **Rehearse.** Before the rule is saved, the arm **dry-runs the new route with an empty gripper**, at low speed, while the Stage view shows the old path in red and the new one in green. Then it asks: "This is what I'd do instead. Save it?" You squeeze.

This is the technical-depth beat. "The model proposes, the checker proves, the arm rehearses, the human approves" is a full sentence about safety that most agent demos cannot say. It also answers the obvious judge question, "what stops the model from learning something dangerous," with a proof rather than a prompt.

### 2.4 Corroboration: two bodies have to agree before it learns

EMG stops the arm. But a sneeze or a dropped pen also co-contracts. Before an incident becomes a **learning** event, require a second, independent signal of recoil from the user-facing camera: SAM 3.1 tracks the person's hand and shoulder in the clip, and the investigator checks that they moved away from the arm within the response window.

Stops stay cheap and frequent. Rules stay rare and corroborated. On screen: "Muscle: brace at 0.00 s. Posture: recoil at 0.12 s. Learning from this."

### 2.5 Speech: Meta end to end, with a local stop word

Replace Parakeet with **Muse Voice Transcribe** over the realtime WebSocket. Two features matter here, both stated in the docs:

- **Keyword biasing** on object names and the approval vocabulary, so "the lidded cup" is heard in a noisy room.
- **Speaker diarization.** Anyone can say "stop" and the arm stops. Only the wearer can approve a rule: a "yes" has to come from the diarized speaker who is also producing the squeeze. This is a small, memorable safety idea, and it is only possible because the ASR tells you who spoke.

Keep local stop-word spotting as the guaranteed fast path. A stop never waits on a socket.

### 2.6 Memory and explanation: ask the robot why

Wonder has a 1M-token context. Keep the whole session there: every incident, every rule, every clip's investigation. Then the embodied layer gets one new tool, `recall(question)`, and the demo gets one new line: "Why did you take the long way?" The arm looks at the laptop and says "Yesterday you braced when I carried water over it. Rule 3." The rule card on screen shows the clip it came from.

This is the "Build for Your World" theme done literally: the robot's memory is your incidents.

### 2.7 Stretch: Muse Glimmer on-device as the always-on narrator

If time allows, run the open-weight Muse Glimmer locally (llama.cpp on the Mac) as a low-latency scene narrator for the Stage view: one short line every second about what the arm is near. It uses the open-weights part of Meta's lineup, it needs no network, and it makes the robot feel awake between tasks. It is not on any safety path. Cut it first if behind.

### 2.8 What not to do

- **Do not use Muse Image to tick a box.** A generated illustration of a rule adds nothing and judges will see the padding. Use it only if a real need appears.
- **Do not add EEG back.** The plan's reasoning stands.
- **Do not claim "reads your mind", "first ever", or "understands emotion."** Say "we could not find prior work" and show the search.
- **Do not let any model near a joint value.** Taught poses only, forever.

---

## 3. Meta model map, before and after

| Model | Before | After |
|---|---|---|
| Muse Wonder | One-shot caption of keyframes | Agentic incident investigator with six tools over video, masks, traces; rule proposer; session memory |
| Muse Spark 1.3 | Intent parsing when grammar fails | Same, plus the embodied tool loop (`look_at`, `gesture`, `say`, `recall`) at low latency |
| SAM 3.1 | Static highlight mask | Measuring instrument: tracks cup, laptop and the person's hand across the clip; produces the near-miss geometry and the recoil corroboration |
| Muse Voice Transcribe | Unused (Parakeet) | Realtime ASR with keyword biasing and diarization; wearer-only approval |
| Muse Glimmer (open-weight) | Unused | Stretch: on-device scene narrator |
| Muse Image | Unused | Unused, deliberately |

Every row in the "after" column is something a judge cannot swap for a generic model without losing a feature: video-in investigation, mask-based measurement, diarized consent, open-weight local narration.

---

## 4. Ambition, restated for the judges

The technical claims the project can now make, each with a proof artefact:

| Claim | Proof |
|---|---|
| Stops on an involuntary brace before speech | Raw EMG with timestamp overlay, unpredictable trigger, slow-motion stop; G1 numbers with n |
| Stop latency is small and measured | Software p50/p95 from logs; physical stop time from 240 fps video |
| Explanations are grounded, not hallucinated | Every stated distance has a SAM mask and a frame number behind it |
| Learned rules cannot make the robot less safe | The verifier's monotonicity check, shown on screen, with a rejected proposal as an example |
| The fix is tested before it is trusted | Replay of the incident against the new rule, then the empty-gripper rehearsal |
| It learns from reflexes, not from noise | Corroboration: muscle plus posture, with a counter-example where a sneeze stops but does not teach |
| It knows who is allowed to teach it | Diarized approval |

---

## 5. Priority order

If you have to cut, cut from the bottom.

1. **G1 on a real person.** Nothing else matters until this is known.
2. Comfort coupling (half a day).
3. Investigator with tools over video and SAM tracking (the core upgrade; two days).
4. Rule DSL, verifier, replay and rehearsal (one and a half days; the verifier is simple, the rehearsal reuses taught routes).
5. Muse Voice Transcribe with diarized approval (one day; keep Parakeet as fallback).
6. Recall and session memory (half a day).
7. Posture corroboration (half a day once SAM tracking exists).
8. Glimmer narrator (stretch).

---

## 6. Risks the upgrade adds

| Risk | Mitigation |
|---|---|
| Wonder latency on a 6 s video clip is unknown; could be 10–30 s | The arm is already stopped and the gestures hide waiting; show a "thinking" beat; cap at one retry, then fall back to Spark on keyframes, then to the spoken question |
| SAM 3.1 video limits (length, object count) are undocumented | Downsample to 2 fps, 6 s, three phrases; test on day one |
| Voice Transcribe adds a network hop | Stop words stay local; approvals tolerate a second of delay |
| More moving parts, more ways for the live demo to fail | Record the video in beats, never as one live take; label every re-enactment |
| Rule DSL scope creep | Four verbs, one condition type. Done. |

---

## 7. Name

"H2M" is a repo name, not a product. Call the entry **Flinch**. One word, it is the demo's first second, and the tagline writes itself: *You don't have to tell it. Your body already did.*
