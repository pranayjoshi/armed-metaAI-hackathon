"""Calibration tools for the shoot: the table homography, avoid zones, route polylines,
the jaw opening, and one check that prints every calibration's state.

``armed calibrate table | zone | route | jaw | check`` (armed/cli.py). The functions that
compute take and return numbers; the few that save edit one line of a YAML file in
place, so the comments around it survive. Nothing here moves the virtual arm, and the
real arm moves only in ``jog_route`` behind ``--i-have-the-estop``.

Where each calibration lives:

- homography: ``vision.table_homography`` in configs/armed.yaml (armed/sensors/vision.py
  reads it at start), with the clicked points, the error and the frame beside it in
  data/calibration/table.json and table.jpg;
- a zone: ``zones.<name>`` in configs/routes.yaml, with data/calibration/zone_<name>.json
  and .jpg for the record (the check reads the record to tell a measured zone from the
  placeholder);
- a route: ``routes.<route>.<slot>`` in configs/routes.yaml with ``measured: true``; each
  pose's table xy also goes to data/calibration/pose_xy.yaml, which the virtual backend
  samples instead of a camera; ``handover`` writes ``handover.polyline`` and ``measured``;
- the jaw: ``grasp.jaw_calibration.points`` in configs/armed.yaml (armed/grasp.py). The
  ``calibrated`` flag is never written here: the safety review switches it on by hand.

Thresholds come from an optional ``calibration:`` section of the config, with the
defaults in ``DEFAULTS`` when it is absent.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from armed.core import ROOT

Point = tuple[float, float]
Box = tuple[float, float, float, float]

CALIBRATION_DIR = ROOT / "data" / "calibration"
JAW_TARGETS = (30.0, 60.0, 100.0)  # gripper percents the jaw opening is measured at
HANDOVER = "handover"
TRAY_POSES = ("tray_above", "tray_release")

DEFAULTS: dict[str, Any] = {
    # Least-squares fit over five or more points: refuse to save above this.
    "max_reprojection_error_px": 3.0,
    # Known marks (the slot tapes) mapped through the homography vs their table xy.
    "max_check_error_mm": 15.0,
    # What the film needs proven before `armed run --profile shoot` starts.
    "film_routes": [["direct", "slot_1"], ["via_high", "slot_1"]],
    "film_zone": "laptop",
    # Slot arc: the three slots sit at one radius from the base when the grasp poses
    # differ by no more than this in shoulder_lift, elbow_flex and wrist_flex ...
    "slot_arc_joint_tolerance_deg": 5.0,
    # ... and, with arm.base_table_xy set, their distances from it spread no more than this.
    "slot_arc_radius_tolerance": 0.02,
    "check_object_mm": 24.0,  # the jaw check prints the opening for an object this wide
}


class CalibrationError(ValueError):
    """A calibration that cannot be computed or saved; the message says why."""


def settings(cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    """``calibration:`` from the config over the defaults."""
    raw = (cfg or {}).get("calibration") or {}
    if not isinstance(raw, Mapping):
        raise CalibrationError("calibration must be a mapping")
    return {**DEFAULTS, **raw}


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def parse_points(text: str) -> list[Point]:
    """``"12,34;56,78"`` -> [(12.0, 34.0), (56.0, 78.0)]."""
    try:
        return [
            (float(x), float(y))
            for x, y in (pair.split(",") for pair in text.replace(" ", "").split(";") if pair)
        ]
    except ValueError:
        raise CalibrationError(
            f"points must be x,y pairs separated by ';' (got {text!r})"
        ) from None


# ── table homography ─────────────────────────────────────────────────────────
def table_corners(width: float, height: float) -> list[Point]:
    """Table xy of a rectangle's corners in click order: top-left, top-right,
    bottom-right, bottom-left, with the origin at the top-left corner of the tape."""
    if not (math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0):
        raise CalibrationError("the table size must be two positive numbers")
    return [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]


def fit_homography(image_pts: Sequence[Point], table_pts: Sequence[Point]) -> np.ndarray:
    """The 3x3 image -> table homography through four or more point pairs (least
    squares above four). CalibrationError when the points are too few or degenerate."""
    import cv2

    src, dst = np.asarray(image_pts, dtype=float), np.asarray(table_pts, dtype=float)
    if src.ndim != 2 or src.shape != dst.shape or src.shape[1] != 2 or len(src) < 4:
        raise CalibrationError("need the same four or more image and table points")
    if not (np.all(np.isfinite(src)) and np.all(np.isfinite(dst))):
        raise CalibrationError("points must be finite numbers")
    H, _ = cv2.findHomography(src, dst, 0)
    if H is None or not np.all(np.isfinite(H)) or abs(H[2, 2]) < 1e-12:
        raise CalibrationError("the points are degenerate (three on a line, or repeated)")
    return H / H[2, 2]


def apply_homography(H: Any, pts: Sequence[Point]) -> list[Point]:
    """Points through ``H`` (image -> table with the fitted one, or its inverse)."""
    arr = np.asarray(pts, dtype=float).reshape(-1, 2)
    v = np.column_stack([arr, np.ones(len(arr))]) @ np.asarray(H, dtype=float).T
    if np.any(np.abs(v[:, 2]) < 1e-12):
        raise CalibrationError("a point maps to infinity: the homography is unusable")
    out = v[:, :2] / v[:, 2:3]
    return [(round(float(x), 4), round(float(y), 4)) for x, y in out]


def reprojection_errors(
    H: Any, image_pts: Sequence[Point], table_pts: Sequence[Point]
) -> list[float]:
    """Pixel distance between each clicked point and its table point mapped back into
    the image. Zero for an exact four-point fit; meaningful from five points up."""
    back = apply_homography(np.linalg.inv(np.asarray(H, dtype=float)), table_pts)
    return [round(math.dist(a, b), 3) for a, b in zip(image_pts, back, strict=True)]


def check_errors(
    H: Any, image_pts: Sequence[Point], known_xy: Sequence[Point], unit_mm: float
) -> list[float]:
    """mm between known table marks and the same marks clicked in the image, through
    ``H`` (``unit_mm``: mm per table unit, grasp.table_unit_mm)."""
    mapped = apply_homography(H, image_pts)
    return [round(math.dist(a, b) * unit_mm, 1) for a, b in zip(mapped, known_xy, strict=True)]


@dataclass(frozen=True)
class TableFit:
    homography: list[list[float]]
    image_points: list[Point]
    table_points: list[Point]
    reprojection_px: list[float]
    check_mm: dict[str, float] = field(default_factory=dict)  # slot id -> mm off

    @property
    def max_reprojection_px(self) -> float:
        return max(self.reprojection_px) if self.reprojection_px else 0.0

    @property
    def max_check_mm(self) -> float:
        return max(self.check_mm.values()) if self.check_mm else 0.0

    def problems(self, cal: Mapping[str, Any]) -> list[str]:
        out = []
        limit = float(cal["max_reprojection_error_px"])
        if len(self.image_points) > 4 and self.max_reprojection_px > limit:
            out.append(f"reprojection error {self.max_reprojection_px:.2f} px is over {limit:g} px")
        limit = float(cal["max_check_error_mm"])
        if self.check_mm and self.max_check_mm > limit:
            worst = max(self.check_mm, key=self.check_mm.get)  # type: ignore[arg-type]
            out.append(
                f"{worst} lands {self.check_mm[worst]:.0f} mm from its table xy (limit {limit:g})"
            )
        return out


def fit_table(
    image_pts: Sequence[Point],
    table_pts: Sequence[Point],
    checks: Mapping[str, tuple[Point, Point]] | None = None,
    unit_mm: float = 1000.0,
) -> TableFit:
    """Fit and score. ``checks``: name -> (image point, known table xy) of marks that
    were not used in the fit, e.g. the slot tapes."""
    H = fit_homography(image_pts, table_pts)
    errors = reprojection_errors(H, image_pts, table_pts)
    check_mm: dict[str, float] = {}
    for name, (img, xy) in (checks or {}).items():
        check_mm[str(name)] = check_errors(H, [img], [xy], unit_mm)[0]
    return TableFit(
        [[round(float(v), 8) for v in row] for row in H],
        [tuple(p) for p in image_pts],
        [tuple(p) for p in table_pts],
        errors,
        check_mm,
    )


def _find_line(
    lines: list[str], prefix: str, start: int = 0, stop: int | None = None
) -> int | None:
    for i in range(start, len(lines) if stop is None else stop):
        if lines[i].strip().startswith(prefix):
            return i
    return None


def _section_end(lines: list[str], start: int) -> int:
    """Index of the next top-level key after the one at ``start`` (or len)."""
    for i in range(start + 1, len(lines)):
        if lines[i] and not lines[i][0].isspace() and not lines[i].startswith("#"):
            return i
    return len(lines)


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _replace_entry(lines: list[str], i: int, text: str) -> None:
    """Replace the ``key:`` line at ``i`` with ``text`` and drop the lines that continued
    its old value (anything indented deeper, e.g. a block-style list), so a one-line
    flow value never leaves the old block's items dangling under it."""
    depth = len(_indent(lines[i]))
    j = i + 1
    while j < len(lines) and (
        not lines[j].strip() or len(_indent(lines[j])) > depth or lines[j].lstrip().startswith("-")
    ):
        if not lines[j].strip() and (j + 1 >= len(lines) or len(_indent(lines[j + 1])) <= depth):
            break  # a blank line before the next key stays
        j += 1
    lines[i:j] = [text]


def _flow(value: Any) -> str:
    return json.dumps(value, separators=(", ", ": "))


def write_homography(config_path: Path, H: Sequence[Sequence[float]], note: str = "") -> None:
    """Replace the ``table_homography:`` line under ``vision:`` in the config file, in
    place. The file is read right before the write so a concurrent edit elsewhere in it
    is kept."""
    path = Path(config_path)
    lines = path.read_text().splitlines(keepends=True)
    vision = _find_line(lines, "vision:")
    if vision is None or not lines[vision].startswith("vision:"):
        raise CalibrationError(f"no vision: section in {path}")
    end = _section_end(lines, vision)
    i = _find_line(lines, "table_homography:", vision + 1, end)
    rows = [[round(float(v), 8) for v in row] for row in H]
    text = f"table_homography: {_flow(rows)}  # armed calibrate table {_stamp()}{note}\n"
    if i is None:
        lines.insert(end, "  " + text)
    else:
        _replace_entry(lines, i, _indent(lines[i]) + text)
    path.write_text("".join(lines))


def read_homography(cfg: Mapping[str, Any]) -> np.ndarray | None:
    raw = ((cfg.get("vision") or {}) if cfg else {}).get("table_homography")
    if not raw:
        return None
    H = np.asarray(raw, dtype=float)
    if H.shape != (3, 3) or not np.all(np.isfinite(H)):
        raise CalibrationError("vision.table_homography must be a 3x3 matrix of numbers")
    return H


def table_record_path() -> Path:
    return CALIBRATION_DIR / "table.json"


def save_table_record(fit: TableFit, frame_path: str | None, path: Path | None = None) -> Path:
    target = path or table_record_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "when": _stamp(),
                "homography": fit.homography,
                "image_points": fit.image_points,
                "table_points": fit.table_points,
                "reprojection_px": fit.reprojection_px,
                "check_mm": fit.check_mm,
                "frame": frame_path,
            },
            indent=2,
        )
    )
    return target


def load_table_record(path: Path | None = None) -> dict[str, Any] | None:
    target = path or table_record_path()
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text())
    except (OSError, ValueError):
        return None


# ── frames and clicks ────────────────────────────────────────────────────────
def grab_frame(index: int, width: int, height: int, warmup: int = 8) -> np.ndarray:
    """One frame from the overhead camera, the way armed/sensors/vision.py opens it (same
    index, size and orientation, no rotation)."""
    import cv2

    cap = cv2.VideoCapture(int(index))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
    if not cap.isOpened():
        raise CalibrationError(
            f"camera {index} did not open (run from a terminal with camera access, or pass --image)"
        )
    frame = None
    try:
        for _ in range(max(1, warmup)):
            ok, got = cap.read()
            if ok and got is not None:
                frame = got
    finally:
        cap.release()
    if frame is None:
        raise CalibrationError(f"camera {index} gave no frame")
    return frame


def load_frame(image: str | None, vision_cfg: Mapping[str, Any]) -> np.ndarray:
    import cv2

    if image:
        frame = cv2.imread(str(image))
        if frame is None:
            raise CalibrationError(f"cannot read image {image}")
        return frame
    return grab_frame(
        int(vision_cfg.get("scene_camera", 0)),
        int(vision_cfg.get("width", 1280)),
        int(vision_cfg.get("height", 720)),
    )


def save_frame(frame: np.ndarray, name: str) -> Path:
    import cv2

    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    path = CALIBRATION_DIR / f"{name}.jpg"
    cv2.imwrite(str(path), frame)
    return path


def pick_points(
    frame: np.ndarray, labels: Sequence[str], title: str = "armed calibrate"
) -> list[Point] | None:
    """Click one point per label on the frame. Enter accepts once all are placed,
    Backspace removes the last, Esc gives None. CalibrationError without a display."""
    import cv2

    pts: list[Point] = []

    def on_mouse(event: int, x: int, y: int, _flags: int, _param: Any) -> None:
        if event == cv2.EVENT_LBUTTONDOWN and len(pts) < len(labels):
            pts.append((float(x), float(y)))

    try:
        cv2.namedWindow(title)
        cv2.setMouseCallback(title, on_mouse)
    except cv2.error as exc:
        raise CalibrationError(
            f"no display for clicking ({exc}); pass the points instead"
        ) from None
    try:
        while True:
            canvas = frame.copy()
            for (x, y), label in zip(pts, labels, strict=False):
                cv2.drawMarker(canvas, (int(x), int(y)), (0, 255, 0), cv2.MARKER_CROSS, 24, 2)
                cv2.putText(
                    canvas,
                    label,
                    (int(x) + 8, int(y) - 8),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2,
                )
            if len(pts) < len(labels):
                prompt = f"click {labels[len(pts)]}  ({len(pts) + 1}/{len(labels)})  Backspace undo, Esc abort"
            else:
                prompt = "Enter to accept, Backspace to undo, Esc to abort"
            cv2.putText(canvas, prompt, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            cv2.imshow(title, canvas)
            key = cv2.waitKey(30) & 0xFF
            if key == 27:
                return None
            if key in (8, 127) and pts:
                pts.pop()
            if key in (13, 10) and len(pts) == len(labels):
                return list(pts)
    finally:
        cv2.destroyWindow(title)


def sam_boxes(
    frame: np.ndarray, prompt: str, vision_cfg: Mapping[str, Any] | None = None
) -> list[Box] | None:
    """Pixel boxes (x1, y1, x2, y2) for ``prompt`` from the SAM 3.1 client in
    armed/sensors/sam.py (``SamClient.segment_phrases``, normalized boxes scaled to the
    frame). None when the module, the key or the call is unavailable, or nothing matched:
    the caller falls back to clicks. The key is read by the client, never here."""
    try:
        from armed.sensors.sam import SamClient
    except ImportError:
        return None
    try:
        client = SamClient(dict(vision_cfg or {}))
        if not client.available:
            return None
        scan = client.segment_phrases(frame, [prompt])
    except Exception:  # noqa: BLE001  # any client failure means: click instead
        return None
    h, w = frame.shape[:2]
    boxes = [
        (m.bbox[0] * w, m.bbox[1] * h, m.bbox[2] * w, m.bbox[3] * h)
        for m in getattr(scan, "masks", [])
    ]
    return [tuple(float(v) for v in b) for b in boxes] or None


def box_centres(boxes: Sequence[Box]) -> list[Point]:
    return [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in boxes]


def order_corners(pts: Sequence[Point]) -> list[Point]:
    """Four points as top-left, top-right, bottom-right, bottom-left (image axes)."""
    if len(pts) != 4:
        raise CalibrationError("need exactly four marker points")
    arr = np.asarray(pts, dtype=float)
    s, d = arr.sum(axis=1), arr[:, 0] - arr[:, 1]
    tl, br = arr[int(np.argmin(s))], arr[int(np.argmax(s))]
    tr, bl = arr[int(np.argmax(d))], arr[int(np.argmin(d))]
    out = [tuple(map(float, p)) for p in (tl, tr, br, bl)]
    if len({tuple(p) for p in out}) != 4:
        raise CalibrationError("the four markers do not form a rectangle")
    return out


# ── zones ────────────────────────────────────────────────────────────────────
def box_from_corners(a: Point, b: Point) -> Box:
    xmin, xmax = sorted((float(a[0]), float(b[0])))
    ymin, ymax = sorted((float(a[1]), float(b[1])))
    if not all(map(math.isfinite, (xmin, ymin, xmax, ymax))) or xmin == xmax or ymin == ymax:
        raise CalibrationError("a zone needs two distinct corners")
    return (round(xmin, 4), round(ymin, 4), round(xmax, 4), round(ymax, 4))


def parse_box(text: str) -> Box:
    try:
        xmin, ymin, xmax, ymax = (float(v) for v in text.replace(" ", "").split(","))
    except ValueError:
        raise CalibrationError("a box is xmin,ymin,xmax,ymax") from None
    return box_from_corners((xmin, ymin), (xmax, ymax))


def write_zone(routes_path: Path, name: str, box: Box, note: str = "") -> None:
    """Set ``zones.<name>`` in routes.yaml in place (replacing the line, placeholder
    comment included, or adding one under ``zones:``)."""
    path = Path(routes_path)
    lines = path.read_text().splitlines(keepends=True)
    zones = _find_line(lines, "zones:")
    if zones is None or not lines[zones].startswith("zones:"):
        raise CalibrationError(f"no zones: section in {path}")
    end = _section_end(lines, zones)
    text = f"  {name}: {_flow([round(float(v), 4) for v in box])}  # measured {_stamp()} by armed calibrate zone{note}\n"
    i = _find_line(lines, f"{name}:", zones + 1, end)
    if i is None:
        lines.insert(zones + 1, text)
    else:
        _replace_entry(lines, i, text)
    path.write_text("".join(lines))


def zone_record_path(name: str) -> Path:
    return CALIBRATION_DIR / f"zone_{name}.json"


def save_zone_record(
    name: str, box: Box, frame_path: str | None, image_pts: Sequence[Point] | None
) -> Path:
    path = zone_record_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "when": _stamp(),
                "zone": name,
                "box": list(box),
                "image_points": list(image_pts or []),
                "frame": frame_path,
            },
            indent=2,
        )
    )
    return path


def zone_measured(name: str, box: Sequence[float]) -> bool:
    """A zone counts as measured when its record exists and matches the file."""
    path = zone_record_path(name)
    if not path.exists():
        return False
    try:
        saved = json.loads(path.read_text()).get("box")
    except (OSError, ValueError):
        return False
    return [round(float(v), 4) for v in saved or []] == [round(float(v), 4) for v in box]


# ── routes ───────────────────────────────────────────────────────────────────
def route_poses(lib: Any, route: str, slot: str | None) -> list[str]:
    """Pose names a carry visits, in order: the slot's ``above`` (over the pick), the
    route's via poses, then the tray. ``handover`` is home -> handover_above ->
    handover_release and takes no slot."""
    if route == HANDOVER:
        return ["home", "handover_above", "handover_release"]
    if route not in lib.routes:
        known = ", ".join(sorted(lib.routes)) or "none"
        raise CalibrationError(f"unknown route {route!r} (poses.yaml routes: {known})")
    if slot is None or slot not in lib.slots:
        known = ", ".join(sorted(lib.slots)) or "none"
        raise CalibrationError(f"route {route} needs a pick slot (one of {known})")
    return [f"{slot}.above", *(p.name for p in lib.routes[route]), *TRAY_POSES]


def pose_xy_path() -> Path:
    return CALIBRATION_DIR / "pose_xy.yaml"


def load_pose_xy(path: Path | None = None) -> dict[str, Point]:
    target = path or pose_xy_path()
    if not target.exists():
        return {}
    data = yaml.safe_load(target.read_text()) or {}
    poses = data.get("poses") if isinstance(data, Mapping) else None
    out: dict[str, Point] = {}
    for name, xy in (poses or {}).items():
        try:
            out[str(name)] = (float(xy[0]), float(xy[1]))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def save_pose_xy(samples: Mapping[str, Point], path: Path | None = None) -> Path:
    target = path or pose_xy_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    known = load_pose_xy(target)
    known.update({k: (round(float(v[0]), 4), round(float(v[1]), 4)) for k, v in samples.items()})
    target.write_text(
        "# Table xy of the gripper at taught poses, sampled through the homography by\n"
        f"# armed calibrate route ({_stamp()}). The virtual backend samples these.\n"
        + yaml.safe_dump({"poses": {k: list(v) for k, v in known.items()}}, sort_keys=True)
    )
    return target


def sample_route_virtual(
    lib: Any, names: Sequence[str], known: Mapping[str, Point]
) -> tuple[list[Point], list[str]]:
    """Table xy per pose without a camera: a slot pose is its slot's ``table_xy``; any
    other pose comes from ``known`` (the pose_xy record plus ``--xy``). Returns the
    polyline and the names it could not place."""
    poly: list[Point] = []
    missing: list[str] = []
    for name in names:
        if name in known:
            poly.append(known[name])
        elif "." in name and name.split(".", 1)[0] in lib.slots:
            poly.append(lib.slots[name.split(".", 1)[0]].table_xy)
        else:
            missing.append(name)
    return poly, missing


def dedupe(poly: Sequence[Point], eps: float = 1e-4) -> list[Point]:
    out: list[Point] = []
    for p in poly:
        q = (round(float(p[0]), 4), round(float(p[1]), 4))
        if not out or math.dist(out[-1], q) > eps:
            out.append(q)
    return out


def write_route(routes_path: Path, route: str, slot: str, poly: Sequence[Point]) -> None:
    """Set ``routes.<route>.<slot>: {measured: true, path: [...]}`` in routes.yaml in
    place, adding the route or slot line when it is new."""
    if len(poly) < 2:
        raise CalibrationError("a route polyline needs two or more points")
    path = Path(routes_path)
    lines = path.read_text().splitlines(keepends=True)
    routes = _find_line(lines, "routes:")
    if routes is None or not lines[routes].startswith("routes:"):
        raise CalibrationError(f"no routes: section in {path}")
    end = _section_end(lines, routes)
    body = _flow({"measured": True, "path": [list(p) for p in dedupe(poly)]})
    text = f"    {slot}: {body}  # armed calibrate route {_stamp()}\n"
    r = _find_line(lines, f"{route}:", routes + 1, end)
    if r is None:
        lines.insert(end, f"  {route}:\n{text}")
    else:
        r_end = next(
            (i for i in range(r + 1, end) if _indent(lines[i]) == "  " and lines[i].strip()), end
        )
        s = _find_line(lines, f"{slot}:", r + 1, r_end)
        if s is None:
            lines.insert(r + 1, text)
        else:
            _replace_entry(lines, s, text)
    path.write_text("".join(lines))


def write_handover(routes_path: Path, poly: Sequence[Point], spot: Point | None = None) -> None:
    """Set ``handover.polyline``, ``handover.measured: true`` and (given) ``handover.spot``."""
    if len(poly) < 2:
        raise CalibrationError("the handover polyline needs two or more points")
    path = Path(routes_path)
    lines = path.read_text().splitlines(keepends=True)
    h = _find_line(lines, "handover:")
    if h is None or not lines[h].startswith("handover:"):
        raise CalibrationError(f"no handover: section in {path}")
    end = _section_end(lines, h)
    stamp = f"  # armed calibrate route handover {_stamp()}\n"
    wanted = {
        "polyline:": f"  polyline: {_flow([list(p) for p in dedupe(poly)])}" + stamp,
        "measured:": "  measured: true" + stamp,
    }
    if spot is not None:
        wanted["spot:"] = (
            f"  spot: {_flow([round(float(spot[0]), 4), round(float(spot[1]), 4)])}" + stamp
        )
    for key, text in wanted.items():
        i = _find_line(lines, key, h + 1, end)
        if i is None:
            lines.insert(h + 1, text)
        else:
            _replace_entry(lines, i, text)
        end = _section_end(lines, h)
    path.write_text("".join(lines))


def crossings(poly: Sequence[Point], zones: Mapping[str, Sequence[float]]) -> dict[str, bool]:
    from armed.rules import _zone_ok, crosses

    return {
        name: crosses(list(poly), tuple(zone)) for name, zone in zones.items() if _zone_ok(zone)
    }  # type: ignore[arg-type]


def film_route_verdicts(
    routes_cfg: Any, slot: str, zone: str
) -> list[tuple[str, str, bool | None, bool]]:
    """(route, state, crosses the zone or None if unmeasured, wanted) for the film's
    pair: ``direct`` must cross the zone and ``via_high`` must not."""
    from armed.rules import crosses

    out = []
    box = routes_cfg.zones.get(zone)
    for name, wanted in (("direct", True), ("via_high", False)):
        poly = (routes_cfg.routes.get(name) or {}).get(slot)
        if poly is None or box is None:
            out.append((name, "missing", None, wanted))
            continue
        measured = (name, slot) not in routes_cfg.unmeasured
        out.append((name, "measured" if measured else "placeholder", crosses(poly, box), wanted))
    return out


# ── hardware route sampling ──────────────────────────────────────────────────
def jog_route(
    arm: Any,
    names: Sequence[str],
    sample: Callable[[str], Point | None],
    speed: float = 0.3,
    timeout_s: float = 60.0,
) -> dict[str, Point]:
    """Move the (connected, armed) arm through ``names`` one taught pose at a time with
    the normal executor (its limits, tracking and stall checks apply), calling
    ``sample(name)`` at each to get the gripper's table xy. Stops on the first pose the
    arm refuses or does not reach. The gripper keeps its opening throughout."""
    from armed.robot.arm import ArmState
    from armed.robot.backend import GRIPPER
    from armed.robot.skills import Plan, Waypoint

    samples: dict[str, Point] = {}
    for name in names:
        pose = arm.poses.pose(name)
        joints = {j: v for j, v in pose.joints.items() if j != GRIPPER}
        plan = Plan("calibrate.route", (Waypoint(name, joints, "approach"),), (pose,))
        ok, why = arm.execute(plan, speed=speed)
        if not ok:
            raise CalibrationError(f"the arm refused {name}: {why}")
        deadline = time.monotonic() + timeout_s
        while arm.state is ArmState.MOVING:
            if time.monotonic() > deadline:
                arm.stop("calibrate timeout")
                raise CalibrationError(f"{name} not reached within {timeout_s:.0f} s")
            time.sleep(0.05)
        if arm.state is not ArmState.READY:
            raise CalibrationError(f"arm is {arm.state} after {name}: {arm.reason}")
        xy = sample(name)
        if xy is None:
            raise CalibrationError(f"no sample at {name}")
        samples[name] = xy
    return samples


# ── jaw ──────────────────────────────────────────────────────────────────────
def parse_jaw_points(text: str) -> list[tuple[float, float]]:
    """``"30:24.5,60:51,98:79"`` -> [(30.0, 24.5), ...]. ValueError if it is not three
    increasing (percent, mm) pairs, or is the placeholder that ships in the config."""
    from armed.grasp import PLACEHOLDER_POINTS, GraspConfigError, JawCalibration

    try:
        points = [(float(p), float(m)) for p, m in (pair.split(":") for pair in text.split(","))]
        JawCalibration(tuple(points), False)
    except (ValueError, GraspConfigError) as exc:
        raise ValueError(f"need three percent:mm pairs, increasing in both ({exc})") from None
    if tuple(points) == PLACEHOLDER_POINTS:
        raise ValueError("those are the placeholder numbers: measure the jaws")
    return points


def write_jaw_points(path: Path, points: Sequence[tuple[float, float]]) -> None:
    """Replace ``grasp.jaw_calibration.points`` in the config file, in place, leaving the
    rest of the file (comments included) as it is. ``calibrated`` is never touched: it is
    switched on by hand after the safety review."""
    lines = Path(path).read_text().splitlines(keepends=True)
    start = _find_line(lines, "jaw_calibration:")
    if start is None:
        raise ValueError("no grasp.jaw_calibration section in the config")
    for i in range(start + 1, len(lines)):
        if lines[i].strip().startswith("points:"):
            body = ", ".join(f"[{p:g}, {m:g}]" for p, m in points)
            _replace_entry(
                lines, i, f"{_indent(lines[i])}points: [{body}]  # measured {_stamp()}\n"
            )
            Path(path).write_text("".join(lines))
            return
    raise ValueError("no points: line under grasp.jaw_calibration")


def prompt_jaw_points(ask: Callable[[str], str] = input) -> list[tuple[float, float]]:
    """Three ruler readings typed at the fixed targets (no arm needed: open the jaws to
    each percent with the operator UI or `armed arm read`, then measure)."""
    points = []
    for target in JAW_TARGETS:
        mm = float(ask(f"Jaws at {target:g}% open. Inner distance between the pads, mm: "))
        points.append((target, mm))
    return parse_jaw_points(",".join(f"{p:g}:{m:g}" for p, m in points))


def jaw_check(
    points: Sequence[tuple[float, float]], object_mm: float, margin_mm: float
) -> tuple[float, float]:
    """(opening mm the pick would use, gripper percent) for an object ``object_mm`` wide."""
    from armed.grasp import JawCalibration

    cal = JawCalibration(tuple((float(p), float(m)) for p, m in points), False)
    opening = float(object_mm) + float(margin_mm)
    return round(opening, 1), round(cal.mm_to_pct(opening), 1)


# ── slot arc ─────────────────────────────────────────────────────────────────
def circumcircle(pts: Sequence[Point]) -> tuple[Point, float] | None:
    """Centre and radius of the circle through three points; None if collinear."""
    if len(pts) != 3:
        return None
    (ax, ay), (bx, by), (cx, cy) = pts
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    ux = (
        (ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)
    ) / d
    uy = (
        (ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)
    ) / d
    return (round(ux, 4), round(uy, 4)), round(math.dist((ux, uy), (ax, ay)), 4)


ARC_JOINTS = ("shoulder_lift", "elbow_flex", "wrist_flex")


def slot_arc(lib: Any, base_xy: Point | None) -> dict[str, Any]:
    """How far the slots are from one arc: the spread of the grasp poses' lift, elbow and
    wrist angles (one radius means only the pan differs), the circle through the three
    slot ``table_xy`` and, with the base position known, the spread of their radii."""
    slots = [lib.slots[k] for k in sorted(lib.slots)]
    out: dict[str, Any] = {"slots": [s.slot_id for s in slots]}
    if slots:
        out["joint_spread_deg"] = round(
            max(
                max(s.grasp.joints[j] for s in slots) - min(s.grasp.joints[j] for s in slots)
                for j in ARC_JOINTS
            ),
            1,
        )
    xys = [s.table_xy for s in slots]
    if len(xys) == 3:
        out["circle"] = circumcircle(xys)
    if base_xy is not None and xys:
        radii = [math.dist(base_xy, xy) for xy in xys]
        out["radii"] = [round(r, 4) for r in radii]
        out["radius_spread"] = round(max(radii) - min(radii), 4)
    return out


# ── check ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CheckRow:
    item: str
    state: str
    detail: str
    ok: bool
    film: bool = False  # the film needs this one


@dataclass(frozen=True)
class CheckReport:
    rows: list[CheckRow]

    @property
    def film_missing(self) -> list[CheckRow]:
        return [r for r in self.rows if r.film and not r.ok]

    @property
    def film_ready(self) -> bool:
        return not self.film_missing

    def table(self) -> str:
        head = ("item", "state", "detail", "film")
        body = [
            (r.item, r.state, r.detail, ("ok" if r.ok else "MISSING") if r.film else "")
            for r in self.rows
        ]
        widths = [max(len(row[i]) for row in [head, *body]) for i in range(4)]
        line = "  ".join(h.ljust(w) for h, w in zip(head, widths, strict=True))
        out = [line, "  ".join("-" * w for w in widths)]
        out += [
            "  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() for row in body
        ]
        return "\n".join(out)


def _fmt_xy(p: Sequence[float]) -> str:
    return f"({p[0]:.3f}, {p[1]:.3f})"


def check(
    cfg: Mapping[str, Any], *, routes_path: Path | None = None, poses_path: Path | None = None
) -> CheckReport:
    """Every calibration's state from the loaded config, routes.yaml and poses.yaml.
    Never touches a camera or the arm."""
    from armed.grasp import PLACEHOLDER_POINTS, GraspConfigError, load_grasp
    from armed.robot.skills import load_poses
    from armed.routing import RoutesUnavailable, load_routes

    cal = settings(cfg)
    rows: list[CheckRow] = []
    film_routes = {(str(r), str(s)) for r, s in cal["film_routes"]}
    film_zone = str(cal["film_zone"])

    # homography
    try:
        H = read_homography(cfg)
    except CalibrationError as exc:
        rows.append(CheckRow("homography", "invalid", str(exc), False, True))
    else:
        record = load_table_record()
        if H is None:
            rows.append(
                CheckRow(
                    "homography", "placeholder", "vision.table_homography is null", False, True
                )
            )
        else:
            detail = "set"
            if record and record.get("homography"):
                same = np.allclose(np.asarray(record["homography"], dtype=float), H, atol=1e-6)
                err = max(record.get("reprojection_px") or [0.0])
                checks = record.get("check_mm") or {}
                detail = (
                    f"reprojection {err:.2f} px over {len(record.get('image_points') or [])} points"
                )
                if checks:
                    detail += f", slot marks within {max(checks.values()):.0f} mm"
                if not same:
                    detail += " (record differs from the config)"
            rows.append(CheckRow("homography", "measured", detail, True, True))

    # routes.yaml: zones, routes, handover
    rpath = routes_path or ROOT / str(
        (cfg.get("rules") or {}).get("routes_file", "configs/routes.yaml")
    )
    try:
        routes = load_routes(rpath)
    except RoutesUnavailable as exc:
        rows.append(CheckRow("routes.yaml", "invalid", str(exc), False, True))
        routes = None
    if routes is not None:
        for name, box in routes.zones.items():
            measured = zone_measured(name, box)
            rows.append(
                CheckRow(
                    f"zone {name}",
                    "measured" if measured else "placeholder",
                    f"[{', '.join(f'{v:.3f}' for v in box)}]",
                    measured,
                    name == film_zone,
                )
            )
        if film_zone not in routes.zones:
            rows.append(CheckRow(f"zone {film_zone}", "missing", "not in routes.yaml", False, True))
        for name, per in routes.routes.items():
            for slot, poly in per.items():
                measured = (name, slot) not in routes.unmeasured
                crossed = [z for z, hit in crossings(poly, routes.zones).items() if hit]
                detail = f"{len(poly)} points, {_fmt_xy(poly[0])} -> {_fmt_xy(poly[-1])}"
                detail += f", crosses {', '.join(crossed)}" if crossed else ", crosses nothing"
                rows.append(
                    CheckRow(
                        f"route {name} {slot}",
                        "measured" if measured else "placeholder",
                        detail,
                        measured,
                        (name, slot) in film_routes,
                    )
                )
        for name, slot in sorted(film_routes):
            if slot not in (routes.routes.get(name) or {}):
                rows.append(
                    CheckRow(
                        f"route {name} {slot}", "missing", "no polyline in routes.yaml", False, True
                    )
                )
        h = routes.handover
        if h is None:
            rows.append(
                CheckRow(
                    "handover", "missing", "no handover section: place_near_user holds", False, True
                )
            )
        else:
            detail = f"{len(h.polyline)} points to spot {_fmt_xy(h.spot)}"
            detail += (
                ", camera sees past the arm"
                if h.camera_sees_past_arm
                else ", camera_sees_past_arm false (set after the teach-time check)"
            )
            rows.append(
                CheckRow(
                    "handover",
                    "measured" if h.measured else "placeholder",
                    detail,
                    h.measured,
                    True,
                )
            )

    # jaw
    try:
        grasp_cfg, jaw = load_grasp(cfg)
    except GraspConfigError as exc:
        rows.append(CheckRow("jaw", "invalid", str(exc), False))
    else:
        placeholder = tuple(map(tuple, jaw.points)) == PLACEHOLDER_POINTS
        opening, pct = jaw_check(jaw.points, float(cal["check_object_mm"]), grasp_cfg.margin_mm)
        pts = ", ".join(f"{p:g}%={m:g}mm" for p, m in jaw.points)
        detail = f"{pts}; {cal['check_object_mm']:g} mm object -> {opening:g} mm -> {pct:g}%"
        detail += "; calibrated: true" if jaw.calibrated else "; calibrated: false (safety review)"
        rows.append(
            CheckRow("jaw", "placeholder" if placeholder else "measured", detail, not placeholder)
        )

    # slot arc
    ppath = poses_path or ROOT / str((cfg.get("arm") or {}).get("poses_file", "configs/poses.yaml"))
    try:
        lib = load_poses(ppath)
    except Exception as exc:  # noqa: BLE001  # a bad poses file is reported, not raised
        rows.append(CheckRow("slot arc", "invalid", f"{ppath.name}: {exc}", False))
    else:
        base = (cfg.get("arm") or {}).get("base_table_xy")
        arc = slot_arc(lib, (float(base[0]), float(base[1])) if base else None)
        ok = arc.get("joint_spread_deg", 0.0) <= float(cal["slot_arc_joint_tolerance_deg"])
        detail = f"{len(arc['slots'])} slots, lift/elbow/wrist spread {arc.get('joint_spread_deg', 0.0):g} deg"
        if arc.get("circle"):
            (cx, cy), r = arc["circle"]
            detail += f", circle centre ({cx:.3f}, {cy:.3f}) r {r:.3f}"
        if "radius_spread" in arc:
            ok = ok and arc["radius_spread"] <= float(cal["slot_arc_radius_tolerance"])
            detail += f", radius spread {arc['radius_spread']:.3f} from the base"
        else:
            detail += " (arm.base_table_xy unset: radii not checked)"
        rows.append(CheckRow("slot arc", "one arc" if ok else "off arc", detail, ok))

    key = bool(os.environ.get("MODEL_API_KEY", "").strip())
    rows.append(
        CheckRow(
            "MODEL_API_KEY",
            "set" if key else "unset",
            "from the environment or .env; never printed",
            key,
            True,
        )
    )
    return CheckReport(rows)


def shoot_problems(cfg: Mapping[str, Any]) -> tuple[list[str], CheckReport]:
    """Why ``armed run --profile shoot`` must not start: no model key, or a route the
    film needs still a placeholder. Everything else in the report is a warning."""
    report = check(cfg)
    problems = []
    if not os.environ.get("MODEL_API_KEY", "").strip():
        problems.append(
            "MODEL_API_KEY is not set (export it or put it in .env; it is never logged)"
        )
    for row in report.film_missing:
        if row.item.startswith("route "):
            problems.append(
                f"{row.item} is a {row.state} polyline: run `armed calibrate route {row.item[6:]}`"
            )
    return problems, report


def write_slot_xy(poses_path: Path, xys: Mapping[str, Point]) -> None:
    """Set ``slots.<id>.table_xy`` in poses.yaml for the given slots (the same
    load/dump round trip as ``save_pose``, so the file keeps its layout)."""
    path = Path(poses_path)
    data = yaml.safe_load(path.read_text()) or {}
    slots = data.get("slots") or {}
    for slot, xy in xys.items():
        if slot not in slots:
            raise CalibrationError(f"no slot {slot} in {path}")
        slots[slot]["table_xy"] = [round(float(xy[0]), 4), round(float(xy[1]), 4)]
    path.write_text(yaml.safe_dump(data, sort_keys=False))


__all__ = [
    "DEFAULTS",
    "JAW_TARGETS",
    "CalibrationError",
    "CheckReport",
    "CheckRow",
    "TableFit",
    "apply_homography",
    "check",
    "check_errors",
    "circumcircle",
    "crossings",
    "fit_homography",
    "fit_table",
    "jaw_check",
    "jog_route",
    "parse_jaw_points",
    "prompt_jaw_points",
    "reprojection_errors",
    "route_poses",
    "sample_route_virtual",
    "settings",
    "shoot_problems",
    "slot_arc",
    "table_corners",
    "write_handover",
    "write_homography",
    "write_jaw_points",
    "write_route",
    "write_slot_xy",
    "write_zone",
]
