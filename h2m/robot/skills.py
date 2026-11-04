"""Taught poses and the skills built from them.

The compiler decides *what* (verb + object); a skill decides *how*: a fixed
sequence of waypoints between taught poses. No free-form motion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from h2m.grasp import ROLL_ENVELOPE_MAX_DEG
from h2m.robot.backend import GRIPPER, JOINTS, Joints


class PoseError(ValueError):
    pass


@dataclass(frozen=True)
class Pose:
    name: str
    joints: dict[str, float]
    taught: bool = False


@dataclass(frozen=True)
class Slot:
    slot_id: str
    table_xy: tuple[float, float]
    above: Pose
    grasp: Pose
    point: Pose


@dataclass
class PoseLibrary:
    poses: dict[str, Pose]
    slots: dict[str, Slot]
    path: Path | None = None
    # Carry routes: name -> taught via poses between lift and tray (empty = direct).
    routes: dict[str, tuple[Pose, ...]] = field(default_factory=dict)

    def pose(self, name: str) -> Pose:
        if "." in name:
            slot_id, kind = name.split(".", 1)
            return getattr(self.slots[slot_id], kind)
        return self.poses[name]

    def nearest_slot(self, xy: tuple[float, float] | None, tolerance: float) -> Slot | None:
        if xy is None:
            return None
        ranked = sorted(self.slots.values(), key=lambda s: math.dist(xy, s.table_xy))
        if ranked and math.dist(xy, ranked[0].table_xy) <= tolerance:
            return ranked[0]
        return None

    def untaught(self) -> list[str]:
        names = [p.name for p in self.poses.values() if not p.taught]
        for slot in self.slots.values():
            names += [p.name for p in (slot.above, slot.grasp, slot.point) if not p.taught]
        return names


def _pose(name: str, raw: Any) -> Pose:
    joints = {str(k): float(v) for k, v in (raw or {}).get("joints", {}).items()}
    if sorted(joints) != sorted(JOINTS):
        raise PoseError(f"pose {name} must define exactly {JOINTS}")
    return Pose(name, joints, bool(raw.get("taught", False)))


def load_poses(path: Path) -> PoseLibrary:
    data = yaml.safe_load(path.read_text()) or {}
    poses = {str(k): _pose(str(k), v) for k, v in (data.get("poses") or {}).items()}
    for required in ("home", "tray_above", "tray_release"):
        if required not in poses:
            raise PoseError(f"poses.{required} is required")
    slots = {}
    for slot_id, raw in (data.get("slots") or {}).items():
        x, y = raw["table_xy"]
        slots[str(slot_id)] = Slot(
            str(slot_id),
            (float(x), float(y)),
            *(_pose(f"{slot_id}.{kind}", raw.get(kind)) for kind in ("above", "grasp", "point")),
        )
    routes = {}
    for name, via in (data.get("routes") or {}).items():
        missing = [str(v) for v in (via or []) if str(v) not in poses]
        if missing:
            raise PoseError(f"route {name} uses undefined poses: {', '.join(missing)}")
        routes[str(name)] = tuple(poses[str(v)] for v in (via or []))
    return PoseLibrary(poses, slots, path, routes)


def save_pose(library: PoseLibrary, name: str, joints: Joints) -> None:
    """Write a taught pose back to the YAML file (used by `h2m arm teach`)."""
    if library.path is None:
        raise PoseError("library has no file")
    data = yaml.safe_load(library.path.read_text()) or {}
    entry = {"taught": True, "joints": {j: round(float(joints[j]), 2) for j in JOINTS}}
    if name.startswith("gesture."):
        # gesture.<gesture>.<step>: one keyframe of a gesture, kept in step order
        _, gesture, step = name.split(".", 2)
        steps = data.setdefault("gestures", {}).setdefault(gesture, [])
        for raw in steps:
            if raw.get("name") == step:
                raw.update(entry)
                break
        else:
            steps.append({"name": step, **entry})
    elif "." in name:
        slot_id, kind = name.split(".", 1)
        data.setdefault("slots", {}).setdefault(slot_id, {})[kind] = entry
    else:
        data.setdefault("poses", {})[name] = entry
    library.path.write_text(yaml.safe_dump(data, sort_keys=False))


@dataclass(frozen=True)
class Waypoint:
    label: str
    joints: dict[str, float]
    stage: str
    gripper_only: bool = False
    dwell_s: float = 0.0


@dataclass(frozen=True)
class Plan:
    skill: str
    waypoints: tuple[Waypoint, ...]
    poses: tuple[Pose, ...] = field(default_factory=tuple)

    def untaught(self) -> list[str]:
        return sorted({p.name for p in self.poses if not p.taught})


def _move(
    label: str, pose: Pose, stage: str, gripper: float | None = None, dwell_s: float = 0.0
) -> Waypoint:
    joints = dict(pose.joints)
    if gripper is not None:
        joints[GRIPPER] = gripper
    return Waypoint(label, joints, stage, dwell_s=dwell_s)


def _arm_only(label: str, pose: Pose, stage: str) -> Waypoint:
    """The five arm joints of a pose; the gripper keeps whatever it is commanded now."""
    return Waypoint(label, {j: v for j, v in pose.joints.items() if j != GRIPPER}, stage)


def plan_home(lib: PoseLibrary, gripper: bool = False) -> Plan:
    """``gripper``: also command the home pose's gripper value. Only for a gripper
    proven empty; otherwise the gripper is left as it is (it may be holding)."""
    home = lib.pose("home")
    move = _move if gripper else _arm_only
    return Plan("home", (move("home", home, "home"),), (home,))


def plan_point(lib: PoseLibrary, slot: Slot, gripper: bool = False) -> Plan:
    """``gripper``: as for plan_home."""
    home = lib.pose("home")
    move = _move if gripper else _arm_only
    return Plan(
        "point",
        (
            move(f"{slot.slot_id}.point", slot.point, "point"),
            Waypoint("hold", {}, "point", dwell_s=0.8),
            move("home", home, "return"),
        ),
        (slot.point, home),
    )


def _with_roll_delta(pose: Pose, delta: float) -> Pose:
    """``pose`` with ``delta`` (already clamped to the envelope) added to its own taught
    wrist_roll; the same object for 0. Keeps the name and taught flag: every joint is the
    taught value except the roll, which moves by ``delta`` from the taught one."""
    if delta == 0.0:
        return pose
    roll = pose.joints["wrist_roll"] + delta
    return Pose(pose.name, {**pose.joints, "wrist_roll": roll}, pose.taught)


def plan_pick_approach(lib: PoseLibrary, slot: Slot, open_pct: float) -> Plan:
    """Stage 1 of a pick whose jaw opening is measured at ``above``: the approach to
    ``slot.above`` only, at the taught opening. The rest of the pick is rebuilt by
    ``plan_pick_place`` once the measurement is in (h2m/grasp.py), or not run at all."""
    return Plan(
        "pick_place",
        (_move(f"{slot.slot_id}.above", slot.above, "approach", gripper=open_pct),),
        (slot.above,),
    )


def plan_pick_place(
    lib: PoseLibrary,
    slot: Slot,
    open_pct: float,
    close_pct: float,
    via: tuple[Pose, ...] = (),
    *,
    grasp_open_pct: float | None = None,
    grasp_roll_deg: float | None = None,
    roll_envelope_deg: float = 25.0,
) -> Plan:
    """``via``: taught poses of the chosen carry route (``lib.routes[name]``),
    visited in order between the lift and the tray.

    SAFETY REVIEW (h2m/grasp.py): app.py may pass these (it passes grasp_open_pct from
    the slot's taught jaws; the SAM-measured values only through ``grasp_params``).
    ``grasp_open_pct`` replaces ``open_pct`` for the approach and the grasp, and the
    release at the tray opens to ``max(open_pct, grasp_open_pct)`` (taught or grasp,
    whichever is wider) so a wide object is let go; that is pending a measured
    ``arm.tray_release_max_pct``, whose measurement and refusal app.py owns.
    ``grasp_roll_deg`` is the target wrist roll at the slot's ``grasp`` pose (as
    ``GraspParams.roll_deg`` gives it): its difference from the taught grasp roll,
    clamped to +- ``roll_envelope_deg`` (itself in 0..ROLL_ENVELOPE_MAX_DEG), is added
    as one delta to the taught roll of ``above``, ``grasp`` and the lift, so taught
    poses keep their roll difference. Both None: the plan is exactly the taught one. A
    bool or non-finite value, or an opening outside 0..100, raises PoseError (a
    ValueError; no plan)."""
    for name, value in (
        ("grasp_open_pct", grasp_open_pct),
        ("grasp_roll_deg", grasp_roll_deg),
        ("roll_envelope_deg", roll_envelope_deg),
    ):
        if isinstance(value, bool):
            raise PoseError(f"{name} must be a number, not a bool")
        if value is not None and not math.isfinite(value):
            raise PoseError(f"{name} must be finite")
    envelope = max(0.0, min(float(roll_envelope_deg), ROLL_ENVELOPE_MAX_DEG))
    if grasp_open_pct is not None and not 0.0 <= grasp_open_pct <= 100.0:
        raise PoseError("grasp_open_pct must be within 0..100")
    approach_open = open_pct if grasp_open_pct is None else grasp_open_pct
    release_open = open_pct if grasp_open_pct is None else max(open_pct, grasp_open_pct)
    delta = 0.0
    if grasp_roll_deg is not None:
        delta = grasp_roll_deg - slot.grasp.joints["wrist_roll"]
        delta = max(-envelope, min(envelope, delta))
    above = _with_roll_delta(slot.above, delta)
    grasp = _with_roll_delta(slot.grasp, delta)
    home, tray_above, tray_release = (lib.pose(n) for n in ("home", "tray_above", "tray_release"))
    return Plan(
        "pick_place",
        (
            _move(f"{slot.slot_id}.above", above, "approach", gripper=approach_open),
            # Settle before the jaws move: arrival only means within arrive_tolerance.
            _move(f"{slot.slot_id}.grasp", grasp, "approach", gripper=approach_open, dwell_s=0.4),
            Waypoint("close", {GRIPPER: close_pct}, "grasp", gripper_only=True, dwell_s=0.4),
            _move("lift", above, "carry", gripper=close_pct),
            *(_move(f"via.{p.name}", p, "carry", gripper=close_pct) for p in via),
            _move("tray_above", tray_above, "carry", gripper=close_pct),
            _move("tray_release", tray_release, "release", gripper=close_pct, dwell_s=0.4),
            Waypoint("open", {GRIPPER: release_open}, "release", gripper_only=True, dwell_s=0.3),
            _move("retreat", tray_above, "return", gripper=release_open),
            _move("home", home, "return"),
        ),
        (slot.above, slot.grasp, *via, tray_above, tray_release, home),
    )


# ── taught skills ─────────────────────────────────────────────────────────────
# Fixed sequences of taught poses from configs/skills.yaml. They always confirm, so
# they may leave the home envelope; they may never name a pose that is not taught.

SKILL_GRIPPER = ("open", "soft", "keep")
SKILL_KEYS = frozenset(
    {"steps", "requires_object", "needs_holding", "needs_empty", "carries", "near_person",
     "speed", "say"}
)
STEP_KEYS = frozenset({"pose", "gripper", "dwell"})
MAX_DWELL_S = 30.0
HANDOVER_TOKEN = "{handover}"
PART_ALL, PART_PICK, PART_CARRY = "all", "pick", "carry"


@dataclass(frozen=True)
class SkillStep:
    pose: str | None  # None: gripper-only step; may contain "{slot}" / "{handover}" (object skills)
    gripper: str | None = None  # open | soft | None (keep)
    dwell_s: float = 0.0


@dataclass(frozen=True)
class Skill:
    name: str
    steps: tuple[SkillStep, ...]
    requires_object: bool = False
    needs_holding: bool = False
    needs_empty: bool = False
    carries: bool = False
    near_person: bool = False
    speed: float = 1.0
    say: str = ""

    @property
    def closes(self) -> bool:
        return any(s.gripper == "soft" for s in self.steps)

    @property
    def uses_handover(self) -> bool:
        """A step names ``{handover}_...``: the pose set for the object's hand spot."""
        return any(s.pose and HANDOVER_TOKEN in s.pose for s in self.steps)

    @property
    def handover_cut(self) -> int | None:
        """Index of the first ``{handover}`` step: a two-stage run stops just before it."""
        return next(
            (i for i, s in enumerate(self.steps) if s.pose and HANDOVER_TOKEN in s.pose), None
        )


def load_skills(path: Path) -> dict[str, Skill]:
    """Parse the skills file. Malformed entries raise PoseError; nothing is defaulted
    silently. Pose names are checked against the library at plan time."""
    data = yaml.safe_load(path.read_text()) or {}
    out: dict[str, Skill] = {}
    for name, raw in (data.get("skills") or {}).items():
        raw = raw or {}
        if unknown := sorted(set(raw) - SKILL_KEYS):
            raise PoseError(f"skill {name}: unknown keys {', '.join(unknown)}")
        for flag in ("requires_object", "needs_holding", "needs_empty", "carries", "near_person"):
            if flag in raw and not isinstance(raw[flag], bool):
                raise PoseError(f"skill {name}: {flag} must be true or false")
        steps = []
        for i, step in enumerate(raw.get("steps") or []):
            step = step or {}
            if unknown := sorted(set(step) - STEP_KEYS):
                raise PoseError(f"skill {name} step {i}: unknown keys {', '.join(unknown)}")
            pose = step.get("pose")
            gripper = step.get("gripper")
            if pose is None and gripper in (None, "keep"):
                raise PoseError(f"skill {name} step {i} moves nothing")
            if gripper is not None and gripper not in SKILL_GRIPPER:
                raise PoseError(f"skill {name} step {i}: gripper must be one of {SKILL_GRIPPER}")
            dwell = float(step.get("dwell", 0.0))
            if not math.isfinite(dwell) or not 0 <= dwell <= MAX_DWELL_S:
                raise PoseError(f"skill {name} step {i}: dwell must be within 0..{MAX_DWELL_S}")
            steps.append(SkillStep(str(pose) if pose else None, None if gripper == "keep" else gripper, dwell))
        if not steps:
            raise PoseError(f"skill {name} has no steps")
        speed = float(raw.get("speed", 1.0))
        if not math.isfinite(speed) or not 0.1 <= speed <= 1.0:
            raise PoseError(f"skill {name}: speed must be within 0.1..1.0")
        uses_slot = any(s.pose and "{slot}" in s.pose for s in steps)
        requires_object = bool(raw.get("requires_object", False))
        if uses_slot and not requires_object:
            raise PoseError(f"skill {name} uses {{slot}} but requires_object is false")
        if any(s.pose and HANDOVER_TOKEN in s.pose for s in steps):
            # The set comes from the object, and its hand spot is next to a person.
            if not requires_object:
                raise PoseError(f"skill {name} uses {{handover}} but requires_object is false")
            if raw.get("near_person") is not True:
                raise PoseError(f"skill {name} uses {{handover}} so it must be near_person")
        out[str(name)] = Skill(
            str(name),
            tuple(steps),
            requires_object=requires_object,
            needs_holding=bool(raw.get("needs_holding", False)),
            needs_empty=bool(raw.get("needs_empty", False)),
            carries=bool(raw.get("carries", False)),
            near_person=bool(raw.get("near_person", False)),
            speed=speed,
            say=str(raw.get("say") or "")[:80],
        )
    return out


def handover_poses(lib: PoseLibrary, prefix: str) -> tuple[Pose, Pose]:
    """The ``<prefix>_above`` / ``<prefix>_release`` pair of a handover set. Both must
    exist, and every set but the original ``handover`` must have both taught, or the set
    is refused (PoseError). (The original set's placeholders still run on the virtual arm,
    as before; the real arm refuses any untaught pose when it starts a plan.)"""
    pair = []
    for suffix in ("above", "release"):
        name = f"{prefix}_{suffix}"
        pose = lib.poses.get(name)
        if pose is None:
            raise PoseError(f"handover set {prefix!r} has no {name} pose")
        if not pose.taught and prefix != "handover":
            raise PoseError(f"handover set {prefix!r}: {name} is not taught yet")
        pair.append(pose)
    return pair[0], pair[1]


def plan_skill(
    lib: PoseLibrary,
    skill: Skill,
    open_pct: float,
    soft_pct: float,
    slot: Slot | None = None,
    handover: str | None = None,
    part: str = PART_ALL,
) -> Plan:
    """Build the skill's waypoints from taught poses only, then return to home. A
    gripper step is gripper-only; a pose step with a gripper commands both. Raises
    KeyError for an undefined pose and PoseError when an object skill has no slot, or
    its ``{handover}`` set is missing or untaught.

    ``part`` splits a ``{handover}`` skill at its first ``{handover}`` step: ``pick`` is
    everything before it (no trip home: the arm stops holding at the lift) and ``carry``
    is the rest, run from there with the object already held."""
    if skill.requires_object and slot is None:
        raise PoseError(f"skill {skill.name} needs an object on a taught slot")
    steps = skill.steps
    holding = skill.needs_holding
    if skill.uses_handover:
        if not handover:
            raise PoseError(f"skill {skill.name} needs a handover set")
        handover_poses(lib, handover)
    if part != PART_ALL:
        cut = skill.handover_cut
        if cut is None:
            raise PoseError(f"skill {skill.name} has no {{handover}} step to split at")
        steps, holding = (steps[:cut], holding) if part == PART_PICK else (steps[cut:], True)
    pct = {"open": open_pct, "soft": soft_pct}
    waypoints: list[Waypoint] = []
    poses: list[Pose] = []
    # Stages drive the freeze's grip policy (arm._keep_grip): a close is "grasp", every
    # step between a close and the next open is "carry" (the grip is kept on a stop),
    # the open is "release", and the rest is "approach" / "return". Going down onto the
    # hand spot with the object is "lower": the hand watchdog runs there and on release.
    first = skill.handover_cut or 0 if part == PART_CARRY else 0  # labels keep the skill's numbering
    for i, step in enumerate(steps, first):
        if step.pose is None:
            if step.gripper == "soft":
                stage, holding = "grasp", True
            else:
                stage, holding = "release", False
            waypoints.append(
                Waypoint(f"gripper_{step.gripper}", {GRIPPER: pct[step.gripper]}, stage,
                         gripper_only=True, dwell_s=step.dwell_s)
            )
            continue
        name = step.pose.replace("{slot}", slot.slot_id) if slot else step.pose
        name = name.replace(HANDOVER_TOKEN, handover or HANDOVER_TOKEN)
        pose = lib.pose(name)
        poses.append(pose)
        if step.gripper is None:
            joints = {j: v for j, v in pose.joints.items() if j != GRIPPER}
        else:
            joints = {**pose.joints, GRIPPER: pct[step.gripper]}
        stage = "carry" if holding else "approach"
        if holding and HANDOVER_TOKEN in step.pose and name.endswith("_release"):
            stage = "lower"
        waypoints.append(Waypoint(f"{i}.{name}", joints, stage, dwell_s=step.dwell_s))
    if part != PART_PICK:
        home = lib.pose("home")
        waypoints.append(_arm_only("home", home, "carry" if holding else "return"))
        poses.append(home)
    return Plan(f"skill.{skill.name}", tuple(waypoints), tuple(poses))


def plan_move(
    lib: PoseLibrary,
    src: Slot,
    dst: Slot,
    open_pct: float,
    close_pct: float,
    via: tuple[Pose, ...] = (),
) -> Plan:
    """Pick at one taught slot and set down at another (tidy / rearrange). Same shape
    as pick_place with the destination slot's poses in place of the tray."""
    if src.slot_id == dst.slot_id:
        raise PoseError("move needs two different slots")
    home = lib.pose("home")
    return Plan(
        "move",
        (
            _move(f"{src.slot_id}.above", src.above, "approach", gripper=open_pct),
            _move(f"{src.slot_id}.grasp", src.grasp, "approach", gripper=open_pct, dwell_s=0.4),
            Waypoint("close", {GRIPPER: close_pct}, "grasp", gripper_only=True, dwell_s=0.4),
            _move("lift", src.above, "carry", gripper=close_pct),
            *(_move(f"via.{p.name}", p, "carry", gripper=close_pct) for p in via),
            _move(f"{dst.slot_id}.above", dst.above, "carry", gripper=close_pct),
            _move(f"{dst.slot_id}.grasp", dst.grasp, "release", gripper=close_pct, dwell_s=0.4),
            Waypoint("open", {GRIPPER: open_pct}, "release", gripper_only=True, dwell_s=0.3),
            _move("retreat", dst.above, "return", gripper=open_pct),
            _move("home", home, "return"),
        ),
        (src.above, src.grasp, *via, dst.above, dst.grasp, home),
    )
