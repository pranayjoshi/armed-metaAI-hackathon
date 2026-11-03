"""Command line: `h2m run`, `h2m replay`, `h2m demo`, `h2m fetch-models`, `h2m arm ...`,
`h2m emg ...`, `h2m bench`."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

from h2m.core import ROOT, load_config, load_env


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if args.arm:
        cfg["arm"]["backend"] = args.arm
    if args.emg:
        cfg["emg"]["source"] = args.emg
    if args.vision:
        cfg["vision"]["source"] = args.vision
    if args.speech:
        cfg["speech"]["source"] = args.speech
    if args.llm:
        cfg["compiler"]["llm"] = args.llm
    if cfg["arm"]["backend"] == "lerobot" and not args.i_have_the_estop:
        print(
            "Refusing to drive the real arm without --i-have-the-estop.\n"
            "Keep the hardware e-stop (or the power switch) in reach, then rerun."
        )
        return 2

    import uvicorn

    from h2m.app import H2M
    from h2m.server import create_app

    h2m = H2M(cfg)
    h2m.start()
    host, port = (
        cfg["server"].get("host", "127.0.0.1"),
        int(args.port or cfg["server"].get("port", 8080)),
    )
    print(
        f"H2M running: http://{host}:{port}  (arm={cfg['arm']['backend']} emg={cfg['emg']['source']} "
        f"vision={cfg['vision']['source']} speech={cfg['speech']['source']} llm={bool(h2m.llm)})"
    )
    try:
        uvicorn.run(create_app(h2m), host=host, port=port, log_level="warning")
    finally:
        h2m.shutdown()
    return 0


def _serve_replay(cfg: dict, app, port: int | None) -> None:
    import uvicorn

    # Same local-only rules as `h2m run` (docs/LOCAL_API.md): the Host check refuses
    # anything but 127.0.0.1 / localhost, whatever address this binds to.
    host = cfg["server"].get("host", "127.0.0.1")
    port = int(port or cfg["server"].get("port", 8080))
    print(f"H2M replay: http://{host}:{port}  (read-only; no arm, no model, no network)")
    uvicorn.run(app, host=host, port=port, log_level="warning")


def cmd_replay(args: argparse.Namespace) -> int:
    """Serve a recorded incident, a session, or a folder of them. Nothing moves."""
    from pathlib import Path

    from h2m.replay import ReplayError, ReplayLibrary
    from h2m.server import create_replay_app

    cfg = load_config(args.config)
    try:
        library = ReplayLibrary.from_path(Path(args.path), root=ROOT)
    except ReplayError as exc:
        print(f"Cannot replay {args.path}: {exc}")
        return 1
    found = library.summaries()
    print(f"{len(found['incidents'])} incident(s), {len(found['sessions'])} session(s)")
    _serve_replay(cfg, create_replay_app(library, cfg), args.port)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Software demo: the bundled synthetic sample plus the scripted scenario on the
    virtual arm (SIMULATION). No arm, no API key, no network."""
    from h2m.replay import ReplayLibrary
    from h2m.scenario import Scenario
    from h2m.server import create_replay_app

    cfg = load_config(args.config)
    sample = ROOT / str((cfg.get("demo", {}) or {}).get("sample_dir", "data/demo"))
    scenario = None if args.no_scenario else Scenario(cfg)
    scan = [sample] + ([scenario.incidents_dir] if scenario else [])
    library = ReplayLibrary(scan_dirs=scan, root=ROOT)
    if not library.incident_ids():
        print(f"no bundled sample in {sample} (python scripts/make_demo_sample.py)")
    if scenario is not None:
        scenario.start(loop=args.loop)
        print(f"SIMULATION running on the virtual arm (work dir {scenario.workdir})")
    try:
        _serve_replay(cfg, create_replay_app(library, cfg, scenario), args.port)
    finally:
        if scenario is not None:
            scenario.close()
    return 0


def cmd_fetch_models(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    from h2m.sensors.vision import Detector, fetch_hand_model

    print("hand landmarker:", fetch_hand_model())
    vision = cfg["vision"]
    detector = Detector(
        cfg["objects"],
        vision.get("detector_model", "yoloe-26s-seg.pt"),
        float(vision.get("min_confidence", 0.25)),
    )
    print("YOLOE ready on", detector.device, "for", ", ".join(detector.prompts))
    from h2m.sensors.speech import make_parakeet

    make_parakeet()
    print("Parakeet ready")
    return 0


def cmd_arm(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)["arm"]
    if args.action == "ports":
        try:
            from serial.tools import list_ports
        except ImportError:
            print("pip install pyserial (comes with lerobot)")
            return 1
        for port in list_ports.comports():
            print(port.device, "-", port.description)
        return 0
    if args.action == "calibrate":
        port = cfg.get("port") or os.environ.get("H2M_ARM_PORT")
        os.execvp(
            "lerobot-calibrate",
            [
                "lerobot-calibrate",
                "--robot.type=so101_follower",
                f"--robot.port={port}",
                f"--robot.id={cfg['robot_id']}",
            ],
        )
    from h2m.robot import build_arm
    from h2m.robot.backend import JOINTS
    from h2m.robot.skills import save_pose

    # read and teach only make sense on the real follower, whatever `run` defaults to.
    arm = build_arm({**cfg, "backend": "lerobot"} if args.action != "check" else cfg)
    if args.action == "check":
        missing = arm.poses.untaught()
        print("all poses taught" if not missing else "untaught: " + ", ".join(missing))
        return 0 if not missing else 1
    arm.backend.connect()
    try:
        if args.action == "read":
            while True:
                joints = arm.backend.read_joints()
                print(
                    "  ".join(f"{j}={joints[j]:7.1f}" for j in JOINTS),
                    f"load={arm.backend.read_gripper_load()}",
                    end="\r",
                    flush=True,
                )
                time.sleep(0.1)
        if args.action == "teach":
            if not args.pose:
                print(
                    "usage: h2m arm teach <pose>   e.g. home, tray_above, slot_1.grasp,"
                    " gesture.nod.nod_down"
                )
                return 1
            arm.backend.set_torque(False)
            input(f"Torque OFF. Support the arm, move it to '{args.pose}', then press Enter...")
            joints = arm.backend.read_joints()
            save_pose(arm.poses, args.pose, joints)
            print("saved", args.pose, {j: round(v, 1) for j, v in joints.items()})
            # Pin the goal here first, or the arm jumps back when torque returns.
            arm.backend.write_goal(joints)
            arm.backend.set_torque(True)
    except KeyboardInterrupt:
        pass
    finally:
        arm.backend.disconnect()
    return 0


def cmd_emg(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if args.source:
        cfg["emg"]["source"] = args.source
    from h2m.core import EventLog
    from h2m.sensors.emg import build_emg

    log = EventLog()
    log.subscribe(
        lambda e: (
            e.kind.startswith("emg.") and e.kind != "emg.started" and print(f"\n{e.kind} {e.data}")
        )
    )
    emg = build_emg(cfg["emg"], log)
    if emg is None:
        print("EMG is off in config")
        return 1
    emg.start()
    time.sleep(1.0)
    if args.action == "calibrate":
        # Real contractions come in bursts well above what a long hold sustains, so the
        # fist and open-hand scales come from three hard pulses, not one steady hold.
        steps = (
            ("rest", "REST: let your forearm go completely limp, supported on the desk", 0),
            ("squeeze", "FIST: at each NOW, squeeze as hard as you can; relax in between", 3),
            ("open", "OPEN HAND: at each NOW, spread and bend back hard; relax in between", 3),
        )
        for phase, text, pulses in steps:
            while True:
                input(f"\n{text}\nPress Enter, then wait for the countdown...")
                for count in (3, 2, 1):
                    print(f"  {count}...", flush=True)
                    time.sleep(1.0)
                timers = []
                for i in range(pulses):
                    timers.append(threading.Timer(2.0 * i, print, ("  NOW, hard!",)))
                    timers.append(threading.Timer(2.0 * i + 1.0, print, ("  relax",)))
                print("  HOLD STILL" if not pulses else "  Recording", flush=True)
                for timer in timers:
                    timer.start()
                result = emg.calibrate(phase, 2.0 * pulses if pulses else 4.0)
                print("  Done, relax.")
                if not result.get("ok"):
                    print(f"  Failed: {result.get('detail')}")
                    return 1
                print(f"  Levels on channels 1-4: {result['rms']}")
                problems = emg.phase_problems(phase)
                for problem in problems:
                    print(f"  WARNING: {problem}")
                prompt = "Enter to redo, or type k to keep it anyway: " if problems else None
                if prompt is None or input(f"  {prompt}").strip().lower() == "k":
                    break
        result = emg.calibrate("finish")
        if not result.get("ok"):
            print(f"\nNot saved: {result.get('detail')}")
            return 1
        profile = result["profile"]
        print(f"\nSaved profile {profile['name']!r}:")
        for key in ("rest", "squeeze_max", "open_max", "brace_max"):
            if profile[key]:
                print(f"  {key:12} {[round(v, 1) for v in profile[key]]}")
        return 0
    try:
        while True:
            s = emg.snapshot()
            if s["error"]:  # all zeros would look like a quiet, connected arm
                return 1
            bar = "#" * int(s["effort"] * 40)
            ext = "#" * int(s["extensor"] * 20)
            print(
                f"grip {s['effort']:.2f} |{bar:<40}| open {s['extensor']:.2f} |{ext:<20}| "
                f"rms {s['rms']} {'calibrated' if s['calibrated'] else 'UNCALIBRATED'}",
                end="\r",
            )
            time.sleep(0.05)
    except KeyboardInterrupt:
        return 0


def cmd_flinch(args: argparse.Namespace) -> int:
    from pathlib import Path

    from h2m import flinch

    if args.action == "record":
        run = flinch.record(
            load_config(args.config)["emg"],
            args.stimuli,
            args.min_gap,
            args.max_gap,
            args.source,
            args.participant,
        )
    else:
        run = Path(args.run) if args.run else flinch.latest_run()
    print(flinch.analyse(run))
    return 0


def cmd_muse_check(args: argparse.Namespace) -> int:
    from h2m.compiler.muse_check import main as check

    return check()


def cmd_bench(args: argparse.Namespace) -> int:
    from h2m.bench import main as bench_main

    return bench_main(args.sessions, args.out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="h2m", description="Human2Machine")
    parser.add_argument("--config", default=str(ROOT / "configs" / "h2m.yaml"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="start the runtime and the operator UI")
    run.add_argument("--arm", choices=["virtual", "lerobot"])
    run.add_argument("--emg", choices=["ganglion", "synthetic", "off"])
    run.add_argument("--vision", choices=["camera", "off"])
    run.add_argument("--speech", choices=["mic", "off"])
    run.add_argument("--llm", choices=["auto", "on", "off"])
    run.add_argument("--port", type=int)
    run.add_argument("--i-have-the-estop", action="store_true", help="required with --arm lerobot")
    run.set_defaults(func=cmd_run)

    rp = sub.add_parser("replay", help="serve a recorded incident or session, read-only")
    rp.add_argument("path", help="data/incidents/<id>, data/sessions/<id>, or a folder of them")
    rp.add_argument("--port", type=int)
    rp.set_defaults(func=cmd_replay)

    demo = sub.add_parser("demo", help="software demo: bundled sample + simulated scenario")
    demo.add_argument("--no-scenario", action="store_true", help="serve the sample only")
    demo.add_argument("--loop", action="store_true", help="re-run the scenario after it ends")
    demo.add_argument("--port", type=int)
    demo.set_defaults(func=cmd_demo)

    fetch = sub.add_parser("fetch-models", help="download and warm all local models")
    fetch.set_defaults(func=cmd_fetch_models)

    arm = sub.add_parser("arm", help="SO-101 bring-up tools")
    arm.add_argument("action", choices=["ports", "calibrate", "read", "teach", "check"])
    arm.add_argument("pose", nargs="?")
    arm.set_defaults(func=cmd_arm)

    emg = sub.add_parser("emg", help="EMG live view and calibration")
    emg.add_argument("action", choices=["view", "calibrate"])
    emg.add_argument("--source", choices=["ganglion", "synthetic"])
    emg.set_defaults(func=cmd_emg)

    fl = sub.add_parser("flinch", help="gate G1: record and score real flinches")
    fl.add_argument("action", choices=["record", "analyse"])
    fl.add_argument("--run", help="recording dir for analyse (default: latest)")
    fl.add_argument("--stimuli", type=int, default=20)
    fl.add_argument("--min-gap", type=float, default=25.0)
    fl.add_argument("--max-gap", type=float, default=75.0)
    fl.add_argument("--source", choices=["ganglion", "synthetic"], default="ganglion")
    fl.add_argument("--participant", default="p01")
    fl.set_defaults(func=cmd_flinch)

    muse = sub.add_parser("muse-check", help="verify MODEL_API_KEY with one image call")
    muse.set_defaults(func=cmd_muse_check)

    bench = sub.add_parser("bench", help="benchmark report from recorded sessions")
    bench.add_argument("sessions", nargs="*", help="session dirs (default: all)")
    bench.add_argument("--out", default=str(ROOT / "data" / "report.md"))
    bench.set_defaults(func=cmd_bench)

    load_env()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
