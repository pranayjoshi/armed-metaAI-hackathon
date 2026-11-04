"""Head direction as a coarse table third.

Pure functions from the SAM 3.1 ``face`` mask (its box or outline, normalized image
coordinates) to ``left`` / ``centre`` / ``right``. No camera, no model import: the caller
passes the mask, so this is testable on fixtures. The result is a TIE-BREAKER input to
intent ranking (h2m.intent). It is never an intent sensor and never selects or confirms
anything on its own.

Sign convention: a face centred toward image-right (+x) means the person is on the
right. Whether image-right is the table's right depends on how the camera faces the
person, so ``invert`` flips it; set it once at setup.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

LEFT, CENTRE, RIGHT = "left", "centre", "right"
THIRDS = (LEFT, CENTRE, RIGHT)


def _finite(v: Any) -> bool:
    return not isinstance(v, bool) and isinstance(v, int | float) and math.isfinite(v)


def face_centre_x(mask: Any) -> float | None:
    """Horizontal centre (0..1) of a face mask given as a box ``[x0, y0, x1, y1]`` or an
    outline ``[[x, y], ...]``, both normalized. None for anything malformed or off-image."""
    try:
        if isinstance(mask, dict):
            mask = mask.get("polygon") or mask.get("bbox")
        if not isinstance(mask, Sequence) or isinstance(mask, str | bytes) or len(mask) < 3:
            return None
        if len(mask) == 4 and all(_finite(v) for v in mask):
            x0, x1 = float(mask[0]), float(mask[2])
        else:
            xs = [float(p[0]) for p in mask if isinstance(p, Sequence) and len(p) >= 2 and _finite(p[0])]
            if len(xs) < 3:
                return None
            x0, x1 = min(xs), max(xs)
    except (TypeError, IndexError, ValueError):
        return None
    if x1 < x0 or x0 < -0.02 or x1 > 1.02:
        return None
    return min(1.0, max(0.0, (x0 + x1) / 2.0))


def face_third(mask: Any, invert: bool = False) -> str | None:
    """Face mask -> ``left`` / ``centre`` / ``right`` of the image, or None without a usable face."""
    x = face_centre_x(mask)
    return None if x is None else third_of_x(x, 0.0, 1.0, flip=invert)


def third_of_x(x: float, lo: float, hi: float, flip: bool = False) -> str | None:
    """Which third of the table span [lo, hi] an x coordinate falls in. ``flip`` makes
    larger x the left. Used to compare an object's table position with the head third."""
    if not all(_finite(v) for v in (x, lo, hi)):
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


__all__ = ["CENTRE", "LEFT", "RIGHT", "THIRDS", "face_centre_x", "face_third", "third_of_x", "valid_third"]
