# Replay mode and the software demo

The entry runs with
**no arm, no API key and no network**. Backend: `armed/replay.py` (loaders),
`armed/scenario.py` (scripted simulation), `armed/sim_view.py` (schematic table view),
`armed/server.py::create_replay_app` (HTTP).

```bash
armed demo                       # bundled sample + the scripted scenario, http://127.0.0.1:8080
armed demo --loop                # re-run the scenario after it ends (demo.loop_pause_s)
armed demo --no-scenario         # the bundled sample only
armed replay data/incidents/<id> # one saved incident
armed replay data/sessions/<id>  # a session, plus the incidents it saved
armed replay data/incidents      # a folder of incidents (newest 200)
.venv/bin/python scripts/make_demo_sample.py   # regenerate data/demo/ (about 10 s)
```

## Rules

- **Read-only.** The replay server has no actuation route at all: no request, confirm,
  choose, cancel, arm, home, gripper, EMG drive or calibration, session, rule approval or
  rule deletion. Those paths answer 404 / 405 even with the token. The only POST is
  `/api/stop`.
- **Same local-API rules** as the live server (`docs/LOCAL_API.md`): Host must be
  `127.0.0.1` or `localhost`, a foreign `Origin` is refused (403, WebSocket 1008), and any
  non-GET except `/api/stop` needs `X-Armed-Token`. `GET /` writes the token meta tag and
  also `<meta name="armed-mode" content="replay">`. A hosted deployment therefore needs to
  reach it as `localhost` (for example a tunnel); the Host check is not relaxed.
- **Labels.** Every payload carries `label` and `simulation`:

  | label | when |
  |---|---|
  | `SIMULATION` | the scenario, the bundled sample (`"synthetic": true` in meta.json), or an incident whose saved `provenance.simulation` is true (virtual arm, synthetic EMG or the virtual camera) |
  | `RECORDED` | `provenance.simulation` is false: real arm, real EMG, real camera |
  | `UNKNOWN SOURCE` | no provenance (incidents saved before this change) |
  | `REPLAY` | the server-level label in `armed replay` (each recording has its own) |

- **Explanations.** A stored `explanation.json` is shown as stored, with its `source`
  (`wonder`, `fallback` or `canned`). With none stored, the fixed text in
  `armed/replay.py::CANNED_EXPLANATION` is shown with `"source": "canned"`,
  `"canned": true`, `"stored": false` and a `note`. Never present it as model output.
- **Proposed rules** are shown with `"approved": false` and the problems the runtime's
  validation would find (unknown zone or tag). Replay never saves one.

## The scripted scenario (`armed demo`)

The real runtime (`Armed`: grammar, gate, rules, routing, learner, incident capture) with
the virtual arm, `SyntheticSource` EMG on the default profile, no camera (the learner
samples a rendered schematic of the virtual table instead), speech and voice off, the
compiler LLM off and the Muse client replaced by `CannedMuse`. It refuses to start
unless the arm backend is `virtual` and the EMG is synthetic. Rules and incidents go to a
temp work dir, never `data/rules` or `data/incidents`. Pacing is `demo:` in
`configs/armed.yaml`; every gate, rule and arm threshold is the shipped one.

Steps (`steps[].name`): `setup`, `request` ("hand me the cup": the gate asks), `confirm`
(scripted, the same `confirm(by="ui")` as a click), `carry` (direct route, until the
schematic gripper reaches the laptop zone), `brace` (both muscle groups driven on the
synthetic source; the real brace detector fires and the arm freezes), `incident`,
`explain` (canned; rule proposed), `approve` (scripted `approve(by="ui")` after
`approve_after_s`, never inside the reaction-time gate), `rearm` (the person takes the
cup back: gripper contents become unknown; the operator resets and arms),
`rerun_request` (the rule applies: `via_left`, still asks), `rerun_confirm`,
`rerun_carry` (completes on the long route).

`POST /api/stop` stops the virtual arm and aborts the scenario (`state: "aborted"`); the
stop stays latched. The script only ever resets the stop its own brace caused.

## Endpoints

All GET unless noted. Times in incident payloads are seconds relative to the freeze
(`t = 0`); scenario times are seconds since the pass started.

| Path | Returns |
|---|---|
| `GET /api/state` | `{mode: "replay", label, simulation, note, replay: {incidents, sessions, routes_error}, scenario}`; while the scenario runs, merged over the normal live snapshot (`arm`, `emg`, `learning`, `events`, ...) so the operator views render it |
| `WS /api/live` | the same object, every 0.1 s (1 s without a scenario) |
| `POST /api/stop` | tokenless. `{"ok": true, "detail": ...}`; stops and aborts the scenario, else "nothing is moving" |
| `GET /api/replay` | `{label, incidents: [summary], sessions: [summary], scenario: bool, zones}` |
| `GET /api/replay/incidents` | `{incidents: [summary]}` |
| `GET /api/replay/incident/{id}` | full incident payload (below) |
| `GET /api/replay/incident/{id}/emg` | EMG series (below) |
| `GET /api/replay/incident/{id}/timeline` | `{id, label, timeline}` |
| `GET /api/replay/incident/{id}/frame/{index}/{cam}.jpg` | keyframe JPEG, `cam` = `overhead` or `wrist` |
| `GET /api/replay/session/{id}` | session payload (below) |
| `GET /api/replay/scenario` | scenario state (below); 404 without a scenario |
| `GET /api/replay/scenario/timeline` | `{label, steps, events}` |
| `GET /api/replay/scenario/frame.jpg` | the current schematic view (SIMULATION watermark) |
| `GET /api/camera/scene.jpg` | the same frame, for views that show the live camera |
| `GET /api/rules`, `/api/rules/proposal`, `/api/incident`, `/api/incident/{id}` | read-only mirrors of the live routes (the scenario's own rule store) |

Errors: 400 bad id, 404 unknown id or frame, 503 unreadable recording.

### Incident summary / payload

```json
{
  "id": "demo-liquid-over-laptop", "kind": "incident",
  "label": "SIMULATION", "simulation": true, "synthetic": true,
  "provenance": {"arm": "virtual", "arm_hardware": false, "emg": "synthetic",
                 "emg_hardware": false, "camera": "virtual", "simulation": true},
  "held": "paper cup (tags: liquid, fragile)", "trigger": "brace", "frames": 7,
  "say": "You braced as I carried ...", "explanation_source": "canned",

  "note": "SYNTHETIC sample: ...",
  "objects": [{"id": "cup", "table_xy": [0.25, 0.55]}, {"id": "laptop", "kind": "avoid_zone", "table_box": [...]}],
  "frames": [{"index": 0, "label": "t-6.0s", "t": -6.0,
              "cams": {"overhead": "/api/replay/incident/<id>/frame/0/overhead.jpg"}}],
  "joints": {"names": ["shoulder_pan", ...], "t": [...], "q": [[...6 values...]], "units": "degrees; gripper % open"},
  "stages": [{"t": -2.4, "stage": "carry", "route": "direct", "verb": "pick_place",
              "object": "paper cup", "tags": ["liquid", "fragile"], "applied_rules": []}],
  "emg": {"url": ".../emg", "onset": {"t": -0.13, "source": "recorded", "trigger": "brace"},
          "samples": 136, "error": null},
  "explanation": {"cause_object": "laptop", "say": "...", "confidence": 0.8, "rule": {...},
                  "source": "canned", "stored": true, "canned": true, "note": "Canned explanation: ..."},
  "proposed_rule": {"rule": {"when_holding": ["liquid"], "avoid": "laptop", "response": "route_around"},
                    "approved": false, "valid": true, "problems": [], "note": "Proposed only. ..."},
  "timeline": [{"t": -6.09, "kind": "clip.start"}, {"t": -2.39, "kind": "stage", "label": "carry (direct route)"},
               {"t": -0.13, "kind": "brace.onset"}, {"t": 0.0, "kind": "freeze"},
               {"t": 0.93, "kind": "clip.end"}, {"t": null, "kind": "explanation", "source": "canned"},
               {"t": null, "kind": "rule.proposed", "valid": true}]
}
```

`emg.onset.source` is `recorded` (saved with the trigger) or `derived` (older incidents:
the last calm sample before the brace crossing nearest the freeze), or `onset` is null.

### EMG series

```json
{"id": "...", "label": "SIMULATION", "t": [-6.0, ...],
 "channels": {"flexor": [...], "extensor": [...], "brace": [...]},
 "brace_effort": 0.35, "brace_calm": 0.12, "onset": {...}, "downsampled_by": 1,
 "units": "normalized effort (0..1) on the fast brace window; t in s from the freeze", "error": null}
```

Channels are the brace detector's fast window (`emg.npy` columns 1-3). Other shapes are
named `ch1..chN`. At most 2000 points (strided).

### Session payload

`{id, kind: "session", label, participant, events (count), incidents: [ids], meta,
trials: [rows of trials.jsonl], timeline: [event with t relative to the first event,
bulky fields reduced], truncated, braces: [{t, onset_t, trace: {t, flexor, extensor,
brace}}]}`. The session summary in `/api/replay` has `trials` as a count. Incidents named
in `incident.saved` events are added to the library when their folder exists.

### Scenario state

```json
{"simulation": true, "label": "SIMULATION", "note": "SIMULATION: virtual arm, ...",
 "state": "running", "run": 1, "step": "carry",
 "steps": [{"name": "carry", "label": "...", "status": "active", "t_start": 2.5, "t_end": null, "detail": ""}],
 "t": 4.1, "incident_id": "...", "brace": {"trigger": "cocontraction", "detect_ms": 177, ...},
 "explanation": {"source": "canned", "canned": true, "note": "...", "say": "...", "rule": {...}},
 "proposal": {"rule": {...}, "expires_in_s": 30.0}, "rule": {"id": "r...", "approved": true, ...},
 "runs": [{"run": 1, "route": "direct", "applied_rules": [], "verdict": "ask",
           "outcome": "frozen: brace (EMG)", "closest_to_zone": 0.0, "frozen_at": [0.29, 0.27]},
          {"run": 2, "route": "via_left", "applied_rules": ["r..."], "verdict": "ask",
           "outcome": "completed", "closest_to_zone": 0.12}],
 "error": null, "settings": {...},
 "arm": {"state": "moving", "backend": "virtual", "hardware": false, "stage": "carry", "joints": {...}},
 "emg": {"source": "synthetic", "hardware": false, "brace": 0.02, ...},
 "gripper_xy": [0.28, 0.33], "frame_url": "/api/replay/scenario/frame.jpg"}
```

`state`: `idle`, `running`, `done`, `aborted` (operator stop or an unplanned stop) or
`failed` (the runtime did not do what the story needs; `error` says what). Step
`status`: `pending`, `active`, `done`, `failed`, `aborted`. `gripper_xy` and
`closest_to_zone` are from the schematic view (joint-space projection onto the taught
plan, drawn along the `routes.yaml` polylines), not forward kinematics.
