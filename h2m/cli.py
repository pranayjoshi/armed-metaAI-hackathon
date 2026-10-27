"""Command line: `h2m run`, `h2m fetch-models`, `h2m arm ...`, `h2m emg ...`, `h2m bench`."""

from __future__ import annotations

import argparse
import os
import sys
import time

from h2m.core import ROOT, load_config


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

    arm = build_arm(cfg)
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
                print("usage: h2m arm teach <pose>   e.g. home, tray_above, slot_1.grasp")
                return 1
            arm.backend.set_torque(False)
            input(f"Torque OFF. Support the arm, move it to '{args.pose}', then press Enter...")
            joints = arm.backend.read_joints()
            save_pose(arm.poses, args.pose, joints)
            print("saved", args.pose, {j: round(v, 1) for j, v in joints.items()})
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
        for phase, text in (
            ("rest", "Relax your forearm completely"),
            ("squeeze", "Squeeze your fist as hard as is comfortable"),
            ("open", "Open your hand wide and bend the wrist back"),
        ):
            input(f"\n{text}. Press Enter, then hold for 4 seconds...")
            print(emg.calibrate(phase, 4.0))
        print(emg.calibrate("finish"))
        return 0
    try:
        while True:
            s = emg.snapshot()
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

    bench = sub.add_parser("bench", help="benchmark report from recorded sessions")
    bench.add_argument("sessions", nargs="*", help="session dirs (default: all)")
    bench.add_argument("--out", default=str(ROOT / "data" / "report.md"))
    bench.set_defaults(func=cmd_bench)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
