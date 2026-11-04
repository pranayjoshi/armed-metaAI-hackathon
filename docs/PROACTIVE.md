# Armed: acting without being asked

Written November 3, 2026. How context reaches Muse continuously, what Muse is allowed to do with it, how the person shapes it, and the full list of things that happen on their own at the desk and at the headboard. Builds on `ACTIONS.md` (what the arm can do), `UI.md` (the card), `SELECTION_API.md` (the card state machine; unchanged) and the trust ladder in `PROACTIVE.md` §2. What exists today: the ranker already uses history, time of day and head direction (`h2m/intent.py`), the world tracker knows the gripper and the hands (`h2m/world.py`), and cards can appear on their own when the arm is idle. What does not exist: routines, triggers, a context packet, or any action that runs without a twitch.

---

## 1. The principle

Most of what a third arm should do, nobody would bother to ask for. Water every hour. The phone when it buzzes. The light when you sit down. Pills at eight. The desk put back at six. A person with busy hands will not stop to request these, and a person in bed may not be able to. So the default is reversed: **Armed proposes, and the person's only job is to say no, or to say yes to the ones that move.** The intelligence is in proposing the right thing at the right moment and staying quiet otherwise.

Muse gets the context, all of it, continuously. What it may *do* with it is decided by a deterministic ladder the person controls, not by the model.

---

## 2. The context packet

A small structured document the runtime assembles and sends to Muse Spark on a cadence and on events. It is the only thing the model sees, and it is shown to the person in the console so they know what the robot knows.

| Field | Source | Example |
|---|---|---|
| `clock` | system | `11:02 Tue`, `since_wake: 3h 10m` |
| `routines` | the person's routine list (§5) | `water: last 10:02, due 11:02, mode ask` · `pills: due 08:00 done` |
| `scene` | YOLOE, SAM 3.1, world tracker | objects with slots, what moved since last packet, what is out of place, what is new, gripper state |
| `people` | diarization, face count, MediaPipe | `user: seated, hands on keyboard, head left` · `other: 1 voice (X) in room` |
| `posture` | MediaPipe, bed vs desk profile | `lying` / `sitting` / `hands under desk` / `hand held out with object` |
| `sounds` | mic events | `phone buzz 2 s ago` · `knock` · `door` |
| `speech` | Muse Voice Transcribe, diarized, last 20 s | `X: "can you give him his phone"` · `user: "ugh where's my pen"` |
| `body` | EMG | `calm` / `active` / `brace 40 s ago, refused: tidy` |
| `history` | recorder | last ten actions, last ten refusals, accepted-after-step counts |
| `rules` | rule memory | restrict-only rules in force, including "don't propose X" |
| `quiet` | settings | `quiet hours 23:00–07:00`, `do not disturb until 12:00` |

Cadence: every 20 s while the person is present and the arm is idle; immediately on an event (sound, speech from another person, an object moving, posture change, a routine coming due); never while the arm is moving. Images go with the packet only on events and once a minute otherwise, to keep latency and cost down.

Muse Spark answers with up to three proposals, each with a trigger, a one-line reason in plain words and an urgency, or with *nothing*. Muse Wonder gets the day's packets at the end of the day and writes the summary that becomes tomorrow's history and any routine it wants to suggest (§5.3).

---

## 3. What Muse may do with it: the ladder, enforced in code

The model proposes. A deterministic policy decides what each proposal is allowed to become. The policy is a pure function of (proposal kind, the person's settings, the rules, the clock), tested like the safety gate.

| Rung | Allowed for | What happens | Person's control |
|---|---|---|---|
| **Does it** | non-motion, non-message actions that cannot harm: a lamp, turning to look at you, a spoken reminder, a card going quiet | Happens, with a one-line note on the card strip | Toggle per action; quiet hours apply |
| **Announces, then does it** (reviewed relaxation) | motion the person has pre-approved as a routine, away from the body: bring the cup to the hand spot, tidy at six, phone to the holder | Robot says what it is about to do, a 5 s veto window with the card showing *brace to cancel*, then it runs. Brace stops it at any point as always | Only for routines the person set to *auto*; off by default; never for near-person stages |
| **Proposes** | everything else that moves, every message, anything near the body | A card; nothing happens without a twitch; the final approach to the face or hand always waits for a twitch even inside an auto routine | Always on |
| **Asks the named person** | requests that came from someone else's voice | Card says *X asked*; the robot says who it is waiting for | Always on |
| **Never** | free model text to a motor, a message or a stranger; a rule that widens anything; anything during quiet hours except a stop | — | — |

The twitch at the moment of approach is kept even for automatic routines because it is cheap for the person and it is the thing that makes "the robot came at my face on its own" impossible to say. The arm can come to the hand spot by itself; it comes to the mouth only when asked, every time.

### Rate and manners
- At most one unsolicited card every `propose_min_gap_s` (default 120 s) unless the trigger is a sound, another person, or a routine that is overdue.
- A refused unsolicited card is not re-proposed for `refuse_cooldown_min` (default 30); refused twice in a day, Muse is told so and the proposal becomes a "don't propose" rule candidate (restrict-only, approved by twitch).
- A double twitch on an unsolicited card snoozes it: the routine shifts by its own interval.
- Quiet hours: no cards, no speech above a whisper level, the lamp rung only; a brace still stops, a knock still gets announced if the person has that on.
- The person can say "stop proposing" (voice or a console toggle) and the robot goes request-only until told otherwise.

---

## 4. Triggers, and what they lead to

| Trigger | Detected by | Leads to |
|---|---|---|
| Routine due | the clock and the routine list | the routine's card, or its announce-then-do |
| Elapsed since last X | history | water, a break, a position change reminder |
| Phone buzz or ring | mic event, phone on the table seen moving | *Show you the phone?* |
| Knock, doorbell, a voice at the door | mic event, diarization | *Say you're coming?*, or the robot announces on its own if set |
| Another person asks | diarization + speech | an *X asked* card, waits for you |
| You sit down / lie down / stand up | posture change | lamp, routine context switches between desk and bed profiles |
| Hands go under the desk, head down | posture | *Light on your hands?* |
| Hand held out with an object | world tracker | *Take this?* |
| Desk check every `desk_check_every` (default 30 min) | the clock, then a scene sort by Spark over the overhead frame and the world tracker | nothing if the desk is clear; otherwise *Put it back?* for catalog objects off their slot, *Clear N things?* for unwanted items |
| New object appears | scene diff | *Read it?* for paper; otherwise nothing |
| You look at the same thing twice in a minute | head direction | that object's card moves to the top; it is a tie-breaker, never a trigger on its own |
| Your own speech mentions a need | diarized speech | the matching card ("so thirsty" → water); never acted on without the card |
| Nothing from you for a long time in daytime (bed profile) | posture, EMG calm, no speech | *Are you okay?* spoken; a twitch clears it; no twitch in 60 s and the escalation routine runs (§6) |
| Triple brace with the arm idle | EMG pattern | the person's chosen "get X" action: a message and a spoken announcement; this is deliberate, and it is the one pattern that bypasses the proposal step |

---

## 5. Routines: the person's schedule, editable, and suggested by Muse

### 5.1 What a routine is
`name · action · when · mode · window · profile`. *When* is a time, an interval, or a trigger from §4. *Mode* is *ask* (a card), *auto* (announce-then-do, §3) or *off*. *Window* is how long the card waits before it lapses. *Profile* is desk or bed.

```yaml
routines:
  water:   {action: bring_cup_to_hand,  every: 60m,  mode: ask,  window: 5m,  profile: bed}
  pills:   {action: pills_to_hand,      at: ["08:00", "20:00"], mode: ask, window: 30m, profile: bed}
  temp:    {action: thermometer,        at: ["09:00", "21:00"], mode: ask, profile: bed}
  phone:   {action: show_phone,         on: phone_buzz, mode: ask, window: 90s}
  desk_check: {action: clear_desk,      every: 30m, mode: auto, profile: desk, keep: [pen_cup], never_touch: [anything within 15 cm of a hand]}
  lamp:    {action: lamp_on,            on: sit_down,  mode: does, profile: desk}
  night:   {action: lights_off,         on: lie_down_after_22, mode: ask}
  knock:   {action: say_coming,         on: knock, mode: ask}
  checkin: {action: are_you_okay,       after_idle: 3h, hours: "08:00-22:00", mode: does, profile: bed}
quiet_hours: "23:00-07:00"
named_person: X
```

### 5.1b The bed's hand overs (built Nov 4)
One taught skill, `hand_over` (`configs/skills.yaml`: pick from `{slot}`, carry to `{handover}_above`, lower to `{handover}_release`, dwell, open, retreat), serves every object. `{handover}` is the object's **pose set**, from `arm.handover_sets` in `configs/bed.yaml`: pill cup → `handover`, water bottle (`cup`) → `handover_bottle`, marker → `x_hand`. Each set has its own measured hand spot in `routes_bed.yaml` (`handover.sets`), and the hand-over-the-spot check (`spot_clear`), the "can I watch the lowering" check (`spot_watchable`) and the hand watchdog all run against that set's spot, never another's. A set with a missing or untaught pose, or with no spot, is refused at the gate before anything moves. The close uses the slot's taught `point` jaws (`arm.use_taught_touch_close`) like every other grasp. The skill is `near_person`, `carries`, capped at `near_person_speed_cap`, and always a card; `trust.announce_enabled` and `near_person_enabled` are untouched.

```yaml
pills: {action: hand_over, object: pill_cup, every: 30m, mode: ask, profile: bed, label: "Pills?",
        say: "It's {time} Pranay, time for your pills."}
```

- **Spoken at card time.** In *ask* mode a routine's `say` line is spoken once, when its card goes up (`{time}` is the local time, "8 pm" or "8:30 pm"). Speech only; the handover starts only after the person confirms the card. It stays silent in quiet hours, when speech is muted (`/api/speech/mute`) or `proactive.speak` is false. The announce rung still says its line with "Brace to cancel", and stays off.
- **Pills, then water.** `arm.handover_then: {pill_cup: cup}`: when the pill hand over completes and the gripper is proven empty, the water card is raised straight away through the normal tool path. Two cards, two confirmations. A stop, cancel or re-arm in between drops it.
- **The marker is two-stage** (`arm.handover_two_stage: [marker]`). *X asked: marker* (or `hand_over` on the marker from any card) ends its first confirm at the lift: the arm picks the marker and stops holding at `marker_slot.above`, nothing else moves, and a second card *Hand it to X?* goes up. Only its confirm runs the carry: `x_hand_above`, lower, open, retreat, home. Like the handover's two stages, the second runs the gate again on fresh facts (holding the marker, above the slot, the spot clear for `x_hand`) under the request epoch and arm_count stage 1 started at, in flight so a brace latches. A brace, stop, cancel or re-arm between the stages drops the card; the marker stays held and the arm stays stopped (*go home* brings it back).
- **Typed or spoken.** "pills", "my pills" and "hand me my pills" resolve to the pill cup's `hand_over` and go through the same gate and confirmation (`h2m/compiler/grammar.py`).

### 5.2 How the person edits it
A Routines page on the console: one row per routine, a cadence picker, the mode as three words, a snooze count, and *last ran*. From the chair or the bed, the same edits by voice ("water every two hours", "stop the water reminders", "pills at nine instead"), each confirmed by a card and a twitch. Changes by voice from anyone else go through the *X asked* rung.

### 5.3 How Muse suggests one
At the end of each day Wonder reads the day's packets and history and may propose at most one new routine or one change: "You asked for water at about 10, 11 and 15 three days running. Make it hourly?" It arrives as a card the next morning, approved by twitch. A suggested routine starts in *ask* mode; only the person can set *auto*. This is the only way the robot's own initiative grows, and it is restrict-only in spirit: it can add a question, never remove one.

---

## 6. The headboard, action by action

Bed profile: the arm clamped to the headboard, the bedside screen in far mode, speech first, the person lying or propped up. Everything near the face or hand is a reviewed relaxation and keeps the twitch at the final approach.

| What happens | Trigger | Rung | What the arm does | Notes |
|---|---|---|---|---|
| **Morning** | 08:00 or the first stir after 06:30 | ask | Card *Pills, then water?*; on twitch, the pill cup comes to the hand spot, then the water bottle to the same hand (the bed arm does not reach the mouth; the straw to the mouth is a desk clip) | Muse reads the pill cup's day label aloud before offering it |
| **Water every hour** | interval, user-set | ask, or auto to the hand spot | Cup from its slot to the hand spot (auto allowed); straw to the mouth on a twitch | A refused water card snoozes an hour; a brace mid-approach freezes and the cup goes back |
| **Pills** | 08:00 and 20:00 | ask | Pill cup to the hand; the label read aloud; logged | Never auto; a missed window is announced once and logged for X |
| **Temperature** | 09:00 and 21:00 | ask | Thermometer to the forehead distance; the reading read aloud from the wrist camera and logged | Reviewed; same approach cap as the straw |
| **Phone** | buzz or ring | ask | Phone from its holder to eye level, follows the face; put back on double twitch or after the call | The holder is a taught slot |
| **Hold this** | hand held out with a book, a tablet, a cup | ask | `take_this` grasp, then held beside the bed until a twitch releases it | Time-limited hold; load watchdog |
| **Read this to me** | paper held up, or a new paper appears | ask | Brings it to the wrist camera; Muse reads it | Model text is spoken, never acted on |
| **Lights** | lie down after 22:00; the first stir after 06:30 | ask at night, does in the morning | Lamp off / on through the plug, or the physical switch pressed | |
| **Someone asks** | another voice | asks the named person | *X asked: pills* · "Waiting for Pranay" | |
| **Tell X** | double twitch on a *Tell X* card, or the person's speech | ask | Message from the taught list | Taught phrases only |
| **Knock** | mic | ask (or does, if set) | "They're coming, one minute" toward the door | |
| **Are you okay?** | no movement, speech or EMG activity for 3 h in daytime | does (speech only) | Spoken question; a twitch clears it | No motion |
| **Get X** | no twitch 60 s after *Are you okay?*, or a deliberate triple brace | the one bypass | Message to X and a spoken announcement, repeated once a minute until a twitch | Deliberate, documented, and shown in the film as a deliberate signal, not as detection of distress |
| **Night** | quiet hours | — | No cards, speech at whisper level, the stop path unchanged | |

## 7. The desk, action by action

| What happens | Trigger | Rung | What the arm does |
|---|---|---|---|
| **You sit down** | posture | does | Lamp on; the arm looks up and nods; the desk profile loads |
| **Phone buzzes while you type** | mic, scene | ask | Phone to eye level; follows your face; back on double twitch |
| **Hand held out with the mug** | world tracker | ask | Takes it, waits for your fingers, puts it away |
| **Hands under the desk, head down** | posture | ask | Holds the torch and points it at your hands |
| **Jar or bottle held toward the arm** | world tracker, scene | ask | Grips it and holds while you open it; releases on twitch |
| **Paper held up to the camera** | scene | ask | Reads it aloud |
| **Something out of place for 60 s** | scene diff | ask | Puts it back |
| **Every 30 min** (user-editable) | routine, then scene | auto (if set) | If the sort finds out-of-place or unwanted items: announces "checking the desk", shows what and why, 5 s veto, then puts catalog objects back and moves unwanted items to the bin or tray. If it finds nothing it says nothing |
| **Ninety minutes of typing** | EMG activity and keyboard posture | does (speech) | "You've been at it ninety minutes." No motion; a stretch is not proposed, since the arm has no business there |
| **Knock** | mic | ask | Says you're coming |
| **Another voice asks for something** | diarization | asks the named person | Waits for you |
| **You stand up and leave** | posture, no person for 5 min | does | Lamp off; the robot goes request-only until you return |

---

## 8. What the film gains

Four beats in `DEMO_STORY.md` become unprompted instead of requested, which is the difference the viewer feels: the morning card nobody asked for, the lamp when you sit, the tidy at six that announces itself and waits five seconds, and *Are you okay?* in the bed scene followed by the twitch that clears it. The counters stay: selections up, words to the robot zero.

---

## 9. What this adds to the build

| Piece | Where | Cost |
|---|---|---|
| Context packet assembler and cadence | `h2m/context.py` (new), fed from world, head, speech, EMG, recorder | half a day |
| Routines: file, loader, scheduler, the Routines page, voice edits through cards | `h2m/routines.py` (new), console `learn` or a new view | one day |
| The ladder policy as a pure function with tests; the *auto* rung as a reviewed relaxation with the 5 s veto window and the twitch at the final approach | `h2m/safety.py`, `h2m/selection.py` | half a day plus review |
| Triggers: mic events (buzz, knock), posture changes, out-of-place timer, hand-held-out | `h2m/sensors/`, `h2m/world.py` | one day |
| Daily Wonder summary and the one-routine suggestion | `h2m/learning.py` | half a day |
| Escalation (`are_you_okay`, `get_x`, triple brace) | `h2m/routines.py`, `send_message` | half a day plus review |

Cut order if behind: the daily Wonder suggestion → posture triggers → the auto rung (everything stays *ask*) → escalation. Never cut: routines with *ask* mode, the phone trigger, the context packet, quiet hours.
