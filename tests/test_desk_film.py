"""The desk on film (Nov 4) on the virtual arm with a fake Spark: the veto ring for routines
the person set to `announce` near a person or carrying (opt-in trust flags), the desk check
that clears only rubbish, Muse raising a moment (someone offering, a call) that a routine
listens for, the water routine handed to Muse, and a skill step that opens to a percent."""

from __future__ import annotations

from pathlib import Path

import pytest

from armed.robot.skills import PoseError, load_skills
from armed.safety import ladder
from armed.think import Thinker
from tests import test_app_embodied as base
from tests.test_selection import kinds
from tests.test_trust import Clock, proactive, tick

armed = base.armed

DESK = """
routines:
  desk_check: {action: pick_place, object: trash, every: 30m, mode: announce, say: "Clearing the desk."}
  water: {action: think, every: 60m, label: "Water time", say: "It's water time."}
  chips: {action: wave, "on": offered, mode: announce, label: "Take it from them?"}
  hold_it: {action: wave, "on": on_call_holding, mode: ask, label: "Hold the jar?"}
  stow_it: {action: stow_held, "on": holding, mode: ask, label: "Put it in the tray?"}
  hold_it_up: {action: hold_out_held, "on": holding, mode: ask, label: "Hold it up for you?"}
"""


def desk(h, tmp_path, clock, near=True):
    for o in h.cfg["objects"]:
        if o["id"] == "block":
            o["tags"] = ["trash"]
        if o["id"] == "cup":
            o["tags"] = ["drink"]
    p = proactive(h, tmp_path, clock, text=DESK, announce=True)
    p.trust.update({"announce_near_person": near, "announce_carries": near})
    return p


def test_the_ladder_rings_near_a_person_or_carrying_only_when_both_flags_say_so():
    on = {"announce_enabled": True}
    assert ladder("announce", motion=True, near_person=True, cfg=on).rung == "ask"
    assert ladder("announce", motion=True, carries=True, cfg=on).rung == "ask"
    both = {**on, "announce_near_person": True, "announce_carries": True}
    assert ladder("announce", motion=True, near_person=True, carries=True, cfg=both).rung == "announce"
    # another speaker's request and quiet hours still win
    assert ladder("announce", motion=True, near_person=True, asked_by="X", cfg=both).rung == "ask"
    assert ladder("announce", motion=True, near_person=True, quiet=True, cfg=both).rung == "never"
    assert ladder("announce", motion=True, near_person=True, cfg={"announce_near_person": True}).rung == "ask"


def test_the_desk_check_rings_for_rubbish_only(armed, tmp_path):
    clock = Clock()
    p = desk(armed, tmp_path, clock)
    assert p._trash_ids(armed.visible_objects()) == ["block"]  # never the phone or the drink
    clock.t += 1801
    menu = tick(armed)
    assert menu is not None and menu.highlighted.routine == "desk_check"
    assert menu.highlighted.tool_call == ("pick_place", {"object_id": "block"})
    assert menu.highlighted.rung == "announce" and menu.veto_until is not None
    assert menu.highlighted.label == "Clear the wooden block?"


def test_a_drink_already_brought_up_stays_on_the_desk(armed, tmp_path):
    p = desk(armed, tmp_path, Clock())
    armed.log.emit("arm.completed", skill="skill.bring_drink", object_id="cup")
    assert p._trash_ids(armed.visible_objects()) == ["block"]


def test_without_the_flags_the_clean_up_is_an_ordinary_card(armed, tmp_path):
    clock = Clock()
    desk(armed, tmp_path, clock, near=False)
    clock.t += 1801
    menu = tick(armed)
    card = next(c for c in menu.cards if c.routine == "desk_check")
    assert card.rung == "ask" and menu.veto_until is None


def test_a_moment_muse_raises_rings_its_announce_routine(armed, tmp_path):
    clock = Clock()
    desk(armed, tmp_path, clock)
    armed.log.emit("scene.event", event="offered", what="X holds out a bag of chips", source="muse-spark-1.3")
    menu = tick(armed)
    assert menu is not None and menu.highlighted.routine == "chips"
    assert menu.highlighted.rung == "announce" and menu.veto_until is not None
    assert menu.highlighted.evidence[0]["text"] == "X holds out a bag of chips"


def test_an_ask_moment_is_a_card(armed, tmp_path):
    clock = Clock()
    desk(armed, tmp_path, clock)
    armed.log.emit("scene.event", event="on_call_holding", what="phone at the ear, jar held out")
    menu = tick(armed)
    assert any(c.routine == "hold_it" and c.rung == "ask" for c in menu.cards)
    assert menu.veto_until is None


class FakeSpark:
    available = True
    model = "muse-spark-1.3"

    def __init__(self, reply):
        self.reply = reply
        self.messages = None

    def chat(self, model, messages):
        self.messages = messages
        return self.reply


def thinker(h, reply):
    t = Thinker(h, {"think": {"enabled": False, "send_image": False}}, client=FakeSpark(reply))
    h.thinker = t
    return t


def test_muse_raises_a_moment_once_and_only_one_a_routine_listens_for(armed, tmp_path):
    desk(armed, tmp_path, Clock())
    t = thinker(armed, '{"notice": "X offers chips", "seen": "offered", "suggest": null}')
    assert [m["id"] for m in t._moments()] == ["on_call_holding", "offered"]  # no routine on leaving
    t.think("speech.utterance")
    t.think("speech.utterance")
    seen = kinds(armed, "scene.event")
    assert len(seen) == 1 and seen[0]["event"] == "offered" and seen[0]["what"] == "X offers chips"
    t.client.reply = '{"seen": "leaving"}'
    t.think("heartbeat")
    assert len(kinds(armed, "scene.event")) == 1


def test_muse_is_offered_the_drink_and_only_rubbish_to_clear(armed, tmp_path):
    desk(armed, tmp_path, Clock())
    t = thinker(armed, "{}")
    rows, by_id = t.options()
    labels = [r["action"] for r in rows]
    assert "Clear the wooden block into the bin" in labels
    assert not any(a.startswith("Clear the phone") or a.startswith("Clear the paper cup") for a in labels)
    # drink_above / drink_up are untaught in the fixture: the drink is not offered until taught
    assert "bring_drink" in armed.embodied.skills
    assert not any(v[1].get("name") == "bring_drink" for v in by_id.values())
    t._skill_ready = lambda skill, untaught: True
    rows, by_id = t.options()
    assert ("skill", {"name": "bring_drink", "object_id": "cup"}, "Bring the paper cup up to drink") in by_id.values()


def test_the_water_routine_wakes_muse_and_raises_no_card(armed, tmp_path):
    clock = Clock()
    desk(armed, tmp_path, clock)
    t = thinker(armed, "{}")
    t.enabled = True
    pokes = []
    t.poke = pokes.append
    clock.t += 3601
    armed.proactive.extras(armed.arm.state)
    assert pokes and pokes[0].startswith("routine water is due now") and "It's water time." in pokes[0]
    assert armed.proactive.book.get("water").next_due > clock.t


def test_a_skill_step_may_open_to_a_percent(tmp_path):
    path = Path(tmp_path) / "skills.yaml"
    path.write_text("skills:\n  take:\n    steps:\n      - {pose: home, gripper: 70, dwell: 3}\n")
    step = load_skills(path)["take"].steps[0]
    assert step.gripper == "open" and step.open_pct == 70.0
    path.write_text("skills:\n  take:\n    steps:\n      - {pose: home, gripper: 140}\n")
    with pytest.raises(PoseError):
        load_skills(path)


def test_taking_the_chips_holds_them_and_asks_tray_or_hold_up(armed, tmp_path):
    desk(armed, tmp_path, Clock())
    armed.gripper.started({"verb": "skill", "skill_name": "accept_item"})
    armed.log.emit("arm.completed", verb="skill", skill_name="accept_item", holds=True,
                 held_after=True, object_id="chips", releases=False)
    held = armed.gripper.state()
    assert held.holding and held.object_id == "chips"
    assert kinds(armed, "scene.event")[-1]["event"] == "holding"
    menu = tick(armed)
    labels = {c.routine: c.label for c in menu.cards}
    assert labels.get("stow_it") == "Put it in the tray?" and labels.get("hold_it_up") == "Hold it up for you?"
    assert all(c.rung == "ask" for c in menu.cards if c.routine in ("stow_it", "hold_it_up"))


def test_held_up_the_tray_waits_for_done(armed, tmp_path):
    import time

    from armed.compiler.grammar import parse
    from armed.contract import Verb

    desk(armed, tmp_path, Clock())
    armed.gripper.started({"verb": "skill", "skill_name": "hold_out_held"})
    armed.log.emit("arm.completed", verb="skill", skill_name="hold_out_held", holds=True,
                 held_after=True, object_id="chips", releases=False)
    deadline = time.monotonic() + 2
    while armed.pending is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert armed.pending is not None and armed.pending.contract.utterance == "stow_held"
    assert armed.pending.expires_at - time.monotonic() > 60  # waits for "done", not 8 s
    assert armed.gripper.state().object_id == "chips"
    assert parse("I'm done", {}).verb is Verb.CONFIRM


def test_the_repo_skills_and_routines_load():
    skills = load_skills(Path("configs/skills.yaml"))
    assert {"bring_drink", "accept_item", "hand_keys", "stow_held", "hold_out_held"} <= set(skills)
    assert skills["accept_item"].steps[0].open_pct == 70.0
    assert skills["accept_item"].holds == "chips" and skills["stow_held"].needs_holding
