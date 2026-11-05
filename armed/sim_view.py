"""Schematic top-down view of the virtual table, for the simulation only.

There is no camera in the software demo, so the scenario (armed.scenario) renders this
picture of the table instead: avoid zones, slots, objects, the active carry route and
an estimate of where the gripper is. The estimate projects the measured joints onto
the taught plan in joint space and maps that onto the route polyline in
configs/routes.yaml. It is a drawing, not forward kinematics, and every frame carries
a SIMULATION label. Nothing here feeds a gate decision.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any

import cv2
import numpy as np

from armed.robot.backend import GRIPPER, JOINTS
from armed.robot.skills import PoseLibrary
from armed.routing import RouteConfig

Point = tuple[float, float]
ARM_JOINTS = [j for j in JOINTS if j != GRIPPER]
HOME_XY: Point = (0.50, 0.92)  # schematic: where the arm parks, on the user's side
SIZE = (320, 240)  # width, height: the incident buffer keeps 320 px anyway
MARGIN = 18
LABEL = "SIMULATION"

# BGR
INK = (40, 28, 22)
TABLE = (70, 56, 46)
GRID = (84, 70, 60)
TEXT = (225, 225, 225)
DIM = (150, 150, 150)
ZONE = (40, 120, 200)  # amber: avoid zone (red is kept for brace / stop)
ROUTE = (200, 170, 90)
TRAY = (120, 120, 120)
GRIP_OK = (120, 210, 120)
GRIP_STOP = (60, 60, 235)
OBJECT_COLORS = {"cup": (230, 170, 60), "block": (60, 120, 180), "ball": (80, 220, 200)}


def _vec(joints: Mapping[str, Any]) -> np.ndarray | None:
    try:
        return np.array([float(joints[j]) for j in ARM_JOINTS])
    except (KeyError, TypeError, ValueError):
        return None


def _arc(points: Sequence[Point]) -> list[float]:
    cum = [0.0]
    for a, b in zip(points, points[1:], strict=False):
        cum.append(cum[-1] + math.dist(a, b))
    return cum


def point_along(points: Sequence[Point], frac: float) -> Point:
    """The point at ``frac`` (0..1) of a polyline's length."""
    if not points:
        return HOME_XY
    if len(points) == 1:
        return points[0]
    cum = _arc(points)
    target = max(0.0, min(1.0, frac)) * cum[-1]
    for i in range(1, len(points)):
        if target <= cum[i] or i == len(points) - 1:
            seg = cum[i] - cum[i - 1]
            t = 0.0 if seg <= 0 else (target - cum[i - 1]) / seg
            (x0, y0), (x1, y1) = points[i - 1], points[i]
            return (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
    return points[-1]


def project(q: np.ndarray, path: Sequence[np.ndarray]) -> tuple[float, float]:
    """(arc fraction along the joint-space path nearest to q, distance to it)."""
    if len(path) == 1:
        return 0.0, float(np.linalg.norm(q - path[0]))
    lengths = [float(np.linalg.norm(b - a)) for a, b in zip(path, path[1:], strict=False)]
    total = sum(lengths)
    best = (0.0, math.inf)
    done = 0.0
    for (a, b), seg in zip(zip(path, path[1:], strict=False), lengths, strict=True):
        t = 0.0 if seg <= 0 else float(np.clip(np.dot(q - a, b - a) / seg**2, 0.0, 1.0))
        dist = float(np.linalg.norm(q - (a + (b - a) * t)))
        if dist < best[1]:
            best = ((done + t * seg) / total if total > 0 else 0.0, dist)
        done += seg
    return best


class TableView:
    """Draws the virtual table. ``render`` is called from the incident sampler thread
    and the HTTP thread; it only reads its arguments and its own trail."""

    def __init__(
        self,
        poses: PoseLibrary,
        routes: RouteConfig | None,
        objects: Sequence[Mapping[str, Any]],
    ) -> None:
        self.poses, self.routes = poses, routes
        self.objects = list(objects)
        self.trail: deque[Point] = deque(maxlen=40)
        self.placed: set[str] = set()  # objects a completed pick left on the tray

    # ── geometry ─────────────────────────────────────────────────────────────
    def route_points(self, route: str | None, slot_id: str | None) -> list[Point]:
        """The table polyline of a carry route from a slot (direct when unnamed)."""
        if self.routes is None or slot_id is None:
            return []
        per = self.routes.routes.get(route or "direct") or self.routes.routes.get("direct") or {}
        return [tuple(p) for p in per.get(slot_id, [])]  # type: ignore[misc]

    def tray_xy(self, slot_id: str | None) -> Point:
        direct = self.route_points("direct", slot_id or next(iter(self.poses.slots), None))
        return direct[-1] if direct else (0.5, 0.2)

    def gripper_xy(
        self,
        joints: Mapping[str, Any],
        stage: str | None,
        route: str | None,
        slot_id: str | None,
    ) -> Point | None:
        """Schematic table position of the gripper for these measured joints."""
        q = _vec(joints)
        slot = self.poses.slots.get(slot_id or "")
        if q is None:
            return None
        try:
            home = _vec(self.poses.pose("home").joints)
            tray_above = _vec(self.poses.pose("tray_above").joints)
            tray_release = _vec(self.poses.pose("tray_release").joints)
        except KeyError:
            return None
        if slot is None:
            return HOME_XY
        above, grasp = _vec(slot.above.joints), _vec(slot.grasp.joints)
        via = [_vec(p.joints) for p in self.poses.routes.get(route or "", ())]
        line = self.route_points(route, slot.slot_id) or [slot.table_xy, self.tray_xy(slot_id)]
        end = line[-1]
        carry_path = [above, *via, tray_above]
        # (joint path, matching table points) per part of the pick
        parts: dict[str, tuple[list[np.ndarray], list[Point]]] = {
            "approach": ([home, above, grasp], [HOME_XY, slot.table_xy, slot.table_xy]),
            "carry": (carry_path, []),
            "release": ([tray_above, tray_release], [end, end]),
            "return": ([tray_above, home], [end, HOME_XY]),
        }
        candidates = [stage] if stage in parts else list(parts)
        best: tuple[float, Point] = (math.inf, HOME_XY)
        for name in candidates:
            path, pts = parts[name]
            if any(p is None for p in path):
                continue
            frac, dist = project(q, path)  # type: ignore[arg-type]
            if name == "carry":
                xy = point_along(line, frac)
            else:
                cum = [0.0]
                for a, b in zip(path, path[1:], strict=False):
                    cum.append(cum[-1] + float(np.linalg.norm(b - a)))
                target = frac * cum[-1]
                i = next((k for k in range(1, len(cum)) if target <= cum[k]), len(cum) - 1)
                seg = cum[i] - cum[i - 1]
                t = 0.0 if seg <= 0 else (target - cum[i - 1]) / seg
                (x0, y0), (x1, y1) = pts[i - 1], pts[i]
                xy = (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
            if dist < best[0]:
                best = (dist, xy)
        return best[1]

    def zone_distance(self, xy: Point | None, zone: str) -> float | None:
        """Table distance from xy to an avoid zone (0 inside), or None."""
        box = self.routes.zones.get(zone) if self.routes else None
        if xy is None or box is None:
            return None
        x0, y0, x1, y1 = box
        dx = max(x0 - xy[0], 0.0, xy[0] - x1)
        dy = max(y0 - xy[1], 0.0, xy[1] - y1)
        return math.hypot(dx, dy)

    # ── drawing ──────────────────────────────────────────────────────────────
    @staticmethod
    def px(xy: Point) -> tuple[int, int]:
        w, h = SIZE
        return (
            round(MARGIN + xy[0] * (w - 2 * MARGIN)),
            round(MARGIN + xy[1] * (h - 2 * MARGIN)),
        )

    def object_box(self, xy: Point, r: int = 9) -> list[float]:
        """Pixel box of an object drawn at xy (for the incident's blame highlight)."""
        cx, cy = self.px(xy)
        return [float(cx - r), float(cy - r), float(cx + r), float(cy + r)]

    def render(
        self,
        joints: Mapping[str, Any],
        stage: str | None,
        route: str | None,
        slot_id: str | None,
        held: str | None,
        stopped: bool,
        caption: str = "",
    ) -> np.ndarray:
        w, h = SIZE
        img = np.full((h, w, 3), INK, dtype=np.uint8)
        tl, br = self.px((0.0, 0.0)), self.px((1.0, 1.0))
        cv2.rectangle(img, tl, br, TABLE, -1)
        for i in range(1, 4):
            x = self.px((i / 4, 0))[0]
            y = self.px((0, i / 4))[1]
            cv2.line(img, (x, tl[1]), (x, br[1]), GRID, 1)
            cv2.line(img, (tl[0], y), (br[0], y), GRID, 1)
        font = cv2.FONT_HERSHEY_SIMPLEX
        for name, (x0, y0, x1, y1) in (self.routes.zones.items() if self.routes else []):
            cv2.rectangle(img, self.px((x0, y0)), self.px((x1, y1)), ZONE, -1)
            cv2.putText(img, name, self.px((x0, y1 + 0.06)), font, 0.35, TEXT, 1, cv2.LINE_AA)
        tray = self.tray_xy(slot_id)
        cv2.rectangle(img, self.px((tray[0] - 0.05, tray[1] - 0.04)),
                      self.px((tray[0] + 0.05, tray[1] + 0.04)), TRAY, 1)
        cv2.putText(img, "tray", self.px((tray[0] - 0.04, tray[1] - 0.06)), font, 0.3, DIM, 1,
                    cv2.LINE_AA)
        line = self.route_points(route, slot_id)
        if len(line) >= 2:
            pts = np.array([self.px(p) for p in line], dtype=np.int32)
            cv2.polylines(img, [pts], False, ROUTE, 1, cv2.LINE_AA)
            end = line[-1]
            cv2.putText(img, route or "direct", self.px((end[0] + 0.07, end[1] + 0.01)), font,
                        0.3, ROUTE, 1, cv2.LINE_AA)
        grip = self.gripper_xy(joints, stage, route, slot_id)
        slots = list(self.poses.slots.values())
        for i, obj in enumerate(self.objects):
            slot = self.poses.slots.get(str(obj.get("slot"))) if obj.get("slot") else (
                None if "slot" in obj else slots[i] if i < len(slots) else None)
            if slot is None or (held is not None and obj.get("id") == held):
                continue
            xy = tray if obj.get("id") in self.placed else slot.table_xy
            color = OBJECT_COLORS.get(str(obj.get("id")), DIM)
            cv2.circle(img, self.px(xy), 8, color, -1, cv2.LINE_AA)
            cv2.putText(img, str(obj.get("id")), self.px((xy[0] - 0.04, xy[1] + 0.09)), font,
                        0.3, TEXT, 1, cv2.LINE_AA)
        if grip is not None:
            self.trail.append(grip)
            trail = list(self.trail)
            for a, b in zip(trail, trail[1:], strict=False):
                cv2.line(img, self.px(a), self.px(b), DIM, 1, cv2.LINE_AA)
            if held is not None:
                color = OBJECT_COLORS.get(held, DIM)
                cv2.circle(img, self.px(grip), 8, color, -1, cv2.LINE_AA)
            ring = GRIP_STOP if stopped else GRIP_OK
            cv2.circle(img, self.px(grip), 11, ring, 2, cv2.LINE_AA)
        cv2.putText(img, LABEL, (6, 12), font, 0.38, GRIP_STOP, 1, cv2.LINE_AA)
        cv2.putText(img, "user side", (w - 58, 12), font, 0.3, DIM, 1, cv2.LINE_AA)
        if caption:
            cv2.putText(img, caption[:58], (6, h - 5), font, 0.3, TEXT, 1, cv2.LINE_AA)
        return img


def jpeg(img: np.ndarray, quality: int = 80) -> bytes | None:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None
