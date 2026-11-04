"""Command line: `h2m run`, `h2m replay`, `h2m demo`, `h2m fetch-models`, `h2m arm ...`,
`h2m emg ...`, `h2m calibrate ...`, `h2m bench`."""

from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from pathlib import Path

from h2m.core import ROOT, load_config, load_env


def _profile(args: argparse.Namespace) -> str | None:
    return getattr(args, "profile", None) or None


def _load(args: argparse.Namespace) -> dict:
    """The config with the named profile merged on; a missing profile exits with 2."""
    try:
        return load_config(args.config, _profile(args))
    except (FileNotFoundError, ValueError) as exc:
        print(exc)
        raise SystemExit(2) from None


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _load(args)
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
    if _profile(args) == "shoot":
        # One switch for the shoot: the overlay above, and nothing starts without the
        # model key or with a placeholder route the film needs (docs/hardware/calibrate.md).
        from h2m.calibrate import shoot_problems

        problems, report = shoot_problems(cfg)
        if problems:
            print("Refusing to start the shoot profile:")
            for problem in problems:
                print(f"  - {problem}")
            print()
            print(report.table())
            return 2
        for row in report.film_missing:
            print(f"warning: {row.item} is {row.state}: {row.detail}")
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
    # Vision is SAM 3.1 over the Meta Model API (h2m/sensors/sam.py): nothing to download.
    print(
        "vision: SAM 3.1 via the Meta Model API, model", cfg["vision"].get("sam_model", "sam-3.1")
    )
    print("speech: Muse Voice Transcribe runs over the API; nothing to fetch")
    return 0


# The jaw helpers live in h2m/calibrate.py; `h2m arm jaw` keeps using them by name.
from h2m.calibrate import JAW_TARGETS, parse_jaw_points, write_jaw_points  # noqa: E402


def cmd_arm_jaw(args: argparse.Namespace) -> int:
    """Record the three-point jaw calibration (docs/hardware/calibrate.md).
    ``--points 30:24.5,60:51,98:79`` writes numbers you already measured. Without it the
    follower is read at three openings you set by hand (torque off) and you type the
    caliper reading at each. The result goes into the config file; ``calibrated`` stays
    false until the safety review has seen the numbers."""
    try:
        if args.points:
            points = parse_jaw_points(args.points)
        else:
            points = _measure_jaw_points(args)
            if points is None:
                return 1
    except ValueError as exc:
        print(exc)
        return 1
    write_jaw_points(Path(args.config), points)
    print("recorded", points, "in", args.config)
    print("grasp.jaw_calibration.calibrated and grasp.sam_width are still false: the")
    print("safety review and a virtual-arm run come first (docs/hardware/calibrate.md).")
    return 0


def set_slot_jaws(path: Path, slot_id: str, percent: float) -> None:
    """Set the jaw opening saved at a slot's ``above`` and ``grasp`` poses, editing only
    those two lines so the rest of the poses file (and its comments) stays as it is."""
    lines = path.read_text().splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if ln == f"  {slot_id}:\n"), None)
    if start is None:
        raise ValueError(f"no slot {slot_id} in {path}")
    done = 0
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("  ") and not lines[i].startswith("   "):
            break  # the next top-level entry under slots
        stripped = lines[i].strip()
        if stripped.startswith("gripper:"):
            lines[i] = f"{lines[i][: lines[i].index('gripper:')]}gripper: {percent:g}\n"
            done += 1
        elif stripped.startswith(("above:", "grasp:")) and "gripper:" in stripped:
            # flow style: above: {taught: true, joints: {..., gripper: 60}}
            lines[i] = re.sub(r"gripper: *[0-9.]+", f"gripper: {percent:g}", lines[i])
            done += 1
        if lines[i].strip() == "point:":
            break
    if done != 2:
        raise ValueError(f"{slot_id}: expected a gripper value under above and grasp, found {done}")
    path.write_text("".join(lines))


def cmd_arm_jaws(args: argparse.Namespace) -> int:
    """``h2m arm jaws slot_3 60``: set the jaw opening used at a slot (above and grasp).
    No arm needed. The runtime clamps it to arm.taught_jaws_range and reads it at start."""
    cfg = _load(args)
    try:
        if not args.pose or args.percent is None:
            raise ValueError("usage: h2m arm jaws <slot> <percent>, e.g. h2m arm jaws slot_3 60")
        percent = float(args.percent)
        if not 0.0 <= percent <= 100.0:
            raise ValueError("percent must be within 0..100")
        set_slot_jaws(_poses_path(args, cfg), args.pose, percent)
    except ValueError as exc:
        print(exc)
        return 1
    low, high = (float(v) for v in (cfg.get("arm") or {}).get("taught_jaws_range", (20, 60)))
    print(f"{args.pose}: jaws {percent:g}% at above and grasp")
    if not low <= percent <= high:
        print(f"note: arm.taught_jaws_range is [{low:g}, {high:g}], so a pick will clamp to it")
    print("restart the runtime to use it")
    return 0


def _measure_jaw_points(args: argparse.Namespace) -> list[tuple[float, float]] | None:
    from h2m.robot import build_arm

    arm = build_arm({**load_config(args.config)["arm"], "backend": "lerobot"})
    arm.backend.connect()
    points: list[tuple[float, float]] = []
    try:
        arm.backend.set_torque(False)
        for target in JAW_TARGETS:
            input(f"Torque OFF. Set the jaws to about {target:g}% open by hand, then Enter...")
            pct = round(float(arm.backend.read_joints()["gripper"]), 1)
            mm = float(input(f"Gripper reads {pct}%. Inner distance between the pads, mm: "))
            points.append((pct, mm))
    except (KeyboardInterrupt, EOFError):
        return None
    except ValueError:
        print("that was not a number")
        return None
    finally:
        arm.backend.disconnect()
    try:
        parse_jaw_points(",".join(f"{p}:{m}" for p, m in points))
    except ValueError as exc:
        print(exc)
        return None
    return points


def cmd_arm(args: argparse.Namespace) -> int:
    if args.action == "jaw":
        return cmd_arm_jaw(args)
    if args.action == "jaws":
        return cmd_arm_jaws(args)
    cfg = _load(args)["arm"]
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


def _routes_path(args: argparse.Namespace, cfg: dict) -> Path:
    if getattr(args, "routes", None):
        return Path(args.routes)
    return ROOT / str((cfg.get("rules") or {}).get("routes_file", "configs/routes.yaml"))


def _poses_path(args: argparse.Namespace, cfg: dict) -> Path:
    if getattr(args, "poses", None):
        return Path(args.poses)
    return ROOT / str((cfg.get("arm") or {}).get("poses_file", "configs/poses.yaml"))


def _fmt_xy(p) -> str:
    return f"({p[0]:.3f}, {p[1]:.3f})"


def _calibrate_table(args: argparse.Namespace, cfg: dict) -> int:
    """Four clicked (or typed, or SAM-found) table corners -> vision.table_homography.
    The slot tapes are clicked afterwards as a check that does not feed the fit."""
    from h2m import calibrate as cal
    from h2m.robot.skills import load_poses

    cal_cfg = cal.settings(cfg)
    vision = cfg.get("vision") or {}
    unit_mm = float((cfg.get("grasp") or {}).get("table_unit_mm", 1000))
    if args.table:
        table_pts = cal.parse_points(args.table)
    elif args.size:
        table_pts = cal.table_corners(*args.size)
    else:
        raise cal.CalibrationError(
            "say how big the taped rectangle is: --size WIDTH HEIGHT (metres)"
        )
    frame = None
    image_pts = None
    if args.points:
        image_pts = cal.parse_points(args.points)
    else:
        frame = cal.load_frame(args.image, vision)
        if args.markers:
            boxes = cal.sam_boxes(frame, args.markers, vision)
            if boxes and len(boxes) == 4:
                image_pts = cal.order_corners(cal.box_centres(boxes))
                print("SAM found the four markers:", "; ".join(_fmt_xy(p) for p in image_pts))
            else:
                print("SAM did not find exactly four markers (or no client / key): click them")
        if image_pts is None:
            labels = [f"corner at table {_fmt_xy(p)}" for p in table_pts]
            image_pts = cal.pick_points(frame, labels, "h2m calibrate table: corners")
            if image_pts is None:
                print("aborted, nothing saved")
                return 1
    if len(image_pts) != len(table_pts):
        raise cal.CalibrationError(
            f"{len(image_pts)} image points for {len(table_pts)} table points"
        )
    lib = load_poses(_poses_path(args, cfg))
    slots = sorted(lib.slots)
    checks = {}
    marks = None
    if args.check_points:
        marks = cal.parse_points(args.check_points)
    elif frame is not None and not args.no_check and slots:
        print("Now the slot tapes as a check (Esc to skip).")
        marks = cal.pick_points(
            frame, [f"{s} tape mark" for s in slots], "h2m calibrate table: slots"
        )
    if marks:
        if len(marks) != len(slots):
            raise cal.CalibrationError(
                f"{len(marks)} slot marks for {len(slots)} slots ({', '.join(slots)})"
            )
        checks = {s: (m, lib.slots[s].table_xy) for s, m in zip(slots, marks, strict=True)}
    fit = cal.fit_table(image_pts, table_pts, checks, unit_mm)
    print(
        f"homography from {len(image_pts)} points; reprojection per point (px): {fit.reprojection_px}"
    )
    if len(image_pts) == 4:
        print(
            "  (an exact fit from four points: 0 px by construction; the slot marks are the real check)"
        )
    mapped = cal.apply_homography(fit.homography, [m for m, _ in checks.values()]) if checks else []
    for (slot, (_, xy)), got in zip(checks.items(), mapped, strict=True):
        print(
            f"  {slot}: tape maps to {_fmt_xy(got)}, poses.yaml says {_fmt_xy(xy)}: {fit.check_mm[slot]:.0f} mm off"
        )
    problems = fit.problems(cal_cfg)
    if args.write_slots:
        problems = [p for p in problems if "table xy" not in p]  # the tapes become the slots
    if problems and not args.force:
        for problem in problems:
            print(f"not saved: {problem}")
        print("re-click the corners (or --force to save anyway)")
        return 1
    frame_path = cal.save_frame(frame, "table") if frame is not None else None
    record = cal.save_table_record(fit, str(frame_path) if frame_path else None)
    cal.write_homography(Path(args.config), fit.homography)
    print(f"saved vision.table_homography in {args.config}; record {record}")
    if args.write_slots and checks:
        new_xy = dict(zip(checks, mapped, strict=True))
        cal.write_slot_xy(_poses_path(args, cfg), new_xy)
        print(
            "wrote slot table_xy into poses.yaml:",
            ", ".join(f"{s} {_fmt_xy(p)}" for s, p in new_xy.items()),
        )
    return 0


def _calibrate_zone(args: argparse.Namespace, cfg: dict) -> int:
    """A named avoid zone: two opposite corners clicked on the overhead frame (through
    the homography), or typed in table xy."""
    from h2m import calibrate as cal
    from h2m.routing import load_routes

    routes_path = _routes_path(args, cfg)
    frame = None
    image_pts = None
    if args.box:
        box = cal.parse_box(args.box)
    else:
        H = cal.read_homography(cfg)
        if H is None:
            raise cal.CalibrationError(
                "no homography yet: `h2m calibrate table` first, or --box in table xy"
            )
        if args.pixels:
            image_pts = cal.parse_points(args.pixels)
        else:
            frame = cal.load_frame(args.image, cfg.get("vision") or {})
            image_pts = cal.pick_points(
                frame,
                [f"{args.name}: one corner", f"{args.name}: the opposite corner"],
                f"h2m calibrate zone {args.name}",
            )
            if image_pts is None:
                print("aborted, nothing saved")
                return 1
        if len(image_pts) != 2:
            raise cal.CalibrationError("a zone is two opposite corners")
        a, b = cal.apply_homography(H, image_pts)
        box = cal.box_from_corners(a, b)
    frame_path = cal.save_frame(frame, f"zone_{args.name}") if frame is not None else None
    cal.write_zone(routes_path, args.name, box)
    cal.save_zone_record(args.name, box, str(frame_path) if frame_path else None, image_pts)
    print(f"zone {args.name} = [{', '.join(f'{v:g}' for v in box)}] saved in {routes_path}")
    routes = load_routes(routes_path)
    for name, per in routes.routes.items():
        for slot, poly in per.items():
            hit = cal.crossings(poly, {args.name: box})[args.name]
            state = "placeholder" if (name, slot) in routes.unmeasured else "measured"
            print(f"  {name} from {slot} ({state}): {'crosses' if hit else 'clear of'} {args.name}")
    return 0


def _calibrate_route(args: argparse.Namespace, cfg: dict) -> int:
    """Record one route's polyline per pick slot. Real arm: jog through the taught poses
    and click the gripper tip on the overhead frame at each (through the homography).
    Virtual: the slots' table_xy plus the recorded or typed xy of the other poses."""
    from h2m import calibrate as cal
    from h2m.robot.skills import load_poses
    from h2m.routing import load_routes

    routes_path = _routes_path(args, cfg)
    lib = load_poses(_poses_path(args, cfg))
    names = cal.route_poses(lib, args.route, args.slot)
    backend = args.arm or cfg["arm"]["backend"]
    if backend == "lerobot":
        if not args.i_have_the_estop:
            print("Refusing to jog the real arm without --i-have-the-estop (keep it in reach).")
            return 2
        H = cal.read_homography(cfg)
        if H is None:
            raise cal.CalibrationError("no homography yet: `h2m calibrate table` first")
        from h2m.robot import build_arm
        from h2m.robot.arm import ArmState

        vision = cfg.get("vision") or {}
        arm = build_arm({**cfg["arm"], "backend": "lerobot"})
        arm.connect()
        if arm.state is ArmState.FAULT:
            print(f"arm: {arm.reason}")
            return 1
        ok, why = arm.arm()
        if not ok:
            print(f"arm: {why}")
            return 1

        def sample(name: str):
            frame = cal.load_frame(None, vision)
            cal.save_frame(frame, f"route_{args.route}_{args.slot or ''}_{name}".replace("__", "_"))
            pts = cal.pick_points(frame, [f"gripper tip at {name}"], f"h2m calibrate route: {name}")
            return cal.apply_homography(H, pts)[0] if pts else None

        try:
            samples = cal.jog_route(arm, names, sample, speed=args.speed)
            cal.jog_route(arm, ["home"], lambda _n: (0.0, 0.0), speed=args.speed)
        finally:
            arm.close()
        cal.save_pose_xy(samples)
        poly = [samples[n] for n in names]
    else:
        known = dict(cal.load_pose_xy())
        for item in args.xy or []:
            name, _, xy = item.partition("=")
            known[name] = cal.parse_points(xy)[0]
        poly, missing = cal.sample_route_virtual(lib, names, known)
        if missing:
            raise cal.CalibrationError(
                f"no table xy for {', '.join(missing)} on the virtual backend: pass --xy {missing[0]}=x,y "
                "or record the route on the real arm"
            )
    if args.route == cal.HANDOVER:
        cal.write_handover(routes_path, poly, spot=poly[-1])
        print(
            f"handover polyline {' -> '.join(_fmt_xy(p) for p in poly)} saved, measured: true, spot {_fmt_xy(poly[-1])}"
        )
    else:
        cal.write_route(routes_path, args.route, args.slot, poly)
        print(
            f"route {args.route} from {args.slot}: {' -> '.join(f'{n} {_fmt_xy(p)}' for n, p in zip(names, poly, strict=True))}"
        )
        print(f"saved with measured: true in {routes_path}")
    routes = load_routes(routes_path)
    hits = cal.crossings(poly, routes.zones)
    print("  crosses: " + (", ".join(z for z, h in hits.items() if h) or "nothing"))
    zone = str(cal.settings(cfg)["film_zone"])
    if args.slot and args.route in ("direct", "via_high"):
        for name, state, hit, wanted in cal.film_route_verdicts(routes, args.slot, zone):
            if hit is None:
                print(f"  {name} from {args.slot}: {state}")
                continue
            verdict = "as the film needs" if hit == wanted else "NOT what the film needs"
            print(
                f"  {name} from {args.slot} ({state}): {'crosses' if hit else 'clear of'} {zone}: {verdict}"
            )
    return 0


def _calibrate_jaw(args: argparse.Namespace, cfg: dict) -> int:
    """The three ruler readings at 30, 60 and 100 percent -> grasp.jaw_calibration.points.
    ``calibrated`` stays false until the safety review. Same file as `h2m arm jaw`."""
    from h2m import calibrate as cal

    try:
        if args.points:
            points = cal.parse_jaw_points(args.points)
        elif (args.arm or cfg["arm"]["backend"]) == "lerobot":
            points = _measure_jaw_points(args)
            if points is None:
                return 1
        else:
            points = cal.prompt_jaw_points()
    except (ValueError, EOFError) as exc:
        print(f"not saved: {exc}")
        return 1
    cal.write_jaw_points(Path(args.config), points)
    object_mm = float(cal.settings(cfg)["check_object_mm"])
    margin = float((cfg.get("grasp") or {}).get("margin_mm", 12))
    opening, pct = cal.jaw_check(points, object_mm, margin)
    print(f"recorded {points} in {args.config}")
    print(
        f"check: a {object_mm:g} mm object opens to {opening:g} mm (margin {margin:g}) = {pct:g}% gripper"
    )
    print(
        "grasp.jaw_calibration.calibrated stays false until the safety review has seen the numbers."
    )
    return 0


def _calibrate_check(args: argparse.Namespace, cfg: dict) -> int:
    from h2m import calibrate as cal

    report = cal.check(cfg, routes_path=_routes_path(args, cfg), poses_path=_poses_path(args, cfg))
    print(report.table())
    missing = report.film_missing
    if not missing:
        print("\ncalibrations done: everything the film needs is measured")
        return 0
    print(f"\n{len(missing)} item(s) the film needs: " + ", ".join(r.item for r in missing))
    return 1


CALIBRATE = {
    "table": _calibrate_table,
    "zone": _calibrate_zone,
    "route": _calibrate_route,
    "jaw": _calibrate_jaw,
    "check": _calibrate_check,
}


def cmd_calibrate(args: argparse.Namespace) -> int:
    from h2m.calibrate import CalibrationError
    from h2m.routing import RoutesUnavailable

    cfg = _load(args)
    try:
        return CALIBRATE[args.what](args, cfg)
    except (CalibrationError, RoutesUnavailable) as exc:
        print(f"not saved: {exc}")
        return 1


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

    return bench_main(args.sessions, args.out, results=args.results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="h2m", description="Human2Machine")
    parser.add_argument("--config", default=str(ROOT / "configs" / "h2m.yaml"))
    parser.add_argument(
        "--profile", help="merge configs/<profile>.yaml onto the config (e.g. shoot)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="start the runtime and the operator UI")
    run.add_argument("--profile", default=argparse.SUPPRESS, help="e.g. shoot: configs/shoot.yaml")
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
    arm.add_argument("action", choices=["ports", "calibrate", "read", "teach", "check", "jaw", "jaws"])
    arm.add_argument("pose", nargs="?")
    arm.add_argument("percent", nargs="?", help="jaws: opening in percent, e.g. 60")
    arm.add_argument("--profile", help="merge configs/<profile>.yaml (e.g. bed: its own poses file)")
    arm.add_argument(
        "--points", help="jaw: percent:mm pairs already measured, e.g. 30:24,60:51,98:79"
    )
    arm.set_defaults(func=cmd_arm)

    files = argparse.ArgumentParser(add_help=False)
    files.add_argument("--routes", help="routes file (default rules.routes_file)")
    files.add_argument("--poses", help="poses file (default arm.poses_file)")
    calib = sub.add_parser("calibrate", help="shoot calibrations: table, zone, route, jaw, check")
    csub = calib.add_subparsers(dest="what", required=True)
    ct = csub.add_parser("table", parents=[files], help="overhead frame -> table homography")
    ct.add_argument(
        "--size",
        nargs=2,
        type=float,
        metavar=("WIDTH", "HEIGHT"),
        help="the taped rectangle in table units (metres); click top-left, top-right, bottom-right, bottom-left",
    )
    ct.add_argument(
        "--table", help="table xy of each clicked point instead of --size: 'x,y;x,y;...'"
    )
    ct.add_argument("--points", help="image points typed instead of clicked: 'px,py;px,py;...'")
    ct.add_argument("--image", help="a saved overhead frame instead of the camera")
    ct.add_argument(
        "--markers", help="SAM prompt for four corner markers (h2m/sensors/sam.py, MODEL_API_KEY)"
    )
    ct.add_argument(
        "--check-points", help="image points of the slot tapes in slot order: 'px,py;...'"
    )
    ct.add_argument("--no-check", action="store_true", help="skip clicking the slot tapes")
    ct.add_argument(
        "--write-slots", action="store_true", help="write the tapes' table xy into poses.yaml"
    )
    ct.add_argument("--force", action="store_true", help="save even over the error limits")
    ct.set_defaults(func=cmd_calibrate)
    cz = csub.add_parser("zone", parents=[files], help="an avoid zone rectangle into routes.yaml")
    cz.add_argument("name")
    cz.add_argument("--box", help="table xy typed: xmin,ymin,xmax,ymax")
    cz.add_argument("--pixels", help="two opposite corners in image px: 'px,py;px,py'")
    cz.add_argument("--image", help="a saved overhead frame instead of the camera")
    cz.set_defaults(func=cmd_calibrate)
    cr = csub.add_parser("route", parents=[files], help="a route polyline, measured: true")
    cr.add_argument("route", help="direct | via_left | via_high | handover")
    cr.add_argument("slot", nargs="?", help="pick slot, e.g. slot_1 (not for handover)")
    cr.add_argument("--xy", action="append", metavar="POSE=X,Y", help="virtual: table xy of a pose")
    cr.add_argument("--arm", choices=["virtual", "lerobot"])
    cr.add_argument("--speed", type=float, default=0.3, help="real arm: share of max speed")
    cr.add_argument("--i-have-the-estop", action="store_true", help="required with the real arm")
    cr.set_defaults(func=cmd_calibrate)
    cj = csub.add_parser("jaw", parents=[files], help="jaw opening at 30, 60 and 100 percent")
    cj.add_argument("--points", help="percent:mm pairs already measured, e.g. 30:24,60:51,100:79")
    cj.add_argument(
        "--arm", choices=["virtual", "lerobot"], help="lerobot: read the gripper at each opening"
    )
    cj.set_defaults(func=cmd_calibrate)
    cc = csub.add_parser("check", parents=[files], help="every calibration's state as a table")
    cc.set_defaults(func=cmd_calibrate)

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
    bench.add_argument(
        "--results",
        action="store_true",
        help="write docs/RESULTS.md (the submission results table) instead of printing the report",
    )
    bench.set_defaults(func=cmd_bench)

    load_env()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
