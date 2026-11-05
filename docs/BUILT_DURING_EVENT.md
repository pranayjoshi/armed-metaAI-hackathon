# What was built during the event

The hackathon window is October 26 to November 4, 2026. Everything below was written inside it by me, one person, working with AI coding agents (Claude Code; their definitions are under `.claude/agents/`), except what is credited under Reused. This list has two parts, and they are different kinds of evidence:

- **Commits** (first part): dated by `git log --date=short main`. A commit date is when I committed, not when the work finished; the one-line summary is mine, the hash and subject are the repository's. Regenerate with `git log --date=short --format='%ad %h %s' main`.
- **Working tree** (second part): files that are **not committed** when this file is generated. They have no commit date. They are listed from `git status --short` so a reader can see what the build contains beyond the history, and they are checked against the history once committed. Nothing in the second part can be dated from the repository.

The package and command are `armed`.

## Part 1: commits on `main`

56 commits on 5 days, verbatim from `git log`. The package was scaffolded as `h2m` on October 27 and renamed to `armed` on November 4; older subjects use the old name.

### 2026-11-04 · 9 commits

- `367f08c` Add screenshots and diagrams to the README
- `9741294` Add live runtime doc and refresh Armed docs
- `f2c4737` Update card and shell styles for the bed flow
- `de932c9` Add bed handover route and runtime tuning for live bed flow
- `40fd660` Run Armed live: always-on Muse thinker, voice arming, Mind view
- `953fe3f` Document Armed: idea, demo story, actions, proactive, UI, compliance
- `74161c0` Add the Electron overlay shell
- `dbd0225` Add the Armed console: card, context view, film mode
- `8577c5a` Add runtime: context, proactive routines, SAM, voice, grasp, calibration

### 2026-11-03 · 6 commits

- `58bbbab` Teach desk poses, jar hold, phone slot; derive nod and look poses
- `c55b90e` Teach handover, via and sorry poses
- `86f6503` Teach slots 2 and 3 and the wave; run the wave at full skill speed
- `3317426` Retune the real arm and rename the console to Armed
- `b1acec3` Add taught skills and slot-to-slot moves
- `1d99c31` Bring up the real arm, add demo and arm plans, restructure the web UI

### 2026-11-01 · 5 commits

- `007671d` Fix consent and freeze gaps found in selection review
- `5ae233b` Add card selection, scanning baseline and EMG twitch/brace refinements
- `41e235e` Add twitch detector, named EMG profiles, intent ranking and head direction
- `856d943` Add replay mode and scripted software demo on the virtual arm
- `cd2f362` Add embodied assistant, learning loop, routing and safety hardening

### 2026-10-31 · 27 commits

- `1fbb493` Add brace detector, rule memory, incident capture and safety fixes
- `90f6957` Add flinch test (gate G1) and Muse key check
- `1738e07` Document reviewing and pushing datasets
- `ee3c3b3` Document the missing datasets extra error
- `0190067` Add script to view a recorded episode in Rerun
- `558fbcf` Record shorter episodes by default
- `2af4934` Document the conda environment in the README
- `9a4fae8` Link hardware bring-up docs from the README
- `06d59cd` Add hardware bring-up index
- `79f944b` Document dataset recording workflow
- `e1beadf` Add hardware troubleshooting guide
- `2aa0e6a` Document teleoperation test order
- `93c6e88` Document camera indices, roles and rotation
- `4439018` Document arm calibration and the pan alignment fix
- `506022a` Document one-time motor setup
- `1040712` Document how the arm ports were identified
- `1d9ab1a` Add LeRobot quickstart notebook configured for our SO-101 setup
- `106c36f` Add dataset recording script
- `72d2a41` Add teleoperation script with cameras and Rerun viewer
- `dce27f7` Add arms-only teleoperation script
- `eee4ad4` Add arm calibration script
- `aff0c08` Add one-time motor id setup script
- `fa9cd71` Add camera finder that saves a frame per camera
- `c9d0ee1` Add serial port finder
- `d89a776` Add shared hardware settings for bring-up scripts
- `aa153b5` Add conda environment for SO-101 bring-up
- `1b4cc91` Ignore local hardware test outputs

### 2026-10-27 · 9 commits

- `1141d8c` Add benchmark report, decision-layer safety tests, and README
- `d350f8e` Tune speech warmup, accept bare 'point', document measured ASR latency
- `6f78c78` Add operator UI: live workspace, step contract, EMG meters, arm, sessions
- `68036f5` Add runtime orchestrator, recorder, API server, and CLI
- `8d96192` Add contract compiler, Muse Spark interpreter, and safety gate
- `71034df` Add perception: YOLOE objects, MediaPipe pointing, on-device speech
- `a4a0893` Add EMG pipeline: grip effort, squeeze-to-confirm, open-hand stop
- `8844056` Add SO-101 arm: virtual + LeRobot backends, taught-pose skills, executor
- `1d0c17c` Scaffold H2M: config, single-clock core, step contract, command grammar

## Part 2: uncommitted working tree

Snapshot of `git status --short` when this file was last generated: 212 entries (the November 4 rename to `armed`, the final docs and the live-run tuning). Regenerate after the final commit.

## Reused, and credited

The SO-101 design and LeRobot's driver, calibration and teleoperation (the bring-up scripts wrap them); OpenBCI's Ganglion and BrainFlow; Three.js (vendored in `web/vendor/three/` with its licence); Electron. See the README's credits.

