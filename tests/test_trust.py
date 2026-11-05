"""The trust ladder and the proactive layer (docs/PROACTIVE.md §3-4; armed/safety.py::ladder,
armed/selection.py veto ring, armed/proactive.py) on the virtual arm with a fake Spark. No
network, no hardware.

Covers: the ladder only lowers a rung (never near a person, never carrying, never for
another speaker's request, never in quiet hours, announce off by default); `never` is
refused in the gate for a typed request; a routine coming due becomes a card with its
evidence rows lit and the packet is published; the veto ring starts only for a
pre-approved card, runs the card through the gate when it lapses untouched, and a brace
inside it cancels quietly (no incident, a "don't propose" entry); an `ask` card never
runs from time; the routines API."""

from __future__ import annotations

import math
import time

import pytest
from fastapi.testclient import TestClient

from armed.contract import StepContract, Verb
from armed.core import now
from armed.intent import Candidate, Option
from armed.proactive import Proposer
from armed.robot import ArmState
from armed.safety import RUNGS, Verdict, action_rung, decide, ladder
from armed.selection import INTENT, Selection
from armed.server import create_app
from tests import test_app_embodied as base
from tests.test_app_embodied import prove_empty, started
from tests.test_selection import enable, kinds, wait

armed = base.armed  # the shared app fixture: virtual arm, EMG / vision / speech off
TOKEN = "t0k"
GOOD = {"X-Armed-Token": TOKEN}
NOON = time.mktime((2026, 11, 3, 12, 0, 0, 0, 0, -1))
ROUTINES = """
routines:
  water: {action: pick_place, object: cup, every: 60m, mode: ask, label: "Water?"}
  check: {action: wave, every: 30m, mode: announce, label: "Check the desk?", say: "Checking the desk."}
  hello: {action: bow, every: 45m, mode: ask}
"""


class Clock:
    def __init__(self, t=NOON):
        self.t = t

    def __call__(self):
        return self.t


# ── the ladder: a pure function that only lowers ───────────────────────────────


def test_ladder_only_lowers_a_rung():
    on = {"announce_enabled": True}
    assert ladder("never", motion=False).rung == "never"
    assert ladder("bogus", motion=False).rung == "never"
    assert ladder("do", motion=False).rung == "do"  # speech: done
    assert ladder("do", motion=True, cfg=on).rung == "announce"  # motion is never silent
    assert ladder("announce", motion=True).rung == "ask"  # relaxation off by default
    assert ladder("announce", motion=True, cfg={"announce_enabled": "true"}).rung == "ask"
    assert ladder("announce", motion=True, cfg=on).rung == "announce"
    assert ladder("announce", motion=True, near_person=True, cfg=on).rung == "ask"
    assert ladder("announce", motion=True, carries=True, cfg=on).rung == "ask"
    assert ladder("do", motion=False, asked_by="X", cfg=on).rung == "ask"  # the wearer decides
    assert ladder("announce", motion=True, quiet=True, cfg=on).rung == "never"
    assert ladder("ask", motion=True, cfg=on).rung == "ask"
    for rung in RUNGS:
        got = ladder(rung, motion=True, cfg=on)
        assert RUNGS.index(got.rung) <= RUNGS.index(rung) and got.asked == rung


def test_action_rung_reads_the_persons_settings_exactly():
    settings = {"sip": "never", "pick_place": "announce", "hold_up:ball": "do", "wave": "bogus"}
    assert action_rung("sip", "sip", settings) == "never"
    assert action_rung("pick_place:cup", "pick_place", settings) == "announce"
    assert action_rung("hold_up:ball", "hold_up", settings) == "do"
    assert action_rung("hold_up:cup", "hold_up", settings) == "ask"
    assert action_rung("wave", "wave", settings) == "never"  # not a rung: never
    assert action_rung("bow", "bow", None, default="nope") == "never"


def test_the_gate_refuses_an_action_set_to_never():
    c = StepContract(Verb.SKILL, utterance="sip", confidence=0.9)
    world = {"arm_ready": True, "skill_known": True, "rung": "never", "gripper_empty": True}
    d = decide(c, world, {"always_confirm": True})
    assert d.verdict is Verdict.REJECT and d.checks["trust_rung"] is False
    world["rung"] = "ask"
    assert decide(c, world, {"always_confirm": True}).verdict is Verdict.ASK


# ── the veto ring in the selection machine ─────────────────────────────────────


def card(cid="skill:wave", kind="skill", rung=None, **kw):
    contract = {"verb": "skill", "confidence": 0.6, "source": "ui", "utterance": "bow"}
    return Candidate(cid, kind, "Check the desk?", "likely", "routine", contract, ("skill", {"name": "wave"}),
                     rung=rung, **kw)


def test_veto_ring_only_for_a_pre_approved_card_and_only_untouched():
    sel = Selection(INTENT, require_ack=False)
    t = 100.0
    assert sel.show([card(rung="ask")], "routine", t) and sel.start_veto("skill:wave", t, 5.0) == [
        ("selection.auto_started", {"menu": 1, "task": 1, "by": "app", "ok": False,
                                    "detail": "only a pre-approved routine gets a veto window"})]
    assert sel.veto_due(t + 100) is None  # time never approves an ask card
    sel.hide(t, "x")
    assert sel.show([card(rung="announce")], "routine", t)
    [(kind, data)] = sel.start_veto("skill:wave", t, 1.0)  # clamped up to the 3 s floor
    assert kind == "selection.auto_started" and data["total_s"] == 3.0 and data["until"] == t + 3.0
    snap = sel.snapshot(t + 1)
    assert snap["veto_until"] == t + 3.0 and snap["auto"] == {"card_id": "skill:wave", "total_s": 3.0, "remaining_s": 2.0}
    assert sel.veto_due(t + 2.9) is None
    ticket = sel.veto_due(t + 3.0)
    assert ticket is not None and ticket.by == "veto_window" and ticket.step == "accept"
    assert sel.snapshot(t + 3.1)["auto"] == {"card_id": "skill:wave", "total_s": 3.0, "remaining_s": 0.0}
    assert sel.veto_due(t + 4) is None  # taken once
    events = sel.finish(ticket, True, "ok", True, t + 3.2)
    assert events[0][1]["by"] == "veto_window" and sel.snapshot(t + 4)["task"]["auto"] is True


def test_any_input_in_the_window_cancels_the_ring_and_a_brace_vetoes_quietly():
    sel = Selection(INTENT, require_ack=False)
    t = 100.0
    sel.show([card(rung="announce")], "routine", t)
    sel.start_veto("skill:wave", t, 5.0)
    ok, _, events = sel.step("double_twitch", t + 1)
    assert ok and events[0][0] == "selection.auto_cancelled" and sel.snapshot(t + 1)["auto"] is None
    assert sel.veto_due(t + 10) is None  # the person touched it: no timer approval any more
    sel.hide(t, "x")
    sel.show([card(rung="announce")], "routine", t)
    sel.start_veto("skill:wave", t, 5.0)
    ok, _, events = sel.dismiss("brace", t + 2)
    names = [k for k, _ in events]
    assert ok and names == ["selection.auto_cancelled", "selection.dismiss", "task.abandoned"]
    assert events[1][1]["vetoed"] is True and events[2][1]["outcome"] == "vetoed"
    assert sel.menu is None and sel.task is None


def test_supersede_only_drops_an_untouched_menu():
    sel = Selection(INTENT, require_ack=False)
    sel.show([card(rung="ask")], "fallback", 1.0)
    sel.step("double_twitch", 2.0)
    assert sel.supersede(3.0, "routine") == [] and sel.menu is not None
    sel.hide(3.0, "x")
    sel.show([card(rung="ask")], "fallback", 4.0)
    [(kind, data)] = sel.supersede(5.0, "routine due")
    assert kind == "task.abandoned" and data["outcome"] == "superseded" and sel.wants_menu(5.0)


# ── the app ────────────────────────────────────────────────────────────────────


def proactive(h, tmp_path, clock, text=ROUTINES, announce=False, **over):
    """Selection on, a Proposer on a temp routines file with a fake wall clock."""
    enable(h)
    prove_empty(h)
    (tmp_path / "routines.yaml").write_text(text)
    cfg = {**h.cfg, "proactive": {"enabled": True, "propose_min_gap_s": 0, "veto_window_s": 3.0, **over},
           "trust": {"announce_enabled": announce, "actions": {"sip": "never"}}}
    h.proactive = Proposer(h, cfg, wall=clock, routines_path=tmp_path / "routines.yaml",
                           state_dir=tmp_path / "state")
    return h.proactive


def tick(h, n=3):
    for _ in range(n):
        h._selection_tick()
    return h.selection.menu


def run(h, seconds, until=None, tick=False):
    """tests.test_app_embodied.run, optionally ticking the selection loop each step (the
    app's loop thread is not running in tests)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if tick:
            h._selection_tick()
        if base.run(h, 0.1, until=until):
            if until is not None and until():
                return True
    return bool(until()) if until is not None else False


def test_a_routine_due_becomes_a_card_with_its_rows_lit_and_the_packet_published(armed, tmp_path):
    clock = Clock()
    p = proactive(armed, tmp_path, clock)
    menu = tick(armed)
    assert menu is not None and menu.highlighted.id == "pick_place:cup"  # the plain menu first
    assert menu.highlighted.routine is None
    snap = armed.snapshot()
    pk = snap["context"]
    assert pk["clock"] == "12:00 Tue" and [o["name"] for o in pk["objects"]] == ["paper cup", "wooden block", "phone"]
    assert pk["objects"][0]["slot"] == "1" and pk["present"] == [] and pk["hand"] is None and pk["lit"] == []
    assert snap["scene"]["packet"] == pk and snap["calls"] == [] and snap["selection"]["auto"] is None
    assert [r["name"] for r in snap["routines"]["routines"]] == ["water", "check", "hello"]
    clock.t += 3601  # check (due at 30 min, announce off -> a card) and water come due: the
    # untouched menu is superseded and the routine cards lead, oldest due first
    menu = tick(armed)
    assert menu is not None and [c.id for c in menu.cards] == ["skill:wave", "skill:bow", "pick_place:cup"]
    assert [c.routine for c in menu.cards] == ["check", "hello", "water"]
    hl, water = menu.highlighted, menu.cards[2]
    assert hl.rung == "ask" and hl.tool_call == ("skill", {"name": "wave"})
    assert water.rung == "ask" and water.label == "Water?" and water.tool_call == ("pick_place", {"object_id": "cup"})
    rows = [e["row"] for e in water.evidence]
    assert rows == ["routines", "clock"] and "every 60 min" in water.evidence[0]["text"]
    snap = armed.snapshot()
    assert snap["context"]["lit"] == ["routines", "clock"]
    assert snap["selection"]["cards"][2]["evidence"][0]["row"] == "routines"
    assert snap["selection"]["cards"][2]["routine"] == "water" and snap["selection"]["veto_until"] is None
    assert any(e["kind"] == "task.abandoned" and e["outcome"] == "superseded" for e in snap["events"])
    assert {e["name"] for e in kinds(armed, "routine.proposed")} == {"check", "hello", "water"}
    assert p.book.get("water").next_due == clock.t + 3600  # proposed: moves on by one interval


def test_an_ask_card_never_runs_from_time_alone(armed, tmp_path):
    clock = Clock()
    proactive(armed, tmp_path, clock, text='routines:\n  water: {action: pick_place, object: cup, every: 60m, label: "Water?"}\n',
              announce=True)
    clock.t += 3601
    menu = tick(armed)
    assert menu.highlighted.routine == "water" and menu.highlighted.rung == "ask"
    assert menu.veto_until is None and armed.selection.veto_due(now() + 1000) is None
    assert not run(armed, 5, until=lambda: started(armed), tick=True)
    assert armed.pending is None and armed.selection.menu is not None and not kinds(armed, "selection.accept")


def test_announce_routine_rings_then_runs_through_the_gate_when_untouched(armed, tmp_path):
    clock = Clock()
    p = proactive(armed, tmp_path, clock, announce=True)
    clock.t += 1801  # check (bow, announce) is due
    menu = tick(armed)
    assert menu is not None and menu.highlighted.id == "skill:wave" and menu.highlighted.rung == "announce"
    assert menu.veto_until is not None and menu.veto_total_s == 3.0
    snap = armed.snapshot()
    assert snap["selection"]["auto"]["card_id"] == "skill:wave" and 0 < snap["selection"]["auto"]["remaining_s"] <= 3.0
    assert snap["selection"]["veto_until"] == menu.veto_until
    assert kinds(armed, "routine.announced")[-1]["say"] == "Checking the desk."
    assert wait(lambda: armed.spoken and "Brace to cancel" in armed.spoken[-1])
    assert not started(armed)
    # nothing touches it: the ring lapses, the card goes through the tool path and the gate
    assert run(armed, 8, until=lambda: started(armed), tick=True), kinds(armed, "selection.accept")[-1]["detail"]
    acc = kinds(armed, "selection.accept")[-1]
    assert acc["ok"] and acc["answered"] and acc["by"] == "veto_window" and acc["card"] == "skill:wave"
    assert kinds(armed, "routine.auto_run")[-1]["name"] == "check"
    assert armed.last["contract"]["utterance"] == "wave" and armed.last["contract"]["evidence"]["confirmed_veto_window"] == 1.0
    assert armed.last["decision"]["verdict"] == "approve"  # the gate's re-check with confirmed=True
    assert run(armed, 60, until=lambda: armed.arm.state is ArmState.READY, tick=True)
    armed._selection_tick()
    done = kinds(armed, "task.completed")[-1]
    assert done["card"] == "skill:wave" and done["auto"] is True
    assert p.book.get("check").runs == 1 and kinds(armed, "routine.done")[-1]["auto"] is True


def test_a_brace_in_the_ring_cancels_quietly_and_writes_dont_propose(armed, tmp_path):
    clock = Clock()
    p = proactive(armed, tmp_path, clock, announce=True)
    clock.t += 1801
    menu = tick(armed)
    assert menu.highlighted.id == "skill:wave" and menu.veto_until is not None
    incidents = len(kinds(armed, "incident.triggered"))
    armed.log.emit("emg.brace", trigger="cocontraction")
    assert wait(lambda: armed.selection.menu is None)
    ev = {e.kind for e in armed.log.recent(100, {"selection.auto_cancelled", "selection.dismiss", "task.abandoned",
                                               "routine.refused", "incident.triggered"})}
    assert {"selection.auto_cancelled", "selection.dismiss", "task.abandoned", "routine.refused"} <= ev
    assert len(kinds(armed, "incident.triggered")) == incidents  # no incident: the arm never moved
    assert kinds(armed, "task.abandoned")[-1]["outcome"] == "vetoed"
    assert armed.arm.state is ArmState.READY and not started(armed)
    ref = kinds(armed, "routine.refused")[-1]
    assert ref["vetoed"] is True and ref["dont_propose"] == "don't propose Check the desk? at the desk in the afternoon"
    assert p.book.blocked("wave") and p.book.get("check").refusals == 1
    assert armed.snapshot()["quiet"]["text"].startswith("Noted.")
    assert armed.snapshot()["context"]["rules"][-1]["text"].startswith("don't propose Check the desk?")
    # cooled down and blocked in this context: forcing it due proposes nothing
    p.book.force_due("check")
    armed.selection._last_veto_t = -math.inf
    armed.selection._quiet_until = 0.0  # past the reshow pause after the abandoned task
    armed._last_stop_t = -math.inf
    for _ in range(5):
        armed._selection_tick()
    menu = armed.selection.menu
    assert menu is not None and all(c.routine != "check" for c in menu.cards)


def test_announce_falls_back_to_a_card_when_the_relaxation_is_off(armed, tmp_path):
    clock = Clock()
    proactive(armed, tmp_path, clock, announce=False)
    clock.t += 1801
    menu = tick(armed)
    assert menu.highlighted.id == "skill:wave" and menu.highlighted.rung == "ask" and menu.veto_until is None


def test_nothing_is_proposed_in_quiet_hours_or_request_only_or_after_a_brace(armed, tmp_path):
    clock = Clock(time.mktime((2026, 11, 3, 23, 30, 0, 0, 0, -1)))
    p = proactive(armed, tmp_path, clock, text=ROUTINES + 'quiet_hours: "23:00-07:00"\n', announce=True)
    clock.t += 3601
    menu = tick(armed)
    assert menu is not None and all(c.routine is None for c in menu.cards)  # the plain menu only
    assert armed.snapshot()["proactive"]["quiet_now"] is True
    clock.t = NOON + 3601
    p.set_request_only(True)
    armed._emit_selection(armed.selection.supersede(now(), "test"))
    menu = tick(armed)
    assert all(c.routine is None for c in menu.cards)
    p.set_request_only(False)
    armed.log.emit("emg.brace", trigger="cocontraction")  # idle brace: refuses a card, holds 3 s
    time.sleep(0.1)
    menu = tick(armed)
    assert menu is None or all(c.routine is None for c in menu.cards)


def test_another_speakers_request_becomes_a_card_the_wearer_decides(armed, tmp_path):
    clock = Clock()
    proactive(armed, tmp_path, clock, announce=True)
    tick(armed)
    armed.log.emit("speech.utterance", text="can you pass him the wooden block", speaker="X", asked_by="X", source="voice")
    menu = tick(armed, 4)
    hl = menu.highlighted
    assert hl.id == "pick_place:block" and hl.asked_by == "X" and hl.rung == "ask"
    assert hl.label == "X asked: bring wooden block" and hl.evidence[0]["row"] == "present"
    snap = armed.snapshot()
    assert snap["selection"]["asked_by"] == "X" and snap["selection"]["cards"][0]["asked_by"] == "X"
    assert snap["context"]["lit"] == ["present"]


def test_a_sound_triggers_its_routine_and_the_ranker_gets_the_packet(armed, tmp_path, monkeypatch):
    clock = Clock()
    text = ROUTINES + '  phone: {action: hold_up, object: ball, "on": phone_buzz, mode: ask, label: "Show you the phone?"}\n'
    proactive(armed, tmp_path, clock, text=text)
    seen: dict = {}

    class Spy:
        model = "fake-spark"
        available = True

        def chat(self, model, messages):
            import json

            seen.update(json.loads(messages[1]["content"]))
            return json.dumps({"choices": [{"option": "o1", "confidence": "likely", "reason": "the phone just buzzed"}]})

    armed.ranker.client = Spy()
    armed.log.emit("sound.event", label="phone buzz")
    menu = tick(armed, 4)
    assert menu.highlighted.id == "skill:hold_up:ball" and menu.highlighted.routine == "phone"
    assert menu.highlighted.evidence[0] == {"row": "sounds", "text": "phone buzz", "age_s": 0}
    assert wait(lambda: menu.source == "muse")
    assert seen["options"][0]["why"] == "just now: phone buzz -> routine phone is due (on phone buzz)"
    assert seen["context"]["sounds"] == ["phone buzz"]
    snap = armed.snapshot()  # the highlighted card keeps its identity; its reason is layered on in the snapshot
    assert snap["selection"]["cards"][0]["evidence"][-1] == {"row": "rank", "text": "the phone just buzzed",
                                                             "source": "muse-spark-1.3"}
    calls = snap["calls"]
    assert calls and calls[-1]["model"] == "fake-spark" and calls[-1]["for"] == "sounds" and "ms" in calls[-1]


def test_a_typed_request_for_a_never_action_is_rejected_by_the_gate(armed, tmp_path):
    proactive(armed, tmp_path, Clock())
    armed.on_utterance("sip", source="typed")
    assert armed.last["decision"]["verdict"] == "reject" and "never" in armed.last["decision"]["reason"]
    assert armed.pending is None and not started(armed)


def test_routines_api_reads_edits_and_never_moves(armed, tmp_path):
    clock = Clock()
    p = proactive(armed, tmp_path, clock)
    c = TestClient(create_app(armed, token=TOKEN), base_url="http://127.0.0.1")
    body = c.get("/api/routines").json()
    assert body["enabled"] and [r["name"] for r in body["routines"]] == ["water", "check", "hello"]
    assert c.post("/api/routines", json={"name": "water", "every": "2h"}).status_code == 403  # token
    r = c.post("/api/routines", json={"name": "water", "every": "2h", "mode": "auto"}, headers=GOOD)
    assert r.status_code == 200 and p.book.get("water").every_s == 7200 and p.book.get("water").mode == "announce"
    assert "every: 2h" in (tmp_path / "routines.yaml").read_text()
    assert c.post("/api/routines", json={"name": "water", "action": "fly"}, headers=GOOD).status_code == 400
    assert c.post("/api/routines", json={"run_now": "nope"}, headers=GOOD).status_code == 404
    assert c.post("/api/routines", json={"run_now": "hello"}, headers=GOOD).status_code == 200
    assert p.book.get("hello").next_due == clock.t
    assert c.post("/api/routines", json={"request_only": True}, headers=GOOD).json()["request_only"] is True
    assert c.post("/api/routines", json={"name": "hello", "delete": True}, headers=GOOD).status_code == 200
    assert p.book.get("hello") is None and not started(armed) and armed.pending is None
    armed.proactive = None
    assert c.get("/api/routines").json()["enabled"] is False
    assert c.post("/api/routines", json={"run_now": "x"}, headers=GOOD).status_code == 409


def test_option_ids_keep_skills_on_objects_apart():
    a = Option(("skill", "hold_up:ball"), "skill", "x", object_id="ball", skill="hold_up")
    b = Option(("skill", "hold_up"), "skill", "x", skill="hold_up")
    assert a.id == "skill:hold_up:ball" and b.id == "skill:hold_up"


@pytest.mark.parametrize("rung", ["ask", "never"])
def test_start_veto_is_refused_for_cards_the_person_did_not_pre_approve(rung):
    sel = Selection(INTENT, require_ack=False)
    sel.show([card(rung=rung)], "routine", 1.0)
    assert sel.start_veto("skill:wave", 1.0, 5.0)[0][1]["ok"] is False and sel.veto_due(100.0) is None


def test_snapshot_keeps_the_wonder_calls_beside_the_spark_calls(armed, tmp_path):
    """The proactive layer appends its Spark calls to the vision, speech and Wonder calls; it never replaces them."""
    proactive(armed, tmp_path, Clock())
    armed.learner.calls.append({"model": "muse-wonder-1.0", "for": "investigate", "ms": 12, "started_at_ms": 1})
    armed.proactive.calls.note({"model": "muse-spark-1.3", "for": "rank", "ms": 8})
    models = {c["model"] for c in armed.snapshot()["calls"]}
    assert {"muse-wonder-1.0", "muse-spark-1.3"} <= models
