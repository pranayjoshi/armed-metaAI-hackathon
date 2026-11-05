"""Play every desk demo move on the arm, one at a time, before filming. Only the moves:
no camera, no Muse, no cards. Each plan is built by the same planners, openings, closes
and speed caps the runtime uses (armed/app.py), and runs through Arm.execute, so the joint
limits, the untaught-pose refusal and the speed limits all apply.

    conda activate lerobot && python scripts/move_check.py --dry      # virtual arm: plans only
    conda activate lerobot && python scripts/move_check.py            # the real arm
    conda activate lerobot && python scripts/move_check.py --only keys,chips

Real arm: press Enter to run a move, s to skip it, q to quit. Ctrl-C during a move
freezes the arm at once (Arm.stop). Put each object on its slot before its move; for a
hand-in move (jar, chips) hold the thing where the arm will close.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from armed.core import ROOT, load_config, load_env  # noqa: E402
from armed.embodied import skill_speed  # noqa: E402
from armed.robot import ArmState, build_arm  # noqa: E402
from armed.robot.skills import Skill, SkillStep, load_skills, plan_home, plan_pick_place, plan_skill  # noqa: E402

SKILL_GRIP_MAX = 0.8  # armed/app.py: a skill's close stays soft


@dataclass
class Move:
    key: str
    title: str
    setup: str
    build: Callable[[], tuple[Any, Any, float]]  # (plan, grip, speed)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry", action="store_true", help="virtual arm: build and simulate each plan")
    ap.add_argument("--only", default="", help="comma list of move keys")
    ap.add_argument("--profile", default="live")
    a = ap.parse_args()

    load_env()  # ARMED_ARM_PORT (and nothing else used here) from .env
    cfg = load_config(None, a.profile)
    arm_cfg = {**cfg["arm"], "backend": "virtual" if a.dry else "lerobot"}
    arm = build_arm(arm_cfg)
    lib, s = arm.poses, arm.settings
    skills = load_skills(ROOT / str(arm_cfg.get("skills_file", "configs/skills.yaml")))
    safety = cfg.get("safety", {})
    catalog = {o["id"]: o for o in cfg.get("objects", [])}

    def slot_of(obj: str):
        return lib.slots[catalog[obj]["slot"]]

    def open_pct(slot) -> float:
        low, high = (float(v) for v in arm_cfg.get("taught_jaws_range", (20, 60)))
        return max(low, min(high, float(slot.grasp.joints["gripper"])))

    def tray_open(slot) -> float:
        return min(open_pct(slot), float(arm_cfg.get("tray_release_max_pct", s.gripper_open)))

    def touch_close(slot, grip, squeeze=None) -> float:
        touch = slot.point.joints.get("gripper")
        if arm_cfg.get("use_taught_touch_close") is not True or not slot.point.taught:
            return grip.close_pct
        squeeze = float(arm_cfg.get("touch_squeeze_pct", 8.0) if squeeze is None else squeeze)
        return max(float(s.gripper_hard_close), float(touch) - squeeze * grip.effort)

    def pick(obj: str, route: str = "direct"):
        def build():
            slot = slot_of(obj)
            effort = float(safety.get("default_grip", 0.5))
            grip = arm.grip_for(effort, float(catalog[obj].get("max_grip", 1.0)))
            plan = plan_pick_place(lib, slot, s.gripper_open,
                                   touch_close(slot, grip, catalog[obj].get("touch_squeeze_pct")),
                                   lib.routes.get(route, ()), grasp_open_pct=tray_open(slot))
            return plan, grip, 1.0
        return build

    def skill(name: str, obj: str | None = None):
        def build():
            sk = skills[name]
            slot = slot_of(obj) if obj else None
            o = catalog.get(obj or "", {})
            grip = None
            close = s.gripper_soft_close
            if sk.closes:
                cap = float(o.get("skill_grip", SKILL_GRIP_MAX))
                effort = cap if "skill_grip" in o else float(safety.get("default_grip", 0.5))
                grip = arm.grip_for(min(effort, cap), float(o.get("max_grip", 1.0)))
                close = touch_close(slot, grip, o.get("touch_squeeze_pct")) if slot else grip.close_pct
            plan = plan_skill(lib, sk, open_pct(slot) if slot else s.gripper_open, close, slot)
            return plan, grip, skill_speed(sk, safety)
        return build

    # The shake needs ~50% to clear the bottle; a pick to the tray opens no wider than
    # arm.tray_release_max_pct (30, unmeasured). This shows whether 50 clears the tray wall.
    wide = Skill("tray_wide", (SkillStep("tray_above"), SkillStep("tray_release", "open", 1.0, 50.0),
                               SkillStep("tray_above")), speed=0.4)
    moves = [
        Move("tray_wide", "Open to 50% at the tray (does it hit the wall?)", "jaws empty; watch the tray wall",
             lambda: (plan_skill(lib, wide, s.gripper_open, s.gripper_soft_close), None, 0.4)),
        Move("home", "Home", "nothing in the jaws", lambda: (plan_home(lib, gripper=True), None, 0.5)),
        Move("toy", "Toy (slot 1) into the bin", "toy on slot 1", pick("toy")),
        Move("print", "3D print (slot 3) into the bin", "3D print on slot 3", pick("print")),
        Move("drink", "Bring the shake up to drink, then back", "shake on slot 4; sit at the desk", skill("bring_drink", "cup")),
        Move("drink_close", "The shake up to drink on the close path (the rule beat)", "shake on slot 4; sit at the desk",
             skill("bring_drink_close", "cup")),
        Move("shake_bin", "Finished shake (slot 4) into the bin", "shake on slot 4", pick("cup")),
        Move("phone", "Hold up the phone (slot 2)", "phone on slot 2", skill("hold_up", "ball")),
        Move("jar", "Hold the jar (stays holding)", "hold the jar out where the arm closes", skill("hold_jar")),
        Move("let_go", "Let go of the jar, go home", "hold the jar", skill("let_go")),
        Move("chips", "Take the chips from X", "X holds the bag out at their spot", skill("accept_item")),
        Move("chips_up", "Hold the chips up for you", "chips held from the last move", skill("hold_out_held")),
        Move("chips_tray", "Chips into the tray", "chips held", skill("stow_held")),
        Move("keys", "Keys (slot 3) to your hand", "keys on slot 3; hand out", skill("hand_keys", "keys")),
        Move("bow", "Bow", "", skill("bow")),
        Move("wave", "Wave", "", skill("wave")),
        Move("sorry", "Sorry", "", skill("sorry")),
    ]
    if a.only:
        want = {k.strip() for k in a.only.split(",")}
        moves = [m for m in moves if m.key in want]

    if a.dry:  # no control thread: the loop below ticks the virtual arm faster than real time
        arm.backend.connect()
        arm._measured = arm.backend.read_joints()
        arm._cmd = dict(arm._measured)
        arm.state = ArmState.DISARMED
    else:
        arm.connect()
    if arm.state is not ArmState.DISARMED:
        print(f"arm not connected: {arm.reason}")
        return 1
    arm.arm()
    results: list[tuple[str, str]] = []
    try:
        for m in moves:
            try:
                plan, grip, speed = m.build()
            except (KeyError, ValueError) as exc:
                results.append((m.key, f"NO PLAN: {exc}"))
                print(f"\n[{m.key}] {m.title}: NO PLAN: {exc}")
                continue
            print(f"\n[{m.key}] {m.title}  ({len(plan.waypoints)} steps, speed {speed:.2f}"
                  + "".join(f", {w.label} jaws {w.joints['gripper']:.0f}%" for w in plan.waypoints
                          if w.gripper_only) + ")")
            if not a.dry:
                if m.setup:
                    print(f"  set up: {m.setup}")
                answer = input("  Enter = run, s = skip, q = quit: ").strip().lower()
                if answer == "q":
                    break
                if answer == "s":
                    results.append((m.key, "skipped"))
                    continue
            ok, why = arm.execute(plan, {"verb": "check", "move": m.key}, grip=grip, speed=speed)
            if not ok:
                results.append((m.key, f"REFUSED: {why}"))
                print(f"  REFUSED: {why}")
                continue
            t0 = time.monotonic()
            try:
                while arm.state is ArmState.MOVING:
                    if a.dry:
                        arm.tick(0.05)
                    else:
                        time.sleep(0.05)
            except KeyboardInterrupt:
                arm.stop("operator Ctrl-C")
                results.append((m.key, "STOPPED by you"))
                print("  stopped. Reset and re-arm to go on.")
                arm.reset()
                arm.arm()
                continue
            took = time.monotonic() - t0
            if arm.state is ArmState.READY:
                results.append((m.key, f"ok ({took:.1f} s)"))
                print(f"  done in {took:.1f} s")
            else:
                results.append((m.key, f"{arm.state}: {arm.reason}"))
                print(f"  {arm.state}: {arm.reason}")
                if arm.reset()[0]:
                    arm.arm()
            if not a.dry:
                note = input("  Looked right? Enter = yes, or type what was wrong: ").strip()
                if note:
                    results[-1] = (m.key, f"{results[-1][1]}  NOTE: {note}")
    finally:
        arm.disarm()
        arm.close()
    print("\nsummary")
    for key, what in results:
        print(f"  {key:11} {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
