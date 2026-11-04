"""Routines: the person's schedule of things the arm offers on its own (PROACTIVE.md §5).

``configs/routines.yaml``::

    routines:
      water:      {action: pick_place, object: cup, every: 60m, mode: ask, window: 5m}
      desk_check: {action: bow, every: 30m, mode: announce, profile: desk,
                   conditions: ["hand not at keyboard"], say: "Checking the desk."}
      pills:      {action: hold_up, object: ball, at: ["08:00", "20:00"], mode: ask}
      phone:      {action: hold_up, object: ball, on: phone_buzz, mode: ask, window: 90s}
    quiet_hours: "23:00-07:00"

``action`` is a skill id from ``configs/skills.yaml`` or one of the card kinds
(``pick_place``, ``look_at``, ``say``); ``object`` names a catalog object when the action
takes one; ``say`` carries the taught phrase id. ``mode`` is the trust rung the person set:
``never`` (``off``), ``ask`` (a card), ``announce`` (``auto``: a card with a veto ring),
``do`` (speech-only actions; motion is announced). The book only decides WHEN a routine
is due; what its rung is allowed to become is ``h2m.safety.ladder``, and running it goes
through the gate like any card.

Schedules: ``every`` (an interval; first due ``first_after`` after load, default the
interval), ``at`` (wall-clock times), ``on`` (an event name such as ``phone_buzz``,
``knock``, ``sit_down``). ``conditions`` is a closed grammar over the context packet's
facts (``h2m.context.facts``) and fails closed: a fact the packet does not have makes the
condition false.

Manners state lives here too: a proposal moves ``every`` routines on by one interval, a
refusal adds a cooldown and a "don't propose <action> <context>" entry (restrict-only,
kept in ``state_dir/refusals.json``) that ``due`` respects, and a snooze shifts by one
interval. All times in this module are wall-clock seconds (``time.time``) because people
write routines in wall-clock terms; the runtime's monotonic clock never appears here.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from h2m.safety import RUNGS

KINDS = ("pick_place", "look_at", "say")  # card kinds a routine may name besides a skill
MODE_ALIASES = {"auto": "announce", "off": "never", "does": "do", "proposes": "ask"}
EVENTS = ("phone_buzz", "knock", "door", "sit_down", "lie_down", "stand_up", "lights_off",
          "new_object", "hand_out", "person_arrived")
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|sec|m|min|h|hr)?\s*$")
MAX_NAME = 40
# condition grammar: each is a regex over the lower-cased text -> (fact, test)
CONDITIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^hand not at (?P<v>[a-z_ ]+)$"), "hand_not_at"),
    (re.compile(r"^hand at (?P<v>[a-z_ ]+)$"), "hand_at"),
    (re.compile(r"^(someone )?present$"), "present"),
    (re.compile(r"^not holding$"), "not_holding"),
    (re.compile(r"^holding$"), "holding"),
    (re.compile(r"^object visible (?P<v>[a-z0-9_ ]+)$"), "object_visible"),
    (re.compile(r"^object not visible (?P<v>[a-z0-9_ ]+)$"), "object_not_visible"),
    (re.compile(r"^heard (?P<v>[a-z0-9_ ]+)$"), "heard"),
)


class RoutineError(ValueError):
    """A routine definition that cannot be used."""


def parse_duration(value: Any) -> float:
    """``"60m"``, ``"90s"``, ``"2h"`` or a number of seconds -> seconds (> 0)."""
    if isinstance(value, bool):
        raise RoutineError(f"not a duration: {value!r}")
    if isinstance(value, (int, float)):
        secs = float(value)
    else:
        m = DURATION.match(str(value))
        if not m:
            raise RoutineError(f"not a duration: {value!r}")
        secs = float(m.group(1)) * {None: 1, "s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600,
                                    "hr": 3600}[m.group(2)]
    if not secs > 0 or secs != secs:
        raise RoutineError(f"duration must be positive: {value!r}")
    return secs


def parse_hhmm(value: Any) -> tuple[int, int]:
    m = HHMM.match(str(value).strip())
    if not m:
        raise RoutineError(f"not a time of day (HH:MM): {value!r}")
    return int(m.group(1)), int(m.group(2))


def parse_quiet_hours(value: Any) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """``"23:00-07:00"`` -> ((23, 0), (7, 0)); None / "" -> None."""
    if value in (None, "", False):
        return None
    parts = str(value).split("-")
    if len(parts) != 2:
        raise RoutineError(f"quiet_hours must be HH:MM-HH:MM: {value!r}")
    return parse_hhmm(parts[0]), parse_hhmm(parts[1])


def in_quiet_hours(wall: float, quiet: tuple[tuple[int, int], tuple[int, int]] | None) -> bool:
    if quiet is None:
        return False
    lt = time.localtime(wall)
    mins = lt.tm_hour * 60 + lt.tm_min
    start, end = quiet[0][0] * 60 + quiet[0][1], quiet[1][0] * 60 + quiet[1][1]
    if start == end:
        return False
    return start <= mins < end if start < end else mins >= start or mins < end


def next_at(times: Iterable[tuple[int, int]], after: float) -> float | None:
    """The earliest wall time strictly after ``after`` at one of the HH:MM times."""
    best: float | None = None
    lt = time.localtime(after)
    for hh, mm in times:
        for day in (0, 1):
            cand = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + day, hh, mm, 0, 0, 0, -1))
            if cand > after and (best is None or cand < best):
                best = cand
                break
    return best


def daypart(wall: float) -> str:
    h = time.localtime(wall).tm_hour
    return "morning" if 5 <= h < 12 else "afternoon" if h < 18 else "evening" if h < 22 else "night"


def normalise_mode(value: Any) -> str:
    mode = MODE_ALIASES.get(str(value).strip().lower(), str(value).strip().lower())
    if mode not in RUNGS:
        raise RoutineError(f"mode must be one of {', '.join(RUNGS)} (or auto/off): {value!r}")
    return mode


def parse_condition(text: Any) -> tuple[str, str | None]:
    if not isinstance(text, str):
        raise RoutineError(f"condition must be text: {text!r}")
    low = " ".join(text.lower().split())
    for pat, name in CONDITIONS:
        m = pat.match(low)
        if m:
            return name, (m.groupdict().get("v") or "").strip() or None
    raise RoutineError(f"unknown condition: {text!r}")


def condition_holds(cond: tuple[str, str | None], facts: Mapping[str, Any]) -> bool:
    """Fails closed: a fact that is None (not known) makes every test on it False."""
    name, value = cond
    hand = facts.get("hand")
    objects = facts.get("objects") or set()
    sounds = facts.get("sounds") or set()
    holding = facts.get("holding")
    if name == "hand_not_at":
        return isinstance(hand, str) and hand.lower() != value
    if name == "hand_at":
        return isinstance(hand, str) and hand.lower() == value
    if name == "present":
        return facts.get("present") is True
    if name == "not_holding":
        return holding is None and facts.get("holding_known", True) is True
    if name == "holding":
        return isinstance(holding, str) and bool(holding)
    if name == "object_visible":
        return value in {str(o).lower() for o in objects}
    if name == "object_not_visible":
        return bool(objects) and value not in {str(o).lower() for o in objects}
    if name == "heard":
        return value in {str(s).lower() for s in sounds}
    return False


@dataclass
class Routine:
    name: str
    action: str
    mode: str = "ask"
    object: str | None = None
    every_s: float | None = None
    at: list[tuple[int, int]] = field(default_factory=list)
    on: str | None = None
    window_s: float = 300.0
    profile: str | None = None
    conditions: list[str] = field(default_factory=list)
    label: str | None = None
    say: str | None = None
    enabled: bool = True
    first_after_s: float | None = None
    # state (wall clock), never written back to the file
    next_due: float | None = None
    last_run: float | None = None
    last_proposed: float | None = None
    snoozed_until: float | None = None
    refusals: int = 0
    runs: int = 0

    @property
    def action_id(self) -> str:
        return f"{self.action}:{self.object}" if self.object else self.action

    @property
    def schedule_text(self) -> str:
        if self.every_s:
            mins = self.every_s / 60
            return f"every {mins:g} min" if mins >= 1 else f"every {self.every_s:g} s"
        if self.at:
            return "at " + ", ".join(f"{h:02d}:{m:02d}" for h, m in self.at)
        if self.on:
            return f"on {self.on.replace('_', ' ')}"
        return "manual"

    def definition(self) -> dict[str, Any]:
        """The YAML form (no runtime state)."""
        out: dict[str, Any] = {"action": self.action, "mode": self.mode}
        if self.object:
            out["object"] = self.object
        if self.every_s:
            out["every"] = _duration_text(self.every_s)
        if self.at:
            out["at"] = [f"{h:02d}:{m:02d}" for h, m in self.at]
        if self.on:
            out["on"] = self.on
        out["window"] = _duration_text(self.window_s)
        if self.profile:
            out["profile"] = self.profile
        if self.conditions:
            out["conditions"] = list(self.conditions)
        if self.label:
            out["label"] = self.label
        if self.say:
            out["say"] = self.say
        if not self.enabled:
            out["enabled"] = False
        if self.first_after_s is not None:
            out["first_after"] = _duration_text(self.first_after_s)
        return out

    def as_dict(self, wall: float) -> dict[str, Any]:
        d = asdict(self)
        d["at"] = [f"{h:02d}:{m:02d}" for h, m in self.at]
        d["schedule"] = self.schedule_text
        d["due_in_s"] = None if self.next_due is None else round(self.next_due - wall, 1)
        d["action_id"] = self.action_id
        return d


def _duration_text(secs: float) -> str:
    if secs % 3600 == 0:
        return f"{int(secs // 3600)}h"
    if secs % 60 == 0:
        return f"{int(secs // 60)}m"
    return f"{secs:g}s"


def parse_routine(name: Any, raw: Any, skills: Iterable[str] | None = None) -> Routine:
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME or not re.fullmatch(
        r"[A-Za-z0-9_-]+", name
    ):
        raise RoutineError(f"bad routine name: {name!r}")
    if not isinstance(raw, Mapping):
        raise RoutineError(f"routine {name}: must be a mapping")
    if True in raw:  # YAML reads an unquoted `on:` key as the boolean true
        raw = {("on" if k is True else k): v for k, v in raw.items()}
    known = {"action", "object", "mode", "every", "at", "on", "window", "profile", "conditions",
             "label", "say", "enabled", "first_after"}
    extra = sorted(set(raw) - known)
    if extra:
        raise RoutineError(f"routine {name}: unknown keys {', '.join(map(str, extra))}")
    action = raw.get("action")
    if not isinstance(action, str) or not action.strip():
        raise RoutineError(f"routine {name}: action is required")
    action = action.strip()
    if skills is not None and action not in KINDS and action not in set(skills):
        raise RoutineError(f"routine {name}: unknown action {action!r} (not a skill or a card kind)")
    obj = raw.get("object")
    if obj is not None and (not isinstance(obj, str) or not obj.strip() or len(obj) > 64):
        raise RoutineError(f"routine {name}: object must be a catalog object id")
    every = raw.get("every")
    at_raw = raw.get("at")
    on = raw.get("on")
    schedules = sum(x is not None for x in (every, at_raw, on))
    if schedules != 1:
        raise RoutineError(f"routine {name}: give exactly one of every / at / on")
    at: list[tuple[int, int]] = []
    if at_raw is not None:
        items = at_raw if isinstance(at_raw, list) else [at_raw]
        at = [parse_hhmm(x) for x in items]
        if not at:
            raise RoutineError(f"routine {name}: at needs at least one time")
    if on is not None and (not isinstance(on, str) or on not in EVENTS):
        raise RoutineError(f"routine {name}: on must be one of {', '.join(EVENTS)}")
    conds = raw.get("conditions") or []
    if not isinstance(conds, list):
        raise RoutineError(f"routine {name}: conditions must be a list")
    for c in conds:
        parse_condition(c)
    profile = raw.get("profile")
    if profile is not None and profile not in ("desk", "bed"):
        raise RoutineError(f"routine {name}: profile must be desk or bed")
    label = raw.get("label")
    say = raw.get("say")
    for k, v in (("label", label), ("say", say)):
        if v is not None and (not isinstance(v, str) or len(v) > 120):
            raise RoutineError(f"routine {name}: {k} must be short text")
    return Routine(
        name=name,
        action=action,
        mode=normalise_mode(raw.get("mode", "ask")),
        object=obj.strip() if isinstance(obj, str) else None,
        every_s=parse_duration(every) if every is not None else None,
        at=at,
        on=on,
        window_s=parse_duration(raw.get("window", "5m")),
        profile=profile,
        conditions=[" ".join(str(c).split()) for c in conds],
        label=label,
        say=say,
        enabled=raw.get("enabled", True) is not False,
        first_after_s=parse_duration(raw["first_after"]) if raw.get("first_after") is not None
        else None,
    )


@dataclass
class Refusal:
    action: str  # action id (e.g. "pick_place:cup", "bow")
    context: str  # "<profile>/<daypart>"
    at: float  # wall
    until: float  # wall
    text: str


class RoutineBook:
    """The routines, their due times and the manners state. Thread-safe."""

    def __init__(
        self,
        path: Path | None,
        *,
        skills: Iterable[str] | None = None,
        state_dir: Path | None = None,
        profile: str = "desk",
        clock: Callable[[], float] = time.time,
        refuse_cooldown_s: float = 1800.0,
        dont_propose_s: float = 86400.0,
    ) -> None:
        self.path = Path(path) if path else None
        self.skills = set(skills) if skills is not None else None
        self.state_dir = Path(state_dir) if state_dir else None
        self.profile = profile
        self.clock = clock
        self.refuse_cooldown_s = max(0.0, float(refuse_cooldown_s))
        self.dont_propose_s = max(0.0, float(dont_propose_s))
        self.quiet: tuple[tuple[int, int], tuple[int, int]] | None = None
        self.request_only = False
        self.error: str | None = None
        self._routines: dict[str, Routine] = {}
        self._refusals: list[Refusal] = []
        self._lock = threading.RLock()
        self.loaded_at = self.clock()
        if self.path is not None:
            self.load()
        self._load_refusals()

    # ── file ────────────────────────────────────────────────────────────────
    def load(self) -> None:
        if self.path is None:
            return
        try:
            raw = yaml.safe_load(self.path.read_text()) if self.path.exists() else {}
        except (OSError, yaml.YAMLError) as exc:
            self.error = f"cannot read {self.path}: {exc}"
            return
        try:
            self.replace(raw or {})
        except RoutineError as exc:  # a bad file means no routines, never a crash or a guess
            self.error = str(exc)

    def replace(self, raw: Mapping[str, Any]) -> None:
        """Validate a whole definition and swap it in (runtime state kept by name)."""
        if not isinstance(raw, Mapping):
            raise RoutineError("routines file must be a mapping")
        routines_raw = raw.get("routines") or {}
        if not isinstance(routines_raw, Mapping):
            raise RoutineError("routines must be a mapping of name -> routine")
        quiet = parse_quiet_hours(raw.get("quiet_hours"))
        parsed = {str(n): parse_routine(n, r, self.skills) for n, r in routines_raw.items()}
        wall = self.clock()
        with self._lock:
            for name, routine in parsed.items():
                old = self._routines.get(name)
                if old is not None and old.definition() == routine.definition():
                    parsed[name] = old  # unchanged: keep its state
                    continue
                self._schedule(routine, wall, fresh=True)
            self._routines = parsed
            self.quiet = quiet
            self.error = None

    def save(self) -> None:
        if self.path is None:
            return
        with self._lock:
            data = {"routines": {n: r.definition() for n, r in self._routines.items()}}
            if self.quiet is not None:
                data["quiet_hours"] = "{:02d}:{:02d}-{:02d}:{:02d}".format(*self.quiet[0], *self.quiet[1])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        tmp.replace(self.path)

    def update(self, name: str, fields: Mapping[str, Any]) -> Routine:
        """Edit (or add) one routine from a partial definition; validated whole."""
        with self._lock:
            old = self._routines.get(name)
            base = old.definition() if old is not None else {}
            merged = {**base, **{k: v for k, v in fields.items() if k in base or k != "name"}}
            # a schedule change replaces the old schedule rather than adding a second one
            if any(k in fields for k in ("every", "at", "on")):
                for k in ("every", "at", "on"):
                    if k not in fields:
                        merged.pop(k, None)
            routine = parse_routine(name, merged, self.skills)
            if old is not None:
                for k in ("last_run", "last_proposed", "refusals", "runs"):
                    setattr(routine, k, getattr(old, k))
            self._schedule(routine, self.clock(), fresh=old is None or old.definition().get("every")
                           != merged.get("every") or old.definition().get("at") != merged.get("at"))
            self._routines[name] = routine
            return routine

    def remove(self, name: str) -> bool:
        with self._lock:
            return self._routines.pop(name, None) is not None

    # ── schedule ────────────────────────────────────────────────────────────
    def _schedule(self, r: Routine, wall: float, fresh: bool) -> None:
        if r.every_s is not None:
            if fresh or r.next_due is None:
                first = r.first_after_s if r.first_after_s is not None else r.every_s
                r.next_due = (r.last_run if r.last_run is not None else wall) + (
                    r.every_s if r.last_run is not None else first
                )
        elif r.at:
            r.next_due = next_at(r.at, max(wall, r.last_run or wall))
        else:
            r.next_due = None  # event routines are never time-due

    def list(self) -> list[Routine]:
        with self._lock:
            return list(self._routines.values())

    def get(self, name: str) -> Routine | None:
        with self._lock:
            return self._routines.get(name)

    def by_action(self, action_id: str) -> Routine | None:
        with self._lock:
            return next((r for r in self._routines.values() if r.action_id == action_id), None)

    def context_key(self, wall: float | None = None) -> str:
        return f"{self.profile}/{daypart(wall if wall is not None else self.clock())}"

    def _usable(self, r: Routine, wall: float, facts: Mapping[str, Any]) -> bool:
        if not r.enabled or r.mode == "never":
            return False
        if r.profile is not None and r.profile != self.profile:
            return False
        if r.snoozed_until is not None and wall < r.snoozed_until:
            return False
        if self.blocked(r.action_id, wall):
            return False
        return all(condition_holds(parse_condition(c), facts) for c in r.conditions)

    def due(self, facts: Mapping[str, Any] | None = None, wall: float | None = None) -> list[Routine]:
        """Time-driven routines due now, oldest due first. Nothing during quiet hours or
        while request-only is set."""
        wall = self.clock() if wall is None else wall
        facts = facts or {}
        with self._lock:
            if self.request_only or in_quiet_hours(wall, self.quiet):
                return []
            out = [
                r for r in self._routines.values()
                if r.next_due is not None and r.next_due <= wall and self._usable(r, wall, facts)
            ]
        return sorted(out, key=lambda r: r.next_due or 0.0)

    def on_event(self, event: str, facts: Mapping[str, Any] | None = None, wall: float | None = None) -> list[Routine]:
        """Routines triggered by ``event`` (a name from EVENTS)."""
        wall = self.clock() if wall is None else wall
        facts = facts or {}
        with self._lock:
            if self.request_only or in_quiet_hours(wall, self.quiet):
                return []
            return [r for r in self._routines.values() if r.on == event and self._usable(r, wall, facts)]

    def overdue_s(self, r: Routine, wall: float | None = None) -> float:
        wall = self.clock() if wall is None else wall
        return 0.0 if r.next_due is None else max(0.0, wall - r.next_due)

    # ── manners state ───────────────────────────────────────────────────────
    def proposed(self, name: str, wall: float | None = None) -> None:
        wall = self.clock() if wall is None else wall
        with self._lock:
            r = self._routines.get(name)
            if r is None:
                return
            r.last_proposed = wall
            if r.every_s is not None:
                r.next_due = wall + r.every_s
            elif r.at:
                r.next_due = next_at(r.at, wall)

    def done(self, name: str, wall: float | None = None) -> None:
        wall = self.clock() if wall is None else wall
        with self._lock:
            r = self._routines.get(name)
            if r is None:
                return
            r.last_run, r.runs = wall, r.runs + 1
            self._schedule(r, wall, fresh=True)

    def snooze(self, name: str, wall: float | None = None) -> None:
        wall = self.clock() if wall is None else wall
        with self._lock:
            r = self._routines.get(name)
            if r is not None:
                r.snoozed_until = wall + (r.every_s or 1800.0)

    def refused(self, name: str, wall: float | None = None) -> Refusal | None:
        """A brace on the routine's card: cool it down and write the "don't propose"
        entry for this action in this context (restrict-only)."""
        wall = self.clock() if wall is None else wall
        with self._lock:
            r = self._routines.get(name)
            if r is None:
                return None
            r.refusals += 1
            r.snoozed_until = wall + self.refuse_cooldown_s
            return self.dont_propose(r.action_id, wall, what=r.label or r.name.replace("_", " "))

    def dont_propose(self, action_id: str, wall: float | None = None, what: str | None = None) -> Refusal:
        wall = self.clock() if wall is None else wall
        ctx = self.context_key(wall)
        profile, part = ctx.split("/", 1)
        text = f"don't propose {what or action_id.replace(':', ' ').replace('_', ' ')} at the {profile} in the {part}"
        ref = Refusal(action_id, ctx, wall, wall + self.dont_propose_s, text)
        with self._lock:
            self._refusals = [x for x in self._refusals if not (x.action == action_id and x.context == ctx)]
            self._refusals.append(ref)
            self._save_refusals()
        return ref

    def blocked(self, action_id: str, wall: float | None = None) -> bool:
        wall = self.clock() if wall is None else wall
        ctx = self.context_key(wall)
        with self._lock:
            return any(x.action == action_id and x.context == ctx and wall < x.until for x in self._refusals)

    def refusals(self, wall: float | None = None) -> list[Refusal]:
        wall = self.clock() if wall is None else wall
        with self._lock:
            self._refusals = [x for x in self._refusals if wall < x.until]
            return list(self._refusals)

    def force_due(self, name: str, wall: float | None = None) -> None:
        """Make a routine due now (the scripted demo; a Routines page's "run now")."""
        wall = self.clock() if wall is None else wall
        with self._lock:
            r = self._routines.get(name)
            if r is not None:
                r.next_due, r.snoozed_until = wall, None

    def _refusals_path(self) -> Path | None:
        return None if self.state_dir is None else self.state_dir / "refusals.json"

    def _load_refusals(self) -> None:
        path = self._refusals_path()
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text())
            self._refusals = [Refusal(**x) for x in raw if isinstance(x, dict)]
        except (OSError, ValueError, TypeError):
            self._refusals = []

    def _save_refusals(self) -> None:
        path = self._refusals_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps([asdict(x) for x in self._refusals], indent=2))
            tmp.replace(path)
        except OSError:
            pass

    # ── views ───────────────────────────────────────────────────────────────
    def due_rows(self, wall: float | None = None) -> list[dict[str, Any]]:
        """The packet's ``routines_due`` row: what is due or coming within an hour."""
        wall = self.clock() if wall is None else wall
        rows = []
        with self._lock:
            for r in self._routines.values():
                if not r.enabled or r.mode == "never" or r.next_due is None:
                    continue
                left = r.next_due - wall
                if left > 3600:
                    continue
                when = "due" if left <= 0 else f"in {int(left // 60)} min" if left >= 60 else f"in {int(left)} s"
                rows.append({"text": f"{(r.label or r.name).replace('_', ' ')} · {when} · {r.mode}",
                             "name": r.name, "mode": r.mode, "due_in_s": round(left, 1)})
        return sorted(rows, key=lambda x: x["due_in_s"])

    def snapshot(self, wall: float | None = None) -> dict[str, Any]:
        wall = self.clock() if wall is None else wall
        with self._lock:
            return {
                "routines": [r.as_dict(wall) for r in self._routines.values()],
                "quiet_hours": None if self.quiet is None else "{:02d}:{:02d}-{:02d}:{:02d}".format(
                    *self.quiet[0], *self.quiet[1]
                ),
                "quiet_now": in_quiet_hours(wall, self.quiet),
                "request_only": self.request_only,
                "profile": self.profile,
                "refusals": [asdict(x) for x in self._refusals if wall < x.until],
                "file": str(self.path) if self.path else None,
                "error": self.error,
            }


__all__ = [
    "EVENTS", "KINDS", "Refusal", "Routine", "RoutineBook", "RoutineError", "condition_holds",
    "daypart", "in_quiet_hours", "next_at", "parse_condition", "parse_duration", "parse_hhmm",
    "parse_quiet_hours", "parse_routine",
]
