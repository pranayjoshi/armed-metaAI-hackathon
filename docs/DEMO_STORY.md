# Armed: the demo, shot by shot

Written November 3, 2026, fourth revision. The locked story for the submission video: one day with a third arm, narrated by Pranay, 2:28. Build state per beat: `ACTIONS.md` §3 and `PROACTIVE.md` §6–7.

## The promise the demo proves

*A third arm that autocompletes you* is the hook. The claim underneath it, and the one every beat has to serve: **an arm that anticipates useful help and lets you decide with minimal effort, while you keep control.** Three pieces of evidence, and nothing in the cut that serves none of them:

1. **It anticipates appropriately.** A proposal is seen emerging from real context, and a change in context is seen changing the proposal.
2. **It reduces effort.** A task completes with fewer inputs (selections plus corrections) and less time than the scanning baseline.
3. **Feedback changes behaviour.** An interruption, the approval of one specific change, and the next execution visibly different.

The models are attributed where they run; the demo is never a tour of what they can do.

## The story

A day has four movements, and the narration follows them rather than the features:

1. **Morning: it looks after me.** Pills, then water, a visitor's request that waits for me.
2. **Midday: it works with me.** It reads the room, and the guess changes when the room does. A jar comes open with one hand.
3. **Afternoon: it gets something wrong, and learns.** It slows when I tense, stops when I brace, investigates itself, proposes one rule, and takes a different route from then on. It can tell someone else why.
4. **Evening: it does the chores and lets me rest.** It checks the desk on its own, finds rubbish, warns me, clears it, takes a no. A stranger wears the band and her first act is a no. Tea takes the long way round the laptop without being told. One frame shows how it is built and why these four models. Lights out.

The origin line at the title is the one sentence in the film that is about Pranay rather than the arm. It must be true. Two shapes that work, pick one and make it yours: *"I built this because my hands are never free"* (the maker's reason) or *"I built this for [a person], who has one muscle that still listens"* (the personal reason). Nothing invented.

## The hooks, one every twenty seconds

| Time | The thing the viewer has not seen before |
|---|---|
| 0:00 | A robot arm acts, and the person's hands never stop typing |
| 0:06 | First person from a bed: a card, one flicker on a muscle meter, the pill cup lands in an open hand |
| 0:12 | The counter: *words spoken to it: 0*, and it will stay there |
| 0:24 | The arm turns to the visitor and says *"Waiting for Pranay."* Nothing moves until he says so |
| 0:36 | SAM 3.1 paints the whole desk with masks live, then the guess changes as the context does: the phone card gives way to the jar card as he holds the jar out |
| 0:54 | The arm visibly slows as the muscle trace rises, then freezes over the laptop when he braces |
| 1:08 | The robot investigates its own mistake on screen, tool by tool; a checker proves the fix only takes options away; its digital twin rehearses the new route beside the frozen real arm |
| 1:32 | The visitor asks why it took the long way, and the arm tells her |
| 1:39 | A desk check finds nothing, then thirty minutes later finds rubbish; a five-second veto ring runs out and the arm clears it; it reaches for the wrong thing and takes a no mid-task |
| 1:53 | Someone who has never worn the band refuses a wrong card, then is helping, in minutes |
| 2:00 | A mug of tea takes the long way round the laptop on its own: the rule learned from water applies to tea |
| 2:05 | The whole day's machinery in one frame, with the real call counts, and *joint values from a model: 0* |
| 2:24 | *"Night, Pranay."* The phone buzzes once in the dark |

## The rules of the cut

- **Meta models only at runtime.** Every model the running build invokes is a Meta model: `muse-spark-1.3`, `muse-wonder-1.0`, `sam-3.1`, `muse-voice-transcribe-1.0`. Objects, hands and the face come from SAM 3.1 by phrase on scene change, on events and on a slow cadence; speech comes from Voice Transcribe; the EMG decoder is signal processing with no learned weights; every motion is a taught pose. No YOLOE, MediaPipe, Parakeet or learned policy appears in the build, the chips or the write-up (`COMPLIANCE.md`).
- **The stops are the brace and the physical e-stop.** The voice "stop" now goes through the network and is the slow one; no beat relies on it.
- **The film opens with what was built, without a card.** The two preview shots carry a small corner stamp, *built Oct 26–Nov 4*, beside their time stamps, and the dated montage follows at 0:12. The cold open stays cold.
- **One day, in order.** After a twelve-second preview, the clock only moves forward: bed in the morning, desk through the day, bed at night. The clamp moving is the only scene change, and it happens twice.
- **The counters start at the title.** The preview's two actions carry their time stamps (*11:02*, *08:00*) so they read as moments from the day and are never counted twice.
- **No action appears more than once.** Each handover appears once, for a different object (pills, water, the marker to X); the straw to the mouth is a desk clip for the README, not in the cut. The stop is a carry. The changed execution is a route.
- **The guess is shown before the action**: the card with the two or three inputs that produced it lit up, then the meter flash, then the arm.
- **The interface is shown as the person sees it, between live shots, never on top of them.** At the desk, the Card appears as two-second cutaways: live shot, cut to a screen recording of Pranay's laptop where the Card slides in over the document or the call while the cursor keeps moving, cut back to live. Not in the opener. In bed, the headboard screen in far mode is already in the first-person shot. The console is seen once, at the context view. The Card is `UI.md` §2; the bedside screen is `UI.md` §3.
- **The HUD is the proof layer**, identical in every shot: clock top-left, counters bottom-left (*selections · words spoken to it*), muscle meter bottom-right, the card where it appears. Burned in from the stage view, never added in editing. *Words spoken to it* is defined on screen once, at the title: words addressed to the arm. Whenever a Meta API call is in flight, a chip shows it with its measured latency (*calling sam-3.1 · 1.2 s*); that is how the film shows live calls.
- **Narration is a demo voice, not a trailer voice.** One or two short sentences per beat, under 150 words per minute, present tense, naming what the system just did and where the input came from. No metaphors, no superlatives, no lines about feelings; the hook is the thing on screen.
- **Claims match footage.** The stop is a brace unless a twenty-stimulus flinch test has passed (then one line changes, marked †). No superlatives.
- **Hands always visible**: typing, under the blanket, on the jar, in the lap.
- **Nothing is ever held in the mouth by the arm.** The straw stops at a taught distance and the final approach always waits for a twitch. **Water never crosses real electronics**: the laptop is a dummy, the cup is sealed with a straw lid.
- **Real takes only.** A failed beat is replaced by a labelled earlier take of the same beat, or cut with its claim. The block pick never stands in for another capability.
- One quiet music bed; it drops out at the stop and does not return until the rule is approved.

## The cut

| Time | Clock | Shot | What happens | On screen | Narration (words) |
|---|---|---|---|---|---|
| **0:00** | 11:02 | Overhead, tight on two hands typing; a phone face-down | The phone buzzes. A card: **Show you the phone?** with *buzzed 2 s ago* and *phone on desk · last scan 4 s* lit. One flash on the meter. The arm lifts the phone to eye level. The fingers never stop. | Card, meter, stamps *11:02* and *built Oct 26–Nov 4* | — |
| **0:06** | 08:00 | First person from the bed: ceiling, blanket, the headboard screen | Card: **Pills?** with *08:00* and *from your routine* lit. One flash. The pill cup crosses the blanket and stops over the open hand, then lets go. | The headboard screen in far mode, one big card, the wide muscle band under it; stamps *08:00* and *built Oct 26–Nov 4* | — |
| **0:12** | — | Title over a build montage, 8 s | **ARMED** · *a third arm that autocompletes you*. Behind it, six dated stills from the event. The counters appear at zero. On-screen line: *for busy hands, and for hands that can't move*. | Title, dated stills, *built Oct 26–Nov 4* | "[Origin line.] Armed guesses what I need from context and lets me answer with one muscle. Here's a day with it." (20 + origin) |
| **0:20** | 08:04 | Bed, side shot, 4 s | Card **Water?** with *08:04*, *after pills* and *like yesterday* lit. Flash. The bottle comes to the same open hand, upright, and the arm lets go. | Card, counter 1, chip *Muse Spark 1.3 · proposed* | "Second card of the morning, from my routine. One twitch." (10) |
| **0:24** | 08:15 | Bed, wide. X in the doorway, 9 s | X: "Can you pass him the marker?" The arm turns to X. Card: **X asked: marker**. The arm: *"Waiting for Pranay."* Two full seconds, nothing moves. Flash. The arm lifts the marker and stops, and asks again. A second flash sends it on: it rolls its wrist toward X and presents the marker to X's hand. | Card with *asked by X*; transcript line, chip *Muse Voice Transcribe · speaker 2 = X* | "A request from someone else becomes a card for me. Nothing moves until I confirm." (15) |
| **0:33** | 11:00 | Transition, 3 s: the clamp lifts off the headboard, cut, it locks onto the desk; Pranay sits | The arm looks up at him. | Strip: *desk profile* | "Same arm, same band. Desk profile." (6) |
| **0:36** | 11:02 | The console's context view, then the desk, one continuous sequence, 18 s | The overhead frame, raw. A scan fires and SAM 3.1's masks paint the desk in front of the viewer, one colour per phrase, labels landing as they resolve: *laptop · phone · jar · cup · hand · face*. The frame slides left and the context packet fills the right: clock, those objects with the age of the scan, who is present, *hand at keyboard*, sounds, last actions, routines. A phone buzz; *phone buzz*, *phone on desk*, *11:02, usually looks* light up and the top card forms: **Show you the phone?** (cut before the lift; the preview showed it). In the same shot he picks up a jar and holds it out while on a call. The hand entering the handover zone fires a scan: *calling sam-3.1* for a second or two, then *hand out · jar in hand · on a call* light; the phone card drops to second; the top card becomes **Hold the jar?** Flash. The arm grips; he twists the lid off one-handed, still talking. Flash: it lets go. | The console's context view for the packet; then a two-second cutaway to the laptop screen recording, the video call full-screen, the Card in the corner, the phone card dropping to second and **Hold the jar?** rising to the top while the call runs; back to the live desk shot; chips *Armed ranker*, *Muse Spark 1.3 · re-ranked*, *SAM 3.1 · objects, hand, face · last scan 4 s* on the scene rows, *Armed scene diff* on what moved, *Armed world tracker* on hand-out, *calling sam-3.1 · [X] s* while the jar scan runs; the gripper load bar holding | "This is what it sees, live. The phone buzz made the first card. Holding out the jar replaced it. No request either time." (23) |
| **0:54** | 14:00 | Desk, wide, then the side camera at 60 fps, 14 s | Card **Water?** with *14:00 · routine* lit. Flash. The arm lifts the sealed cup and carries it toward the hand spot, straight over the laptop. As it crosses, the muscle trace rises and the arm visibly slows with it. Shoulders jump. Freeze, mid-air, above the keyboard. *"Sorry."* Slow motion: the trace, the slow-down, the stop, a ruler: *3 cm above the keys · [X] ms*. | Meter and arm in one frame with a speed readout tied to the meter: *100% · 70% · 40% · 0*; red edge, *Stopped*; chip *Armed stop controller · no model* | "Water over the laptop. My forearm tenses, the arm slows with it. I brace, it stops. That path has no model in it." (23) † |
| **1:08** | 14:00 | The desk screen, full frame, 19 s | The investigation runs as a tool loop, one line every two seconds: *track cup · track laptop · measure: 3 cm over the keyboard at 11 cm/s · propose: holding liquid, never cross the laptop; take the high route · check: removes options only ✓ · replay: incident prevented ✓ · rehearse* (the console's 3D twin, picture-in-picture beside the real arm, rehearses the high route while the real arm stays frozen). Then the card, held four seconds: **Keep liquids off the laptop. From now on?** Flash. | The loop in the Muse hue with chips as each line runs: *Muse Wonder 1.0 · investigating*, *SAM 3.1 · masks and tracks*, *Armed measure · calibrated table*, *Armed checker* on the two ticks; the rule card; counter | "Muse Wonder reads the clip, measures the miss, proposes one rule. The checker proves it only removes options. I approve with a twitch." (24) |
| **1:27** | 14:02 | Desk, overhead, 5 s | Same card, **Water?**, flash. The arm takes the high route around the laptop; the route overlay shows the old path in red and the new in green. | Strip: *rule 1 applied · via_high* | "Next carry: the high route." (5) |
| **1:32** | 14:05 | Desk. X walks in, 7 s | X: "Why did it go the long way?" The arm turns to X: *"He braced when I carried water over his laptop. I keep liquids off it now."* | Transcript line labelled *X*; the rule card thumbnail with its clip | "It states the rule to whoever asks, with the clip behind it." (12) |
| **1:39** | 17:30 → 18:00 | Desk, wide, 14 s. Two desk checks, 30 minutes apart | At 17:30 the arm looks at the desk and finds nothing to do; a quiet strip: *desk check · nothing out of place*. Clock jumps. By 18:00 the desk has a wrapper, a used cup and the pen cup off its spot. No input. The arm: *"Checking the desk."* Card: **Clear 2 things?** with *wrapper · not yours to keep* and *used cup · not on a slot* lit, and a five-second ring. Nobody touches anything; the ring runs out; the wrapper goes in the bin *1/2*, the cup to the tray *2/2*. It then reaches for the pen cup, and Pranay braces. It stops, backs off, *"Noted."* Strip: *pen cup stays*. | The strip *every 30 min · last check 17:30*, the two inputs lit on the card, the veto ring, the progress strip, chips *Muse Spark 1.3 · desk scene*, *SAM 3.1 · objects · scan 17:30, scan 18:00*, *Armed gate · pre-approved routine*, *Armed stop controller* | "Every thirty minutes it checks the desk. Half five: nothing. Six: a wrapper and a cup, so it says so and waits five seconds. The pen cup I refuse. That's a rule now." (33)
| **1:53** | 18:30 | Desk. A friend who has never worn the band, 7 s | Time-lapse: pads on, four prompts. The first card is wrong: **Bring the cup?** She braces; it drops. The second: **Bring the block?** Flash. The arm sets the block by her hand. A stopwatch stops at [m:ss]. | Stopwatch; the refused card fading, the accepted one lit | "Second person. Her first move was a no. Helping in [m:ss]." (10) |
| **2:00** | 20:00 | Desk, overhead, 5 s | Card **Tea?** with *20:00 · routine* lit. Flash. The arm lifts a mug of tea and, unasked, takes the high route around the laptop. | Strip: *rule 1 applied · liquid* with the rule's thumbnail; chip *Armed gate* | "A different cup, same rule. It knows tea is a liquid." (10) |
| **2:05** | — | The stage view's architecture frame, 12 s | The nine-step chain drawn left to right as it ran today, each node lighting as it is named. Four Muse-hue nodes with one line each under them: *Muse Spark 1.3 · image in, tools out, under 2 s* · *Muse Wonder 1.0 · video in, parallel tools, the day in memory* · *SAM 3.1 · phrase to masks and tracks, the ruler* · *Muse Voice Transcribe 1.0 · who said it*. Between them, the Armed-accent nodes: *EMG decoder · signal processing, no weights* · *gate · pure function* · *taught poses · 31 taught by hand* · *stop controller · no model, no network* · *checker · removes options only*. Along the bottom, today's real counts from the log: *Spark calls [n] · Wonder [n] · SAM scans [n] · minutes transcribed [n] · joint values from a model: 0*. | The frame, with *built Oct 26–Nov 4 · one person* small in the corner | "Four Meta models, each where only it fits: Spark proposes, Wonder investigates from video, SAM measures, Voice Transcribe knows who's talking. Everything that moves is my code: taught poses, a gate, a stop with no model in it." (37) |
| **2:17** | — | Black, 7 s | One large number: **inputs per task (selections plus corrections), Armed vs a scanning menu**, with n. Two medium: physical stop time from 60 fps; second-person setup time. One small line: proposals accepted first time, cost per day. Then the counters, final: *Selections [N] · Rules learned 2 · Words spoken to it 0*. | Results | "Inputs per task against a scanning switch, with n. Stop latency. Setup time. Failures in the repo." (17) |
| **2:24** | 22:30 | Bed, lights low, 4 s | Card **Lights off?** Flash. Dark. The arm: *"Night, Pranay."* The phone buzzes once in the dark. | — | "Last card of the day." (5) |
| **2:28** | — | End card, 2 s | **Armed** · repo · *Able-bodied demonstrator. Taught poses only. n stated.* | | — |

Runs 2:30 as written. Beats trimmed by a second or two to make room for the build beat: pills 4 s, X asks 9 s, the jar sequence 18 s, the investigation 19 s, the desk check 14 s, the friend 7 s, tea 5 s, results 7 s. No opening card.

† If a twenty-stimulus flinch test passes before the shoot, the 0:54 line becomes: "Water over the laptop. My forearm tensed before I knew it had; the arm slowed, then stopped." If not, *flinch* appears nowhere.

Total narration: about 255 words over 150 seconds; the build beat is 37 words in 12 s, the fastest line in the cut, so record it first and cut "each where only it fits" if it crowds.

## What each beat proves

| Beat | Anticipates | Less effort | Feedback changes behaviour |
|---|---|---|---|
| Phone while typing (0:00) | from the buzz and the phone on the desk | one flash | |
| Pills from the bed (0:06) | from the clock and the routine | one flash | |
| Water after pills (0:20) | from the routine order and yesterday | one flash | |
| X asks (0:24) | | the wearer decides, one flash | |
| Context, then the jar (0:36) | the proposal changes when the context does | one flash, no request | |
| The slow-down and the stop (0:54) | | | the interruption, graded then absolute |
| The investigation (1:08) | | | one specific change, approved |
| The high route (1:27) | | | the changed execution |
| Explaining to X (1:32) | | | the change is legible to others |
| Desk check (1:39) | from the scene every 30 min: nothing at 17:30, rubbish at 18:00 | zero inputs to start | a refusal becomes a rule |
| The friend (1:53) | | set up in minutes; the first act is a refusal | |
| Tea (2:00) | from the routine | one flash | the rule generalizes to a new object |
| How it's built, why Muse (2:05) | | | the four models named where they fit; the motion is all code |
| Results (2:17) | | inputs and time vs scanning | |
| Lights off (2:24) | from the hour and lying down | one flash | |

## Naming what runs

Every time a model or one of Armed's own components does something on screen, a small chip names it, in two colours: the Muse hue for Meta models, the Armed accent for Armed's own engineering. Exact ids: `muse-spark-1.3`, `muse-wonder-1.0`, `sam-3.1`, `muse-voice-transcribe-1.0`; and *Armed ranker*, *Armed stop controller*, *Armed gate*, *Armed measure*, *Armed checker*, *EMG decoder*. Muse Image and Glimmer get a chip only if they are actually in the cut. No chip names YOLOE, MediaPipe or Parakeet, because none of them runs. The chain the viewer should be able to retell, and the chain the 2:05 frame draws:

1. Muse Spark turns context into a proposed action (0:20, 0:36).
2. The Ganglion and Armed's EMG decoder capture the confirmation, the tensing and the brace (every flash, 0:54).
3. Armed's gate authorises, and its controller executes a taught motion, at a speed coupled to the muscle level (every action, 0:54).
4. The brace reaches Armed's stop controller with no model in the path (0:54).
5. Muse Wonder investigates the interrupted action by calling tools (1:08).
6. SAM 3.1 supplies the masks and tracks; Armed's calibrated measure turns them into centimetres and speeds (1:08).
7. Armed's checker proves the proposed restriction only removes options (1:08).
8. The person approves, the next execution visibly applies it (1:27), the arm can state the rule to someone else (1:32), and the rule carries to a new object (2:00).
9. Muse Voice Transcribe attributes requests from other people in the room (0:24, 1:32).

## What was cut, and why

- **Tell X (the message)**: not the arm's work. No `send_message` beat or claim.
- **Offline (the router pull)**: a resilience point, not a help point; it served none of the three claims strongly. The fact stays in the write-up: the stop path has no network in it.
- **The torch, the thermometer, the lamp coming on by itself**: fetch-and-point, highest-risk motion, and generic, respectively.
- **The second and third handover**: the stop is now the laptop carry and the changed execution is a route; each handover appears once, for a different object.
- **The knock, *Are you okay?*, the mug from the hand, the pill-label read, celebrate and bow**: README GIFs and social-cut B-roll.

## New in this cut, for the build

- **Comfort coupling** (0:54): the carry speed scales down with sub-threshold co-contraction before the brace threshold. A few lines on the executor's `speed` from the EMG level; the stop path is unchanged. Review as a speed change only.
- **Explain to X** (1:32): a `recall` answer from the rule's stored incident, spoken to the diarized asker. Taught sentence frame, the rule's own text filled in; never free model text.
- **The laptop carry** (0:54, 1:27): the original flinch beat, with the dummy laptop and the sealed cup; `via_high` is taught; the route overlay exists in the stage view.

## Meta-only runtime: what it changes in the cut

- **Scan cadence.** SAM 3.1 runs on scene change, on events (a sound, a card, a hand entering the handover zone, a new voice) and every 10 s otherwise; the ten-call latency test decides the cadence (`COMPLIANCE.md` §3.1). The jar card and the arm turning toward X arrive one or two seconds after the trigger; the *calling sam-3.1* chip covers the wait. If the test lands over 3 s, pre-stage the jar at the desk edge so the hand-in-zone event fires earlier, and let the arm look at the camera instead of at X if her face scan is not ready.
- **No gaze.** Head direction leaves the packet display and every evidence line; it was a tie-breaker and the film makes no gaze claim.
- **Speech.** All speech goes to Voice Transcribe, including the video call at 0:36: mute the mic for that take, or keep it and say so in the write-up.
- **Scene stale.** The Card gets a grey-edged state with *last seen [n] s ago* (`UI.md`); not in the cut, but it is what a judge sees if a scan lags during the hosted replay.
- **README.** The "what runs where" section is `COMPLIANCE.md` §2 verbatim, with the Meta / conventional column, under one sentence: the EMG decoder is signal processing with no learned weights.

## Also new for the build

- **Live mask paint** (0:36): the stage view draws SAM 3.1 masks over the overhead frame as the scan result lands, one colour per phrase, labels after. Pure rendering of a result the runtime already has.
- **Speed readout** (0:54): the HUD shows the executor's commanded speed fraction next to the meter.
- **Twin rehearsal in frame** (1:08): the console's 3D twin runs the proposed route as a picture-in-picture while the real arm is frozen; exists in the stage view, needs a trigger from the rule proposal.
- **Tags on the catalog** (2:00): the tea mug carries `liquid`; the route chooser already generalises by tag.
- **The Mind view and the always-on thinker:** the console's Mind view shows each model working as it happens, cut in between live shots, never in the opener; the thinker's single spoken question (*Hold the jar while you open it?*) and voice arming are live. Placement, captions, the lines that are true today and what not to claim: `LIVE_RUNTIME.md` §9.

## Fallbacks

- A beat that fails on the day is replaced by a labelled earlier successful take of the same beat (*earlier take*, small, in the corner), or cut with its narration and its claim.
- The block pick appears once, in the second-person beat, and may serve once as a two-second transition shot. It never stands in for another capability.
- No substitute exists for the stop, the investigation or the changed route. If those cannot be filmed, the submission says so and shows them in replay, labelled *SIMULATION*.

## The 45-second social cut

0:00 phone while typing (6 s) · 0:06 POV pills (6 s) · 0:12 title (3 s) · 0:15 the carry slowing, then freezing over the laptop, "Sorry" (8 s) · 0:23 the rule card and the high route (7 s) · 0:30 X asks, "Waiting for Pranay", flash (8 s) · 0:36 lights off, "Night, Pranay", the buzz (4 s) · 0:42 *Words spoken to it: 0* (3 s). No narration.

## Shot list

- The overhead camera feed (the robot's own); a phone on a tripod at three-quarter showing Pranay, the arm and the screen; a side phone at 60 fps for the stop, with a ruler on the dummy laptop.
- First-person bed shot: a phone held at the pillow, or a chest strap.
- A screen recording of Pranay's laptop running throughout the desk beats, for the Card cutaway over the call, in sync with the cameras.
- The stage view recorded to a file in sync with the cameras; the HUD comes from it. The muscle meter and the arm must share a frame at 0:54.
- A small speaker next to the arm for its voice; a lav on Pranay.
- The clamp move in one shot each time.
- Every beat: two clean runs before a take; every take logged with its time stamp so the clock in the HUD is real.
