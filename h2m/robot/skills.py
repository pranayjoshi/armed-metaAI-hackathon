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
    return PoseLibrary(poses, slots, path)


def save_pose(library: PoseLibrary, name: str, joints: Joints) -> None:
    """Write a taught pose back to the YAML file (used by `h2m arm teach`)."""
    if library.path is None:
        raise PoseError("library has no file")
    data = yaml.safe_load(library.path.read_text()) or {}
    entry = {"taught": True, "joints": {j: round(float(joints[j]), 2) for j in JOINTS}}
    if "." in name:
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


def _move(label: str, pose: Pose, stage: str, gripper: float | None = None) -> Waypoint:
    joints = dict(pose.joints)
    if gripper is not None:
        joints[GRIPPER] = gripper
    return Waypoint(label, joints, stage)


def plan_home(lib: PoseLibrary) -> Plan:
    home = lib.pose("home")
    return Plan("home", (_move("home", home, "home"),), (home,))


def plan_point(lib: PoseLibrary, slot: Slot) -> Plan:
    home = lib.pose("home")
    return Plan(
        "point",
        (
            _move(f"{slot.slot_id}.point", slot.point, "point"),
            Waypoint("hold", {}, "point", dwell_s=0.8),
            _move("home", home, "return"),
        ),
        (slot.point, home),
    )


def plan_pick_place(lib: PoseLibrary, slot: Slot, open_pct: float, close_pct: float) -> Plan:
    home, tray_above, tray_release = (lib.pose(n) for n in ("home", "tray_above", "tray_release"))
    return Plan(
        "pick_place",
        (
            _move(f"{slot.slot_id}.above", slot.above, "approach", gripper=open_pct),
            _move(f"{slot.slot_id}.grasp", slot.grasp, "approach", gripper=open_pct),
            Waypoint("close", {GRIPPER: close_pct}, "grasp", gripper_only=True, dwell_s=0.4),
            _move("lift", slot.above, "carry", gripper=close_pct),
            _move("tray_above", tray_above, "carry", gripper=close_pct),
            _move("tray_release", tray_release, "release", gripper=close_pct),
            Waypoint("open", {GRIPPER: open_pct}, "release", gripper_only=True, dwell_s=0.3),
            _move("retreat", tray_above, "return", gripper=open_pct),
            _move("home", home, "return"),
        ),
        (slot.above, slot.grasp, tray_above, tray_release, home),
    )
