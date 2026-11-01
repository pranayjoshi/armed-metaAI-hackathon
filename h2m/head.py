"""Head direction as a coarse table third (Reflex build plan item 3b).

Pure functions from MediaPipe face-mesh landmarks to ``left`` / ``centre`` / ``right``.
No camera, no mediapipe import: the caller passes landmarks as plain dicts, so this is
testable on fixtures. The result is a TIE-BREAKER input to intent ranking (h2m.intent).
It is never an intent sensor and never selects or confirms anything on its own.

Landmarks are normalized image coordinates, either a mapping ``{index: {"x","y","z"}}``
or a sequence of such dicts indexed by landmark number (468 or 478 points).

Sign convention: positive yaw means the nose points toward image-right (+x). Whether
image-right is the table's right depends on how the camera faces the person, so
``invert`` flips it; set it once at setup.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

LEFT, CENTRE, RIGHT = "left", "centre", "right"
THIRDS = (LEFT, CENTRE, RIGHT)

# MediaPipe face-mesh indices: nose tip and the two outermost cheek points.
NOSE_TIP = 1
CHEEK_IMAGE_LEFT = 234  # the face's right cheek edge, appears on the image left when unmirrored
CHEEK_IMAGE_RIGHT = 454
MIN_FACE_WIDTH = 0.02  # normalized; narrower than this is too small or degenerate to trust
CENTRE_HALF_WIDTH_DEG = 15.0  # |yaw| at or below this counts as centre


def _point(landmarks: Any, index: int) -> tuple[float, float] | None:
    try:
        if isinstance(landmarks, Mapping):
            lm = landmarks.get(index)
        elif isinstance(landmarks, Sequence) and not isinstance(landmarks, str | bytes):
            lm = landmarks[index] if 0 <= index < len(landmarks) else None
        else:
            return None
        if not isinstance(lm, Mapping):
            return None
        x, y = lm.get("x"), lm.get("y")
    except (TypeError, KeyError, IndexError):
        return None
    for v in (x, y):
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
            return None
    return float(x), float(y)


def landmarks_to_yaw(landmarks: Any) -> float | None:
    """Approximate head yaw in degrees from the nose offset across the cheek span.

    ``asin`` of the nose position between the cheek edges (-1 at the image-left cheek,
    +1 at the image-right cheek). Positive = nose toward image-right. None when the
    landmarks are missing, non-finite or the face is too small to trust."""
    nose = _point(landmarks, NOSE_TIP)
    a = _point(landmarks, CHEEK_IMAGE_LEFT)
    b = _point(landmarks, CHEEK_IMAGE_RIGHT)
    if nose is None or a is None or b is None:
        return None
    width = b[0] - a[0]
    if width < MIN_FACE_WIDTH:  # also rejects a mirrored or inverted landmark order
        return None
    ratio = (nose[0] - (a[0] + b[0]) / 2.0) / (width / 2.0)
    ratio = max(-1.0, min(1.0, ratio))
    return math.degrees(math.asin(ratio))


def yaw_to_third(
    yaw_deg: float | None,
    centre_half_width_deg: float = CENTRE_HALF_WIDTH_DEG,
    offset_deg: float = 0.0,
    invert: bool = False,
) -> str | None:
    """Table third for a yaw. ``offset_deg`` is the yaw that means "straight ahead" for
    this setup (camera not square to the person); ``invert`` swaps left and right."""
    if isinstance(yaw_deg, bool) or not isinstance(yaw_deg, int | float) or not math.isfinite(yaw_deg):
        return None
    if not math.isfinite(centre_half_width_deg) or centre_half_width_deg < 0:
        return None
    y = float(yaw_deg) - offset_deg
    if invert:
        y = -y
    if abs(y) <= centre_half_width_deg:
        return CENTRE
    return RIGHT if y > 0 else LEFT


def head_third(
    landmarks: Any,
    centre_half_width_deg: float = CENTRE_HALF_WIDTH_DEG,
    offset_deg: float = 0.0,
    invert: bool = False,
) -> str | None:
    """Landmarks -> ``left`` / ``centre`` / ``right`` or None when no face is usable."""
    return yaw_to_third(landmarks_to_yaw(landmarks), centre_half_width_deg, offset_deg, invert)


def third_of_x(x: float, lo: float, hi: float, flip: bool = False) -> str | None:
    """Which third of the table span [lo, hi] an x coordinate falls in. ``flip`` makes
    larger x the left. Used to compare an object's table position with the head third."""
    vals = (x, lo, hi)
    if any(isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) for v in vals):
        return None
    if hi <= lo:
        return None
    f = (x - lo) / (hi - lo)
    if flip:
        f = 1.0 - f
    if f < 1 / 3:
        return LEFT
    if f > 2 / 3:
        return RIGHT
    return CENTRE


def valid_third(value: Any) -> str | None:
    """The value if it is one of left/centre/right, else None (untrusted input guard)."""
    return value if isinstance(value, str) and value in THIRDS else None
