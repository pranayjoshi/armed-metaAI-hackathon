# Human2Machine (H2M)

**Say it, point at it, squeeze to confirm — and how hard you squeeze is how hard the robot grips. Open your hand to stop it.**

H2M compiles speech, pointing and forearm muscle activity (EMG) into a *step contract* for an SO-101 robot arm. Every contract passes a deterministic safety gate that acts, asks, or holds. It never guesses a target.

```
mic ─► Parakeet ASR ─► grammar (Muse Spark if unparsed) ─┐
camera ─► YOLOE objects + MediaPipe pointing ────────────┼─► step contract ─► safety gate ─► SO-101
Ganglion EMG ─► grip effort · squeeze · open hand ───────┘        approve / ask / hold / reject
```

Everything runs in one process on one clock. Audio, video and EMG stay on the machine; only free-form requests the local grammar cannot parse go to Muse Spark (text plus an optional scene frame).

## What happens

| You do | H2M does |
|---|---|
| "Hand me the cup" | Finds the cup, asks for confirmation |
| Squeeze your fist | Confirms; squeeze strength becomes grip strength, capped per object (paper cup ≤ 35%) |
| "Give me that" + point | Scores the index-finger ray against each object; if two are close it asks "which one?" |
| Open your hand / say "stop" / press Space | Arm freezes in place and latches until reset and re-armed |
| "Gently" / "firmly" | Sets grip without EMG |

## Quick start

```bash
uv venv --python 3.12 && uv pip install -e ".[dev,emg,speech,vision]"
h2m fetch-models            # MediaPipe hand model, YOLOE-26, Parakeet (downloads once)
h2m run --emg synthetic --vision off --speech off   # no hardware: virtual arm + table
```

Open http://127.0.0.1:8080. **Arm** the virtual arm, type "hand me the cup", then drag *Flexor drive* in Setup to squeeze.

YOLOE text prompts need OpenAI CLIP for new prompts (`pip install "clip @ git+https://github.com/ultralytics/CLIP.git"`). Embeddings are cached in `models/` per prompt set, so changing objects in `configs/h2m.yaml` needs CLIP once.

## Hardware

| Device | Setup |
|---|---|
| SO-101 follower | `uv pip install "lerobot[feetech]"`, `h2m arm ports`, `export H2M_ARM_PORT=/dev/tty.usbmodem…`, `h2m arm calibrate`, then teach poses (below) |
| OpenBCI Ganglion | Flexors (palm side of forearm) on channels 1–2, extensors (back) on 3–4, reference on the elbow. `h2m emg calibrate`, then `h2m emg view` |
| Overhead camera | `vision.scene_camera` index; wrist camera optional |
| Microphone | default input, or `speech.device` |

Teach poses with torque off (support the arm): `h2m arm teach home`, `tray_above`, `tray_release`, then `slot_N.above`, `slot_N.grasp` and `slot_N.point` for each table slot. `h2m arm check` must report all poses taught. Then:

```bash
h2m run --arm lerobot --emg ganglion --i-have-the-estop
```

The real arm refuses untaught poses, needs operator **Arm** in the UI, keeps gripper torque ≤ 50%, freezes on any fault, and asks for confirmation before every motion (`safety.always_confirm`).

## Muse Spark

Set `MODEL_API_KEY` (Meta Model API) and H2M uses `muse-spark-1.1` for requests the grammar cannot parse ("could you put away the thing I was drinking from"). The model returns the same structure as the grammar; target fusion with pointing and the safety decision remain deterministic. STOP and CANCEL never reach it.

## Benchmarking

Sessions tab → start a session → for each trial set the expected action, object and condition (e.g. `speech`, `speech+point`, `speech+point+emg`), let the participant act, click the outcome. Then:

```bash
h2m bench            # writes data/report.md
```

The report covers:
- wrong-action rate (headline), with Wilson 95% intervals;
- success per condition, and how often the system asked or needed a choice;
- which channel confirmed (EMG, voice or click);
- latency: request → decision, ASR, and open hand → stop;
- applied grip and crushed/dropped outcomes per object.

Measured so far, on an M1 Pro, with synthesized speech and a virtual arm (no participants yet):

| Stage | Measured |
|---|---|
| Parakeet ASR, 1–2 s command | 200–320 ms |
| YOLOE-26s, one frame on MPS | 55–126 ms |
| MediaPipe hand landmarks | ~76 ms |
| Open hand → arm frozen (software) | 2–5 ms (servo response not included) |

## Layout

```
h2m/core.py         one clock, config, event log
h2m/contract.py     the step contract
h2m/compiler/       grammar · resolve (target fusion) · llm (Muse Spark)
h2m/safety.py       approve / ask / hold / reject
h2m/sensors/        emg (Ganglion) · vision (YOLOE + MediaPipe) · speech (Parakeet)
h2m/robot/          backends (virtual, LeRobot) · skills · arm executor
h2m/app.py          orchestration · recorder.py · server.py · bench.py · cli.py
web/                operator UI (no build step)
configs/            h2m.yaml (every behavior threshold) · poses.yaml
```

Tests cover the safety invariants only: `pytest`.
