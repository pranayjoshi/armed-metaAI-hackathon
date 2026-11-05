"""The context packet (docs/PROACTIVE.md §2, armed/context.py): rows from real sources only,
empty when a source is missing, never a guess. No network, no arm."""

from __future__ import annotations

import time

from armed.context import ROWS, build_packet, clock_text, facts, for_model
from armed.core import Event

T = 1000.0


def ev(kind, t, **data):
    return Event(seq=1, t=t, kind=kind, data=data)


def test_empty_sources_give_empty_rows_and_a_clock():
    p = build_packet(t_now=T)
    assert set(p) >= {"clock", "objects", "present", "hand", "sounds", "last_actions",
                      "routines_due", "rules"}
    assert p["objects"] == [] and p["present"] == [] and p["hand"] is None
    assert p["sounds"] == [] and p["last_actions"] == [] and p["routines_due"] == []
    assert p["rules"] == [] and "holding" not in p
    assert isinstance(p["clock"], str) and ":" in p["clock"]
    assert len(ROWS) == 8 and "posture" not in ROWS and "gaze" not in ROWS


def test_clock_text_is_wall_clock_local_time():
    wall = time.mktime((2026, 11, 3, 11, 2, 0, 0, 0, -1))
    assert clock_text(wall) == "11:02 Tue"


def test_objects_prefer_the_scan_then_masks_then_the_detector():
    vision = {"objects": [{"id": "cup", "name": "paper cup", "table_xy": (0.1, 0.5), "seen_t": T - 4}]}
    scene = {"scan_age_s": 2.0, "objects": [{"name": "phone", "slot": 3}, {"name": "hand"}]}
    p = build_packet(t_now=T, scene=scene, vision=vision)
    assert p["objects"] == [{"name": "phone", "slot": "3", "age_s": 2.0}]  # hand is not a thing
    p = build_packet(t_now=T, scene={"scan_age_s": 1.5, "masks": [{"phrase": "jar"}, {"phrase": "face"}]},
                     vision=vision)
    assert p["objects"] == [{"name": "jar", "slot": None, "age_s": 1.5}]
    p = build_packet(t_now=T, vision=vision, slot_of=lambda xy: "slot_1")
    assert p["objects"] == [{"name": "paper cup", "slot": "slot_1", "age_s": 4.0}]
    p = build_packet(t_now=T, vision={"objects": [{"id": "block", "name": "wooden block", "virtual": True}]})
    assert p["objects"] == [{"name": "wooden block", "slot": None, "age_s": 0.0}]


def test_slot_lookup_failures_leave_the_cell_empty():
    vision = {"objects": [{"id": "cup", "name": "cup", "table_xy": (0.1, 0.5)}]}

    def boom(xy):
        raise RuntimeError("no arm")

    assert build_packet(t_now=T, vision=vision, slot_of=boom)["objects"][0]["slot"] is None


def test_present_from_the_scan_or_speech_or_recent_speakers():
    assert build_packet(t_now=T, scene={"present": ["user", {"name": "X"}]})["present"] == ["user", "X"]
    assert build_packet(t_now=T, speech={"present": ["X"]})["present"] == ["X"]
    events = [ev("speech.utterance", T - 10, speaker="X", text="hi"),
              ev("speech.utterance", T - 200, speaker="old", text="gone"),
              ev("speech.utterance", T - 5, speaker="X", text="again")]
    assert build_packet(t_now=T, events=events)["present"] == ["X"]


def test_hand_row_needs_a_source_that_says_where():
    assert build_packet(t_now=T, vision={"hands_seen": object()})["hand"] is None  # a pass is not a place
    assert build_packet(t_now=T, scene={"hand": {"where": "keyboard", "since_t": T - 12}})["hand"] == {
        "where": "keyboard", "s": 12.0}
    assert build_packet(t_now=T, vision={"hand": "held out"})["hand"] == {"where": "held out", "s": None}


def test_sounds_and_actions_come_from_the_event_log_newest_first():
    events = [
        ev("sound.event", T - 40, label="knock"),  # too old
        ev("sound.event", T - 2, label="phone buzz"),
        ev("arm.completed", T - 30, verb="pick_place", object_id="cup"),
        ev("task.completed", T - 30, card="pick_place:cup"),
        ev("arm.completed", T - 9, verb="skill", skill_name="bow"),
    ]
    p = build_packet(t_now=T, events=events)
    assert p["sounds"] == [{"label": "phone buzz", "age_s": 2.0}]
    assert p["last_actions"] == [{"text": "bow", "age_s": 9.0}, {"text": "card pick_place:cup", "age_s": 30.0},
                                 {"text": "pick_place cup", "age_s": 30.0}]


def test_rules_row_lists_restrictions_and_dont_propose_entries():
    class R:
        when_holding = ["liquid"]
        avoid = "laptop"
        response = "route_around"

    p = build_packet(t_now=T, rules=[R(), {"bad": 1}],
                     refusals=[{"action": "bow", "text": "don't propose bow at the desk in the evening"}],
                     routines_due=[{"text": "water · due · every 60 min", "name": "water"}], holding="cup")
    assert p["rules"] == [{"text": "holding liquid: route around laptop", "key": "laptop"},
                          {"text": "don't propose bow at the desk in the evening", "key": "bow"}]
    assert p["routines_due"][0]["name"] == "water" and p["holding"] == "cup"


def test_facts_fail_closed_and_the_model_view_has_no_positions():
    p = build_packet(t_now=T, vision={"objects": [{"id": "cup", "name": "Cup", "table_xy": (0.1, 0.5)}]},
                     events=[ev("sound.event", T - 1, label="Knock")])
    f = facts(p)
    assert f["hand"] is None and f["present"] is None and f["objects"] == {"cup"} and f["sounds"] == {"knock"}
    m = for_model(p)
    assert m["objects"] == ["Cup"] and m["hand"] is None and "table_xy" not in str(m)
    assert set(m) == {"clock", "objects", "present", "hand", "sounds", "last_actions", "routines_due", "rules"}

