"""Routines (docs/PROACTIVE.md §5, h2m/routines.py): parsing, due times, conditions that
fail closed, manners state and the file round trip. Wall clock is a fake."""

from __future__ import annotations

import time

import pytest
import yaml

from h2m.routines import (
    RoutineBook,
    RoutineError,
    condition_holds,
    in_quiet_hours,
    next_at,
    parse_condition,
    parse_duration,
    parse_quiet_hours,
    parse_routine,
)

SKILLS = ["wave", "bow", "hold_up", "sip"]
NOON = time.mktime((2026, 11, 3, 12, 0, 0, 0, 0, -1))


class Clock:
    def __init__(self, t=NOON):
        self.t = t

    def __call__(self):
        return self.t


def book(tmp_path, text, clock=None, **kw):
    path = tmp_path / "routines.yaml"
    path.write_text(text)
    return RoutineBook(path, skills=SKILLS, state_dir=tmp_path / "state", clock=clock or Clock(), **kw)


def test_durations_times_and_quiet_hours_parse():
    assert parse_duration("60m") == 3600 and parse_duration("90s") == 90 and parse_duration("2h") == 7200
    assert parse_duration(5) == 5
    for bad in ("0m", "-1", "soon", True):
        with pytest.raises(RoutineError):
            parse_duration(bad)
    assert parse_quiet_hours("23:00-07:00") == ((23, 0), (7, 0)) and parse_quiet_hours(None) is None
    with pytest.raises(RoutineError):
        parse_quiet_hours("23-7")
    night = time.mktime((2026, 11, 3, 23, 30, 0, 0, 0, -1))
    assert in_quiet_hours(night, ((23, 0), (7, 0))) and not in_quiet_hours(NOON, ((23, 0), (7, 0)))
    assert in_quiet_hours(NOON, ((9, 0), (13, 0))) and not in_quiet_hours(NOON, ((12, 0), (12, 0)))


def test_next_at_is_the_first_time_strictly_after():
    assert next_at([(8, 0), (20, 0)], NOON) == NOON + 8 * 3600
    assert next_at([(8, 0)], NOON) == NOON + 20 * 3600  # tomorrow 08:00
    assert next_at([(12, 0)], NOON) == NOON + 24 * 3600  # exactly now is not "after"


def test_routine_definitions_are_validated_against_the_skills_and_the_grammar():
    r = parse_routine("water", {"action": "wave", "every": "60m", "mode": "auto"}, SKILLS)
    assert r.mode == "announce" and r.every_s == 3600 and r.window_s == 300 and r.action_id == "wave"
    r = parse_routine("phone", {"action": "hold_up", "object": "ball", True: "phone_buzz"}, SKILLS)
    assert r.on == "phone_buzz" and r.action_id == "hold_up:ball"  # YAML's `on:` is the boolean true
    bad = [
        {"action": "fly", "every": "1m"},  # not a skill or a card kind
        {"action": "wave"},  # no schedule
        {"action": "wave", "every": "1m", "at": ["08:00"]},  # two schedules
        {"action": "wave", "on": "earthquake"},
        {"action": "wave", "every": "1m", "mode": "always"},
        {"action": "wave", "every": "1m", "conditions": ["user is happy"]},
        {"action": "wave", "every": "1m", "profile": "car"},
        {"action": "wave", "every": "1m", "extra": 1},
    ]
    for raw in bad:
        with pytest.raises(RoutineError):
            parse_routine("x", raw, SKILLS)
    with pytest.raises(RoutineError):
        parse_routine("bad name!", {"action": "wave", "every": "1m"}, SKILLS)


def test_conditions_fail_closed_on_unknown_facts():
    assert parse_condition("Hand not at keyboard") == ("hand_not_at", "keyboard")
    known = {"hand": "mug", "present": True, "objects": {"cup"}, "holding": None, "holding_known": True,
             "sounds": {"knock"}}
    unknown = {"hand": None, "present": None, "objects": set(), "holding": None, "holding_known": False,
               "sounds": set()}
    for text, want in [("hand not at keyboard", True), ("hand at mug", True), ("present", True),
                       ("not holding", True), ("object visible cup", True), ("object not visible jar", True),
                       ("heard knock", True), ("hand at keyboard", False), ("holding", False)]:
        assert condition_holds(parse_condition(text), known) is want, text
        assert condition_holds(parse_condition(text), unknown) is False, text


def test_every_routines_come_due_on_the_clock_and_move_on_when_proposed(tmp_path):
    clock = Clock()
    b = book(tmp_path, "routines:\n  water: {action: wave, every: 60m}\n  soon: {action: bow, every: 30m, first_after: 1m}\n",
             clock)
    assert b.error is None and [r.name for r in b.due()] == []
    clock.t += 61
    assert [r.name for r in b.due()] == ["soon"]
    b.proposed("soon")
    assert b.due() == [] and b.get("soon").next_due == clock.t + 1800
    clock.t += 3600
    assert [r.name for r in b.due()] == ["soon", "water"]  # oldest due first
    b.done("water")
    assert b.get("water").runs == 1 and b.get("water").next_due == clock.t + 3600
    b.snooze("soon")
    assert b.due() == []
    rows = b.due_rows()
    assert rows and all({"text", "name", "mode", "due_in_s"} <= set(r) for r in rows)


def test_at_and_event_routines(tmp_path):
    clock = Clock()
    b = book(tmp_path, 'routines:\n  pills: {action: wave, at: ["08:00", "20:00"]}\n  knock: {action: bow, "on": knock}\n',
             clock)
    assert b.get("pills").next_due == NOON + 8 * 3600 and b.get("knock").next_due is None
    assert b.due() == [] and [r.name for r in b.on_event("knock")] == ["knock"]
    assert b.on_event("door") == []
    clock.t = NOON + 8 * 3600 + 1
    assert [r.name for r in b.due()] == ["pills"]
    b.done("pills")
    assert b.get("pills").next_due == NOON + 20 * 3600


def test_conditions_profile_quiet_hours_and_request_only_gate_due(tmp_path):
    clock = Clock()
    b = book(tmp_path, 'routines:\n  tidy: {action: bow, every: 1m, profile: desk, conditions: ["hand not at keyboard"]}\n'
                       '  bed: {action: wave, every: 1m, profile: bed}\nquiet_hours: "23:00-07:00"\n', clock)
    clock.t += 120
    assert b.due({"hand": None}) == []  # the hand is unknown: fails closed
    assert [r.name for r in b.due({"hand": "mug"})] == ["tidy"]  # bed profile not offered at the desk
    b.request_only = True
    assert b.due({"hand": "mug"}) == []
    b.request_only = False
    clock.t = time.mktime((2026, 11, 3, 23, 30, 0, 0, 0, -1))
    assert b.due({"hand": "mug"}) == [] and b.on_event("knock", {"hand": "mug"}) == []


def test_a_refusal_cools_the_routine_and_writes_a_dont_propose_entry_that_persists(tmp_path):
    clock = Clock()
    b = book(tmp_path, "routines:\n  tidy: {action: bow, every: 1m, label: 'Tidy up?'}\n", clock,
             refuse_cooldown_s=600, dont_propose_s=3600)
    clock.t += 61
    assert [r.name for r in b.due()] == ["tidy"]
    ref = b.refused("tidy")
    assert ref.action == "bow" and ref.text == "don't propose Tidy up? at the desk in the afternoon"
    assert b.blocked("bow") and b.due() == [] and b.get("tidy").refusals == 1
    clock.t += 601  # cooldown over, the entry still holds in this context
    assert b.due() == []
    clock.t += 3600
    assert [r.name for r in b.due()] == ["tidy"]
    # persisted beside the state, read back by a new book
    b.refused("tidy")
    again = book(tmp_path, "routines:\n  tidy: {action: bow, every: 1m}\n", clock)
    assert [x.action for x in again.refusals()] == ["bow"] and again.blocked("bow")


def test_edit_save_and_reload_round_trip(tmp_path):
    clock = Clock()
    b = book(tmp_path, "routines:\n  water: {action: wave, every: 60m}\n", clock)
    r = b.update("water", {"every": "2h", "mode": "auto", "label": "Water?"})
    assert r.every_s == 7200 and r.mode == "announce" and r.next_due == clock.t + 7200
    b.update("pills", {"action": "hold_up", "object": "ball", "at": ["08:00"]})
    with pytest.raises(RoutineError):
        b.update("pills", {"action": "fly"})
    with pytest.raises(RoutineError):
        b.update("water", {"every": "0m"})
    b.save()
    raw = yaml.safe_load((tmp_path / "routines.yaml").read_text())
    assert raw["routines"]["water"] == {"action": "wave", "mode": "announce", "every": "2h", "window": "5m",
                                        "label": "Water?"}
    assert raw["routines"]["pills"]["at"] == ["08:00"]
    again = book(tmp_path, (tmp_path / "routines.yaml").read_text(), clock)
    assert {r.name for r in again.list()} == {"water", "pills"}
    assert b.remove("pills") and not b.remove("pills")
    snap = b.snapshot()
    assert [r["name"] for r in snap["routines"]] == ["water"] and snap["quiet_hours"] is None


def test_a_bad_file_means_no_routines_never_a_crash(tmp_path):
    b = book(tmp_path, "routines:\n  x: {action: fly, every: 1m}\n")
    assert b.list() == [] and "unknown action" in (b.error or "")
    b = book(tmp_path, "routines: [1, 2]\n")
    assert b.list() == [] and b.error
