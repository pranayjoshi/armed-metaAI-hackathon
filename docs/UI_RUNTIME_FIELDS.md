# Runtime fields the UI now reads (UI.md §10)

Written November 3, 2026, for the runtime session. The Card, Stage and Context views are built (`web/card/`, `web/views/stage/`, `web/views/context/`). Every field below is **optional**: the UI renders nothing when it is absent and never invents a value, so these can land one at a time. Until they do, the demo and `h2m run` show none of the §10 elements.

All times in seconds unless the name ends `_ms`. Coordinates are normalised 0..1 on the overhead frame.

## Snapshot (`/api/live`)

| Field | Shape | Used by |
|---|---|---|
| `chips` | `[str]` | Card. Muse hue for `muse-*` / `sam-*`, Armed accent for `Armed …` / `EMG decoder`. A chip shows only if listed, so list only what ran. |
| `calls` | `[{model, for?, started_at_ms?, elapsed_ms?, ms?}]` | Card, Context. `ms` unset = in flight (clock counts up from `elapsed_ms`, or `s.t*1000 - started_at_ms`, same clock as `s.t`). Keep a landed entry (with `ms`) in the list for ~1.5 s. `for` is a row id (`objects`, `present`, `hand`, `sounds`, `actions`, `routines`, `rules`, `clock`) or `"scan"`. |
| `selection.cards[i].evidence` | `[str \| {text, age_s?, source?}]` | Card. Plain words from the ranker; scan age as `age_s`. No gaze or posture. |
| `selection.cards[i].asked_by` | `str \| null` | Card. Only when another speaker asked. |
| `selection.cards[i].stale` | `bool` | Card. Target's scan too old; the gate will not move toward it. |
| `selection.auto` | `{card_id, remaining_s, total_s, chain?: {i, n}}` | Card veto ring. Keep `auto` with `chain` (`i` 1-based) in the snapshot while the routine runs. |
| `scene` | `{scan_age_s, stale?, limit_s, scan_ms, frame_url?, masks, packet?}` | Card (stale state), Context. |
| `scene.masks` | `[{phrase, polygon: [[x,y]…] \| bbox: [x0,y0,x1,y1]}]` | Context mask paint. |
| `scene.packet` (alias `context`) | `{clock (str or epoch s), objects: [{name, slot, age_s}], present: [str\|{name}], hand: {where, s} \| str, sounds: [{label, age_s}], last_actions, routines_due, rules}` | Context packet. No posture or gaze rows. |
| `quiet` | `{text, until_ms?}` | Card quiet strip (page times 3 s itself). |
| `transcript` | `{speaker, text}` | Card, speaker-labelled line from Voice Transcribe. |
| `arm.speed_frac` | `0..1` | Stage speed readout (100 · 70 · 40 · 0). |
| `arm.stop_latency_ms` | number | Card stopped state (already read). |
| `counts` | `{spark_calls, wonder, sam_scans, transcribed_min}` | Stage architecture frame. Otherwise derived from events since page load. |
| `learning.proposal.rehearsal` | `{old_route, new_route, progress?, state?, note?}`; routes are `[[x,y]…]`, `{points}`, or a route name from `/api/rules` | Stage rehearsal picture-in-picture. |
| `learning.proposal.verifier` | `{name: bool}` or `[{label, ok}]` | Rule card ticks (already read). |

## Events

- `incident.tool` must be emitted **and added to the event kinds list in `snapshot()` in `h2m/app.py`**, or it never reaches the page. Fields: `tool` (or `name` / `model`, matched against wonder, sam, measure, check), a one-line `result` (or `line` / `summary` / `detail` / `text`), optional `ok`, and for the checker `ticks: [bool, bool]` or `checks: [{ok}]`.
- `speech.utterance` should carry `audio_s` (or `duration_s`) so "minutes transcribed" is real; it shows "—" until then.
- `emg.brace` during an auto routine currently cancels the ring in the UI. An explicit `selection.auto_cancelled` event would be more robust than inferring it.

## Open questions for the runtime

1. Is there an endpoint to cancel an auto routine by click? The ring is brace-only until there is.
2. Should "words spoken to the robot" count only words addressed to the arm? The UI counts every `speech.utterance`.
3. Far-mode LAN decision (UI.md §3) is still open and needs `safety-reviewer`.

## Elsewhere

Earlier needs still stand: `GET /card` route, `h2m desktop` subcommand (snippet in `desktop/README.md`), per-card `pictogram`, scenario payload route/zone geometry and loop flag, and the `Scenario.run()` `_abort` reset at the pass boundary flagged for safety review.
