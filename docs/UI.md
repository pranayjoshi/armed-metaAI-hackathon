# Armed: the interface

Written November 3, 2026. How a person meets Armed on a desk, in a bed, and as a judge in a browser. Builds on `docs/SELECTION_API.md` (the card state machine, which this document does not change), `docs/REPLAY_API.md`, `web/README.md` (the console's rules and tokens).

---

## 1. The decision: not a web app, not a desktop app, one page in three places

The question was whether Armed is a web page, a desktop agent, or a desktop thing you launch from the web. The answer comes from where the person's eyes are when a card matters:

- At the desk, their eyes are on whatever they were doing: a document, a video call. A card has to appear **over that**, without a tab switch, without taking the keyboard, because their hands are typing. That is a desktop overlay, and no web page can do it.
- In bed, there is no computer in front of them. The card has to live on a **screen near the arm**, readable from a metre away with the lights low, and the voice has to carry what the screen says.
- For a judge, there is no arm. The entry has to run in a **browser** with nothing installed, in replay.
- For the operator (you, during setup, calibration, teaching and review), the existing **web console** already does the job.

So: **one card page, rendered in three places, and one console.** The card page is served by the runtime that already serves the console, so the overlay, the bedside screen and the hosted replay are the same HTML. There is no second UI codebase.

| Surface | What it is | Where it runs | Who sees it |
|---|---|---|---|
| **The Card** | A small always-on-top, non-focus-stealing window showing the current proposal and the muscle meter. Hidden when there is nothing to say. | A thin Electron shell loading `http://127.0.0.1:<port>/card` from the running `h2m` | The person at the desk |
| **The Bedside screen** | The same `/card` page in *far* mode: one dominant card, 3× type, speech first | A tablet or phone on the headboard, or a small display mounted on the arm | The person in bed, and whoever is in the room |
| **The Console** | The existing operator UI: home, stage, flow, signals, learn, lab, replay | Browser on the Mac, usually a second screen | The operator |
| **The hosted replay** | `h2m demo` with the card page included in the scripted scenario | Browser, anywhere, no key, no arm | Judges |
| **The body** | Gestures, speech through a speaker next to the arm, the status light | The arm itself | Everyone in the room |

The robot's body is a surface on purpose. "Waiting for Pranay" is said out loud to the person who asked, and the arm turns to look at who is speaking. Half of what the film shows is this surface.

---

## 2. The Card

### 2.1 What it looks like

A rounded dark panel, about 360 × 150 px, in the top-right of the screen by default (configurable corner). No window chrome, no title bar, a 1 px edge that takes the signal colour of the current state. Three cards inside: the highlighted one large with its pictogram, the other two as a short stack beneath it, so a double twitch visibly rotates the stack.

```
┌─────────────────────────────────────────────┐
│ ▮ likely                        asked by —   │   state edge + confidence word + who asked
│ [pictogram]  Show you the phone              │   label from h2m.intent, never model text
│              buzzed 2 s ago · phone on desk  │   evidence line, with the scan age
│              · 11:02, you usually look       │
│ ────────────────────────────── ◔ 0.4 s       │   muscle meter + reaction ring (accept_in_s)
│  Take the mug          Lights off            │   the two other cards, small
└─────────────────────────────────────────────┘
```

Every string on it comes from the snapshot: `selection.cards[*].label`, `confidence`, `evidence` (new field, see §6), `asked_by` (new field), `state.reply` for the robot's sentence. The page never composes a decision and never invents a value, exactly as `web/README.md` rule 1 says for the console.

### 2.2 How it behaves

- **Appears only when there is something to say.** The window is hidden while `selection.cards` is empty and the arm is idle. It slides in (180 ms, `--ease-out`) when cards arrive, a question is asked, the arm starts moving, or the arm stops.
- **Never steals focus.** The shell sets the window non-focusable and click-through except on its own buttons, so the person's keystrokes keep going to their document or call. This is the one property the whole product depends on, and it is why the Card is not a browser tab.
- **Acknowledges after paint.** The page POSTs `/api/selection/ack` with `card_id`, `menu` and `highlight` on the animation frame after the highlighted card is drawn, and again whenever `acked` turns false. The server's reaction clock starts at that ack; the Card shows the `accept_in_s` countdown as a thin ring filling clockwise so the person can see when a twitch will count.
- **Two steps for motion, one for speech.** On the first twitch the highlighted card grows to fill the panel with the robot's spoken question captioned under it ("Show you the phone? Twitch again to confirm, or double twitch to go back"). The ring restarts after the announcement finishes. A `say` card is taken at once.
- **Refusal is quiet.** A brace dismisses the card with a short fade and a low tone; no red, no alarm. Red is reserved for a stop.
- **Running.** While the arm moves, the panel shrinks to a one-line strip: the task label, the stage (approach · grasp · carry · release), a progress bar, and the words *brace to stop*. The muscle meter stays.
- **Stopped.** Red edge, the word *Stopped*, the reason from `arm.reason`, the measured stop latency when the runtime has it, and the robot's "Sorry" captioned. Then the Muse hue takes the edge while the investigation runs, with its tool calls listed one per line as they arrive (`track · measure · propose`), and then the rule card.
- **The rule card.** The restriction in plain words ("Approach your face slower, stop at 12 cm"), the verifier's two ticks (*only restricts* · *would have prevented this*), a tiny loop of the virtual rehearsal, and *twitch to save*. It uses the same ack and reaction gate as any card.
- **Waiting for a named person.** When a request came from someone else: the card shows *asked by X* and the edge pulses slowly; the robot says "Waiting for Pranay" once. Nothing else changes; the twitch is the same twitch.
- **Offline.** A small chip *offline · scanning* when `selection.degraded` is set or the network is gone; the cards keep working in scene order. No other visual change, because nothing else about the interaction changed.
- **Disarmed.** Grey edge and the words *arm it from the console*. No cards.


### 2.6 Added by the final cut (`DEMO_STORY.md`)

- **Model chips.** Whenever a model or an Armed component acts on screen, a small chip names it: the Muse hue for `muse-spark-1.3`, `muse-wonder-1.0`, `sam-3.1`, `muse-voice-transcribe-1.0`; the Armed accent for *Armed ranker*, *Armed stop controller*, *Armed gate*, *Armed measure*, *Armed checker*, *EMG decoder*. A chip appears only for something that ran; the snapshot carries `chips: [str]` per state so the page never guesses.
- **The auto rung.** For a pre-approved routine the Card shows the proposal with a five-second ring draining and the words *brace to cancel*; the ring is the only countdown that leads to motion, and a brace at any point ends it. After the ring the card becomes the running strip with chain progress *1/2 · 2/2*.
- **The quiet strip.** A routine that found nothing to do (*desk check · nothing out of place · last 17:30*) shows one line for three seconds and hides. It never sounds.
- **Re-ranking in view.** When the context changes while cards are up, the cards re-sort with a short slide and the new top card's evidence lines light; the highlighted card never changes under the person (`SELECTION_API.md` keeps the highlight), so the slide happens behind it.
- **Cutaways, not overlays.** In the film the Card is shown as a screen recording of the person's laptop between live shots, never composited over them; the HUD is the stage view, burned in.
- **Scene stale.** When the last SAM 3.1 scan is older than the configured limit or the network is gone, the card edge turns grey and a line reads *last seen 40 s ago*; cards stay in their last order, and any card whose target is stale shows *waiting for the scene* instead of the reaction ring, because the gate will not move toward a stale target. A *calling sam-3.1 · 1.2 s* chip shows while a scan is in flight.
- **Explain to X.** The spoken rule appears as a caption with the rule card's thumbnail beside it, and the transcript line is labelled with the asker.

### 2.3 The muscle meter

One thin bar along the bottom of the panel. Flexor level fills from the left in `--grip`; extensor from the right in `--open`; when both are up the overlap turns `--brace`. A twitch flashes the bar once. A brace fills it red for as long as it is held. The bar runs at the snapshot rate (10 Hz) and is the one element that is always present when the panel is visible, so by the third card the viewer reads it without thinking. It carries the `SYNTHETIC EMG` tag when the source is synthetic, as the console does.

### 2.4 Sound

A soft tick when cards appear, a click on accept, a low tone on refuse, nothing on a stop (the robot's "Sorry" is the sound), nothing while running. All sounds off in far mode where speech carries everything, and all off under `prefers-reduced-motion`.

### 2.5 Stop

A red STOP button is always on the panel's edge, even in the one-line running strip, and it is the one element that is never click-through. It calls `POST /api/stop`, the same route as the console's Space bar and the physical e-stop beside the arm. The Card never covers the console's top bar because the console is on another screen.

---

## 3. The Bedside screen (far mode)

The same page at `/card?mode=far`. Differences, all CSS and layout:

- One card fills the screen; the other two are a thin line of text at the bottom. Type is 3× the desk size; the label is readable at 1 m with the lights low.
- The evidence line is spoken, not read: the robot says the card's label and the person answers; the screen confirms what was heard.
- The muscle meter is a wide band under the card, so the person can see their own twitch register without looking for it.
- No sound effects; speech is the sound.
- A full-screen red *Stopped* state, since there is no console in the room.

The screen is a tablet or phone browser on the LAN, which is a problem the runtime has to solve: `docs/LOCAL_API.md` refuses any Host that is not localhost, and the ack POST needs the token. Two acceptable answers, for the build stream to pick and `safety-reviewer` to approve: (a) a read-only `/card` route that may be served to an allow-listed LAN address, with ack moved to a token-bearing query the runtime issues per device, or (b) a small display driven by the Mac directly (USB or AirPlay), which keeps everything on localhost and is the safer choice for the film.

---

## 4. The Console

Unchanged in its rules (`web/README.md`). Three additions:

- **Film mode** on the Stage view (`?film=1`): the two EMG traces burned in with a shared timecode, the counters (*selections · flinches · rules learned · words spoken to the robot*), the current card mirrored small, and the robot's sentence as a caption. This is what the camera records beside the arm; it is the proof layer in `DEMO_STORY.md` (the evidence table and the rules of the cut).
- **A Cards panel** on the Learn view: the proposal history of the session with what was accepted, stepped past or refused, and Muse's upgraded ranking where it arrived, for the top-1 number.
- **A Rules page** already exists in Learn; each rule gets its incident clip and its verifier result, and a delete.

---

## 5. The hosted replay

`h2m demo` already runs the scripted scenario in a browser with the `SIMULATION` label on every payload. Add the card page to it: cards appear in the scenario as they would live, the scripted twitches accept them, the brace stops the virtual arm, and the rule card shows the verifier passing. A judge with no hardware sees the whole interaction in two minutes. The replay server has no actuation routes, so this is display only.

---

## 6. What the runtime needs to add (for the build stream)

| Need | Where | Note |
|---|---|---|
| `GET /card` serving the card page, and `GET /card?mode=far` | `h2m/server.py` | Same token meta tag as `/` |
| `evidence: [str]` per card: the reasons the ranker used, in plain words | `h2m/intent.py`, snapshot | From the deterministic ranker's features and, when Muse upgraded the order, its short reason; never free model text on a motion card |
| `asked_by: str \| null` per card | `h2m/intent.py`, diarization | Only when the request came from another speaker |
| `pictogram` per card id (not in the cut; optional) | a small cache in `data/pictograms/` | Muse Image, generated once per card id, served as a file; fallback glyph by `kind` |
| stop latency in the snapshot when measured | `arm.snapshot()` | From `arm.stopped` `latency_ref` and the EMG onset |
| investigation progress events | `h2m/incident.py` | `incident.tool` events with the tool name and its one-line result, for the Card and the film overlay |
| rule verifier result and rehearsal state | `h2m/learning.py` | For the rule card's two ticks |
| LAN decision for far mode | `h2m/server.py`, review | §3 |

---

## 7. The desktop shell

`desktop/` with one `main.js` and a `package.json`, about a hundred lines:

- Starts `h2m run` if it is not already listening, or attaches to it.
- Opens one `BrowserWindow`: `frame: false`, `transparent: true`, `alwaysOnTop: true` at the floating level, `focusable: false`, `skipTaskbar: true`, `resizable: false`, loading `http://127.0.0.1:<port>/card`. Same origin as the console, so the local API rules hold with no change.
- `setIgnoreMouseEvents(true, {forward: true})` by default; the page tells the shell to take the mouse only while the pointer is over STOP or a card's buttons.
- Hides and shows the window on the page's say-so (`window.armed.show()` / `.hide()`), so the page owns the states and the shell owns the window.
- A menu-bar (tray) icon with a status dot in the arm's state colour and four items: Arm / Disarm, Stop, Open console, Quit. Quit stops the arm first.
- Launch: `h2m desktop` runs the shell; `h2m run` alone still works with the console only.

Electron is the pragmatic choice because `focusable: false` plus `alwaysOnTop` plus click-through is three lines and works today; Tauri or a native `NSPanel` would be lighter and can replace it after the hackathon without touching the page. If the shell is not ready for a take, the fallback is the far-mode page full-screen on a second display beside the arm, which the film can shoot the same way.

---

## 8. Rules the interface keeps

1. **Everything shown comes from the runtime.** No number, label, latency or evidence is composed in the page.
2. **The page never decides.** It renders cards, acks their paint, forwards a click to the same endpoints a twitch uses, and that is all. A card can only act through the server's gate.
3. **It never takes the keyboard.** The Card is non-focusable, always. Typing continues.
4. **Stop is always one click away** on every surface, never click-through, never covered.
5. **Synthetic and virtual are labelled** wherever a signal drives a visual.
6. **One colour, one meaning:** `--grip` flexor, `--open` extensor, `--brace` stop, `--ok` done, `--muse` model reasoning. The Card uses the console's tokens unchanged.
7. **Readable from where the person is**: at arm's length on the desk, at a metre in bed, in a 1080p video for the judges.
8. **Quiet by default.** Hidden when there is nothing to say; no idle animation; refusal is quiet; red means stopped and nothing else.

---

## 9. Build order and cost

| Item | Owner | Cost |
|---|---|---|
| Card page (`/card`, states in §2, meter, ack, sounds) | `ui-builder` | 1 day |
| Runtime fields in §6 | `runtime-builder`, `muse-integrator` | half a day |
| Electron shell and `h2m desktop` | `runtime-builder` or the user | half a day |
| Far mode CSS and the LAN decision | `ui-builder`, review | half a day |
| Film mode on Stage | `ui-builder` | half a day |
| Card page inside `h2m demo` | `ui-builder` | half a day |
| Pictogram cache | `muse-integrator` | half a day, cut first |

If only one of these ships, it is the Card page in far mode on a second display: it is enough for every beat in the film.


---

## 10. What the final cut and the Meta-only runtime add (Nov 3)

`DEMO_STORY.md` is now the specification the interface has to meet, and `COMPLIANCE.md` changed where its data comes from. In order of what the film needs first:

### 10.1 The Card
- **Reasons with their age.** Every evidence line that came from a scan carries the scan age (*phone on desk · 4 s*). Head direction and glance never appear; they are gone from the runtime's display.
- **A live-call chip.** Whenever a Meta API call is in flight the card strip shows *calling sam-3.1 · 1.2 s* (or `muse-spark-1.3`, `muse-wonder-1.0`, `muse-voice-transcribe-1.0`) with the elapsed time counting, then the measured latency for one second after it lands. This is the film's proof that the models run live, and it also tells the person why a card is a second late.
- **Re-ranking in view** (§2.2 above) is the jar beat: the top card changes while the person watches. It needs the slide to be visible at a glance from arm's length: 180 ms, the new top card arriving from below.
- **The veto ring.** A pre-approved routine (desk check, tidy) shows its card with a thin ring filling over five seconds and the words *brace to cancel*; when the ring completes the action starts and the card becomes the progress strip (*1/2 · 2/2*). A brace during the ring dismisses with the quiet fade, not red.
- **Refusal that reads.** A braced card fades and drops out of the stack; the next card rises into the highlight with a fresh ack. The friend's first beat depends on this being legible in a time-lapse.
- **Asked by.** The *asked by X* chip with the slow edge pulse, and the transcript line under the card with the speaker label from Voice Transcribe (*X: "can you pass him the marker?"*). The arm's spoken answer (*Waiting for Pranay*) is captioned.
- **Scene stale** (§2.2 above) stays as a state even though the offline beat left the cut; the hosted replay shows it when a scan lags.

### 10.2 The console: context view
- **Mask paint.** The context view opens on the raw overhead frame; when a scan lands, SAM 3.1's masks paint in, one colour per phrase, labels after the masks (*laptop · phone · jar · cup · hand · face*). Then the frame slides left and the packet fills the right half. This is the 0:36 hook, and it is rendering of a result the runtime already has.
- **The packet, honestly.** Rows: clock; objects with their slot and scan age; who is present; *hand at keyboard · 4 s*; sounds; last actions; routines due; rules in force. No posture row, no gaze row. The two or three rows that produced the current card light up in the card's colour.
- **Live call chips** here too, on the row the call is for.

### 10.3 The console: stage view and film mode
- **Speed readout** beside the muscle meter: the executor's commanded speed fraction (*100% · 70% · 40% · 0*), so the slow-down at 0:54 is a number.
- **Twin rehearsal in frame.** When a rule is proposed, the 3D twin runs the proposed route as a picture-in-picture over the stage view while the real arm stays frozen; a *rehearsal* chip, the old route in red and the new in green. Needs one trigger from the rule proposal; the twin and the route overlay exist.
- **The investigation as a feed.** Tool calls arrive one per line in the Muse hue with their chip (*Muse Wonder 1.0*, *SAM 3.1*, *Armed measure*, *Armed checker*), two ticks for the checker, then the rule card. Each line appears when its result lands, never on a timer.
- **The architecture frame** for the 2:05 beat: the nine-step chain drawn left to right from the day's log, nodes in the two chip colours, one line under each model saying why it is there, and the day's real counts along the bottom (*Spark calls · Wonder · SAM scans · minutes transcribed · joint values from a model: 0*). Exported from the stage view with `?film=1&frame=architecture`.
- **Counters.** *Selections* and *words spoken to it*, defined once on screen at the title as words addressed to the arm; the film mode shows the definition on first appearance.

### 10.4 Far mode (the headboard)
- One big card, the wide muscle band, the live-call chip in the corner, the veto ring at the same size as the card so it can be read from the pillow. The speaker label and the arm's caption at the bottom in large type.

### 10.5 Beyond the film: the UX that makes it a product
- **Why this card** on long-press or hover: the full reason list with sources (*routine · 3 days running*, *SAM scan 4 s*, *X's request*), so trust is inspectable, not asserted.
- **The trust ladder as settings**, one row per action with the rung as three plain words (*does it · asks · never*) and quiet hours; `PROACTIVE.md` §5.
- **The routines page**, editable by voice through a card and a twitch, with *last ran* and a snooze count.
- **Rules with their clips**: each rule shows the incident thumbnail, the checker's result and a delete.
- **Honest waits.** Every API wait is shown with its clock; nothing spins without a number.

### 10.6 Order of work
Live-call chip and scan ages (half a day) → mask paint in the context view (half a day) → speed readout and veto ring (half a day) → twin rehearsal trigger and the investigation feed (half a day) → architecture frame export (half a day) → refusal fade and far-mode sizing (half a day). Everything in §10.5 is after the submission.
