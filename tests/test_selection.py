"""Reflex cards wired into the app (virtual arm, synthetic EMG events, mocked Muse).

Covers: selection ships off; a twitch selects only the highlighted card, only
``reaction_s`` after the UI acknowledged rendering it (no ack refuses) and never within
``after_veto_s`` of a brace or open hand (swallowed ones too); a motion card is announced
and only a second twitch ``reaction_s`` after the announcement confirms it, through the
same tool path, gate and confirmation (a carry still asks); a double twitch steps, or goes
back from "confirm?"; a Muse upgrade never changes the highlighted card; cards are rebuilt
after reset + arm and refused while an incident is captured or explained; a twitch-band
burst onset freezes a moving arm first; a confirm race won by another answer keeps the
task's contract; a brace or open hand on an idle arm dismisses, once per reaction; a brace
during motion still freezes first and latches; a twitch never confirms another request, a
stale card or a rule; the scanning baseline; degradation to scanning; per-task counters;
the tokened UI endpoints. The reviewer repros R1-R4 and R6 are kept as positive tests
(``test_R*``)."""

from __future__ import annotations

import json
import math
import threading
import time

import pytest
from fastapi.testclient import TestClient

import h2m.app as app_mod
from h2m.core import load_config, now
from h2m.intent import Candidate, SparkClient
from h2m.learning import RuleProposal
from h2m.robot import ArmState
from h2m.robot.skills import plan_home
from h2m.rules import Rule
from h2m.selection import INPUTS, INTENT, MODES, Selection
from h2m.sensors import emg
from h2m.server import create_app
from tests import test_app_embodied as base
from tests.test_app_embodied import prove_empty, run, started
from tests.test_twitch import Rig as EmgRig

h2m = base.h2m  # the shared app fixture: virtual arm, EMG / vision / speech off

TOKEN = "t0k"
GOOD = {"X-H2M-Token": TOKEN}
PICKS = ["pick_place:cup", "pick_place:block", "pick_place:ball"]
LOOKS = ["look_at:cup", "look_at:block", "look_at:ball"]
ANSWERS = {"selection.accept", "selection.select"}


class FakeSpark:
    """Muse Spark stand-in for the ranker."""

    model = "fake"

    def __init__(self, reply="", delay=0.0, available=True):
        self.reply, self.delay, self.available = reply, delay, available
        self.calls = 0

    def chat(self, model, messages):
        self.calls += 1
        time.sleep(self.delay)
        return self.reply


def choose(*picks):
    return json.dumps({"choices": [{"option": o, "confidence": c} for o, c in picks]})


def enable(h, **over):
    """Selection on, as a bench run would set it (headless: no UI ack unless asked)."""
    sel = {**h.cfg["selection"], "enabled": True, "require_ack": False, **over}
    h._build_selection({**h.cfg, "selection": sel})
    h.ranker.client = None  # no Muse unless a test mocks it
    h.say_s = 0.05  # each fake `say` plays briefly, so an announcement finishes
    return h


@pytest.fixture
def h(h2m):
    enable(h2m)
    prove_empty(h2m)  # the plain confirmation; see the unknown-gripper test for the other
    return h2m


def kinds(h, kind):
    return [e.data for e in h.log.recent(500, {kind})]


def answers(h):
    return [{**e.data, "kind": e.kind} for e in h.log.recent(500, ANSWERS)]


def wait(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return bool(pred())


def show(h):
    h._selection_tick()
    return h.selection.menu


def ids(menu):
    return [c.id for c in menu.cards]


def announced(menu):
    return wait(lambda: math.isfinite(menu.announce_t))


def twitch(h, reaction_s=1.0, detect_ms=650):
    """A twitch whose burst began ``reaction_s`` after the clock it is gated on (the
    highlight and its ack, or the announcement of a card asking "confirm?"); waits for
    its answer (a selection.select or selection.accept event)."""
    menu = h.selection.menu
    onset = now() - detect_ms / 1000
    if menu is not None:
        if menu.confirming is not None:
            assert announced(menu)
            menu.announce_t = onset - reaction_s
        else:
            menu.highlighted_t = onset - reaction_s
            if menu.ack_t is not None:
                menu.ack_t = onset - reaction_s
    n = len(answers(h))
    h.log.emit("emg.twitch", held_ms=200, detect_ms=detect_ms)
    assert wait(lambda: len(answers(h)) > n)
    return answers(h)[-1]


def accept(h, **kw):
    """Select the highlighted motion card, then confirm it with a second twitch."""
    sel = twitch(h, **kw)
    assert sel["kind"] == "selection.select" and sel["ok"], sel
    return twitch(h, **kw)


def forget_vetoes(h):
    h.selection._last_veto_t = -math.inf


def complete(h):
    assert run(h, 90, until=lambda: h.arm.state is ArmState.READY)
    h._selection_tick()


# ── shipped config (fix 1) ─────────────────────────────────────────────────────
def test_selection_ships_off_with_the_ack_and_the_slower_reaction(h2m):
    cfg = load_config()["selection"]
    assert cfg["enabled"] is False and cfg["require_ack"] is True
    assert cfg["reaction_s"] >= 0.7 and cfg["after_veto_s"] >= 3.0
    assert h2m.selection_enabled is False  # the shared fixture uses the shipped config
    assert h2m.selection.require_ack and h2m.selection.reaction_s == pytest.approx(0.7)
    h2m.ranker.client = None
    assert show(h2m) is None
    h2m.on_utterance("hand me the block", source="typed")
    pending = h2m.pending
    h2m.log.emit("emg.twitch", held_ms=200, detect_ms=900)
    time.sleep(0.1)
    assert h2m.pending is pending and not started(h2m) and not answers(h2m)


def test_config_can_only_make_the_selection_gates_stricter(h2m):
    enable(h2m, reaction_s=0.05, after_veto_s=0.0, require_ack="no", confirm_s=600)
    sel = h2m.selection
    assert sel.reaction_s == pytest.approx(0.3)  # MIN_CONFIRM_REACTION_S floor
    assert sel.after_veto_s == pytest.approx(3.0) and sel.confirm_s == pytest.approx(30.0)
    assert sel.require_ack is True  # only the literal false turns the ack off


# ── the UI render acknowledgement (fix 1) ──────────────────────────────────────
def test_the_reaction_clock_starts_at_the_ui_ack_of_that_exact_card(h):
    enable(h, require_ack=True)
    menu = show(h)
    menu.highlighted_t -= 10  # up for ages on the server, but never acknowledged
    h.log.emit("emg.twitch", held_ms=200, detect_ms=650)
    assert wait(lambda: answers(h))
    assert not answers(h)[-1]["ok"] and "has not shown" in answers(h)[-1]["detail"]
    assert h.selection_ack("pick_place:ball", menu.id)[0] is False  # not the highlighted card
    assert h.selection_ack("pick_place:cup", menu.id + 1)[0] is False  # another menu
    assert not h.selection_snapshot()["acked"]
    ok, _ = h.selection_ack("pick_place:cup", menu.id)
    assert ok and h.selection_snapshot()["acked"]
    # Began 0.5 s after the ack: inside reaction_s (0.7) of the render, refused.
    menu.ack_t = now() - 0.65 - 0.5
    h.log.emit("emg.twitch", held_ms=200, detect_ms=650)
    assert wait(lambda: "too soon" in answers(h)[-1]["detail"])
    menu.ack_t = now() - 0.65 - 0.8
    n = len(answers(h))
    h.log.emit("emg.twitch", held_ms=200, detect_ms=650)
    assert wait(lambda: len(answers(h)) > n)
    assert answers(h)[-1]["kind"] == "selection.select"
    # Back to the menu: a new highlight needs a new ack.
    h.log.emit("emg.double_twitch")
    assert menu.confirming is None and menu.ack_t is None
    menu.highlighted_t -= 10
    h.log.emit("emg.twitch", held_ms=200, detect_ms=650)
    assert wait(lambda: "has not shown" in answers(h)[-1]["detail"])
    assert not started(h) and h.pending is None


def test_default_reaction_is_slower_than_the_squeeze_gate(h):
    show(h)
    acc = twitch(h, reaction_s=0.5)  # passed the old 0.3 s gate
    assert not acc["ok"] and "too soon" in acc["detail"]
    assert twitch(h, reaction_s=0.75)["kind"] == "selection.select"


# ── R1 / fix 2: no accept within after_veto_s of any veto ─────────────────────
def test_R1_one_startle_refuses_a_card_but_its_twitch_never_takes_the_next(h):
    """Recorded startle: open hand +0.76 s, open hand +1.11 s (swallowed by the
    refractory), twitch emitted +2.62 s with detect_ms 605 (onset +2.015 s)."""
    menu = show(h)
    assert menu.highlighted.id == "pick_place:cup"
    h.log.emit("emg.open_hand", extensor=0.687)  # +0.76: refuses the cup
    time.sleep(0.35)
    h.log.emit("emg.open_hand", extensor=0.758)  # +1.11: same reaction, swallowed
    assert ids(menu) == PICKS[1:]
    menu.highlighted_t -= 5  # whatever the highlight clock says
    hl = menu.hl
    time.sleep(1.51)
    h.log.emit("emg.twitch", held_ms=250, detect_ms=605)  # +2.62
    assert wait(lambda: answers(h))
    [acc] = answers(h)
    assert not acc["ok"] and "refusal" in acc["detail"]
    assert menu.confirming is None and menu.hl > hl  # refused AND re-highlighted
    assert h.selection.menu.highlighted_t > now() - 0.5
    time.sleep(0.2)
    assert not started(h) and h.pending is None and h.arm.state is ArmState.READY
    forget_vetoes(h)  # 3 s later (and a fresh reaction) the block can be selected
    assert twitch(h)["card"] == "pick_place:block"


def test_a_veto_that_froze_the_arm_also_holds_off_the_next_accept(h):
    show(h)
    assert accept(h)["ok"] and h.arm.state is ArmState.MOVING
    h.log.emit("emg.brace", onset_t=now())
    assert h.arm.state is ArmState.STOPPED
    h.learner._queue.get_nowait()  # the incident worker is not running in tests
    h.selection._quiet_until = 0.0
    menu = show(h)
    assert menu is not None
    acc = twitch(h)
    assert not acc["ok"] and "refusal" in acc["detail"]


# ── fix 3: two steps for a motion card ─────────────────────────────────────────
def test_first_twitch_only_selects_and_announces_and_a_second_confirms(h):
    menu = show(h)
    sel = twitch(h)
    assert sel["kind"] == "selection.select" and sel["card"] == "pick_place:cup"
    assert announced(menu)
    [said] = kinds(h, "selection.announced")
    assert said["card"] == "pick_place:cup" and said["text"].startswith("Bring")
    assert h.spoken[-1] == said["text"] and h.reply == said["text"]
    snap = h.selection_snapshot()
    assert snap["confirming"] == "pick_place:cup" and snap["cards"][0]["confirm"]
    assert not started(h) and h.pending is None and h.arm.state is ArmState.READY
    # Inside reaction_s of the announcement: refused, still asking.
    acc = twitch(h, reaction_s=0.4)
    assert not acc["ok"] and "too soon after I asked" in acc["detail"]
    assert menu.confirming is not None and not started(h)
    acc = twitch(h)
    assert acc["kind"] == "selection.accept" and acc["ok"] and acc["answered"]
    [s] = started(h)
    assert s["object_id"] == "cup" and s["confirmed_by"] == "twitch"


def test_double_twitch_while_asking_goes_back_to_the_menu(h):
    menu = show(h)
    assert twitch(h)["kind"] == "selection.select" and announced(menu)
    t0 = menu.highlighted_t
    h.log.emit("emg.double_twitch", interval_ms=300)
    assert menu.confirming is None and menu.cursor == 0  # back, same card
    assert menu.highlighted_t > t0 and math.isinf(menu.announce_t)
    [step] = kinds(h, "selection.step")
    assert step["back"] and step["card"] == "pick_place:cup"
    h.log.emit("emg.twitch", held_ms=200, detect_ms=650)  # needs a fresh reaction
    assert wait(lambda: "too soon" in answers(h)[-1]["detail"])
    h.log.emit("emg.double_twitch", interval_ms=300)  # on the menu it steps on
    assert menu.cursor == 1
    assert not started(h) and h.pending is None


def test_R6_a_missed_second_burst_only_selects_and_moves_nothing(h):
    """An intended double twitch read as ONE twitch: it selects the card and the arm asks;
    the person's next double twitch takes it back. Nothing moves."""
    menu = show(h)
    assert twitch(h)["kind"] == "selection.select"
    assert announced(menu) and not started(h) and h.pending is None
    h.log.emit("emg.double_twitch", interval_ms=250)
    assert menu.confirming is None
    run(h, 1.0)
    assert not started(h) and h.arm.state is ArmState.READY


def test_an_unanswered_confirm_lapses_back_to_the_menu(h):
    menu = show(h)
    assert twitch(h)["kind"] == "selection.select" and announced(menu)
    menu.announce_t -= h.selection.confirm_s + 1
    h._selection_tick()
    assert menu.confirming is None
    assert kinds(h, "selection.lapsed")[0]["card"] == "pick_place:cup"
    assert not started(h)


def test_a_brace_while_asking_refuses_the_card(h):
    menu = show(h)
    assert twitch(h)["kind"] == "selection.select" and announced(menu)
    h.log.emit("emg.brace", onset_t=now())
    assert menu.confirming is None and ids(menu) == PICKS[1:]
    assert h.arm.state is ArmState.READY and not started(h)


# ── review 2, item 1: the confirm clock starts when the question finished ──────
def unselected(h):
    return kinds(h, "selection.unselected")


def test_N1_nothing_confirms_while_the_question_is_still_playing(h):
    """`say` is non-blocking: the confirm clock must start when the question finished
    playing, not when it was spawned. A burst 0.7 s after the select, with the question
    still playing, is refused; the clock starts at the end of the speech."""
    h.say_s = 1.0
    menu = show(h)
    t_select = now()
    assert twitch(h)["kind"] == "selection.select"
    assert wait(lambda: h.embodied.speech.speaking())
    time.sleep(0.75)
    assert h.embodied.speech.speaking() and math.isinf(menu.announce_t)
    n = len(answers(h))
    h.log.emit("emg.twitch", held_ms=200, detect_ms=0)  # onset select + 0.75: question on
    assert wait(lambda: len(answers(h)) > n)
    assert not answers(h)[-1]["ok"] and "still asking" in answers(h)[-1]["detail"]
    assert announced(menu)
    assert not h.embodied.speech.speaking()
    assert menu.announce_t - t_select >= 0.95  # the end of the speech, not its start
    assert not started(h) and h.pending is None
    assert twitch(h)["ok"] and started(h)  # reaction_s after the end: confirms


@pytest.mark.parametrize("how", ["voice_off", "say_missing"])
def test_N2_an_unspoken_announcement_undoes_the_select(h, how):
    """Voice output off, or `say` missing: the question is never heard, so the select is
    undone and no twitch can confirm the card."""
    if how == "voice_off":
        h.embodied.speech.enabled = False
    else:
        def missing(argv):
            raise OSError("no say")
        h.embodied.speech._spawn = missing
    menu = show(h)
    assert twitch(h)["kind"] == "selection.select"
    assert wait(lambda: unselected(h))
    assert menu.confirming is None and math.isinf(menu.announce_t)
    assert "aloud" in unselected(h)[-1]["reason"] and "aloud" in h.reply
    assert not kinds(h, "selection.announced") and h.spoken == []
    acc = twitch(h)  # the next twitch is a fresh select, never a confirm
    assert acc["kind"] == "selection.select"
    time.sleep(0.2)
    assert not started(h) and h.pending is None and h.arm.state is ArmState.READY


def test_N3_a_failing_announcement_unselects_instead_of_pinning_confirm(h, monkeypatch):
    def boom(text):
        raise RuntimeError("say failed")

    monkeypatch.setattr(h, "_say", boom)
    menu = show(h)
    assert twitch(h)["kind"] == "selection.select"
    assert wait(lambda: unselected(h))
    assert menu.confirming is None and "RuntimeError" in unselected(h)[-1]["reason"]
    assert h.selection.menu is menu and not started(h)


def test_a_question_cut_off_by_other_speech_undoes_the_select(h):
    h.say_s = 0.4
    menu = show(h)
    assert twitch(h)["kind"] == "selection.select"
    assert wait(lambda: h.embodied.speech.speaking())
    h._say("Something else entirely.")  # replaces the question mid-sentence
    assert wait(lambda: unselected(h))
    assert "cut off" in unselected(h)[-1]["reason"] and menu.confirming is None
    assert not kinds(h, "selection.announced") and not started(h)


def test_a_question_that_never_finishes_is_undone_at_the_cap(h):
    h.say_s = None  # plays until cut off
    h.selection_cfg = {**h.selection_cfg, "announce_max_s": 0.3}
    menu = show(h)
    assert twitch(h)["kind"] == "selection.select"
    assert wait(lambda: unselected(h))
    assert "too long" in unselected(h)[-1]["reason"] and menu.confirming is None
    assert math.isinf(menu.announce_t) and not started(h)


def test_a_stop_while_the_question_plays_never_announces_it(h):
    h.say_s = 1.0
    menu = show(h)
    assert twitch(h)["kind"] == "selection.select"
    assert wait(lambda: h.embodied.speech.speaking())
    h.stop("stop")  # cuts the speech off and bumps the epoch
    time.sleep(0.2)
    assert not kinds(h, "selection.announced") and math.isinf(menu.announce_t)
    assert not started(h)


def base_card(card_id):
    kind, target = card_id.split(":")
    return Candidate(card_id, kind, f"Bring {target}", "maybe", "fallback",
                     {"verb": kind, "object_id": target}, (kind, {"object": target}), target)


def test_review4_a_confirm_never_announced_lapses_confirm_s_after_the_select():
    """Item 4: a "confirm?" whose announcement never finished still lapses."""
    sel = Selection(INTENT, require_ack=False, confirm_s=8.0)
    cards = [base_card("pick_place:cup")]
    sel.show(cards, "fallback", 0.0)
    ticket, _, _ = sel.take("twitch", 1.0, 1.0)
    assert ticket is not None and sel.menu.confirming is not None
    assert math.isinf(sel.menu.announce_t) and sel.menu.select_t == 1.0
    assert sel.expire(8.9) == []
    [(kind, data)] = sel.expire(9.1)
    assert kind == "selection.lapsed" and sel.menu.confirming is None


def test_require_ack_the_confirm_needs_a_fresh_ack_and_counts_from_it(h):
    """With require_ack the select is a new highlight: the UI must ack the "confirm?"
    rendering, and the confirm clock is max(announcement end, ack) + reaction_s."""
    enable(h, require_ack=True)
    menu = show(h)
    assert h.selection_ack(menu.highlighted.id, menu.id, menu.hl)[0]
    menu.highlighted_t -= 5
    menu.ack_t -= 5
    hl = menu.hl
    h.log.emit("emg.twitch", held_ms=200, detect_ms=650)
    assert wait(lambda: answers(h)) and answers(h)[-1]["kind"] == "selection.select"
    assert announced(menu)
    assert menu.hl == hl + 1 and menu.ack_t is None  # the select cleared the ack
    menu.announce_t -= 5  # the question ended long ago ...
    n = len(answers(h))
    h.log.emit("emg.twitch", held_ms=200, detect_ms=650)
    assert wait(lambda: len(answers(h)) > n)
    assert "has not shown the question" in answers(h)[-1]["detail"]  # ... but no ack
    assert h.selection_ack(menu.highlighted.id, menu.id, menu.hl)[0]
    n = len(answers(h))
    h.log.emit("emg.twitch", held_ms=200, detect_ms=650)  # began before the ack
    assert wait(lambda: len(answers(h)) > n)
    assert "too soon after I asked" in answers(h)[-1]["detail"]
    assert h.selection_snapshot()["accept_in_s"] > 0
    menu.ack_t -= 2
    n = len(answers(h))
    h.log.emit("emg.twitch", held_ms=200, detect_ms=650)
    assert wait(lambda: len(answers(h)) > n)
    assert answers(h)[-1]["ok"] and started(h)


def test_H1_an_ack_is_pinned_to_the_highlight_generation(h):
    """A late ack of the menu rendering (same card id) must not ack the "confirm?"
    rendering that replaced it."""
    enable(h, require_ack=True)
    menu = show(h)
    drawn = menu.hl  # the UI drew this generation and sent its ack ...
    assert h.selection_ack(menu.highlighted.id, menu.id, drawn)[0]
    menu.highlighted_t -= 5
    menu.ack_t -= 5
    h.log.emit("emg.twitch", held_ms=200, detect_ms=650)
    assert wait(lambda: answers(h)) and announced(menu)
    # ... and a duplicate of it arrives after the select bumped the generation.
    ok, why = h.selection_ack(menu.highlighted.id, menu.id, drawn)
    assert not ok and "highlight changed" in why and menu.ack_t is None
    for bad in (True, "2", 2.0):
        assert not h.selection_ack(menu.highlighted.id, menu.id, bad)[0]
    assert h.selection_ack(menu.highlighted.id, menu.id, menu.hl)[0]
    assert menu.ack_t is not None


# ── accept ─────────────────────────────────────────────────────────────────────
def test_cards_go_up_when_idle_with_the_top_one_highlighted(h):
    menu = show(h)
    assert ids(menu) == PICKS and menu.cursor == 0 and menu.source == "fallback"
    [shown] = kinds(h, "selection.shown")
    assert shown["cards"] == PICKS and shown["mode"] == "intent" and not shown["upgraded"]
    snap = h.selection_snapshot()
    assert snap["highlighted"] == "pick_place:cup" and snap["blocked"] is None
    assert [c["highlighted"] for c in snap["cards"]] == [True, False, False]


def test_twitch_accepts_the_highlighted_card_through_the_tool_path_and_gate(h):
    show(h)
    acc = accept(h)
    assert acc["ok"] and acc["by"] == "twitch" and acc["card"] == "pick_place:cup"
    [s] = started(h)
    assert s["verb"] == "pick_place" and s["object_id"] == "cup" and s["confirmed_by"] == "twitch"
    cup = [e for e in kinds(h, "intent") if e["contract"]["object_id"] == "cup"]
    # The tool call was gated and asked (a carry always asks); the twitch answered it and
    # the re-check with confirmed=True approved.
    assert cup[0]["tool"] == "pick_place" and cup[0]["decision"]["verdict"] == "ask"
    assert cup[-1]["confirmed_by"] == "twitch" and cup[-1]["decision"]["verdict"] == "approve"
    assert cup[-1]["contract"]["evidence"].get("confirmed_twitch") == 1.0
    assert "emg_grip" not in cup[-1]["contract"]["evidence"]  # a twitch never sets grip
    assert h.pending is None and h.selection.menu is None and h._in_flight == 0
    complete(h)
    [done] = kinds(h, "task.completed")
    assert done["card"] == "pick_place:cup" and done["outcome"] == "completed"
    assert done["counts"]["twitch"] == 2 and done["selections"] == 2
    assert 0 <= done["to_motion_s"] <= done["to_complete_s"]


def test_twitch_inside_the_reaction_time_or_without_an_onset_is_refused(h):
    menu = show(h)
    acc = twitch(h, reaction_s=0.1)
    assert not acc["ok"] and "too soon" in acc["detail"]
    h.log.emit("emg.twitch", held_ms=200)  # no detect_ms or onset_t: fails closed
    acc = answers(h)[-1]
    assert not acc["ok"] and "time" in acc["detail"]
    h.log.emit("emg.twitch", held_ms=200, detect_ms=math.nan, onset_t=math.inf)
    assert not answers(h)[-1]["ok"]
    assert not started(h) and h.pending is None and h.selection.menu is menu
    assert accept(h)["ok"] and started(h)[0]["object_id"] == "cup"


def test_the_detector_onset_is_used_when_it_is_earlier(h):
    menu = show(h)
    menu.highlighted_t = now() - 5
    # detect_ms says the burst began 3 s after the highlight; the detector's own onset
    # says 0.2 s: the earlier one is gated, and it is too soon.
    h.log.emit("emg.twitch", held_ms=200, detect_ms=2000, onset_t=menu.highlighted_t + 0.2)
    assert wait(lambda: answers(h))
    assert "too soon" in answers(h)[-1]["detail"]


def test_double_twitch_steps_and_wraps_and_a_twitch_takes_only_the_card_now_highlighted(h):
    menu = show(h)
    for expected in (1, 2, 0, 1):
        h.log.emit("emg.double_twitch", interval_ms=300)
        assert menu.cursor == expected
    assert [e["card"] for e in kinds(h, "selection.step")] == [*PICKS[1:], *PICKS[:2]]
    # The highlight just moved: a twitch that began before that (+ reaction) is stale.
    h.log.emit("emg.twitch", held_ms=200, detect_ms=650)
    acc = answers(h)[-1]
    assert not acc["ok"] and "too soon" in acc["detail"] and not started(h)
    assert accept(h)["card"] == "pick_place:block"
    [s] = started(h)
    assert s["object_id"] == "block"


def test_the_gate_still_decides_a_disarmed_arm_holds_and_the_card_stays(h):
    h.disarm("test")
    menu = show(h)
    assert menu is not None
    acc = accept(h)
    assert not acc["ok"] and "not armed" in acc["detail"]
    assert not started(h) and h.pending is None and h.selection.menu is menu
    assert menu.taken is None and menu.confirming is None
    assert menu.highlighted.id == "pick_place:cup"
    h.log.emit("emg.twitch", held_ms=200, detect_ms=650)  # a retry needs a fresh reaction
    assert wait(lambda: "too soon" in answers(h)[-1]["detail"])


def test_say_card_speaks_and_completes_without_motion(h):
    h.selection_cfg = {**h.selection_cfg, "phrases": {"thanks": "Thank you"}}
    h.selection_set_mode("scanning")
    menu = show(h)
    assert ids(menu)[-1] == "say:thanks"
    for _ in range(len(menu.cards) - 1):
        h.log.emit("emg.double_twitch")
    acc = twitch(h)  # one step: a say card never moves the arm
    assert acc["kind"] == "selection.accept" and acc["ok"]
    assert h.spoken[-1] == "Thank you" and not started(h)
    [done] = kinds(h, "task.completed")
    assert done["card_kind"] == "say" and done["to_motion_s"] is None
    assert done["selections"] == len(menu.cards)  # 6 steps + the twitch


# ── consent: a twitch is only ever the answer to the highlighted card ──────────
def test_a_warning_the_card_never_showed_is_not_answered_by_the_twitch(h2m):
    """Gripper not proven empty (startup): the gate's question warns the object may
    drop. The card did not say that, so the request waits for a squeeze / yes / click."""
    h = enable(h2m)
    assert h.gripper.state().status == "unknown"
    show(h)
    acc = accept(h)
    assert acc["ok"] and not acc["answered"] and "can't tell if my gripper is empty" in acc["detail"]
    pending = h.pending
    assert pending is not None and pending.contract.object_id == "cup" and not started(h)
    assert h.reply == pending.decision.reason  # the warning is what the person sees now
    h.log.emit("emg.twitch", held_ms=200, detect_ms=900)  # a twitch cannot answer it
    time.sleep(0.1)
    assert h.pending is pending and not started(h)
    pending.announced_at = now() - 2
    h.log.emit("emg.squeeze", effort=0.4, held_ms=400)  # the normal answer, reaction-gated
    assert wait(lambda: started(h))
    [s] = started(h)
    assert s["object_id"] == "cup" and s["confirmed_by"] == "emg"
    complete(h)
    [done] = kinds(h, "task.completed")
    assert done["card"] == "pick_place:cup" and done["counts"]["twitch"] == 3


def test_twitch_never_confirms_a_request_that_is_not_the_card(h):
    show(h)
    h.on_utterance("hand me the block", source="typed")
    pending = h.pending
    assert pending is not None
    acc = twitch(h)
    assert not acc["ok"] and "request is waiting" in acc["detail"]
    assert h.selection_snapshot()["blocked"].startswith("a request is waiting")
    h.log.emit("emg.double_twitch")
    assert h.selection.menu.cursor == 0  # stepping is blocked too
    h.selection.hide(now(), "test")  # no cards at all: a twitch does nothing
    h.log.emit("emg.twitch", held_ms=200, detect_ms=900)
    time.sleep(0.1)
    assert h.pending is pending and not started(h)


def test_twitch_never_approves_a_rule(h, monkeypatch):
    show(h)
    proposal = RuleProposal(Rule(["liquid"], "laptop", "route_around"), None, "x", now() - 5,
                            now() + 30)
    with h._lock:
        h.learner.proposal = proposal
    approvals = []
    monkeypatch.setattr(h.learner, "approve", lambda **kw: approvals.append(kw) or (True, ""))
    acc = twitch(h)
    assert not acc["ok"] and "rule is waiting" in acc["detail"]
    h.log.emit("emg.double_twitch")
    h.selection.hide(now(), "test")
    h.log.emit("emg.twitch", held_ms=200, detect_ms=900)  # no cards: still nothing
    time.sleep(0.2)
    assert approvals == [] and h.learner.proposal is proposal and h.rules.list() == []
    assert not started(h)
    h.log.emit("emg.squeeze", effort=0.5, held_ms=400)  # control: a squeeze still answers it
    assert wait(lambda: approvals) and approvals[0]["by"] == "emg"


def test_a_brace_between_the_twitch_and_the_gate_drops_the_card(h, monkeypatch):
    menu = show(h)
    assert twitch(h)["kind"] == "selection.select" and announced(menu)
    real_thread = threading.Thread

    class BraceFirst(real_thread):
        def start(self):
            if self.name == "h2m-select":
                h.log.emit("emg.brace", onset_t=now())  # lands before the gate runs
            super().start()

    monkeypatch.setattr(app_mod.threading, "Thread", BraceFirst)
    acc = twitch(h)
    monkeypatch.setattr(app_mod.threading, "Thread", real_thread)
    assert not acc["ok"] and "cancelled" in acc["detail"]
    assert not started(h) and h.pending is None and h.arm.state is ArmState.READY


def test_cards_go_and_the_ranker_is_cancelled_when_another_request_moves_the_arm(h):
    h.ranker.client = FakeSpark(choose(("o3", "maybe")), delay=0.3)
    show(h)
    gen = h.ranker._gen
    h.on_utterance("hand me the block", source="typed")
    assert h.confirm(by="ui")[0] and h.arm.state is ArmState.MOVING
    assert h.selection.menu is None and h.ranker._gen > gen
    h._selection_tick()
    [ab] = kinds(h, "task.abandoned")
    assert ab["outcome"] == "interrupted"
    time.sleep(0.5)
    assert not [e for e in kinds(h, "selection.shown") if e["upgraded"]]  # late reply dropped
    assert show(h) is None  # no cards while the arm moves


def test_selection_off_means_a_twitch_does_nothing(h):
    h.selection_enabled = False
    assert show(h) is None
    h.on_utterance("hand me the block", source="typed")
    pending = h.pending
    h.log.emit("emg.twitch", held_ms=200, detect_ms=900)
    h.log.emit("emg.double_twitch")
    time.sleep(0.1)
    assert h.pending is pending and not started(h) and not answers(h)


# ── fix 7: another answer wins the race for the card's request ─────────────────
def test_a_confirm_race_won_by_a_click_keeps_the_task_and_completes_it(h, monkeypatch):
    real = h.confirm

    def confirm(by="ui", effort=None, expected=None):
        if by == "twitch":  # the operator's click (its own thread) lands first
            clicked = []
            click = threading.Thread(target=lambda: clicked.append(real(by="ui")))
            click.start()
            click.join(5)
            assert clicked and clicked[0][0], clicked
        return real(by=by, effort=effort, expected=expected)

    monkeypatch.setattr(h, "confirm", confirm)
    show(h)
    acc = accept(h)
    assert acc["ok"] and "another answer" in acc["detail"]
    [s] = started(h)
    assert s["object_id"] == "cup" and s["confirmed_by"] == "ui"
    task = h.selection.task
    assert task is not None and task.contract_id == s["contract_id"]
    complete(h)
    assert not kinds(h, "task.abandoned")
    [done] = kinds(h, "task.completed")
    assert done["card"] == "pick_place:cup" and done["contract_id"] == s["contract_id"]


def test_a_confirm_race_won_before_the_motion_started_keeps_the_binding(h, monkeypatch):
    real = h.confirm
    taken = {}

    def confirm(by="ui", effort=None, expected=None):
        if by == "twitch":  # another confirm popped it and is still building the plan
            with h._lock:
                taken["p"], h.pending = h.pending, None
                taken["p"].popped = True
        return real(by=by, effort=effort, expected=expected)

    monkeypatch.setattr(h, "confirm", confirm)
    show(h)
    acc = accept(h)
    assert acc["ok"] and "another answer" in acc["detail"]
    assert not started(h) and h.selection.menu is None
    contract = taken["p"].contract
    assert h.selection.task.contract_id == contract.id
    assert h._confirm(taken["p"], "ui", None)[0]  # the winner's plan starts now
    h._selection_tick()
    assert not kinds(h, "task.abandoned")
    complete(h)
    [done] = kinds(h, "task.completed")
    assert done["contract_id"] == contract.id


def test_finish_failure_after_the_motion_started_keeps_the_binding():
    from h2m.intent import rank_candidates

    scene = [{"id": "cup", "name": "cup", "table_xy": [0.25, 0.55]}]
    sel = Selection("intent", require_ack=False)
    sel.show(rank_candidates(scene, [], [], None, [], None), "fallback", 0.0)
    ticket, _, _ = sel.take("twitch", 5.0, 4.0)
    sel.announced(ticket, 5.0, "x")
    ticket, why, _ = sel.take("twitch", 7.0, 6.5)
    assert ticket is not None, why
    sel.bind(ticket, "c1")
    sel.on_arm_started("c1", 7.1)  # another answer started it first
    sel.finish(ticket, False, "nothing to confirm", True, 7.2)
    assert sel.task.contract_id == "c1"
    [(kind, data)] = sel.on_arm_completed("c1", None, 9.0)
    assert kind == "task.completed" and data["card"] == "pick_place:cup"


# ── brace / open hand ──────────────────────────────────────────────────────────
def test_brace_on_an_idle_arm_dismisses_the_highlighted_card_only(h):
    h.selection.veto_refractory_s = 0.0  # separate deliberate refusals
    menu = show(h)
    h.log.emit("emg.brace", onset_t=now())
    assert h.arm.state is ArmState.READY and not kinds(h, "incident.triggered")
    assert ids(menu) == PICKS[1:]
    h.log.emit("emg.double_twitch")
    h.log.emit("emg.open_hand", extensor=0.9)  # the open hand refuses too
    assert ids(menu) == ["pick_place:block"]
    h.log.emit("emg.brace", onset_t=now())
    assert [e["card"] for e in kinds(h, "selection.dismiss")] == [
        "pick_place:cup", "pick_place:ball", "pick_place:block"]
    [ab] = kinds(h, "task.abandoned")
    assert ab["outcome"] == "dismissed" and ab["selections"] == 4
    assert ab["counts"] == {**dict.fromkeys(INPUTS, 0), "brace": 2, "open_hand": 1,
                            "double_twitch": 1}
    assert h.selection.menu is None and not h.selection.wants_menu(now())  # quiet a while
    assert h.arm.state is ArmState.READY and not started(h)


def test_one_flinch_firing_brace_and_open_hand_refuses_one_card(h):
    menu = show(h)
    h.log.emit("emg.brace", onset_t=now())
    h.log.emit("emg.open_hand", extensor=0.9)
    assert ids(menu) == PICKS[1:]
    assert h.selection.task.counts["brace"] == 1 and h.selection.task.counts["open_hand"] == 1


def test_brace_during_a_card_motion_freezes_first_and_latches(h, monkeypatch):
    show(h)
    assert accept(h)["ok"] and h.arm.state is ArmState.MOVING
    run(h, 1.0)
    order = []
    real_stop = h.arm.stop_if_active

    def stop_if_active(reason, plan_in_flight=False):
        order.append("stop")
        return real_stop(reason, plan_in_flight=plan_in_flight)

    monkeypatch.setattr(h.arm, "stop_if_active", stop_if_active)
    for name in ("count", "dismiss", "take", "step", "on_arm_stopped", "hide"):
        real = getattr(h.selection, name)

        def spy(*a, _real=real, _name=name, **k):
            order.append(_name)
            return _real(*a, **k)

        monkeypatch.setattr(h.selection, name, spy)
    h.log.emit("emg.brace", onset_t=now())
    assert h.arm.state is ArmState.STOPPED
    # Nothing of the card handling runs before the freeze, and the brace only stopped.
    assert order[0] == "stop" and order == ["stop", "on_arm_stopped"]
    assert kinds(h, "incident.triggered")[0]["source"] == "brace"
    h.learner._queue.get_nowait()  # the incident worker is not running in tests
    h._selection_tick()
    [ab] = kinds(h, "task.abandoned")
    assert ab["outcome"] == "stopped" and ab["counts"]["brace"] == 1 and ab["counts"]["twitch"] == 2
    assert ab["to_motion_s"] is not None and "to_complete_s" not in ab
    # Latched: new cards may go up, but nothing a twitch accepts moves until reset + arm.
    h.selection._quiet_until = 0.0
    assert show(h) is not None
    forget_vetoes(h)
    acc = accept(h)
    assert not acc["ok"] and len(started(h)) == 1 and h.arm.state is ArmState.STOPPED


def test_open_hand_during_a_card_motion_freezes(h):
    show(h)
    assert accept(h)["ok"]
    h.log.emit("emg.open_hand", extensor=0.9)
    assert h.arm.state is ArmState.STOPPED
    h.learner._queue.get_nowait()
    h._selection_tick()
    [ab] = kinds(h, "task.abandoned")
    assert ab["outcome"] == "stopped" and ab["counts"]["open_hand"] == 1


# ── fix 6: a twitch-band burst onset freezes a moving arm (emg.burst_freeze) ───
def burst_freeze_on(h):
    h.burst_freeze = True  # what emg.burst_freeze: true sets (after a bench session)
    return h


def test_burst_freeze_ships_off_and_selection_alone_does_not_enable_it(h):
    """Review item 3 (and test_review4): with the shipped config a burst onset (the
    person grips their cup) neither stops a moving arm nor feeds the learner, with
    selection on or off."""
    assert load_config()["emg"]["burst_freeze"] is False
    assert h.selection_enabled and h.burst_freeze is False
    assert h.arm.execute(plan_home(h.arm.poses))[0] and h.arm.state is ArmState.MOVING
    h.log.emit("emg.burst_onset", onset_t=now())
    assert h.arm.state is ArmState.MOVING
    assert not kinds(h, "incident.triggered") and h.learner._queue.empty()


@pytest.mark.parametrize("value", ["true", 1, "yes"])
def test_only_the_literal_true_enables_the_burst_freeze(h2m, value):
    cfg = {**h2m.cfg, "emg": {**h2m.cfg["emg"], "source": "off", "burst_freeze": value}}
    assert app_mod.H2M(cfg).burst_freeze is False
    cfg["emg"]["burst_freeze"] = True
    assert app_mod.H2M(cfg).burst_freeze is True


def test_a_retwitch_after_a_card_carry_starts_does_not_latch_a_stop(h):
    """N4: a "did it register?" re-twitch while the confirmed carry moves. Shipped
    config: the burst onset is ignored, the carry continues."""
    show(h)
    assert accept(h)["ok"] and h.arm.state is ArmState.MOVING
    h.log.emit("emg.burst_onset", onset_t=now())
    assert h.arm.state is ArmState.MOVING
    assert not kinds(h, "incident.triggered")


def test_burst_onset_while_moving_freezes_first_and_latches(h, monkeypatch):
    burst_freeze_on(h)
    assert h.arm.execute(plan_home(h.arm.poses))[0] and h.arm.state is ArmState.MOVING
    order = []
    real_stop = h.arm.stop_if_active

    def stop_if_active(reason, plan_in_flight=False):
        order.append(("stop", reason))
        return real_stop(reason, plan_in_flight=plan_in_flight)

    monkeypatch.setattr(h.arm, "stop_if_active", stop_if_active)
    monkeypatch.setattr(h, "_silence", lambda: order.append("silence"))
    real_trigger = h.learner.trigger
    monkeypatch.setattr(h.learner, "trigger", lambda *a: order.append("learner") or real_trigger(*a))
    muse_calls = []
    monkeypatch.setattr(h.learner, "muse", type("M", (), {"__getattr__": lambda s, n: muse_calls.append(n)})())
    t0 = time.perf_counter()
    h.log.emit("emg.burst_onset", onset_t=now())
    elapsed = time.perf_counter() - t0
    assert h.arm.state is ArmState.STOPPED and h.arm.reason == app_mod.BURST_STOP
    assert order[0] == ("stop", "twitch-band burst while moving")
    assert order[1:] == ["silence", "learner"] and muse_calls == []
    assert elapsed < 0.05  # the stop, then only an enqueue on the event thread
    assert kinds(h, "incident.triggered")[0]["source"] == "burst"
    ok, _ = h.arm.execute(plan_home(h.arm.poses))
    assert not ok  # latched until reset + arm
    assert h.arm.reset()[0] and h.arm.arm()[0]
    assert h.arm.execute(plan_home(h.arm.poses))[0]


def test_burst_onset_with_a_plan_in_flight_drops_it_without_latching(h):
    """Review item 3: a plan in flight on a READY arm (compile, gate, handover recheck)
    is dropped through the request epoch; no stop is latched."""
    burst_freeze_on(h)
    epoch = h._epoch
    with h._plan_in_flight():
        h.log.emit("emg.burst_onset", onset_t=now())
        assert h.arm.state is ArmState.READY and h._epoch > epoch
        ok, why = h.arm.execute(plan_home(h.arm.poses), still_wanted=lambda: not h._stale(epoch))
        assert not ok and "cancelled" in why  # the in-flight plan never starts
    assert kinds(h, "intent.cancelled")[-1]["reason"] == app_mod.BURST_DROP
    assert not kinds(h, "incident.triggered")
    assert h.arm.execute(plan_home(h.arm.poses))[0]  # no reset + arm needed


def test_burst_onset_stops_a_plan_that_starts_between_its_check_and_the_drop(h, monkeypatch):
    """The in-flight plan passes its epoch check and starts right after the burst's first
    check-and-stop: the second check-and-stop finds it MOVING and freezes it (latched)."""
    burst_freeze_on(h)
    real_stop = h.arm.stop_if_active
    calls = []

    def stop_if_active(reason, plan_in_flight=False):
        stopped = real_stop(reason, plan_in_flight=plan_in_flight)
        calls.append((reason, plan_in_flight, stopped))
        if len(calls) == 1:
            assert h.arm.execute(plan_home(h.arm.poses))[0]  # lands in the gap
        return stopped

    monkeypatch.setattr(h.arm, "stop_if_active", stop_if_active)
    with h._plan_in_flight():
        h.log.emit("emg.burst_onset", onset_t=now())
    assert [c[1:] for c in calls] == [(False, False), (False, True)]  # never a READY latch
    assert h.arm.state is ArmState.STOPPED and h.arm.reason == app_mod.BURST_STOP
    assert not h.arm.execute(plan_home(h.arm.poses))[0]  # latched until reset + arm


def test_burst_onset_on_an_idle_arm_changes_nothing(h):
    burst_freeze_on(h)
    menu = show(h)
    h.on_utterance("hand me the block", source="typed")
    pending, epoch = h.pending, h._epoch
    h.log.emit("emg.burst_onset", onset_t=now())
    assert h.arm.state is ArmState.READY and h.pending is pending and h._epoch == epoch
    assert h.selection.menu is menu and ids(menu) == PICKS
    assert not kinds(h, "incident.triggered") and not kinds(h, "selection.dismiss")


def test_burst_onset_during_a_card_motion_freezes_and_ends_the_task(h):
    burst_freeze_on(h)
    show(h)
    assert accept(h)["ok"] and h.arm.state is ArmState.MOVING
    run(h, 0.5)
    h.log.emit("emg.burst_onset", onset_t=now())
    assert h.arm.state is ArmState.STOPPED
    h.learner._queue.get_nowait()
    h._selection_tick()
    [ab] = kinds(h, "task.abandoned")
    assert ab["outcome"] == "stopped" and ab["reason"] == app_mod.BURST_STOP


def test_brace_and_open_hand_still_freeze_without_waiting(h2m):
    for kind, data in (("emg.brace", {"onset_t": now()}), ("emg.open_hand", {"extensor": 0.9})):
        if h2m.arm.state is ArmState.STOPPED:
            h2m.arm.reset()
            h2m.arm.arm()
        assert h2m.arm.execute(plan_home(h2m.arm.poses))[0]
        t0 = time.perf_counter()
        h2m.log.emit(kind, **data)
        assert h2m.arm.state is ArmState.STOPPED and time.perf_counter() - t0 < 0.05


# ── fix 5: re-arming rebuilds the cards; incidents block ──────────────────────
def test_R3_cards_shown_while_stopped_are_rebuilt_after_reset_and_arm(h):
    h.stop("brace (EMG)")
    h.selection._quiet_until = 0.0
    menu = show(h)
    assert menu is not None and h.arm.state is ArmState.STOPPED
    menu.highlighted_t -= 10  # it has been up a while
    forget_vetoes(h)
    h.arm.reset()
    h.arm.arm()
    # The first twitch after arming, before the loop saw the new arming: refused.
    h.log.emit("emg.twitch", held_ms=200, detect_ms=605)
    assert wait(lambda: answers(h))
    assert not answers(h)[-1]["ok"] and "re-armed" in answers(h)[-1]["detail"]
    time.sleep(0.1)
    assert not started(h) and menu.confirming is None
    h._selection_tick()  # the old cards go and new ones go up at once
    [ab] = kinds(h, "task.abandoned")
    assert ab["outcome"] == "cancelled" and "re-armed" in ab["reason"]
    new = show(h)
    assert new is not None and new.id != menu.id and new.arm_count == h.arm.arm_count
    h.log.emit("emg.twitch", held_ms=200, detect_ms=605)  # fresh highlight: too soon
    assert wait(lambda: "too soon" in answers(h)[-1]["detail"])
    assert not started(h)


@pytest.mark.parametrize("state", ["capturing", "explaining"])
def test_R4_no_card_is_answered_while_an_incident_is_captured_or_explained(h, state):
    menu = show(h)
    with h._lock:
        h.learner.incident = {"state": state}
    assert h.learner.proposal is None
    menu.highlighted_t -= 5
    h.log.emit("emg.twitch", held_ms=200, detect_ms=605)
    assert wait(lambda: answers(h))
    assert not answers(h)[-1]["ok"] and "what just happened" in answers(h)[-1]["detail"]
    h.log.emit("emg.double_twitch")
    assert menu.cursor == 0
    assert h.selection_snapshot()["blocked"] == "I'm still looking at what just happened"
    time.sleep(0.1)
    assert not started(h) and menu.confirming is None
    with h._lock:
        h.learner.incident = {"state": "explained"}
    assert twitch(h)["kind"] == "selection.select"


def test_a_queued_incident_blocks_the_cards_too(h):
    menu = show(h)
    h.learner.trigger(now(), "brace")  # enqueued, the worker has not picked it up
    acc = twitch(h)
    assert not acc["ok"] and "what just happened" in acc["detail"]
    h.learner._queue.get_nowait()
    assert twitch(h)["kind"] == "selection.select" and menu.confirming is not None


# ── scanning baseline and degradation ─────────────────────────────────────────
def test_scanning_menu_is_every_option_in_scene_order_with_counts(h):
    h.ranker.client = FakeSpark(choose(("o3", "maybe")))
    assert h.selection_set_mode("scanning")[0]
    menu = show(h)
    assert menu.source == "scanning" and ids(menu) == PICKS + LOOKS
    for _ in range(4):
        h.log.emit("emg.double_twitch")
    assert menu.highlighted.id == "look_at:block"
    assert accept(h)["ok"]
    [s] = started(h)
    assert s["verb"] == "point" and s["object_id"] == "block"
    complete(h)
    [done] = kinds(h, "task.completed")
    assert done["mode"] == "scanning" and done["selections"] == 6
    assert done["counts"]["double_twitch"] == 4 and done["counts"]["twitch"] == 2
    assert h.ranker.client.calls == 0  # the baseline never asks Muse


def test_no_key_degrades_to_scanning_for_the_rest_of_the_run(h):
    h.ranker.client = SparkClient({})  # no MODEL_API_KEY: available is False
    menu = show(h)
    assert menu.source == "fallback" and ids(menu) == PICKS  # cards at once, no network
    assert wait(lambda: kinds(h, "selection.degraded"))
    [d] = kinds(h, "selection.degraded")
    assert d == {"note": "no_key", "from": "intent", "to": "scanning"}
    assert h.selection.mode == "scanning" and h.selection.menu is menu  # same cards stay up
    h.cancel("test")
    h.selection._quiet_until = 0.0
    assert show(h).source == "scanning" and ids(h.selection.menu) == PICKS + LOOKS
    assert h.selection_snapshot()["degraded"] == "no_key"


def test_slow_muse_degrades_and_an_invalid_reply_keeps_the_fallback(h):
    h.selection_cfg = {**h.selection_cfg, "deadline_s": 0.05}
    h.ranker.client = FakeSpark("not json at all")
    menu = show(h)
    assert wait(lambda: h.ranker.client.calls == 1)
    time.sleep(0.2)
    assert not kinds(h, "selection.degraded") and h.selection.mode == "intent"
    assert menu.source == "fallback" and ids(menu) == PICKS
    h.cancel("test")
    h.selection._quiet_until = 0.0
    h.ranker.client = FakeSpark(choose(("o3", "maybe")), delay=0.5)
    show(h)
    assert wait(lambda: kinds(h, "selection.degraded"))
    assert kinds(h, "selection.degraded")[0]["note"] == "timeout_or_error"


# ── fix 4: a Muse upgrade never changes the highlighted card ───────────────────
def test_R2_upgrade_mid_reaction_keeps_the_card_the_person_is_looking_at(h):
    h.ranker.client = FakeSpark(choose(("o3", "maybe")), delay=0.2)
    menu = show(h)
    seen, t_seen, hl = menu.highlighted, menu.highlighted_t, menu.hl
    assert seen.id == "pick_place:cup"
    assert wait(lambda: menu.source == "muse")
    # Muse put the ball first; the highlighted cup stays where it is, unchanged.
    assert ids(menu) == ["pick_place:cup", "pick_place:ball", "pick_place:block"]
    assert menu.highlighted is seen and menu.highlighted_t == t_seen and menu.hl == hl
    up = kinds(h, "selection.shown")[-1]
    assert up["upgraded"] and up["kept"] == "pick_place:cup"
    # A twitch that began 0.31 s after the upgrade but long after the cup appeared
    # selects the cup: the card the person was reacting to.
    menu.highlighted_t -= 2
    n = len(answers(h))
    h.log.emit("emg.twitch", held_ms=200, detect_ms=650)
    assert wait(lambda: len(answers(h)) > n)
    assert answers(h)[-1]["card"] == "pick_place:cup"


def test_muse_upgrade_applies_only_before_the_first_input(h):
    h.ranker.client = FakeSpark(choose(("o3", "likely")), delay=0.05)
    menu = show(h)
    assert wait(lambda: menu.source == "muse")
    assert ids(menu) == ["pick_place:cup", "pick_place:ball", "pick_place:block"]
    assert menu.cards[1].confidence == "maybe"  # no history: the model's "likely" is not evidence
    h.cancel("test")
    h.selection._quiet_until = 0.0
    h.ranker.client = FakeSpark(choose(("o3", "maybe")), delay=0.3)
    menu = show(h)
    h.log.emit("emg.double_twitch")  # the person started selecting
    time.sleep(0.6)
    assert menu.source == "fallback" and ids(menu) == PICKS and menu.cursor == 1


def test_an_upgrade_that_beats_the_first_cards_is_applied_on_show():
    from h2m.intent import rank_candidates

    scene = [{"id": o, "name": o, "table_xy": [0.25 * (i + 1), 0.55]}
             for i, o in enumerate(("cup", "block", "ball"))]
    fallback = rank_candidates(scene, [], [], None, [], None)
    muse = list(reversed(fallback))
    sel = Selection("intent")
    assert sel.upgrade(7, muse, 1.0) == []  # no menu yet: held for generation 7
    [(kind, data)] = sel.show(fallback, "fallback", 1.0, ranker_gen=7)
    assert kind == "selection.shown" and data["source"] == "muse"
    assert data["cards"] == [c.id for c in muse]  # nothing was on screen yet
    sel2 = Selection("intent")
    sel2.upgrade(6, muse, 1.0)  # an older generation never applies
    assert sel2.show(fallback, "fallback", 1.0, ranker_gen=7)[0][1]["source"] == "fallback"


# ── UI endpoints ───────────────────────────────────────────────────────────────
def test_ui_selection_endpoints_are_tokened_pinned_acked_and_two_step(h):
    enable(h, require_ack=True)
    c = TestClient(create_app(h, token=TOKEN), base_url="http://127.0.0.1")
    show(h)
    sel = c.get("/api/selection").json()
    assert sel["enabled"] and sel["mode"] == "intent" and sel["cursor"] == 0
    assert sel["require_ack"] and not sel["acked"] and sel["accept_in_s"] is None
    assert [card["id"] for card in sel["cards"]] == PICKS
    menu, top = sel["menu"], sel["highlighted"]
    url, ack = "/api/selection/accept", "/api/selection/ack"
    pins = {"card_id": top, "menu": menu}
    for path in (url, ack):
        assert c.post(path, json=pins).status_code == 403  # no token
        assert c.post(path, headers={**GOOD, "Origin": "https://evil.example"},
                      json=pins).status_code == 403
        assert c.post(path, headers=GOOD, json={"card_id": top}).status_code == 400
        assert c.post(path, headers=GOOD, content="[1]").status_code == 400
        assert c.post(path, headers=GOOD, json={"card_id": top, "menu": True}).status_code == 400
    h.selection.menu.highlighted_t -= 5
    r = c.post(url, headers=GOOD, json=pins)
    assert r.status_code == 409 and "has not shown" in r.json()["detail"]  # no ack
    r = c.post(ack, headers=GOOD, json={"card_id": "pick_place:ball", "menu": menu})
    assert r.status_code == 409 and "not the highlighted" in r.json()["detail"]
    assert c.post(ack, headers=GOOD, json=pins).status_code == 200
    assert c.get("/api/selection").json()["acked"]
    r = c.post(url, headers=GOOD, json=pins)
    assert r.status_code == 409 and "too soon" in r.json()["detail"]  # just rendered
    h.selection.menu.ack_t -= 1.0
    r = c.post(url, headers=GOOD, json={"card_id": "pick_place:ball", "menu": menu})
    assert r.status_code == 409 and "not the highlighted" in r.json()["detail"]
    r = c.post(url, headers=GOOD, json={"card_id": top, "menu": menu + 1})
    assert r.status_code == 409 and "changed" in r.json()["detail"]
    assert not started(h)
    assert c.post("/api/selection/step", headers=GOOD).status_code == 200
    sel = c.get("/api/selection").json()
    assert sel["highlighted"] == "pick_place:block" and not sel["acked"]  # new highlight
    r = c.post("/api/selection/dismiss", headers=GOOD, json={"card_id": "pick_place:cup"})
    assert r.status_code == 409  # pinned to the highlighted card
    r = c.post("/api/selection/dismiss", headers=GOOD, json={"card_id": "pick_place:block"})
    assert r.status_code == 200
    assert c.get("/api/selection").json()["highlighted"] == "pick_place:ball"
    ball = {"card_id": "pick_place:ball", "menu": menu}
    assert c.post(ack, headers=GOOD, json=ball).status_code == 200
    h.selection.menu.ack_t -= 1.0
    h.selection.menu.highlighted_t -= 1.0
    r = c.post(url, headers=GOOD, json=ball)  # first accept: selects, the arm asks
    assert r.status_code == 200 and "confirm" in r.json()["detail"], r.json()
    sel = c.get("/api/selection").json()
    assert sel["confirming"] == "pick_place:ball" and not started(h)
    assert not sel["acked"] and sel["accept_in_s"] is None  # "confirm?" is a new highlight
    r = c.post(url, headers=GOOD, json=ball)
    assert r.status_code == 409 and "has not shown the question" in r.json()["detail"]
    assert c.post(ack, headers=GOOD, json=ball).status_code == 200
    r = c.post(url, headers=GOOD, json=ball)
    assert r.status_code == 409 and "too soon after I asked" in r.json()["detail"]
    h.selection.menu.announce_t -= 1.0
    h.selection.menu.ack_t -= 1.0
    r = c.post(url, headers=GOOD, json=ball)
    assert r.status_code == 200, r.json()
    [s] = started(h)
    assert s["object_id"] == "ball" and s["confirmed_by"] == "ui_card"
    counts = h.selection.task.counts
    assert counts["ui_step"] == 1 and counts["ui_dismiss"] == 2 and counts["ui_accept"] == 8
    assert c.post("/api/selection/mode", headers=GOOD, json={"mode": "x"}).status_code == 400
    assert c.post("/api/selection/mode", json={"mode": "scanning"}).status_code == 403
    assert c.post("/api/selection/mode", headers=GOOD, json={"mode": "scanning"}).status_code == 200
    assert c.get("/api/selection").json()["mode"] == "scanning"
    assert "selection" in c.get("/api/state").json()


# ── config and the EMG split ───────────────────────────────────────────────────
def test_both_brace_features_stay_selectable():
    cfg = load_config()
    assert cfg["emg"]["brace_feature"] in ("cocontraction", "flexor_spike")  # the EMG side's pick
    assert cfg["selection"]["mode"] in MODES
    for feature in ("cocontraction", "flexor_spike"):
        svc = emg.EmgService({**cfg["emg"], "brace_feature": feature}, emg.SyntheticSource(),
                             app_mod.EventLog())
        assert svc.brace_feature == feature
    with pytest.raises(ValueError):
        Selection("bogus")


def test_synthetic_emg_brace_dismisses_and_two_twitches_accept_end_to_end(h, monkeypatch):
    rig = EmgRig(monkeypatch, "cocontraction")  # the cleanest twitch / brace split
    rig.log.subscribe(h._on_event)
    menu = show(h)
    rig.run(0.5, 0.9, 0.9)  # a sudden co-contraction on the idle arm
    rig.run(2.0, 0.0, 0.0)  # relax past the brace lockout
    assert rig.events("emg.brace") and h.arm.state is ArmState.READY
    assert ids(menu) == PICKS[1:]  # one card refused, whatever else the flinch fired
    # The rig's sample clock is simulated: open the gates that compare it to real time.
    menu.highlighted_t = -1e9
    forget_vetoes(h)
    h.selection.confirm_s = math.inf
    rig.burst(0.15)  # a flexor-only twitch: selects
    rig.run(0.9, 0.0, 0.0)
    assert [e.kind for e in rig.log.recent(50) if e.kind.startswith("emg.")][-1] == "emg.twitch"
    assert wait(lambda: menu.confirming is not None) and announced(menu)
    assert not started(h)
    menu.announce_t = -1e9
    rig.burst(0.15)  # the second twitch confirms
    rig.run(0.9, 0.0, 0.0)
    assert wait(lambda: started(h))
    [s] = started(h)
    assert s["object_id"] == "block" and s["confirmed_by"] == "twitch"


# ── state machine details ──────────────────────────────────────────────────────
def test_completed_tasks_feed_the_next_ranking(h):
    show(h)
    h.log.emit("emg.double_twitch")
    assert accept(h)["card"] == "pick_place:block"
    complete(h)
    assert h.selection.recent_history() == [
        {"kind": "pick_place", "object_id": "block", "text_id": None}]
    menu = show(h)
    assert menu is not None and ids(menu)[0] == "pick_place:block"  # deterministic recency


def test_an_accepted_motion_that_never_completes_ends_the_task_when_idle(h):
    show(h)
    assert accept(h)["ok"]
    h._selection_tick()  # still moving: the task stays open
    assert h.selection.task is not None
    h.arm.state = ArmState.READY  # the plan ended without an arm.completed for it
    h.arm._plan = None
    h._selection_tick()
    [ab] = kinds(h, "task.abandoned")
    assert ab["outcome"] == "incomplete" and ab["card"] == "pick_place:cup"

# imports added with the tests below
