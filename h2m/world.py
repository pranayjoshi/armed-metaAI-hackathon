"""World facts the gate needs that must be proven, not assumed.

- ``GripperTracker``: what is in the gripper, from plans that ran to completion or
  were interrupted at a known stage. It starts UNKNOWN and returns to UNKNOWN after
  an interruption mid-grasp or mid-release, or a fault outside a holding stage. A stop
  or fault while carrying or lowering a known object leaves it HOLDING (the arm keeps
  its grip there). Only a completed release (or an interruption after one) proves the
  gripper empty.
- ``spot_is_clear``: no tracked hand near the marked handover spot, on a fresh
  hand-tracking pass. No tracker, a stale pass, a pass older than the caller needs
  (``newer_than``), the arm hiding the spot, or no spot all fail closed.
- ``pose_deviation``: largest arm-joint distance between two poses (gripper excluded).

Every function here fails closed: missing or malformed input means "not proven".
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from h2m.contract import Verb
from h2m.robot.backend import GRIPPER, JOINTS

UNKNOWN, EMPTY, HOLDING = "unknown", "empty", "holding"
RELEASE_VERBS = frozenset({str(Verb.PICK_PLACE), str(Verb.PLACE_NEAR_USER), str(Verb.MOVE)})
APPROACH = "approach"  # plan meta phase: place_near_user up to above the spot (no release)


@dataclass(frozen=True)
class GripperState:
    status: str = UNKNOWN  # unknown | empty | holding
    object_id: str | None = None  # set only when holding
    why: str = "not observed since start"

    @property
    def empty(self) -> bool:
        return self.status == EMPTY

    @property
    def holding(self) -> bool:
        return self.status == HOLDING


class GripperTracker:
    """Fed from arm events (started / completed / stopped / fault). Its own small
    lock, never held across anything else, so it is safe on the stop listener."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = GripperState()
        self._active: dict[str, Any] | None = None  # verb/object of the running plan

    def state(self) -> GripperState:
        with self._lock:
            return self._state

    def started(self, meta: Mapping[str, Any]) -> None:
        with self._lock:
            self._active = {
                "verb": meta.get("verb"),
                "object_id": meta.get("object_id"),
                # skills: does the plan close on something / end with the jaws open
                "closes": meta.get("closes") is True,
                "releases": meta.get("releases") is True,
            }

    def completed(self, meta: Mapping[str, Any]) -> None:
        with self._lock:
            self._active = None
            if meta.get("verb") in RELEASE_VERBS and meta.get("phase") != APPROACH:
                # the plan opened at the drop and retreated
                self._state = GripperState(EMPTY, None, f"released after {meta.get('verb')}")
            elif meta.get("verb") == str(Verb.SKILL) and meta.get("releases") is True:
                self._state = GripperState(EMPTY, None, f"released after {meta.get('skill_name')}")
            # home / point / gesture never command the gripper on an object, and the
            # handover approach stops above the spot: the state is unchanged

    def interrupted(self, stage: str | None, fault: bool, reason: str = "") -> None:
        """A stop or fault. ``stage`` is the waypoint stage the plan was on."""
        with self._lock:
            active, self._active = self._active, None
            if active is None:
                return  # nothing was running: a latched stop changes nothing
            prior = self._state
            verb = active.get("verb")
            if fault:
                self._state = self._after_fault(
                    prior, verb, stage, active.get("object_id"), reason
                )
            elif verb == str(Verb.PICK_PLACE):
                self._state = self._after_pick(prior, stage, active.get("object_id"))
            elif verb == str(Verb.PLACE_NEAR_USER):
                self._state = self._after_place(prior, stage)
            elif verb == str(Verb.MOVE):
                self._state = self._after_pick(prior, stage, active.get("object_id"))
            elif verb == str(Verb.SKILL) and not active.get("closes"):
                # wave, sip, press: the jaws are never commanded on an object
                self._state = replace(prior, why="stopped during a skill; gripper untouched")
            elif verb == str(Verb.SKILL):
                self._state = self._after_pick(prior, stage, active.get("object_id"))
            elif verb not in (str(Verb.HOME), str(Verb.POINT), str(Verb.GESTURE)):
                self._state = GripperState(UNKNOWN, None, f"stopped during {verb}")

    def releasing(self, by: str = "operator") -> tuple[GripperState, GripperState]:
        """The operator is about to open the gripper: contents unknown until it is open.
        Set before the arm is told, so nothing reads HOLDING while it opens. Returns
        (prior state, the state set here) for ``restore`` if the arm refuses."""
        with self._lock:
            prior = self._state
            self._state = GripperState(UNKNOWN, None, f"gripper being released by {by}")
            return prior, self._state

    def restore(self, prior: GripperState, expected: GripperState) -> bool:
        """Undo ``releasing`` after the arm refused (the gripper never moved), only if
        nothing changed the state since (``expected`` is what ``releasing`` set)."""
        with self._lock:
            if self._state is not expected:
                return False
            self._state = prior
            return True

    def released_by_operator(self) -> bool:
        """The operator opened the gripper (H2M.release_gripper) with no plan running:
        EMPTY. Refused (False, unchanged) while a plan is active."""
        with self._lock:
            if self._active is not None:
                return False
            self._state = GripperState(EMPTY, None, "released by the operator")
            return True

    @staticmethod
    def _after_fault(
        prior: GripperState, verb: Any, stage: str | None, object_id: Any, reason: str
    ) -> GripperState:
        """A fault keeps the grip in carry / lower (arm.HOLD_STAGES), so a known object
        is still held. Anything else is UNKNOWN: a fault never proves the gripper empty."""
        if verb in (str(Verb.PICK_PLACE), str(Verb.MOVE), str(Verb.SKILL)) and stage == "carry" and object_id:
            return GripperState(HOLDING, str(object_id), "fault while carrying; grip kept")
        if verb == str(Verb.PLACE_NEAR_USER) and stage in ("carry", "lower") and prior.holding:
            doing = "carrying" if stage == "carry" else "lowering"
            return replace(prior, why=f"fault while {doing} to the spot; grip kept")
        return GripperState(UNKNOWN, None, f"fault during {verb}: {reason}")

    @staticmethod
    def _after_pick(prior: GripperState, stage: str | None, object_id: Any) -> GripperState:
        if stage == "approach" and prior.empty:
            return prior  # approach only opens the gripper: still empty
        if stage == "carry" and object_id:
            return GripperState(HOLDING, str(object_id), "stopped while carrying")
        if stage == "return":
            return GripperState(EMPTY, None, "stopped after the release")
        return GripperState(UNKNOWN, None, f"stopped during pick ({stage})")

    @staticmethod
    def _after_place(prior: GripperState, stage: str | None) -> GripperState:
        if stage == "carry" and prior.holding:
            return replace(prior, why="stopped while carrying to the spot")
        if stage == "lower" and prior.holding:  # lowering keeps the grip closed
            return replace(prior, why="stopped while lowering onto the spot")
        if stage == "return":
            return GripperState(EMPTY, None, "stopped after the release")
        return GripperState(UNKNOWN, None, f"stopped during place ({stage})")


def gripper_facts(state: GripperState, load: Any, load_empty_max: float) -> dict[str, bool]:
    """``gripper_empty`` / ``holding`` / ``gripper_loaded`` for the gate. A gripper load
    reading above ``load_empty_max`` (or NaN) contradicts "empty" and blocks a pick: it
    can only veto, never prove. No reading is not a load (and not proof of empty)."""
    loaded = (
        isinstance(load, (int, float))
        and not isinstance(load, bool)
        and (load != load or load > load_empty_max)
    )
    return {
        "gripper_empty": state.empty and not loaded,
        "holding": state.holding,
        "gripper_loaded": bool(loaded),
    }


# ── hands near the handover spot ───────────────────────────────────────────────


@dataclass(frozen=True)
class HandsSeen:
    """One hand-tracking pass: frame time and every landmark of every hand (table xy)."""

    t: float
    points: tuple[tuple[float, float], ...]


def spot_is_clear(
    hands: HandsSeen | None,
    t_now: float,
    stale_s: float,
    spot: Sequence[float] | None,
    radius: float,
    *,
    hidden: bool = False,
    newer_than: float | None = None,
) -> tuple[bool, str]:
    """``hidden``: the arm is between the camera and the spot, so "no hand seen" proves
    nothing. ``newer_than``: the pass must come from a frame taken after this time."""
    if spot is None:
        return False, "the marked spot is not defined"
    if hidden:
        return False, "my arm hides the spot from the camera"
    if not isinstance(hands, HandsSeen):
        return False, "I can't see hands, so I can't prove the spot is clear"
    if not (t_now - hands.t) <= stale_s:  # also catches NaN
        return False, "my view of the spot is stale"
    if newer_than is not None and not hands.t > newer_than:
        return False, "I have no view of the spot since I got here"
    sx, sy = float(spot[0]), float(spot[1])
    for x, y in hands.points:
        d = math.dist((x, y), (sx, sy))
        if not d > radius:  # NaN counts as near
            return False, "a hand is over the marked spot"
    return True, "spot clear"


# ── poses ──────────────────────────────────────────────────────────────────────


def pose_deviation(a: Mapping[str, Any], b: Mapping[str, Any]) -> float | None:
    """Largest |a[j] - b[j]| over the arm joints (gripper excluded). None if a joint
    is missing or not a finite number."""
    worst = 0.0
    for j in JOINTS:
        if j == GRIPPER:
            continue
        try:
            d = abs(float(a[j]) - float(b[j]))
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(d):
            return None
        worst = max(worst, d)
    return worst


__all__ = [
    "APPROACH", "EMPTY", "HOLDING", "UNKNOWN", "GripperState", "GripperTracker", "HandsSeen",
    "gripper_facts", "pose_deviation", "spot_is_clear",
]
