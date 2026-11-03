"""The step contract: the one structure every input compiles into.

A contract says what the robot should do next (verb), to what (object and
where it is), how (grip effort, speed) and how sure the system is, plus what
is still unresolved. The safety gate decides whether it runs.
"""

from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from h2m.core import now


class Verb(StrEnum):
    PICK_PLACE = "pick_place"  # pick the object and put it in the tray
    POINT = "point"  # point at the object
    HOME = "home"
    GESTURE = "gesture"  # small taught motion near home, empty gripper (nod, tilt, ...)
    PLACE_NEAR_USER = "place_near_user"  # put the held object on the marked spot near the user
    SKILL = "skill"  # taught sequence from configs/skills.yaml (wave, hold_up, take_this, ...)
    MOVE = "move"  # pick the object and set it down on another taught slot
    SAY = "say"  # speak; no motion
    STOP = "stop"
    CANCEL = "cancel"
    CONFIRM = "confirm"
    UNKNOWN = "unknown"


MOTION_VERBS = frozenset(
    {Verb.PICK_PLACE, Verb.POINT, Verb.HOME, Verb.GESTURE, Verb.PLACE_NEAR_USER, Verb.SKILL,
     Verb.MOVE}
)
TARGETED_VERBS = frozenset({Verb.PICK_PLACE, Verb.POINT, Verb.MOVE})
# Verbs that move a held or grasped object. A skill carries when its definition says so
# (the gate reads world["carries"]).
CARRY_VERBS = frozenset({Verb.PICK_PLACE, Verb.PLACE_NEAR_USER, Verb.MOVE})

_ids = itertools.count(1)


@dataclass
class Candidate:
    object_id: str
    name: str
    score: float


@dataclass
class StepContract:
    verb: Verb
    object_id: str | None = None
    object_name: str | None = None
    table_xy: tuple[float, float] | None = None
    grip_effort: float | None = None  # 0..1, from EMG or words like "gently"
    speed: float = 1.0  # 0..1 scale on the arm's configured max speed
    confidence: float = 0.0
    target_margin: float | None = None
    evidence: dict[str, float] = field(default_factory=dict)
    candidates: list[Candidate] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    utterance: str = ""
    source: str = "grammar"  # grammar | llm | ui
    route: str | None = None  # taught route chosen by h2m.rules, if any
    destination: str | None = None  # move: the slot to set the object down on
    applied_rules: list[str] = field(default_factory=list)  # ids of rules that shaped it
    id: str = field(default_factory=lambda: f"c{next(_ids):05d}")
    created_at: float = field(default_factory=now)

    @property
    def needs_motion(self) -> bool:
        return self.verb in MOTION_VERBS

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["verb"] = str(self.verb)
        return data
