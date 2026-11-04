"""The context packet: what the runtime knows right now, row by row (docs/PROACTIVE.md §2).

A pure builder. It reads whatever snapshot pieces exist and writes exactly the rows the
console's context view draws (``web/views/context/packet.js``):

    {clock, objects: [{name, slot, age_s}], present: [name], hand: {where, s} | None,
     sounds: [{label, age_s}], last_actions: [{text, age_s}], routines_due: [{text, ...}],
     rules: [{text}]}

Degrades honestly: a source that is absent leaves its row empty. Nothing here is guessed,
nothing is a model's text, and no posture or gaze row exists. The same packet (minus the
object positions) is what Muse Spark is shown when it ranks, so the person always sees
what the robot knew.

Sources, in order of preference per row:

- ``objects``: ``scene.objects`` (the SAM 3.1 scan, when the vision session publishes it),
  else ``scene.masks`` phrases, else today's ``vision.objects`` (detector or virtual table).
- ``present``: ``scene.present``, else ``speech.present``, else the speakers heard in the
  last ``SPEECH_WINDOW_S`` (``speech.utterance`` events with a ``speaker``).
- ``hand``: ``scene.hand`` or ``vision.hand`` as ``{where, s}``; otherwise empty.
- ``sounds``: ``sound.event`` events (``label``) in the last ``SOUND_WINDOW_S``.
- ``last_actions``: completed card tasks and arm plans, newest first.
- ``routines_due``: given by the caller (``h2m.routines``).
- ``rules``: the rule store's restrict-only rules and the "don't propose" entries.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

ROWS = ("clock", "objects", "present", "hand", "sounds", "actions", "routines", "rules")
SOUND_WINDOW_S = 30.0
SPEECH_WINDOW_S = 60.0
MAX_OBJECTS = 12
MAX_SOUNDS = 5
MAX_ACTIONS = 5
MAX_RULES = 8
NOT_OBJECTS = frozenset({"hand", "face", "person", "user"})  # scan phrases that are not things
ACTION_KINDS = frozenset({"task.completed", "arm.completed"})


def _text(value: Any, limit: int = 60) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in value).split())[:limit]


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value and abs(value) != float("inf") else None


def _age(t: Any, t_now: float) -> float | None:
    at = _num(t)
    return None if at is None else round(max(0.0, t_now - at), 1)


def _get(entry: Any, name: str) -> Any:
    return entry.get(name) if isinstance(entry, Mapping) else getattr(entry, name, None)


def clock_text(wall: float | None = None) -> str:
    """``11:02 Tue`` for the wall clock (local time)."""
    lt = time.localtime(wall if wall is not None else time.time())
    return time.strftime("%H:%M %a", lt)


def objects_row(
    scene: Mapping[str, Any] | None,
    vision: Mapping[str, Any] | None,
    t_now: float,
    slot_of: Callable[[Any], str | None] | None = None,
) -> list[dict[str, Any]]:
    """``slot_of`` maps an object's ``table_xy`` to the taught slot it sits on (the arm
    knows the slots; the vision list does not)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    scan_age = _num((scene or {}).get("scan_age_s")) if isinstance(scene, Mapping) else None
    if isinstance(scene, Mapping):
        for o in scene.get("objects") or []:
            name = _text(_get(o, "name") or _get(o, "phrase") or _get(o, "id"), 40)
            if not name or name.lower() in NOT_OBJECTS or name in seen:
                continue
            seen.add(name)
            age = _num(_get(o, "age_s"))
            out.append({"name": name, "slot": _slot(_get(o, "slot")),
                        "age_s": age if age is not None else scan_age})
        if not out:
            for m in scene.get("masks") or []:
                name = _text(_get(m, "phrase"), 40)
                if not name or name.lower() in NOT_OBJECTS or name in seen:
                    continue
                seen.add(name)
                out.append({"name": name, "slot": None, "age_s": scan_age})
    if not out and isinstance(vision, Mapping):
        for o in vision.get("objects") or []:
            name = _text(_get(o, "name") or _get(o, "id"), 40)
            if not name or name in seen:
                continue
            seen.add(name)
            age = _age(_get(o, "seen_t"), t_now)
            slot = _slot(_get(o, "slot"))
            if slot is None and slot_of is not None and _get(o, "table_xy") is not None:
                try:
                    slot = _slot(slot_of(_get(o, "table_xy")))
                except Exception:  # noqa: BLE001  # an unknown slot is an empty cell, not a guess
                    slot = None
            out.append({"name": name, "slot": slot,
                        "age_s": 0.0 if age is None and _get(o, "virtual") else age})
    return out[:MAX_OBJECTS]


def _slot(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value))
    return _text(value, 20) or None


def present_row(
    scene: Mapping[str, Any] | None,
    speech: Mapping[str, Any] | None,
    events: Iterable[Any],
    t_now: float,
) -> list[str]:
    names: list[str] = []
    for src in (scene, speech):  # the camera's or the mic's own list first ...
        if isinstance(src, Mapping) and isinstance(src.get("present"), list):
            found = [_text(_get(p, "name") if not isinstance(p, str) else p, 30) for p in src["present"]]
            names = [n for n in found if n]
            break
    for e in events:  # ... then anyone else heard recently
        if len(names) >= 6:
            break
        if _get(e, "kind") != "speech.utterance":
            continue
        data = _data(e)
        if _age(_get(e, "t"), t_now) is None or t_now - float(_get(e, "t")) > SPEECH_WINDOW_S:
            continue
        who = _text(data.get("speaker"), 30)
        if who and who not in names:
            names.append(who)
    return names[:6]


def hand_row(
    scene: Mapping[str, Any] | None, vision: Mapping[str, Any] | None, t_now: float
) -> dict[str, Any] | None:
    for src in (scene, vision):
        if not isinstance(src, Mapping):
            continue
        hand = src.get("hand")
        if isinstance(hand, str) and _text(hand):
            return {"where": _text(hand, 30), "s": None}
        if isinstance(hand, Mapping):
            where = _text(hand.get("where") or hand.get("at") or hand.get("zone"), 30)
            if not where:
                continue
            s = _num(hand.get("s"))
            if s is None:
                s = _age(hand.get("since_t"), t_now)
            return {"where": where, "s": s}
    return None


def _data(e: Any) -> Mapping[str, Any]:
    data = _get(e, "data")
    if isinstance(data, Mapping):
        return data
    return e if isinstance(e, Mapping) else {}


def sounds_row(events: Iterable[Any], t_now: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in reversed(list(events)):
        if _get(e, "kind") != "sound.event":
            continue
        age = _age(_get(e, "t"), t_now)
        if age is None or age > SOUND_WINDOW_S:
            continue
        label = _text(_data(e).get("label"), 30)
        if label:
            out.append({"label": label, "age_s": age})
        if len(out) >= MAX_SOUNDS:
            break
    return out


def actions_row(events: Iterable[Any], t_now: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in reversed(list(events)):
        kind = _get(e, "kind")
        if kind not in ACTION_KINDS:
            continue
        data = _data(e)
        if kind == "task.completed":
            card = _text(data.get("card"), 40)
            text = f"card {card}" if card else "card taken"
        else:
            verb = _text(data.get("skill_name") or data.get("verb"), 30) or "motion"
            obj = _text(data.get("object_id"), 30)
            text = f"{verb} {obj}".strip()
        age = _age(_get(e, "t"), t_now)
        if out and out[-1]["text"] == text and out[-1]["age_s"] == age:
            continue  # one plan reported by both the arm and the card task
        out.append({"text": text, "age_s": age})
        if len(out) >= MAX_ACTIONS:
            break
    return out


def rules_row(rules: Iterable[Any], refusals: Iterable[Any] = ()) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rules:
        when = _get(r, "when_holding")
        avoid, resp = _text(_get(r, "avoid"), 30), _text(_get(r, "response"), 30)
        if not isinstance(when, list) or not avoid or not resp:
            continue
        held = ", ".join(_text(w, 20) for w in when[:4] if isinstance(w, str))
        out.append({"text": f"holding {held}: {resp.replace('_', ' ')} {avoid}", "key": avoid})
    for ref in refusals:
        text = _text(_get(ref, "text"), 80)
        if text:
            out.append({"text": text, "key": _text(_get(ref, "action"), 40)})
    return out[:MAX_RULES]


def build_packet(
    *,
    t_now: float,
    wall: float | None = None,
    scene: Mapping[str, Any] | None = None,
    vision: Mapping[str, Any] | None = None,
    speech: Mapping[str, Any] | None = None,
    events: Sequence[Any] = (),
    rules: Iterable[Any] = (),
    refusals: Iterable[Any] = (),
    routines_due: Iterable[Mapping[str, Any]] = (),
    holding: str | None = None,
    slot_of: Callable[[Any], str | None] | None = None,
) -> dict[str, Any]:
    """The packet. ``t_now`` is the runtime clock the events are stamped with; ``wall``
    is the wall clock for the clock row (default: now)."""
    due = [dict(r) for r in routines_due if isinstance(r, Mapping)]
    packet: dict[str, Any] = {
        "clock": clock_text(wall),
        "objects": objects_row(scene, vision, t_now, slot_of),
        "present": present_row(scene, speech, events, t_now),
        "hand": hand_row(scene, vision, t_now),
        "sounds": sounds_row(events, t_now),
        "last_actions": actions_row(events, t_now),
        "routines_due": due,
        "rules": rules_row(rules, refusals),
    }
    if holding:
        packet["holding"] = _text(holding, 40)
    return packet


def facts(packet: Mapping[str, Any]) -> dict[str, Any]:
    """The closed set of facts routine conditions are checked against (h2m.routines).
    Unknown stays None so a condition on it fails closed."""
    hand = packet.get("hand")
    return {
        "hand": hand.get("where") if isinstance(hand, Mapping) else None,
        "present": bool(packet.get("present")) or None,
        "objects": {o["name"].lower() for o in packet.get("objects") or [] if o.get("name")},
        "holding": packet.get("holding"),
        "sounds": {s["label"].lower() for s in packet.get("sounds") or [] if s.get("label")},
    }


def for_model(packet: Mapping[str, Any]) -> dict[str, Any]:
    """The packet as Spark sees it: text and ages only (no positions, nothing to act on)."""
    return {
        "clock": packet.get("clock"),
        "objects": [o.get("name") for o in packet.get("objects") or []][:MAX_OBJECTS],
        "present": list(packet.get("present") or [])[:6],
        "hand": (packet.get("hand") or {}).get("where") if packet.get("hand") else None,
        "sounds": [s.get("label") for s in packet.get("sounds") or []],
        "last_actions": [a.get("text") for a in packet.get("last_actions") or []],
        "routines_due": [r.get("text") for r in packet.get("routines_due") or []],
        "rules": [r.get("text") for r in packet.get("rules") or []],
    }


__all__ = ["ROWS", "build_packet", "clock_text", "facts", "for_model"]
