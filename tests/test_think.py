"""Muse, always on (h2m/think.py): ambient speech goes to the thinker, never to a chatbot
turn; a suggestion is a request that waits for one confirmation; remarks have manners;
voice arms (after one "yes") and disarms. No network: a fake Spark client."""

from __future__ import annotations

import json

from h2m.core import load_config
from h2m.robot import ArmState
from h2m.think import Thinker
from tests import test_app_embodied as base

h2m = base.h2m  # the shared app fixture


class FakeSpark:
    model = "muse-spark-1.3"
    available = True

    def __init__(self, reply=None):
        self.reply = reply or {}
        self.sent = []

    def chat(self, model, messages):
        self.sent.append(json.loads(messages[1]["content"][0]["text"]))
        return json.dumps(self.reply)


def thinker(h, reply=None, **cfg):
    h.say_s = 0.0  # every `say` finishes at once
    t = Thinker(h, {"think": {"enabled": True, "comment_min_gap_s": 40, **cfg}}, client=FakeSpark(reply))
    h.thinker = t
    return t


def option_id(t, label_start):
    rows, _ = t.options()
    return next(r["id"] for r in rows if r["action"].startswith(label_start))


def test_options_are_a_closed_list_without_gestures(h2m):
    t = thinker(h2m)
    rows, by_id = t.options()
    tools = {tool for tool, _, _ in by_id.values()}
    assert "gesture" not in tools and "say" not in tools
    assert ("pick_place", {"object_id": "cup"}, "Clear the paper cup into the tray") in by_id.values()
    assert all(set(r) == {"id", "action"} for r in rows)


def test_ambient_speech_never_becomes_a_chatbot_turn(h2m):
    t = thinker(h2m)
    calls = []
    h2m.llm = type("L", (), {"call_tools": lambda *a, **k: calls.append(a), "model": "x",
                             "last_ms": 1, "last_error": None})()
    h2m.on_utterance("I need to get going soon, the meeting ran long", {"echo": False})
    h2m.on_utterance("can you believe the weather", {"echo": False})
    assert calls == [] and h2m.pending is None
    assert t._wake.is_set()  # the thinker heard it


def test_a_named_request_is_still_a_command(h2m):
    thinker(h2m)
    h2m.on_utterance("hand me the paper cup", {"echo": False})
    assert h2m.pending is not None and h2m.pending.contract.object_id == "cup"


def test_a_suggestion_waits_for_one_confirmation_and_its_question_is_spoken(h2m):
    t = thinker(h2m)
    oid = option_id(t, "Clear the paper cup")
    t.client.reply = {"notice": "an empty cup by the laptop", "comment": None, "suggest": oid,
                      "ask": "Clear the empty cup?", "why": "empty since lunch"}
    t.think("heartbeat")
    assert h2m.pending is not None and h2m.pending.contract.object_id == "cup"
    assert any("Clear the empty cup? Say yes" in s for s in h2m.spoken)
    assert h2m.arm.state is ArmState.READY  # nothing moved: it waits for the person
    note = t.snapshot()["notes"][-1]
    assert note["notice"] == "an empty cup by the laptop" and note["suggest"]["queued"] is True
    sent = t.client.sent[-1]
    assert sent["setting"] == "desk" and "bed" in sent["other_setting"] and sent["robot"]["armed"]


def test_an_id_outside_the_list_is_dropped(h2m):
    t = thinker(h2m, {"suggest": "o99", "ask": "Do something?"})
    t.think("heartbeat")
    assert h2m.pending is None and t.snapshot()["notes"][-1]["dropped"] == "not an option"


def test_remarks_have_manners(h2m):
    t = thinker(h2m, {"notice": "x", "comment": "Your water has been there since two."})
    t.think("heartbeat")
    t.think("heartbeat")  # within the gap and the same words: once
    assert h2m.spoken.count("Your water has been there since two.") == 1
    t.client.reply = {"comment": "Want anything?"}
    h2m.arm.state = ArmState.MOVING
    t.think("heartbeat")
    assert "Want anything?" not in h2m.spoken


def test_no_suggestion_while_something_waits_or_the_arm_is_disarmed(h2m):
    t = thinker(h2m)
    oid = option_id(t, "Clear the paper cup")
    t.client.reply = {"suggest": oid, "ask": "Clear it?"}
    h2m.disarm("test")
    t.think("heartbeat")
    assert h2m.pending is None and t.snapshot()["notes"][-1]["suggest"]["queued"] is False


def test_heard_and_recent_reach_muse(h2m):
    t = thinker(h2m, {})
    h2m.on_utterance("Armed, is my phone on the desk?", {"echo": False})
    h2m.log.emit("scene.event", event="hand_out", what="hand out · right")
    t.think("speech.utterance")
    sent = t.client.sent[-1]
    assert sent["heard"][-1] == {"who": "you", "text": "Armed, is my phone on the desk?", "age_s": 0, "to_you": True}
    assert {"what": "seen: hand out · right", "age_s": 0} in sent["recent"]


def test_voice_arms_after_one_yes_and_disarms_at_once(h2m):
    thinker(h2m)
    h2m.disarm("test")
    h2m.on_utterance("stand down", {"echo": False})
    assert h2m.arm.state is ArmState.DISARMED
    h2m.on_utterance("arm yourself", {"echo": False})
    assert h2m.arm.state is ArmState.DISARMED and "Arm and start helping? Say yes." in h2m.spoken
    h2m.on_utterance("yes", {"echo": False, "asked_by": "Sam"})
    assert h2m.arm.state is ArmState.DISARMED  # only the wearer
    h2m.on_utterance("yes", {"echo": False})
    assert h2m.arm.state is ArmState.READY
    h2m.on_utterance("please stand down", {"echo": False})
    assert h2m.arm.state is ArmState.DISARMED


def test_another_speaker_cannot_ask_to_arm(h2m):
    thinker(h2m)
    h2m.disarm("test")
    h2m.on_utterance("arm yourself", {"echo": False, "asked_by": "Sam"})
    assert h2m._power_offer is None and "Only you can arm me." in h2m.spoken


def test_profiles_stack():
    cfg = load_config(profile="live,bed")
    assert cfg["arm"]["poses_file"] == "configs/poses_bed.yaml" and cfg["think"]["enabled"] is True
    assert cfg["proactive"]["profile"] == "bed" and cfg["arm"]["backend"] == "lerobot"


def test_its_own_voice_is_not_heard_as_the_person(h2m):
    t = thinker(h2m, {})
    t._wake.clear()
    h2m.on_utterance("Water bottle, highlighter on white box.", {"echo": True})
    assert not t._wake.is_set()
    t.think("heartbeat")
    assert t.client.sent[-1]["heard"] == []
