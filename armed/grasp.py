"""Meta-measured grasp parameters: jaw opening, wrist roll, grip profile (docs/hardware/calibrate.md).

Pure functions, no I/O. A model measures or classifies; this module clamps and chooses.
No value a model returns becomes a joint value except through the clamps below:

- opening: ``clamp(mm_to_pct(width + margin_mm), max(open_min_pct, taught), open_max_pct)``
  where ``taught`` is the slot's taught opening, so a measured width can only widen the
  descent, never narrow it below what was taught; the taught opening itself when there is
  no width or the jaws are not calibrated;
- wrist roll: the taught grasp roll +- ``roll_envelope_deg`` (floored at 0, hard ceiling
  ``ROLL_ENVELOPE_MAX_DEG``, both applied at parse time and again at use), or None (the
  taught roll) when the mask gives no axis, the roll would not change, or the opening fell
  back: the fallback is always the taught plan;
- the width is measured across the jaws *after* that clamped roll (``width_across_jaws``),
  so the axis measured is the one the arm reaches, not the one the mask asked for;
- close cap: ``min(catalog max_grip, profile max_grip)``. A profile can only lower the
  cap, an unrecognised profile is ``unknown`` (the softest one), and the close itself never
  depends on the measurement. The close is bounded by the gripper servo's torque-limit
  register (``Torque_Limit``, written from the grip before the close); there is no
  load-based stop. Gripper load is read back only to be logged and for the empty proof.

Units: masks and boxes are in image pixels; ``homography`` maps them to table coordinates
(``vision.table_homography``) and ``table_unit_mm`` says how many mm one table unit is
(1000: metres, the unit of the slot ``table_xy`` values). Angles are in degrees in the
table frame. ``jaw_axis_deg`` is the direction of the jaw faces seen from above at a
slot's taught grasp. The jaws close across it, so the width that matters is the
object's extent perpendicular to it.

OFF BY DEFAULT. With ``grasp.sam_width`` anything but true, ``grasp_params`` returns the
taught opening and no roll whatever it is given; the jaw calibration also ships
``calibrated: false``, which on its own keeps the taught opening, and ``calibrated: true``
with the placeholder points is refused.

Where the runtime calls SAM (wired in armed/app.py as ``Armed._continue_grasp``):

1. In ``Armed._execute_plan`` (armed/app.py), in the ``Verb.PICK_PLACE`` branch, when
   ``grasp.sam_width`` is true, run a stage-1 plan to ``slot.above`` only
   (the approach, at the taught opening), the same two-stage way as ``place_near_user``
   (``_handover_job`` -> ``_continue_handover``): remember (contract, epoch, arm_count).
2. On that plan's ``arm.completed``, off the control thread and never under the arm lock
   or in the stop path, take an overhead frame newer than the arrival (or the wrist
   frame, whose scale at ``above`` is fixed) and call
   ``MuseClient.segment(sam_model, frame, sam_label(object name))`` (armed/incident.py)
   once, with its own timeout (1-2 s). It returns a box. A polygon needs the one-bit
   mask decoded (cv2.findContours on the raster), which ``segment`` does not do yet;
   without a polygon, ``roll_from_mask`` gets nothing and the roll stays taught.
3. With ``taught_roll`` = the slot's ``grasp`` pose wrist_roll:
   ``roll = roll_for_axis(roll_from_mask(to_table(polygon, H), cfg.round_aspect),
   cfg.jaw_axis_deg.get(slot_id), taught_roll, cfg.roll_sign)``, then
   ``width = width_across_jaws(box_or_polygon, H, slot_id, taught_roll, roll, cfg)``
   (never ``width_from_mask`` with an axis of your own), then
   ``p = grasp_params(width, roll, taught_roll, profile, cfg, cal,
   catalog_max_grip=<catalog max_grip>, taught_open_pct=<the slot's taught opening>)``,
   where the taught opening is the one the taught plan already uses for this slot
   (``Armed._slot_open_pct(slot)``), not ``arm.settings.gripper_open``. Any failure (no
   key, timeout, no match, no homography) passes None and gets the fallback.
4. Rebuild the plan with ``plan_pick_place(..., grasp_open_pct=p.open_pct,
   grasp_roll_deg=p.roll_deg, roll_envelope_deg=cfg.roll_envelope_deg)``: on fallback
   ``p.roll_deg`` is None and ``p.open_pct`` is the taught opening, i.e. the taught plan.
   Use ``p.close_cap`` where the catalog ``max_grip`` is the ``grip_for`` ceiling today
   (learned-rule caps still multiply on top). Execute only if the request epoch and
   ``arm.arm_count`` are unchanged and the gate's decision still stands; a stop, cancel
   or re-arm in between drops it, as for the handover. Show ``p.chip`` on screen.

The muse classification of the profile (``rigid``/``fragile``/``liquid``/``unknown``) is
app.py's too. Anything it cannot classify is passed as ``unknown``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

ROLL_ENVELOPE_MAX_DEG = 45.0  # hard ceiling on the config envelope
PROFILES = ("rigid", "fragile", "liquid", "unknown")
UNKNOWN = "unknown"
PLACEHOLDER_POINTS = ((30.0, 25.0), (60.0, 50.0), (100.0, 80.0))
DEFAULT_PROFILES = {"rigid": 0.9, "fragile": 0.35, "liquid": 0.5, "unknown": 0.3}
_RESAMPLE = 256  # boundary points for the PCA, evenly spaced by arc length


class GraspConfigError(ValueError):
    pass


def _finite(value: Any, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise GraspConfigError(f"grasp.{name} must be a number") from None
    if not math.isfinite(out):
        raise GraspConfigError(f"grasp.{name} must be finite")
    return out


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _measured(value: Any) -> float | None:
    """A model-derived number, or None: bools, non-numbers and non-finite values are
    not measurements (``True`` is not a width of 1 mm)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _section(raw: Any, name: str) -> Mapping[str, Any]:
    """A config mapping (None or absent = empty); anything else is a GraspConfigError."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise GraspConfigError(f"{name} must be a mapping")
    return raw


# ── jaw calibration ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class JawCalibration:
    """Three measured (gripper percent, jaw opening mm) points, both strictly increasing.
    Only ``calibrated`` = the literal true lets a measured width set the opening."""

    points: tuple[tuple[float, float], ...] = PLACEHOLDER_POINTS
    calibrated: bool = False

    def __post_init__(self) -> None:
        if self.calibrated and tuple(map(tuple, self.points)) == PLACEHOLDER_POINTS:
            raise GraspConfigError(
                "grasp.jaw_calibration: calibrated is true but the points are the placeholder"
            )
        if len(self.points) != 3:
            raise GraspConfigError("grasp.jaw_calibration needs exactly three points")
        for (p0, m0), (p1, m1) in zip(self.points, self.points[1:], strict=False):
            if not (p1 > p0 and m1 > m0):
                raise GraspConfigError("grasp.jaw_calibration must increase in percent and mm")
        for pct, mm in self.points:
            if not (0.0 <= pct <= 100.0 and mm >= 0.0):
                raise GraspConfigError("grasp.jaw_calibration: percent 0..100, mm >= 0")

    @classmethod
    def from_config(cls, raw: Any) -> JawCalibration:
        raw = _section(raw, "grasp.jaw_calibration")
        pts = raw.get("points", PLACEHOLDER_POINTS)
        if not isinstance(pts, (list, tuple)):
            raise GraspConfigError("grasp.jaw_calibration.points must be a list")
        try:
            points = tuple(
                (_finite(p, "jaw_calibration"), _finite(m, "jaw_calibration")) for p, m in pts
            )
        except (TypeError, ValueError) as exc:
            raise GraspConfigError(f"grasp.jaw_calibration.points: {exc}") from None
        return cls(points, raw.get("calibrated") is True)

    def mm_to_pct(self, mm: float) -> float:
        """Gripper percent for an opening of ``mm``: piecewise linear through the points,
        the end segments extended, clamped to [0, 100]."""
        pct = _interp(float(mm), [m for _, m in self.points], [p for p, _ in self.points])
        return _clamp(pct, 0.0, 100.0)

    def pct_to_mm(self, pct: float) -> float:
        """Jaw opening in mm at ``pct`` (clamped to [0, 100] first), never below 0."""
        pct = _clamp(float(pct), 0.0, 100.0)
        return max(0.0, _interp(pct, [p for p, _ in self.points], [m for _, m in self.points]))


def _interp(x: float, xs: list[float], ys: list[float]) -> float:
    """Piecewise linear; outside the points, the nearest end segment extended."""
    if not math.isfinite(x):
        raise ValueError("interpolation input must be finite")
    i = 0  # below the second point (including below the first): the first segment
    while i < len(xs) - 2 and x > xs[i + 1]:
        i += 1  # stops on the last segment above the last point
    x0, x1, y0, y1 = xs[i], xs[i + 1], ys[i], ys[i + 1]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


# ── config ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class GraspConfig:
    margin_mm: float = 12.0
    open_min_pct: float = 30.0
    open_max_pct: float = 100.0
    roll_envelope_deg: float = 25.0
    round_aspect: float = 1.3
    sam_width: bool = False
    table_unit_mm: float = 1000.0
    roll_sign: float = 1.0
    roll_sign_verified: bool = False
    jaw_axis_deg: dict[str, float] = field(default_factory=dict)
    profiles: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_PROFILES))

    @classmethod
    def from_config(cls, raw: Mapping[str, Any] | None) -> GraspConfig:
        """Parse ``grasp:`` from configs/armed.yaml. Bad values raise GraspConfigError;
        the roll envelope is capped at ROLL_ENVELOPE_MAX_DEG, and ``unknown`` is forced
        to the softest cap of all profiles. A section of the wrong type (not a mapping)
        is a GraspConfigError too, never an AttributeError."""
        raw = _section(raw, "grasp")
        d = cls()
        margin = _finite(raw.get("margin_mm", d.margin_mm), "margin_mm")
        lo = _finite(raw.get("open_min_pct", d.open_min_pct), "open_min_pct")
        hi = _finite(raw.get("open_max_pct", d.open_max_pct), "open_max_pct")
        env = _finite(raw.get("roll_envelope_deg", d.roll_envelope_deg), "roll_envelope_deg")
        aspect = _finite(raw.get("round_aspect", d.round_aspect), "round_aspect")
        unit = _finite(raw.get("table_unit_mm", d.table_unit_mm), "table_unit_mm")
        sign = _finite(raw.get("roll_sign", d.roll_sign), "roll_sign")
        if margin < 0:
            raise GraspConfigError("grasp.margin_mm must be >= 0")
        if not 0.0 <= lo <= hi <= 100.0:
            raise GraspConfigError("grasp: need 0 <= open_min_pct <= open_max_pct <= 100")
        if env < 0:
            raise GraspConfigError("grasp.roll_envelope_deg must be >= 0")
        if aspect < 1.0:
            raise GraspConfigError("grasp.round_aspect must be >= 1")
        if unit <= 0:
            raise GraspConfigError("grasp.table_unit_mm must be > 0")
        if sign not in (1.0, -1.0):
            raise GraspConfigError("grasp.roll_sign must be 1 or -1")
        axes = {
            str(k): _finite(v, f"jaw_axis_deg.{k}")
            for k, v in _section(raw.get("jaw_axis_deg"), "grasp.jaw_axis_deg").items()
        }
        profiles: dict[str, float] = {}
        listed = _section(raw.get("profiles"), "grasp.profiles") or DEFAULT_PROFILES
        for name, prof in listed.items():
            cap = prof.get("max_grip") if isinstance(prof, Mapping) else prof
            cap = _finite(cap, f"profiles.{name}.max_grip")
            if not 0.0 <= cap <= 1.0:
                raise GraspConfigError(f"grasp.profiles.{name}.max_grip must be within 0..1")
            profiles[str(name)] = cap
        # unknown is the softest, whatever the file says (or if it says nothing).
        profiles[UNKNOWN] = min(profiles.values()) if profiles else 0.0
        return cls(
            margin_mm=margin,
            open_min_pct=lo,
            open_max_pct=hi,
            roll_envelope_deg=min(env, ROLL_ENVELOPE_MAX_DEG),
            round_aspect=aspect,
            sam_width=raw.get("sam_width") is True,
            table_unit_mm=unit,
            roll_sign=sign,
            roll_sign_verified=raw.get("roll_sign_verified") is True,
            jaw_axis_deg=axes,
            profiles=profiles,
        )

    def profile_cap(self, profile: str | None) -> float:
        """Cap of a named profile; anything unrecognised is ``unknown``."""
        if isinstance(profile, str) and profile in self.profiles:
            return self.profiles[profile]
        return self.profiles[UNKNOWN]


# ── measurement ──────────────────────────────────────────────────────────────
def _points(mask_or_box: Any) -> np.ndarray | None:
    """(N, 2) image points: the four corners of a box (x1, y1, x2, y2), or a polygon of
    three or more points. None if degenerate, non-finite or malformed."""
    try:
        arr = np.asarray(mask_or_box, dtype=float)
    except (TypeError, ValueError):
        return None
    if not np.all(np.isfinite(arr)):
        return None
    if arr.shape == (4,):
        x1, y1, x2, y2 = arr
        if x2 - x1 < 1.0 or y2 - y1 < 1.0:  # under a pixel, or inverted
            return None
        return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
    if arr.ndim == 2 and arr.shape[1] == 2 and arr.shape[0] >= 3:
        if _area(arr) < 1.0:
            return None
        return arr
    return None


def _area(poly: np.ndarray) -> float:
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def to_table(points: Any, homography: Any) -> np.ndarray | None:
    """Image points through the 3x3 homography; None if it is missing or malformed,
    or any point maps to (or across) the line at infinity."""
    if homography is None:
        return None
    try:
        h = np.asarray(homography, dtype=float)
        pts = np.asarray(points, dtype=float)
    except (TypeError, ValueError):
        return None
    if h.shape != (3, 3) or not np.all(np.isfinite(h)) or pts.ndim != 2 or pts.shape[1] != 2:
        return None
    v = np.column_stack([pts, np.ones(len(pts))]) @ h.T
    w = v[:, 2]
    scale = max(1.0, float(np.max(np.abs(v[:, :2]))))
    if np.any(np.abs(w) < 1e-9 * scale) or not (np.all(w > 0) or np.all(w < 0)):
        return None
    out = v[:, :2] / w[:, None]
    return out if np.all(np.isfinite(out)) else None


def width_from_mask(
    mask_or_box: Any,
    homography: Any,
    jaw_axis_deg: float | None,
    unit_mm: float = 1000.0,
) -> float | None:
    """The object's extent perpendicular to the jaw faces (the closing direction), in
    table mm, from a SAM box (x1, y1, x2, y2) or mask polygon in image pixels. With
    ``jaw_axis_deg`` None (the slot's jaw axis was never measured) it is the widest
    extent in any direction, so the opening errs wide. A box is axis-aligned in the
    image, so it over-estimates a rotated object, which is the safe side for an opening.
    None for a degenerate box or polygon, or a missing or malformed homography."""
    pts = _points(mask_or_box)
    if pts is None:
        return None
    table = to_table(pts, homography)
    if table is None or not isinstance(unit_mm, (int, float)):
        return None
    if not (math.isfinite(unit_mm) and unit_mm > 0):
        return None
    if jaw_axis_deg is None:
        angles = np.radians(np.arange(0.0, 180.0, 1.0))
    else:
        if not math.isfinite(jaw_axis_deg):
            return None
        angles = np.radians(np.array([jaw_axis_deg + 90.0]))
    dirs = np.column_stack([np.cos(angles), np.sin(angles)])
    proj = table @ dirs.T
    width = float(np.max(proj.max(axis=0) - proj.min(axis=0))) * float(unit_mm)
    return width if math.isfinite(width) and width > 0 else None


def _resample(poly: np.ndarray, n: int = _RESAMPLE) -> np.ndarray:
    """Points evenly spaced along the closed boundary, so vertex density does not bias
    the PCA (a contour has many points on curves and few on straight edges)."""
    closed = np.vstack([poly, poly[:1]])
    seg = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    total = float(seg.sum())
    if total <= 0:
        return poly
    s = np.concatenate([[0.0], np.cumsum(seg)])
    t = np.linspace(0.0, total, n, endpoint=False)
    return np.column_stack([np.interp(t, s, closed[:, 0]), np.interp(t, s, closed[:, 1])])


def roll_from_mask(mask_polygon: Any, round_aspect: float = 1.3) -> float | None:
    """Principal axis of the polygon (PCA on its boundary), in degrees in (-90, 90] in the
    polygon's own frame (pass table-frame points from ``to_table``). None for a near-round
    object (extent along the axis / extent across it < ``round_aspect``) or a degenerate
    polygon. A box gives no orientation: pass a mask polygon, not a box."""
    try:
        arr = np.asarray(mask_polygon, dtype=float)
    except (TypeError, ValueError):
        return None
    if arr.ndim != 2 or arr.shape[1] != 2 or arr.shape[0] < 3 or not np.all(np.isfinite(arr)):
        return None
    if _area(arr) <= 0.0:
        return None
    pts = _resample(arr)
    centred = pts - pts.mean(axis=0)
    vals, vecs = np.linalg.eigh(np.cov(centred.T))
    major, minor = vecs[:, 1], vecs[:, 0]
    along = centred @ major
    across = centred @ minor
    extent_across = float(across.max() - across.min())
    if extent_across <= 0:
        return None
    if float(along.max() - along.min()) / extent_across < round_aspect:
        return None
    angle = math.degrees(math.atan2(major[1], major[0]))
    return _wrap90(angle)


def _wrap90(angle: float) -> float:
    """An undirected axis angle into (-90, 90]."""
    a = (angle + 90.0) % 180.0 - 90.0
    return 90.0 if a == -90.0 else a


def roll_for_axis(
    object_axis_deg: float | None,
    jaw_axis_deg: float | None,
    taught_roll: float,
    roll_sign: float = 1.0,
) -> float | None:
    """The wrist roll that turns the jaw faces parallel to the object's long axis (so the
    jaws close across its narrow side), by the shortest turn. ``roll_sign`` is +1 if
    wrist_roll increasing turns the jaws counter-clockwise in the table frame (UNVERIFIED
    on the arm). None when either axis is unknown: the caller keeps the taught roll.
    Unclamped: ``grasp_params`` applies the envelope."""
    if object_axis_deg is None or jaw_axis_deg is None:
        return None
    if not all(math.isfinite(v) for v in (object_axis_deg, jaw_axis_deg, taught_roll)):
        return None
    return taught_roll + roll_sign * _wrap90(object_axis_deg - jaw_axis_deg)


# ── parameters ───────────────────────────────────────────────────────────────
def _envelope(cfg: GraspConfig) -> float:
    """The roll envelope as used: in [0, ROLL_ENVELOPE_MAX_DEG] whatever the config says
    (a hand-built config skips from_config); non-finite is 0."""
    env = cfg.roll_envelope_deg
    if isinstance(env, bool) or not isinstance(env, (int, float)) or not math.isfinite(env):
        return 0.0
    return _clamp(float(env), 0.0, ROLL_ENVELOPE_MAX_DEG)


def _roll_delta(roll_deg: Any, taught_roll: float, cfg: GraspConfig) -> float:
    """The change from ``taught_roll`` the arm will actually make for a target
    ``roll_deg``: 0 with sam_width off or no usable target, otherwise clamped to the
    envelope. The one clamp shared by ``grasp_params`` and ``width_across_jaws``."""
    target = _measured(roll_deg)
    if cfg.sam_width is not True or target is None:
        return 0.0
    env = _envelope(cfg)
    return _clamp(target - taught_roll, -env, env)


def width_across_jaws(
    mask_or_box: Any,
    homography: Any,
    slot_id: str,
    taught_roll: float,
    roll_deg: float | None,
    cfg: GraspConfig,
) -> float | None:
    """The object's width across the jaws as they will be after the clamped roll, in mm.

    The jaw axis is the slot's ``jaw_axis_deg`` turned by ``roll_sign`` times the roll
    change ``grasp_params`` will allow (the same clamp, envelope and 45 deg cap), not the
    roll asked for: a long object at 80 deg with a 25 deg envelope is measured across the
    jaws at 25 deg, i.e. nearly its length. With no measured jaw axis for the slot, or
    ``roll_sign_verified`` not true, it is the widest extent in any direction. None when
    the width cannot be measured or the taught roll is unusable (the caller falls back)."""
    taught = _measured(taught_roll)
    if taught is None:
        return None
    axis = cfg.jaw_axis_deg.get(slot_id) if cfg.roll_sign_verified is True else None
    if axis is not None:
        axis = float(axis) + cfg.roll_sign * _roll_delta(roll_deg, taught, cfg)
    return width_from_mask(mask_or_box, homography, axis, cfg.table_unit_mm)


@dataclass(frozen=True)
class GraspParams:
    open_pct: float
    roll_deg: float | None  # None: keep the taught roll (pass grasp_roll_deg=None)
    close_cap: float
    source: str  # "sam": the opening came from a measured width; "fallback": taught opening
    width_mm: float | None = None

    @property
    def chip(self) -> str:
        if self.source != "sam" or self.width_mm is None:
            return f"taught · open {self.open_pct:.0f}%"
        return f"sam-3.1 · width {self.width_mm:.0f} mm · open {self.open_pct:.0f}%"


def grasp_params(
    width_mm: float | None,
    roll_deg: float | None,
    taught_roll: float,
    profile: str | None,
    cfg: GraspConfig,
    calibration: JawCalibration,
    *,
    catalog_max_grip: float,
    taught_open_pct: float,
) -> GraspParams:
    """Opening, roll and close cap for one pick. ``width_mm`` (from ``width_across_jaws``)
    and ``roll_deg`` (a target wrist roll, e.g. from ``roll_for_axis``) come from a
    measurement and may be None; a bool or non-finite value is treated as None.
    ``profile`` comes from a classifier and may be anything. ``taught_roll`` and
    ``taught_open_pct`` are the slot's taught grasp roll and taught opening (ValueError if
    either is not a finite number, the opening outside 0..100). Every output is bounded:
    open in [max(open_min_pct, taught_open_pct), open_max_pct] from a width, never below
    the taught opening, exactly ``taught_open_pct`` on fallback; roll None (taught) on
    fallback or when unchanged, else within the envelope of ``taught_roll``; close_cap <=
    ``catalog_max_grip``. With ``cfg.sam_width`` off, everything but the profile cap is
    taught (the profile cap still applies: it only lowers)."""
    taught = _measured(taught_roll)
    if taught is None:
        raise ValueError("taught_roll must be a finite number")
    taught_open = _measured(taught_open_pct)
    if taught_open is None or not 0.0 <= taught_open <= 100.0:
        raise ValueError("taught_open_pct must be a finite number within 0..100")
    width = _measured(width_mm)
    if cfg.sam_width is True and calibration.calibrated is True and width and width > 0:
        floor = max(cfg.open_min_pct, taught_open)  # a width never narrows the descent
        open_pct = _clamp(calibration.mm_to_pct(width + cfg.margin_mm), floor, cfg.open_max_pct)
        source = "sam"
    else:
        open_pct, source, width = taught_open, "fallback", None
    delta = round(_roll_delta(roll_deg, taught, cfg), 2) if source == "sam" else 0.0
    roll = None if delta == 0.0 else round(taught + delta, 2)
    try:
        catalog = float(catalog_max_grip)
    except (TypeError, ValueError):
        catalog = 0.0
    if not math.isfinite(catalog):
        catalog = 0.0  # an unreadable catalog cap is the softest possible
    close_cap = max(0.0, min(catalog, cfg.profile_cap(profile)))
    return GraspParams(round(open_pct, 2), roll, round(close_cap, 3), source, width)


def load_grasp(cfg: Mapping[str, Any] | None) -> tuple[GraspConfig, JawCalibration]:
    """Both halves of ``grasp:`` from the loaded configs/armed.yaml dict."""
    raw = _section(_section(cfg, "config").get("grasp"), "grasp")
    return GraspConfig.from_config(raw), JawCalibration.from_config(raw.get("jaw_calibration"))


__all__ = [
    "PROFILES",
    "ROLL_ENVELOPE_MAX_DEG",
    "GraspConfig",
    "GraspConfigError",
    "GraspParams",
    "JawCalibration",
    "grasp_params",
    "load_grasp",
    "roll_for_axis",
    "roll_from_mask",
    "to_table",
    "width_across_jaws",
    "width_from_mask",
]
