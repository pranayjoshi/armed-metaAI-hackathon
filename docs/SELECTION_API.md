# Selection API (Reflex cards)

Cards the person answers with one muscle (Reflex build plan items 2b and 2c). State
machine: `h2m/selection.py`. Wiring: `h2m/app.py` (section "selection"). Endpoints:
`h2m/server.py`. Config: `selection:` in `configs/h2m.yaml`. Tests: `tests/test_selection.py`.

The endpoints follow `docs/LOCAL_API.md`: Host must be local, a browser `Origin` must be
this origin, and every `POST` needs `X-H2M-Token`. There is no tokenless selection route.

## Inputs

| Person does | UI equivalent | Effect |
|---|---|---|
| (the UI draws the highlighted card) | `POST /api/selection/ack` | Starts that card's reaction clock (`require_ack`) |
| twitch (`emg.twitch`) on the menu | `POST /api/selection/accept` | **Select** the highlighted card: a motion card is announced and asks "confirm?"; a `say` card is taken at once |
| twitch while a card asks "confirm?" | `POST /api/selection/accept` (same pins) | Confirm it: the card's request goes through the gate |
| double twitch (`emg.double_twitch`) | `POST /api/selection/step` | Next card (wraps); while asking "confirm?", back to the menu |
| brace / open hand on an idle arm | `POST /api/selection/dismiss` | Refuse the highlighted card |
| brace / open hand while moving | (`POST /api/stop`) | Freeze, exactly as before; the task ends |
| twitch-band burst onset (`emg.burst_onset`) while moving or a plan is in flight | | Freeze (stop reason `twitch-band burst while moving`); idle arm: nothing |

- **Off by default.** `selection.enabled` ships `false`. Only the literal `true` turns
  the cards and the twitch-as-answer on.
- **Render acknowledgement.** With `require_ack: true` (shipped) the reaction clock for
  a highlight starts when the UI says it drew that exact card: `POST
  /api/selection/ack {"menu", "card_id"}`, timed by its arrival on the server. A select
  with no ack for the highlighted card is refused ("the screen has not shown that card
  yet"). Every new highlight (shown, stepped to, dismissed onto, a refused accept, back
  from "confirm?") clears the ack: the UI acks again when `acked` is false. Headless
  tests and scanning runs without a UI set `require_ack: false`; then the clock starts
  at the server-side highlight.
- **Reaction gate.** A select counts only if the input began at least `reaction_s`
  (0.7 s, floor 0.3) after the later of the highlight and its ack. A twitch's onset is the
  earliest of the detector's `onset_t` and the event time minus `detect_ms` (an earlier
  onset only makes the gate stricter); a missing or non-finite onset fails closed. A UI
  accept is gated on its arrival time.
- **After a veto.** No select or confirm begins within `after_veto_s` (3 s, floor 3) of
  the last brace or open hand: the ones that refused a card, the ones swallowed by the
  refractory, the ones that cancelled a request and the ones that froze the arm. Such an
  accept is refused and the card is highlighted afresh (fresh ack, fresh reaction). One
  recorded startle refused a card with an open hand and then twitched 0.9 s later.
- **Two steps for a motion card.** The first twitch selects: the arm says
  "<label>? Twitch again to confirm, or double twitch to go back." (spoken and in
  `reply`), and the card shows `confirm: true`. The confirm clock starts when that
  announcement went out (`selection.announced`); a second twitch at least `reaction_s`
  after it confirms. A double twitch (or `ui_step`) goes back to the menu, a brace or
  open hand refuses the card, and an unanswered question lapses back to the menu after
  `confirm_s` (8 s, cap 30; `selection.lapsed`). A `say` card is one step.
- **Confirming goes through the normal path.** The card's tool call goes to
  `Embodied.handle_tool_call`, then contract, safety gate, pending confirm (with its
  compare-and-set and request epoch). The card's motion always asks; the confirming
  twitch is the answer to exactly the request that card raised, pinned by identity. The
  re-check with `confirmed=True` decides. A card the gate holds (arm not armed, route not
  proven, ...) goes back to the menu, its reason goes in `reply`, and a retry needs a
  fresh select.
- **A warning the card never showed is not answered by it.** The confirming twitch
  answers the gate's question only when that question is the plain confirmation
  (`safety.CONFIRM`). If it carries a warning (gripper not proven empty: "if it holds
  something, it drops when I open"; holding something on a look), the request is left
  waiting with the warning in `reply`, and only a squeeze, "yes" or a click answers it
  (normal reaction gate from that question). `selection.accept` then has `ok: true,
  answered: false`. At start-up the gripper is not proven empty, so the first carry needs
  that answer.
- **Another answer wins the race.** If a squeeze, "yes" or click confirms the card's
  request before the twitch does, the twitch's confirm finds nothing, but the task keeps
  the card's contract and waits for its motion (`selection.accept` `ok: true`, detail
  "confirmed by another answer").
- **One card at a time.** While a card is being checked, no other input is taken.
- **A menu belongs to one arming.** Cards record the arm's `arm_count`. After reset + arm
  the old cards go (`task.abandoned`, outcome `cancelled`, reason "the arm was re-armed")
  and new ones go up at once; an accept that races the rebuild is refused.
- **Blocked inputs.** While a request is waiting (voice, typed, click), a rule proposal is
  waiting, the arm is moving or a plan is in flight, an operator release is open, or an
  incident is queued, being captured or being explained, accept and step are refused
  (`blocked` in `GET /api/selection` says why) and no new cards go up. A twitch never
  confirms any request that is not the highlighted card and never approves a rule (only a
  squeeze, "yes" or the UI approve button do).
- **Brace and open hand.** The freeze logic runs first and is unchanged. Only a brace or
  open hand that neither stopped the arm nor cancelled a waiting request or in-flight plan
  dismisses a card. One flinch often fires both events: within `veto_refractory_s`
  (1.5 s) the second is counted but refuses no second card.
- **Burst onset.** `emg.burst_onset` (payload `onset_t`) comes on the first hop of a
  calm rise above the twitch level, long before a twitch is decided. With the arm moving
  or a plan in flight the app calls the brace's `stop_if_active` first, with nothing
  (no lock wait, model, network or disk) ahead of it; then it drops the request epoch and
  any pending request, cuts speech and starts an incident (`source: burst`). The stop
  latches until reset + arm. On an idle arm it does nothing (a squeeze or twitch may
  follow).
- Cards go away whenever the arm moves or stops, and on cancel or disarm. The Muse ranker
  is cancelled then too.

## Modes

- `intent` (default): up to `cards` (3) cards from `h2m.intent.IntentRanker`. The
  deterministic ranking (history recency and frequency, head direction as tie-break, then
  scene order) shows at once. Muse Spark is asked off-thread only with the arm READY or
  STOPPED and the app idle. Its ranking applies only before the person's first input on
  that menu, and never changes the highlighted card: that card stays where it is (same
  highlight time and ack) and only the cards behind it are reordered (`selection.shown`
  with `upgraded: true`, `kept`). A ranking that arrives before the first cards go up is
  used as is.
- `scanning` (the baseline): every option in the fixed scene order (picks, put down near
  me while holding, looks, taught phrases), no model. Stepped by double twitch, accepted by
  twitch; same executor and gate.

**Degradation.** If the ranker reports `no_key`, `timeout_or_error` or `error`, the mode
switches to `scanning` for the rest of the run (event `selection.degraded`). The cards
already showing stay. An `invalid_reply` keeps the deterministic intent cards. `POST
/api/selection/mode` with `intent` clears the degraded flag.

## Endpoints

All answer `200 {"ok": true, "detail": ...}` on success and `409 {"detail": why}` when
refused. A body that is not a JSON object, or a pin of the wrong type, answers `400`.

### `GET /api/selection`

```json
{
  "enabled": true,
  "blocked": null,
  "mode": "intent", "configured_mode": "intent", "degraded": null, "k": 3,
  "menu": 4, "source": "fallback",
  "cards": [
    {"id": "pick_place:cup", "kind": "pick_place", "label": "Bring paper cup",
     "confidence": "maybe", "source": "fallback", "object_id": "cup", "text_id": null,
     "contract": {"verb": "pick_place", "...": "..."}, "highlighted": true,
     "confirm": false}
  ],
  "cursor": 0, "highlighted": "pick_place:cup", "highlight": 1,
  "require_ack": true, "acked": true, "confirming": null,
  "accept_in_s": 0.12,
  "checking": false,
  "task": {"task": 4, "mode": "intent", "selections": 1, "counts": {"twitch": 0, "...": 0},
           "card": null, "card_kind": null, "contract_id": null, "elapsed_s": 2.4,
           "to_motion_s": null},
  "last_task": {"...": "the summary of the last task.completed / task.abandoned"},
  "tasks_done": 3
}
```

`accept_in_s` is the time left before an accept can count (`null` while an ack is
needed, or while the "confirm?" announcement is still going out). `highlight` is bumped on
every new highlight; `acked` says whether the UI acknowledged it; `confirming` is the card
asking "confirm?" (its card has `confirm: true`). `checking` is true while a card goes
through the gate. The same object is in `GET /api/state` (and `/api/live`)
under `selection`. Card labels, ids and contracts are built by `h2m.intent` from the
visible, reachable scene and taught phrases, never from model text.

### `POST /api/selection/ack`

Body (both required): `{"card_id": "pick_place:cup", "menu": 4}`. The UI drew this
highlighted card. Refused if the menu changed or the card is not the highlighted one.
Only the first ack of a highlight counts. Never moves anything and is not a selection
input.

### `POST /api/selection/accept`

Body (both required): `{"card_id": "pick_place:cup", "menu": 4}`, from the
`GET /api/selection` the person saw. Refused if the menu changed, the card is not the
highlighted one, no ack was given (`require_ack`), the reaction time has not passed, a
veto was too recent, the arm was re-armed, inputs are blocked, or the gate does not
approve the request. On a motion card the first accept selects it and answers with the
announcement; the second (same pins, `reaction_s` after the announcement) confirms it,
runs the gate in a worker thread and answers with its result.

### `POST /api/selection/step`

Body optional: `{"menu": 4}`. Highlights the next card, or goes back to the menu from
"confirm?".

### `POST /api/selection/dismiss`

Body optional: `{"card_id": "...", "menu": 4}` (pins are checked when given). Removes the
highlighted card. Refusing the last card ends the task (`task.abandoned`, outcome
`dismissed`) and new cards wait `reshow_s`.

### `POST /api/selection/mode`

Body: `{"mode": "intent"}` or `{"mode": "scanning"}`. Ends the current task (outcome
`cancelled`) and puts up the new menu on the next idle tick.

```bash
TOKEN=$(curl -s http://127.0.0.1:8080/ | sed -n 's/.*name="h2m-token" content="\([^"]*\)".*/\1/p')
curl -s http://127.0.0.1:8080/api/selection
curl -s -X POST -H "X-H2M-Token: $TOKEN" http://127.0.0.1:8080/api/selection/step
curl -s -X POST -H "X-H2M-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"card_id": "pick_place:block", "menu": 4}' http://127.0.0.1:8080/api/selection/accept
```

## Events (for `h2m bench`)

| Event | Fields |
|---|---|
| `selection.shown` | `menu`, `task`, `mode`, `source`, `cards` (ids), `labels`, `cursor`, `upgraded` (Muse reordered the cards behind the highlighted one, `kept`) |
| `selection.step` | `menu`, `task`, `by`, `ok`, `cursor`, `card`, `label`, `back` (true: back from "confirm?") (or `ok: false`, `detail`) |
| `selection.select` | `menu`, `task`, `by`, `ok`, `card`, `label` (a motion card selected; refusals are `selection.accept` with `ok: false`) |
| `selection.announced` | `menu`, `task`, `card`, `text` (the confirm clock starts here) |
| `selection.lapsed` | `menu`, `task`, `card` (an unanswered "confirm?" went back to the menu) |
| `selection.accept` | `menu`, `task`, `by`, `ok`, `answered` (false: the request waits for a squeeze / yes / click), `detail`, `card`, `card_kind` (refusals: `ok: false`, `detail`) |
| `selection.dismiss` | `menu`, `task`, `by`, `ok`, `card`, `remaining` (or `ok: false`, `detail`) |
| `selection.degraded` | `note`, `from`, `to` |
| `selection.mode` | `mode`, `previous` |
| `task.completed` | task summary + `outcome: completed`, `to_complete_s` |
| `task.abandoned` | task summary + `outcome` (`dismissed`, `stopped`, `fault`, `cancelled` (also: re-armed), `interrupted`, `incomplete`), `reason` |

`by` is the input: `twitch`, `double_twitch`, `brace`, `open_hand`, `ui_accept`,
`ui_step`, `ui_dismiss`. The task summary is `task`, `mode`, `selections` (sum of
`counts`), `counts` (one per input; every input during the task counts, including
refused ones and the brace that froze a motion; a squeeze, "yes" or click that answered a
warning is not a selection input and is not counted), `card` and `card_kind` (the accepted
card), `contract_id`, `elapsed_s`, and `to_motion_s` (first card shown to the accepted
contract's `arm.started`). A task runs from the first card shown to the accepted motion's
last `arm.completed` (for `place_near_user`, the second stage), or to the speech for a
`say` card. A confirmed request uses `confirmed_by: "twitch"` or `"ui_card"`.

## Config (`selection:`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` (shipped; absent = off) | Only the literal `true` turns cards and the twitch-as-answer on |
| `reaction_s` | `0.7` | Select: this long after the highlight and its UI ack. Confirm: this long after the announcement. Floor 0.3 |
| `require_ack` | `true` | The reaction clock starts at `POST /api/selection/ack` for that exact card; no ack refuses. Only the literal `false` turns it off (headless tests, scanning runs without a UI) |
| `after_veto_s` | `3.0` | No select or confirm begins this soon after any brace or open hand (swallowed ones too). Floor 3.0 |
| `confirm_s` | `8.0` | An unanswered "confirm?" goes back to the menu. Cap 30 |
| `mode` | `intent` | `intent` or `scanning` |
| `cards` | `3` | Intent cards shown (at most 3) |
| `muse` | `true` | Ask Muse Spark to rank (needs `MODEL_API_KEY`); `false` = deterministic ranking only |
| `deadline_s` | `2.5` | Later Muse rankings are dropped; a timeout degrades to scanning |
| `spark` | `{model: null, timeout_s: 2.0, reasoning_effort: minimal}` | `SparkClient` config; `base_url` defaults to `compiler.base_url` |
| `reshow_s` | `3.0` | Quiet time after a task is refused, stopped or cancelled |
| `veto_refractory_s` | `1.5` | Brace and open hand this close together are one refusal |
| `table_x` | `[0.0, 1.0]` | Table x range for head-direction thirds |
| `phrases` | `{}` | Taught `say` cards, `{id: text}` |

The scripted demo (`h2m demo`) turns selection and its Muse ranking off.

## Not verified

Only run on the virtual arm with synthetic EMG events (and one end-to-end pass through the
synthetic EMG detector) and a mocked Muse. The Spark wire format has not been exercised
against the live API, no real twitch has been recorded against a card, the web UI does
not send `POST /api/selection/ack` yet (so with `require_ack: true` a UI run refuses every
select until it does), and `reaction_s`, `after_veto_s`, `confirm_s` and the veto
refractory are untuned. The burst-onset freeze has only seen synthetic `emg.burst_onset`
events and the synthetic detector; how often a real moving-arm session fires one (false
stops) is unmeasured.
