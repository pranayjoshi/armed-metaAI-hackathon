# What was built during the event

The hackathon window is October 26 to November 4, 2026. Everything below was written inside it by me, one person, working with AI coding agents (Claude Code; their definitions are under `.claude/agents/`), except what is credited under Reused. This list has two parts, and they are different kinds of evidence:

- **Commits** (first part): dated by `git log --date=short main`. A commit date is when I committed, not when the work finished; the one-line summary is mine, the hash and subject are the repository's. Regenerate with `git log --date=short --format='%ad %h %s' main`.
- **Working tree** (second part): files that exist in the checkout on November 3 and are **not committed**. They have no commit date. They are listed from `git status --short` so a reader can see what the build contains beyond the history, and they are checked against the history once committed. Nothing in the second part can be dated from the repository.

The package and command are called `h2m`, which is what the project was named when it was scaffolded on October 27; the product is Armed.

## Part 1: commits on `main`

47 commits on 4 days.

### 2026-11-03 · 6 commits

- `58bbbab` Teach desk poses, jar hold, phone slot; derive nod and look poses: Taught desk poses, jar hold and phone slot; nod and look poses derived; camera preview script.
- `c55b90e` Teach handover, via and sorry poses: Taught handover, via and sorry poses.
- `86f6503` Teach slots 2 and 3 and the wave; run the wave at full skill speed: Taught poses for slots 2 and 3 and the wave; the wave runs at full skill speed.
- `3317426` Retune the real arm and rename the console to Armed: Real-arm retune; the console renamed Armed; UI and film docs; motor health script.
- `b1acec3` Add taught skills and slot-to-slot moves: Taught skills (`configs/skills.yaml`) and slot-to-slot moves.
- `1d99c31` Bring up the real arm, add demo and arm plans, restructure the web UI: Real arm bring-up; demo, arm and pitch plans; web UI restructured into views (home, flow, lab, learn, replay, signals) with vendored Three.js; frontend design-skill files and more agent definitions (development tooling, not runtime).

### 2026-11-01 · 5 commits

- `007671d` Fix consent and freeze gaps found in selection review: Consent and freeze gaps found in the selection review, fixed with tests.
- `5ae233b` Add card selection, scanning baseline and EMG twitch/brace refinements: Card selection state machine, the scanning baseline mode, EMG twitch and brace refinements.
- `41e235e` Add twitch detector, named EMG profiles, intent ranking and head direction: Twitch detector, named EMG profiles, intent ranking, head direction.
- `856d943` Add replay mode and scripted software demo on the virtual arm: Replay mode, scripted software demo on the virtual arm (`h2m demo`), bundled synthetic sample, 3D sim view.
- `cd2f362` Add embodied assistant, learning loop, routing and safety hardening: Embodied assistant (tool loop), learning loop, route chooser, world model, speech output, local API; safety hardening and its tests.

### 2026-10-31 · 27 commits

- `1fbb493` Add brace detector, rule memory, incident capture and safety fixes: Brace detector, rule memory (`h2m/rules.py`), incident capture, safety fixes and their tests; coding-agent definitions under `.claude/agents/`.
- `90f6957` Add flinch test (gate G1) and Muse key check: Flinch test (`h2m flinch`, gate G1) and the Muse key check (`h2m muse-check`).
- `1738e07` Document reviewing and pushing datasets: Docs: reviewing and pushing datasets.
- `ee3c3b3` Document the missing datasets extra error: Docs: missing datasets extra error.
- `0190067` Add script to view a recorded episode in Rerun: Script: view a recorded episode in Rerun.
- `558fbcf` Record shorter episodes by default: Recording script defaults to shorter episodes.
- `2af4934` Document the conda environment in the README: README documents the conda environment.
- `9a4fae8` Link hardware bring-up docs from the README: README links to the bring-up docs.
- `06d59cd` Add hardware bring-up index: Docs: hardware bring-up index.
- `79f944b` Document dataset recording workflow: Docs: dataset recording workflow.
- `e1beadf` Add hardware troubleshooting guide: Docs: hardware troubleshooting guide.
- `2aa0e6a` Document teleoperation test order: Docs: teleoperation test order.
- `93c6e88` Document camera indices, roles and rotation: Docs: camera indices, roles and rotation.
- `4439018` Document arm calibration and the pan alignment fix: Docs: arm calibration and the pan alignment fix.
- `506022a` Document one-time motor setup: Docs: one-time motor setup.
- `1040712` Document how the arm ports were identified: Docs: how the arm ports were identified.
- `1d9ab1a` Add LeRobot quickstart notebook configured for our SO-101 setup: LeRobot quickstart notebook configured for this SO-101 setup.
- `106c36f` Add dataset recording script: Script: dataset recording.
- `72d2a41` Add teleoperation script with cameras and Rerun viewer: Script: teleoperation with cameras and the Rerun viewer.
- `dce27f7` Add arms-only teleoperation script: Script: arms-only teleoperation (wraps LeRobot).
- `eee4ad4` Add arm calibration script: Script: arm calibration (wraps LeRobot).
- `aff0c08` Add one-time motor id setup script: Script: one-time motor id setup (wraps LeRobot).
- `fa9cd71` Add camera finder that saves a frame per camera: Script: find cameras, saving one frame per camera.
- `c9d0ee1` Add serial port finder: Script: find the arm's serial ports.
- `d89a776` Add shared hardware settings for bring-up scripts: Shared hardware settings for the bring-up scripts.
- `aa153b5` Add conda environment for SO-101 bring-up: Conda environment for SO-101 bring-up (LeRobot extras).
- `1b4cc91` Ignore local hardware test outputs: Git ignore for local hardware test captures.

### 2026-10-27 · 9 commits

- `1141d8c` Add benchmark report, decision-layer safety tests, and README: Benchmark report (`h2m bench`), decision-layer safety tests, first README.
- `d350f8e` Tune speech warmup, accept bare 'point', document measured ASR latency: Speech warm-up tuning, grammar fix, measured recognition latency noted.
- `6f78c78` Add operator UI: live workspace, step contract, EMG meters, arm, sessions: First operator web UI: live workspace, contract view, EMG meters, arm view, sessions.
- `68036f5` Add runtime orchestrator, recorder, API server, and CLI: Runtime orchestrator, session recorder, HTTP API server, command line.
- `8d96192` Add contract compiler, Muse Spark interpreter, and safety gate: Contract compiler, Muse Spark interpreter, safety gate and target resolution.
- `71034df` Add perception: YOLOE objects, MediaPipe pointing, on-device speech: First perception and speech clients (the submitted build moves these to SAM 3.1 and Muse Voice Transcribe; see the README's what-runs-where table).
- `a4a0893` Add EMG pipeline: grip effort, squeeze-to-confirm, open-hand stop: EMG pipeline for the OpenBCI Ganglion: filters, RMS, grip effort, squeeze-to-confirm, open-hand stop.
- `8844056` Add SO-101 arm: virtual + LeRobot backends, taught-pose skills, executor: SO-101 arm layer: virtual and LeRobot backends, taught-pose skills, the executor, arm safety tests.
- `1d0c17c` Scaffold H2M: config, single-clock core, step contract, command grammar: Project scaffold: config loader, single-clock event core, the step contract, the command grammar, package layout.

## Part 2: today (November 3), uncommitted working tree

Snapshot of `git status --short`: 73 entries. `M` is a tracked file modified since its last commit; `??` is a new file or folder that is not tracked yet. Other changes were still landing when this was written; rerun `git status --short` for the current list.

### Runtime (`h2m/`)

New, untracked: `h2m/calibrate.py`, `h2m/context.py`, `h2m/grasp.py`, `h2m/sensors/sam.py`

Modified: `h2m/app.py`, `h2m/bench.py`, `h2m/cli.py`, `h2m/compiler/grammar.py`, `h2m/compiler/llm.py`, `h2m/compiler/muse_check.py`, `h2m/incident.py`, `h2m/robot/skills.py`

### Web UI (`web/`)

New, untracked: `web/card/`, `web/views/context/`, `web/views/learn/cards.js`, `web/views/stage/extras.js`, `web/views/stage/film.js`

Modified: `web/js/main.js`, `web/views/home/facts.js`, `web/views/home/index.js`, `web/views/home/style.css`, `web/views/learn/index.js`, `web/views/learn/rules.js`, `web/views/learn/style.css`, `web/views/stage/deck.js`, `web/views/stage/index.js`, `web/views/stage/style.css`

### Desktop shell (`desktop/`, Electron)

New, untracked: `desktop/`

### Configuration

Modified: `configs/h2m.yaml`, `configs/poses.yaml`

### Tests

New, untracked: `tests/test_bench_results.py`, `tests/test_grasp.py`, `tests/test_grasp_app.py`

Modified: `tests/test_app_embodied.py`, `tests/test_app_stop.py`, `tests/test_arm_safety.py`, `tests/test_brace.py`, `tests/test_confirm_echo.py`, `tests/test_embodied.py`, `tests/test_emg_liveness.py`, `tests/test_held_object.py`, `tests/test_incident.py`, `tests/test_intent.py`, `tests/test_learning.py`, `tests/test_operator_release.py`, `tests/test_replay.py`, `tests/test_routing.py`, `tests/test_rules.py`, `tests/test_selection.py`, `tests/test_server_local.py`, `tests/test_skills.py`, `tests/test_twitch.py`

### Documentation

New, untracked: `docs/ACTIONS.md`, `docs/ARMED_IDEA.md`, `docs/BUILT_DURING_EVENT.md`, `docs/COMPLIANCE.md`, `docs/DEMO_STORY.md`, `docs/PROACTIVE.md`, `docs/RESULTS.md`, `docs/TEACH_AND_RECORD.md`, `docs/UI_RUNTIME_FIELDS.md`

Modified: `docs/PITCH.md`, `docs/UI.md`

### Coding-agent definitions

Modified: `.claude/agents/submission-writer.md`

### Other

Modified: `README.md`

## Reused, and credited

The SO-101 design and LeRobot's driver, calibration and teleoperation (the bring-up scripts wrap them); OpenBCI's Ganglion and BrainFlow; Three.js (vendored in `web/vendor/three/` with its licence); Electron. See the README's credits.

## Not yet checked

- The submission checklist item "the what-was-built list checked against `git log` dates" is open: this file must be regenerated after the working-tree files are committed, and the commit dates will then replace Part 2.
