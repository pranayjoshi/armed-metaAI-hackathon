# Armed: the live runtime

What runs on the real SO-101 with every Meta model live, and what was measured on Nov 4. The design is `ARMED_IDEA.md`.

## 1. Start it

```bash
~/miniforge3/envs/lerobot/bin/armed run --profile live --i-have-the-estop
```

The bed setting adds its own overlay on top: `--profile live,bed` (bed poses, bed objects, bed routines). Open the console, then the **Mind** view (second icon in the dock).

The `live` profile (`configs/live.yaml`) turns on:

| Part | Live as | Notes |
|---|---|---|
| Arm | the real SO-101 through LeRobot | starts **disarmed**; arm it by voice or with the Arm button |
| Camera | SAM 3.1 scans of the overhead camera | until `armed calibrate table` runs, an object SAM sees takes its taught slot's position, marked *assumed* |
| Mic | Muse Voice Transcribe 1.0, diarized | |
| Thinking | Muse Spark 1.3, always on (`armed/think.py`) | see §2 |
| Cards | Muse Spark 1.3 ranks every menu | |
| Stops | Muse Wonder 1.0 investigates every brace-stop | |
| EMG | the OpenBCI Ganglion on the forearm, Armed's decoder | twitch, double twitch, squeeze, brace |
| Routines | desk routines, quiet hours lifted | `configs/routines.yaml` keeps 23:00–07:00 for normal use |

The key comes from the git-ignored `.env`. Run from your own terminal: macOS grants it the camera and mic.

## 2. The thinker: Muse, always on

Armed is not a voice assistant. It never asks what you need. It watches, listens and judges, and either says something useful or proposes the one action you are about to need.

**When it thinks.** On every change, about a second after it:
- something said near it;
- a scene change (a hand held out, a new object, someone sitting down);
- a sound;
- a finished task;
- a stop or brace;
- a refused card.

Otherwise it thinks every 25 s. Each turn is one Muse Spark 1.3 call with the camera frame.

**What it sees** (the JSON beside the frame):
- **setting:** desk or bed, with both settings described, so the same judgement works at the desk and at the headboard;
- **now:** the context packet (clock, objects, who is present, the hand, sounds, last actions, routines due, rules);
- **heard:** the last 3 minutes of conversation, with who said it and whether it was addressed to Armed by name. The robot's own voice is filtered out.
- **recent:** a 10-minute timeline (what was seen, done, refused and stopped, and what it said and suggested);
- **robot:** armed or not, gripper empty / holding / unknown, what is waiting for you, which cards are up;
- **said_recently and refused,** so it never repeats itself or re-proposes a refusal;
- **options:** the closed list of actions it may suggest, built from the scene and the taught skills. It contains no gestures.

**What it may do**, and the rules on each:

| Output | Where it goes | Manners |
|---|---|---|
| **notice** | the Mind view's thought stream | always shown |
| **comment** | spoken aloud | at most one every 40 s (an answer to "Armed, …" is exempt), never while moving or waiting for you, never twice, never in quiet hours or after "stop proposing" |
| **suggest** | becomes a request: gate → spoken question → **one** yes, squeeze or click | at most one every 20 s; only when the arm is armed and idle; if the gate holds it, Armed says why in one line |

Nothing the model writes reaches the gate except an option id from the closed list. A wrong id is dropped and logged.

## 3. Speech

- **Commands** go straight to the gate: stop, cancel, yes and no as answers, arm and disarm, a short request with a named target or a taught skill ("hand me the paper cup", "hold the jar"), and anything addressed by name ("Armed, …").
- **Everything else is conversation.** It goes to the thinker as context and is never a chatbot turn.
- **Arm by voice:** say "arm yourself" (or "power up" / "start helping"). Armed replies "Arm and start helping? Say yes." Your "yes" within 10 s arms it, and a latched stop is reset first. Only the wearer can arm it; another speaker gets "Only you can arm me."
- **Disarm by voice:** "stand down", "disarm" or "power down" works from anyone, at once. Disarming only makes the arm safer: torque stays on and the arm holds still.

## 4. One confirmation

- **Suggestions and spoken requests** wait as one request. The question is spoken, and one "yes", squeeze or click runs it through the gate.
- **Cards** keep the reviewed twitch flow: a twitch selects, the action is spoken as "confirm?", and a second twitch confirms. That flow exists because a startle can fire one twitch.
- **A gate warning the card never showed** (for example "I can't prove my gripper is empty") always needs a squeeze, a yes or a click.

## 5. Desk and bed

Muse knows which setting it is in, and gets a description of both on every call:
- **desk:** laptop, calls, drinks, three slots, a tray, a handover spot by the hand;
- **bed:** headboard, resting, water, pills, thermometer and phone on slots, handover to the open hand.

The poses, objects and routines of each setting come from its profile, because the arm is mounted differently. Switching settings means a restart with `--profile live,bed`.

## 6. The Mind view

Laid out left to right:
- **Senses:** Camera (SAM 3.1), Microphone (Voice Transcribe), Forearm (EMG decoder), Clock and routines.
- **What it knows:** the packet, refreshed every second.
- **Thinking:** Muse Spark 1.3, with the reason for each turn and the last four thoughts.
- **Outputs:** Says, Suggests, Cards (and the card ranker).
- **Action chain:** Safety gate, You confirm once, the Arm.
- **Lower lane:** Brace → Freeze → Investigates (Muse Wonder) → Rule.

How it shows activity:
- **Glow and timer:** a node glows with a running timer only while its model call is really in flight.
- **Dots:** a dot travels along a connection only when a runtime event crossed it.
- **Hues:** violet = Meta model, grey = Armed's own code, red = the stop path. Each node's chip names the exact model id.

It needs a full-screen window; at 800×600 the text is cramped.

## 7. Measured live, Nov 4 (n = number of calls)

| What | Measured | n |
|---|---|---|
| SAM 3.1 scan (catalog phrases + hand + face, 960 px frame) | 1.2–2.5 s | ~10 |
| Voice Transcribe: final text after speech ends | ~1.0 s (connect 1.0 s) | 2 |
| Spark 1.3 thinker turn (frame + context) | 2.5–8 s | ~10 |
| Spark 1.3 card ranking, text only, `reasoning_effort: minimal` | 4–13 s | ~12 |
| Spark 1.3 spoken request → tool call | ~7 s | 2 |
| Spark 1.2, same ranking prompt, for comparison | 1.6 s | 1 |
| Wonder 1.0 and Spark 1.3 key check (one image) | ~4 s each | 1 |

Notes:
- **Thinking tokens:** Spark 1.3 spends 300–1000 hidden thinking tokens even at `minimal`, the lowest level it accepts; `none` is rejected. Every Spark call therefore gets 2048 output tokens, and timeouts of 15–20 s.
- **Cards show at once** in a fixed order, and Spark's ranking replaces them when it lands, only before your first input.
- **Spark 1.2** answers the same ranking prompt in under two seconds when speed matters more than the newest model.

What it said and did, live, with real Spark:

| Situation | Muse Spark 1.3 |
|---|---|
| Idle heartbeat, nothing changed | silent |
| "I've been on this call for an hour and I'm parched" | said *"No water on the desk right now."* |
| "Hold on, let me open this jar" + hand held out | suggested *"Hold the jar while you open it?"*, waited for one yes |
| "Armed, what can you see on my desk right now?" (real mic) | answered *"Water bottle, highlighter on white box."* in 3.2 s |
| Heartbeat, desk with a bottle SAM's catalog does not name | noticed *"Water bottle on desk edge"* from the frame |
| Hand held out (scene event), card ranking | ranked *Take it from you?* and *Hold the jar?* first, reason *"you held your hand out"*, 3 of 3 runs |

## 8. In the design

**What it adds** (`ARMED_IDEA.md` §4): one line between the context packet and the Card. Muse Spark also runs as an always-on judge: on every change it may remark aloud or put one action from the closed list to you as a single question. The Card and the twitch stay the primary answer; the voice "yes" and the squeeze are the hands-busy answer.

## 9. What did not change

- Every motion is a taught pose; joint values from a model: 0.
- The brace, the open hand, "stop" and the STOP button freeze the arm with no model and no network in the path.
- The gate is a pure function; rules only restrict.
- A twitch never confirms a gate warning.
- Another speaker never approves anything.
