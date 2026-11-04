# Armed: what the arm does, and what it should do

Written November 3, 2026, against commit `c55b90e`. The first half is the inventory as it stands in the code. The second half is the action set the film needs if "a third arm" is going to look like one. Hardware facts that decide everything: the SO-101 is one arm with position servos, the taught jaw opening is about 2–3 cm, there is no force sensing beyond gripper load readback, every motion is a taught pose sequence, and anything near a person is a reviewed relaxation behind `near_person_enabled`.

---

## 1. What exists today

### Verbs (`h2m/contract.py`)
`pick_place` (object to the tray) · `move` (object to another slot) · `place_near_user` (held object to the hand spot) · `point` · `gesture` · `skill` · `say` · `home` · stop / cancel / confirm.

### Taught skills (`configs/skills.yaml`)
| Skill | What it is | Near person |
|---|---|---|
| `wave`, `bow`, `celebrate`, `sorry` | expressive sequences | no |
| `hold_up` | hold the held object up at eye level (the phone beat) | yes |
| `take_this` | close on an offered object, wait for the hand to let go, put it away | yes |
| `sip` | straw to the mouth, from below | yes |
| `press_button` | press down on a taught spot | no |

### Gestures (`h2m/embodied.py`)
`nod`, `tilt`, `shake`, `look_at_user`, `thinking`, all inside a 15° envelope of home.

### Muse tools
`pick_place`, `place_near_user`, `look_at`, `gesture`, `say`. Skills are reachable by typed command and by cards; the model's tool list does not yet include them.

### Poses
19 taught (home, slots 1–3, tray, handover pair, via routes, wave, sorry); 9 placeholders (bow, celebrate, hold_up, face pair, press pair).

### Demo beats already planned 
These are the actions the arm was going to perform in the film, each built from the verbs and skills above.

| Beat | Where | Built from | State |
|---|---|---|---|
| Hello / goodbye: wave, look at you, bow | desk | `wave`, `bow`, `look_at_user` | wave taught |
| Three cards, one twitch: pick from any spot to the tray | desk | `pick_place` | taught, runs |
| Show the phone while you type: pick it, hold at eye level, put back | desk | `hold_up` (near person) | pose placeholder, review pending |
| Take this: someone hands you something, the arm takes it and puts it away | desk, bed | `take_this` (near person) | handover poses taught, review pending |
| Hand me the cup | desk, bed | `place_near_user` | taught |
| Water to your mouth: straw to 6 cm from the lips | desk (the bed arm cannot reach) | `sip` (near person) | face poses placeholders, review pending |
| Pills at 8am: pill cup from its slot to your hand | bed | pick then `place_near_user` | not chained yet |
| It notices: pen cup bumped off its spot, moved back | desk | `move` | taught |
| Press the call button or the light | bed, desk | `press_button` | poses placeholders |
| Flinch over the laptop, "sorry", the high route next time | desk | brace stop, `sorry`, routes | sorry taught, via routes taught |
| Point / where are my keys | desk | `point`, `look_at_user` | taught |
| Send a message | anywhere | `send_message` | not built |
| Headboard scene: water, pills, phone from the bed | bed | second poses file | day 2 |
| Hold this beside you | bed | `hold_side` | listed, not built |
| Forehead thermometer | bed | — | listed, not built |
| Closed-jaw push (nudge an object) | desk | — | listed, not built |
| Gentler face approach | bed | `face_low` | listed, not built |

### The honest read
The planned beats already cover a good spread: someone handing you something, the phone, pills, water, tidying, a button, the bedside. What they have in common is that the arm moves an object between taught places and the person watches. Nothing yet works *with* the person mid-task (the arm holding while your hands do something), nothing perceives *for* them (reading a label, a note, a thermometer), nothing hands anything to a second person, and nothing chains more than one step under one card. Those four gaps are what the updated set fills; the planned beats all stay.

## 2. What makes an action impactful here

Four properties, in order of how much they move a viewer. An action that has two of them is worth filming; one that has none is filler.

1. **Collaboration, not fetching.** The arm does half of a task while the person does the other half with their own hands. A third hand is most obviously a third hand when the other two are busy in the same shot.
2. **Perception for the person.** The arm brings something to the camera and Muse reads, recognises or checks it, then speaks. This is where the model earns its place in the loop and where the assistive story is strongest.
3. **A change in the room**, not just a change of object position: a light, a switch, a message, another person's hands.
4. **A chain, not a step.** Two or three taught actions as one card ("tidy up"), with the arm deciding order from the scene, is the difference between a macro and an assistant.

---

## 3. The updated action set

Each row: what the person sees, why it is more than a fetch, what it needs on this hardware, and the risk. **Keep** means it already exists and stays; **new** means a new skill or tool; **R** means a reviewed relaxation.

### Tier 1: the actions that make the film (build these first)

| Action | Card | What it shows | Needs | Risk |
|---|---|---|---|---|
| **Hold this steady** (new, R) | *Hold the jar?* | Bimanual with a human: the arm grips a jar, a bottle or a cable end at the hand spot and holds it while you unscrew, plug, or tie. The torque cap and load readback become visible, since the grip must hold against your twisting without crushing. The most literal third-hand moment possible. | `take_this`'s grasp, then a hold stage with a torque limit from the object's `max_grip`, release on a twitch or when load drops; a jar the jaws can hold (2–3 cm lid, or a bottle neck) | Review: a held object under human force; the stop must release cleanly |
| **Hold the light** (new, R) | *Light on your hands?* | The arm holds a small torch and points it where your hands are working (a cable behind the desk, a watch strap, a sewing needle). The classic reason people wish for a third arm, and the pointing follows your hands from the overhead camera. | A torch the jaws hold; `point` toward the hand landmark's table position, re-targeted every second inside a taught envelope | Review: a pointed object near the person; cap the envelope and speed |
| **Read this to me** (new) | *Read it?* | You hold up a letter, a pill bottle, a receipt, or the arm picks it from a slot; the arm brings it to the wrist camera and Muse reads it aloud or answers "what is this?". Assistive for low vision, dyslexia and anyone whose hands are full; the model does real work on camera. | `hold_up` to a taught camera pose; Spark vision on the wrist frame; `say` with a 280-character cap and speech cut-off on stop | None physical; the words are model output and are spoken, never acted on |
| **Desk check** (new chain) | *Clear 2 things?* | Every `desk_check_every` (default 30 minutes, user-editable) Muse Spark looks at the overhead frame and the world tracker and sorts what is on the desk into belongs (catalog objects on their slots), out of place (catalog objects off their slot: put back) and unwanted (rubbish, used cups, anything not in the catalog and not on the keep list: bin or tray). If there is nothing, it says nothing. If there is something, it proposes the chain, with the reason lit on the card, and the order chosen from the scene. The arm is seen planning, not fetching. | `move` chained; each object's home slot in the catalog; a chain card that confirms once and shows progress 1/3 · 2/3 · 3/3; brace stops the chain | Low; three picks in a row is the reliability test the whole film needs anyway |
| **Give it to X** (new, R) | *Hand the block to X?* | The arm hands an object to a second person at a second hand spot, facing the other way. Reverses the consent beat: you ask it to act on someone else. | A second handover spot taught toward the room; `place_near_user` with a target person; the hand watchdog | Review: a release near a person who is not the wearer |
| **Lights and switches** (keep `press_button`, add `set_light`) | *Lights off?* | The arm presses a physical lamp switch, or the runtime flips a smart plug. The physical press is the one to film: a third arm that reaches the switch you can't. | `press_button` taught over a large rocker switch; a smart plug as the fallback | Low |
| **Forehead thermometer** (was listed, not built; R) | *Take your temperature?* | The arm holds a no-touch forehead thermometer at the taught distance, the reading goes to the wrist camera, Muse reads it aloud and logs it. Bedside care that a person alone cannot do for themselves, and perception in the same beat. | A thermometer the jaws hold; a face pose like `face_above` but at the forehead; `read_to_me` on the display | Review: near the face; same cap as `sip` |
| **Hold this beside you** (was listed, not built; R) | *Hold it here?* | The arm holds a book, a tablet or a cup still beside the bed at a taught spot, for as long as you want, released on a twitch. The bedside version of hold-this-steady. | `hold_side` as a held stage at a taught pose; release on twitch; load watchdog | Review: a long hold near a person; cap the speed and add a time limit |
| **Nudge** (was listed, not built) | *Push it closer?* | Closed-jaw push of an object toward you or off a spot, for things the jaws cannot grip (a plate, a book). | `press_button`'s closed-jaw motion along a taught line | Low |
| **Phone at eye level, following you** (keep `hold_up`, add tracking, R) | *Show you the phone?* | The phone stays in front of your face while you lean; the arm turns slowly to follow, using head direction. A video call you can take with no hands. | `hold_up` plus a slow pan inside a ±15° envelope driven by the head-direction estimate | Review: already a near-person skill; add the envelope and a speed cap |

### Tier 2: strong, if the day allows

| Action | What it shows | Needs | Risk |
|---|---|---|---|
| **Pour, dry** | The arm pours cereal, rice or beans from a small cup into a bowl with a wrist roll. The one motion on the list that looks like dexterity. Never liquid near electronics. | A taught pour pair (above the bowl, rolled); a light cup the jaws hold; dry goods | Low; mess |
| **Today's pills** | A weekly row of small labelled cups; the arm picks the one for today after Muse reads the labels from the overhead frame and the clock. Routine plus perception plus assistive need in one beat. | Seven small cups on a taught row of slots; Spark reads the day labels; the routine knows the day | Low physically; wrong-cup risk is the point of the confirm step |
| **Take a photo for me** | The arm brings the wrist camera to a page or an object and the runtime saves and reads back the frame, or sends it to X. | `hold_up` camera pose; the message tool | None |
| **Drop it in the bin** | A `pick_place` whose target is the bin; trivial, but "throw this away" is one of the most asked-for things from people who cannot reach. | A bin slot | None |
| **Steady my cup** | The arm holds a cup still at the hand spot while you drink through a straw from the chair, without the near-face approach. The bedside sip without the review. | `take_this` grasp, hold at the hand spot | Low |

### Tier 3: looks good, cut first

Stir · wipe the table with a cloth over a zone · open a drawer by a loop handle · flip a page · `celebrate`, `bow` and the composed gestures. None of them collaborates, perceives, changes the room or chains. They are for the last ten seconds of the film if there is time, and nowhere else.

---

## 4. The final cut's actions (`DEMO_STORY.md`, fourth revision)

| Beat | Action | Built from |
|---|---|---|
| Phone while typing | pick the phone, hold at eye level | `hold_up` (R) |
| Straw at the desk | straw to the lips | `sip` (R; README clip) |
| Pills; X asks for the marker | pick to the hand spot | pick + `place_near_user` |
| Context, then the jar | grip the held-out jar and hold while it is opened | `hold_steady` (new, R) |
| Water over the laptop | carry on the direct route, slowing with the muscle level, freezing on the brace | `pick_place` to the hand spot + comfort coupling (new, R) + brace stop + `sorry` |
| High route | the same carry on `via_high` | rules + routes |
| Explain to X | spoken rule from its incident | `recall` (new), `say` |
| Desk check | look, propose, clear two things to bin and tray, take a refusal | `look_at`, `move` chain (new), bin slot, refusal rule (new) |
| The friend | bring the block to the hand spot | pick + `place_near_user` |
| Lights off | plug | `set_light` (new) |

Out of the cut, kept as README GIFs: hold the light, the thermometer, take the mug, tidy at six as a routine card, read-this-to-me, today's pills, pour, wave, bow, celebrate.

## 5. What this costs

| Work | Owner | Time |
|---|---|---|
| Teach: jar hold spot, torch grasp and point envelope, camera pose, second handover spot, lamp switch pair, bin slot, pour pair, pill row | user, `hardware-guide` runbook | 2–3 hours of arm time |
| Skills: `hold_steady`, `hold_light`, `read_to_me`, `tidy` chain, `give_to`, `pour`, `today_pills` | `runtime-builder`, `embodied-engineer` | 1.5 days |
| Muse: `read_to_me` vision call with the cap and cut-off; pill-label reading; skills exposed as tools | `muse-integrator` | half a day |
| Reviews: hold-steady, hold-light, give-to, phone tracking | `safety-reviewer`, batched in one request | one round |
| Props: jar with a 2–3 cm lid or a bottle, small torch, labelled pill cups, a large rocker lamp switch, dry cereal and a bowl | user | an hour |

Build order: tidy chain (reliability first, nothing new near a person) → read-to-me (no physical risk, big payoff) → hold-steady → hold-light → give-to → lamp press → pills → pour. Everything in Tier 1 reuses `take_this`, `hold_up`, `point` or `move`; no new motion primitive is required, only new taught poses and chains.

---

## 6. What not to add

- **Liquid pouring.** Water near a laptop and electrodes is the one accident the film cannot survive.
- **Fine manipulation**: pills by the pill, keys, cables into ports. The jaws are 2–3 cm and there is no force sensing. A failed grasp on camera costs more than the beat earns.
- **Anything learned by a policy in the demo** until it has passed two clean runs inside the gate.
- **Gestures for their own sake.** The arm nods when it is asked something and bows once at the end. That is enough.
