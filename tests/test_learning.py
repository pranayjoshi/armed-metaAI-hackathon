"""Flinch -> incident -> explanation -> approval -> reroute, on the virtual arm with a
mocked Muse client. Covers: nothing slow on the freeze path, stops stay latched,
a brace never approves, approval needs a reaction-time-gated answer, and saved
rules only restrict (route, speed, grip) and fail closed when state is missing."""

from __future__ import annotations

import json
import threading
import time

import pytest

import h2m.learning as learning_mod
from h2m.core import load_config, now
from h2m.incident import FALLBACK_SAY
from h2m.robot import ArmState
from h2m.robot.skills import plan_home
from h2m.rules import Rule, RuleStore

GOOD = {
    "cause_object": "laptop",
    "relation": "carried_over",
    "held_property": "liquid",
    "say": "You braced as I carried water over your laptop. Should I keep liquids away from it?",
    "confidence": 0.8,
    "rule": {"when_holding": ["liquid"], "avoid": "laptop", "response": "route_around"},
}


class FakeMuse:
    def __init__(self, reply=GOOD, block: threading.Event | None = None, fail=False):
        self.reply, self.block, self.fail = reply, block, fail
        self.calls: list[tuple[str, list]] = []
        self.threads: list[str] = []

    def chat(self, model, messages):
        self.threads.append(threading.current_thread().name)
        self.calls.append((model, messages))
        if self.block is not None:
            self.block.wait(5)
        if self.fail:
            raise RuntimeError("API down")
        return json.dumps(self.reply)


@pytest.fixture
def h2m(monkeypatch, tmp_path):
    monkeypatch.setattr("h2m.recorder.SESSIONS", tmp_path / "sessions")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    cfg = load_config()
    for part in ("emg", "vision", "speech"):
        cfg[part] = {**cfg[part], "source": "off"}
    cfg["compiler"] = {**cfg["compiler"], "llm": "off"}
    cfg["incident"] = {**cfg["incident"], "after_s": 0.0}
    from h2m.app import H2M

    h = H2M(cfg)
    h.rules = RuleStore("test", root=tmp_path / "rules")
    h.learner.root = tmp_path / "incidents"
    h.learner.muse = FakeMuse()
    h.arm.backend.connect()
    h.arm._measured = h.arm.backend.read_joints()
    h.arm._cmd = dict(h.arm._measured)
    h.arm.state = ArmState.DISARMED
    h.arm.arm()
    return h


def kinds(h, kind):
    return [e for e in h.log.recent(500) if e.kind == kind]


def tick_until(h, stage, limit_s=30.0, dt=0.05):
    t = 0.0
    while t < limit_s:
        h.arm.tick(dt)
        t += dt
        if h.arm.snapshot()["stage"] == stage:
            return
    raise AssertionError(f"never reached stage {stage}")


def start_carry(h, text="hand me the cup"):
    h.on_utterance(text, source="typed")
    assert h.pending is not None, h.last
    ok, why = h.confirm(by="ui")
    assert ok, why
    tick_until(h, "carry")
    h.learner.sample()  # the sampler thread's step: joints, contract, stage


def flinch_and_explain(h):
    start_carry(h)
    h.log.emit("emg.brace", onset_t=now())
    assert h.arm.state is ArmState.STOPPED
    assert h.learner.run_once(timeout=1.0)
    stop_was_long_ago(h)  # answers below come after the after-stop hold-off
    return kinds(h, "incident.explained")[-1].data


def stop_was_long_ago(h, s=10.0):
    """Pretend the last brace / open hand / burst stop was ``s`` seconds ago (past the
    app's after_stop_s hold-off; that hold-off has its own tests)."""
    h._last_stop_t = now() - s


def took_it_back(h):
    """The flinch froze the arm holding the cup. A pick while HOLDING holds ("place it
    first"), so model the person taking the cup out and the runtime restarting: the
    gripper is unknown again (never assumed empty)."""
    from h2m.world import GripperTracker

    assert h.gripper.state().holding
    h.gripper = GripperTracker()


def answer_later(p, s=1.0):
    """Pretend the question was asked s seconds ago (past the reaction-time gate)."""
    p.announced_at = now() - s


# ── freeze path ──────────────────────────────────────────────────────────────
def test_brace_freeze_never_waits_on_incident_or_muse(h2m):
    gate = threading.Event()
    h2m.learner.muse = FakeMuse(block=gate)
    start_carry(h2m)
    t0 = time.perf_counter()
    h2m.log.emit("emg.brace", onset_t=now())
    elapsed = time.perf_counter() - t0
    assert h2m.arm.state is ArmState.STOPPED
    assert elapsed < 0.05  # only an enqueue on the event thread
    assert h2m.learner.muse.calls == []  # no model on the freeze path
    assert not h2m.learner.buffer.frozen  # freeze/save not done on the event thread either
    worker = threading.Thread(target=h2m.learner.run_once, args=(2.0,), name="h2m-incident")
    worker.start()
    deadline = time.time() + 2
    while not h2m.learner.muse.calls and time.time() < deadline:
        time.sleep(0.01)
    assert h2m.learner.muse.threads == ["h2m-incident"]
    gate.set()
    worker.join(2)
    assert kinds(h2m, "incident.explained")


def test_idle_brace_starts_no_incident(h2m):
    h2m.log.emit("emg.brace", onset_t=now())
    assert not h2m.learner.run_once(timeout=0.05)


# ── explain and propose ─────────────────────────────────────────────────────
def test_incident_saved_explained_and_proposed_unapproved(h2m, tmp_path):
    data = flinch_and_explain(h2m)
    assert data["say"] == GOOD["say"]
    rule = data["proposal"]["rule"]
    assert rule["approved"] is False and rule["avoid"] == "laptop"
    assert h2m.rules.list() == []  # nothing saved without approval
    inc_dir = tmp_path / "incidents" / data["incident_id"]
    assert (inc_dir / "meta.json").exists() and (inc_dir / "explanation.json").exists()
    meta = json.loads((inc_dir / "meta.json").read_text())
    assert meta["held"] == "paper cup (tags: liquid, fragile)"
    assert any(c["contract"]["stage"] == "carry" for c in meta["contracts"])
    # the prompt names the avoid zones so a rule can point at one
    prompt = h2m.learner.muse.calls[0][1][1]["content"][0]["text"]
    assert "avoid_zone" in prompt and "laptop" in prompt
    # the stop is still latched: the incident pipeline never resets or arms
    assert h2m.arm.state is ArmState.STOPPED
    assert not h2m.arm.execute(plan_home(h2m.arm.poses))[0]


@pytest.mark.parametrize("muse", [None, FakeMuse(fail=True), FakeMuse(reply={"say": "hm"})])
def test_muse_missing_or_failing_falls_back(h2m, muse):
    h2m.learner.muse = muse
    data = flinch_and_explain(h2m)
    assert data["say"] == FALLBACK_SAY and data["proposal"] is None
    assert data["explanation"]["source"] == "fallback"
    assert h2m.arm.state is ArmState.STOPPED


@pytest.mark.parametrize(
    "rule, problem",
    [
        ({"when_holding": ["liquid"], "avoid": "kitchen", "response": "route_around"}, "zone"),
        ({"when_holding": ["radioactive"], "avoid": "laptop", "response": "slow_down"}, "tag"),
    ],
)
def test_rule_with_unknown_zone_or_tag_is_not_proposed(h2m, rule, problem):
    h2m.learner.muse = FakeMuse(reply={**GOOD, "rule": rule})
    data = flinch_and_explain(h2m)
    assert data["proposal"] is None and problem in " ".join(data["problems"])
    assert data["say"] == FALLBACK_SAY  # don't ask a yes/no we cannot act on


def test_muse_save_failure_still_explains(h2m, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(h2m.learner.buffer, "save", boom)
    data = flinch_and_explain(h2m)
    assert data["say"] == FALLBACK_SAY and kinds(h2m, "incident.error")
    assert not h2m.learner.buffer.frozen  # recording resumes


# ── approval ─────────────────────────────────────────────────────────────────
def test_brace_never_approves(h2m):
    flinch_and_explain(h2m)
    p = h2m.learner.proposal
    answer_later(p)
    h2m.log.emit("emg.brace", onset_t=now())
    time.sleep(0.1)
    assert h2m.learner.proposal is p and h2m.rules.list() == []
    for by in ("brace", "auto", "system"):
        assert h2m.learner.approve(by=by) == (False, f"{by} cannot approve a rule")
    assert h2m.rules.list() == []


def _squeeze(h, since_announce_s, held_ms=350):
    p = h.learner.proposal
    p.announced_at = now() - held_ms / 1000 - since_announce_s
    h.log.emit("emg.squeeze", effort=0.6, held_ms=held_ms)
    time.sleep(0.2)
    return p


def test_squeeze_too_soon_does_not_approve(h2m):
    flinch_and_explain(h2m)
    p = _squeeze(h2m, since_announce_s=0.1)
    assert h2m.learner.proposal is p and h2m.rules.list() == []
    assert kinds(h2m, "emg.squeeze_rejected")


def test_squeeze_after_reaction_time_approves_and_saves(h2m):
    flinch_and_explain(h2m)
    p = _squeeze(h2m, since_announce_s=learning_mod.MIN_APPROVE_REACTION_S + 0.2)
    rules = h2m.rules.list()
    assert [r.id for r in rules] == [p.rule.id] and rules[0].approved is True
    assert rules[0].incident_id == p.incident_id and rules[0].clip
    assert kinds(h2m, "rule.approved")[-1].data["by"] == "emg"


def test_squeeze_is_ambiguous_with_request_and_rule_waiting(h2m):
    from tests.test_app_stop import make_pending

    flinch_and_explain(h2m)
    make_pending(h2m, announced_at=now() - 5)
    answer_later(h2m.learner.proposal, 5)
    h2m.log.emit("emg.squeeze", effort=0.6, held_ms=350)
    time.sleep(0.2)
    assert h2m.rules.list() == [] and h2m.pending is not None
    assert "ambiguous" in kinds(h2m, "emg.squeeze_rejected")[-1].data["reason"]


def test_yes_approves_and_no_rejects(h2m):
    flinch_and_explain(h2m)
    answer_later(h2m.learner.proposal)
    h2m.on_utterance("yes", source="typed")
    assert len(h2m.rules.list()) == 1
    h2m.arm.reset(), h2m.arm.arm()
    took_it_back(h2m)
    # The second carry is already rerouted, so the same rule would change nothing and the
    # checker rejects it (see test_duplicate_rule_that_changes_nothing_is_not_proposed).
    # A different one (slow down) is proposed, and "no" drops it.
    h2m.learner.muse = FakeMuse(reply={**GOOD, "rule": {**GOOD["rule"], "response": "slow_down"}})
    flinch_and_explain(h2m)
    answer_later(h2m.learner.proposal)
    h2m.on_utterance("no", source="typed")
    assert h2m.learner.proposal is None and len(h2m.rules.list()) == 1


def test_spoken_yes_counts_from_speech_onset(h2m):
    flinch_and_explain(h2m)
    h2m.learner.proposal.announced_at = now() - 0.5
    # 0.5 s ago minus 0.4 s of audio and 0.2 s of ASR: began before the question + 0.3 s
    h2m.on_utterance("yes", {"asr_ms": 200, "audio_s": 0.4, "echo": False}, source="voice")
    assert h2m.rules.list() == [] and h2m.learner.proposal is not None


def test_ui_approval_pins_the_proposal_and_expires(h2m):
    flinch_and_explain(h2m)
    p = h2m.learner.proposal
    answer_later(p)
    assert h2m.learner.approve(by="ui", expected_id="r_stale") == (False, "proposal changed")
    p.expires_at = now() - 1
    assert h2m.learner.approve(by="ui", expected_id=p.rule.id) == (False, "proposal expired")
    assert h2m.rules.list() == []


def test_unanswered_proposal_expires(h2m):
    flinch_and_explain(h2m)
    h2m.learner.proposal.expires_at = now() - 1
    h2m.learner.expire()
    assert h2m.learner.proposal is None and kinds(h2m, "rule.expired")


def test_approval_fails_closed_on_corrupt_store(h2m):
    flinch_and_explain(h2m)
    answer_later(h2m.learner.proposal)
    h2m.rules.path.parent.mkdir(parents=True, exist_ok=True)
    h2m.rules.path.write_text("{not json")
    ok, why = h2m.learner.approve(by="ui")
    assert not ok and "could not save" in why
    assert h2m.rules.path.read_text() == "{not json"  # never overwritten


# ── review 2, item 2: no squeeze answer or rule approval right after a stop ────
def wait_for(pred, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not pred():
        time.sleep(0.01)
    return bool(pred())


def test_after_stop_hold_off_ships_at_three_seconds_and_only_rises(h2m):
    from h2m.app import H2M

    assert load_config()["safety"]["after_stop_s"] == 3.0 and h2m.after_stop_s == 3.0
    cfg = {**h2m.cfg, "safety": {**h2m.cfg["safety"], "after_stop_s": 0.5}}
    assert H2M(cfg).after_stop_s == 3.0  # floor
    assert h2m.stopped_recently(float("nan")) and h2m.stopped_recently(float("inf"))


@pytest.mark.parametrize("veto", ["emg.brace", "emg.open_hand"])
def test_N5_a_startle_squeeze_after_an_idle_brace_confirms_nothing(h2m, veto):
    """R8 / N5: a startle on an idle arm (brace or open hand), a request waiting, and the
    startle's squeeze ~2 s later. The squeeze passes the request's own reaction gate but
    began within 3 s of the brace: rejected. 3 s later a squeeze confirms."""
    h2m.log.emit(veto, onset_t=now(), extensor=0.9)
    assert h2m.arm.state is ArmState.READY  # idle: the brace stopped nothing
    h2m.on_utterance("hand me the cup", source="typed")
    pending = h2m.pending
    assert pending is not None
    pending.announced_at = now() - 2.5
    h2m.log.emit("emg.squeeze", effort=0.78, held_ms=405)
    time.sleep(0.2)
    assert h2m.pending is pending and not kinds(h2m, "arm.started")
    assert kinds(h2m, "emg.squeeze_rejected")[-1].data["reason"] == "too soon after a stop"
    stop_was_long_ago(h2m, 3.5)
    h2m.log.emit("emg.squeeze", effort=0.78, held_ms=405)
    assert wait_for(lambda: kinds(h2m, "arm.started"))


def test_a_squeeze_right_after_a_burst_stop_confirms_nothing(h2m):
    h2m.burst_freeze = True
    start_carry(h2m)
    h2m.log.emit("emg.burst_onset", onset_t=now())
    assert h2m.arm.state is ArmState.STOPPED
    h2m.learner._queue.get_nowait()  # the incident worker is not running here
    assert h2m.arm.reset()[0] and h2m.arm.arm()[0]
    took_it_back(h2m)
    h2m.on_utterance("hand me the block", source="typed")
    pending = h2m.pending
    assert pending is not None
    pending.announced_at = now() - 2
    n = len(kinds(h2m, "arm.started"))
    h2m.log.emit("emg.squeeze", effort=0.6, held_ms=350)
    time.sleep(0.2)
    assert h2m.pending is pending and len(kinds(h2m, "arm.started")) == n
    assert kinds(h2m, "emg.squeeze_rejected")[-1].data["reason"] == "too soon after a stop"


@pytest.mark.parametrize("how", ["ui", "yes", "squeeze"])
def test_no_rule_is_approved_within_three_seconds_of_a_stop(h2m, how):
    flinch_and_explain(h2m)
    p = h2m.learner.proposal
    answer_later(p, 5)
    h2m.log.emit("emg.open_hand", extensor=0.9)  # another startle while the card is up
    if how == "ui":
        ok, why = h2m.learner.approve(by="ui", expected_id=p.rule.id)
        assert not ok and "too soon after a stop" in why
    elif how == "yes":
        h2m.on_utterance("yes", source="typed")
        assert "too soon after a stop" in h2m.reply
    else:
        h2m.log.emit("emg.squeeze", effort=0.6, held_ms=350)
        time.sleep(0.2)
    assert h2m.rules.list() == [] and h2m.learner.proposal is p
    stop_was_long_ago(h2m, 3.5)
    assert h2m.learner.approve(by="ui", expected_id=p.rule.id)[0]


# ── review 2, item 3: a burst stop is possibly voluntary ──────────────────────
def burst_stop(h):
    h.burst_freeze = True
    start_carry(h)
    h.log.emit("emg.burst_onset", onset_t=now())
    assert h.arm.state is ArmState.STOPPED and kinds(h, "incident.triggered")


def prompt_text(h):
    return h.learner.muse.calls[-1][1][1]["content"][0]["text"]


def test_burst_stop_with_no_flinch_after_it_proposes_no_rule(h2m, monkeypatch):
    monkeypatch.setattr(learning_mod, "BURST_FOLLOW_S", 0.3)
    burst_stop(h2m)
    assert h2m.learner.run_once(timeout=1.0)
    data = kinds(h2m, "incident.explained")[-1].data
    assert data["source"] == "burst" and data["proposal"] is None
    assert learning_mod.NO_FOLLOW in data["problems"] and h2m.learner.proposal is None
    assert data["say"] == FALLBACK_SAY  # no yes/no question it could not act on
    text = prompt_text(h2m)
    assert "POSSIBLY VOLUNTARY" in text and "No brace or open hand followed" in text
    meta = json.loads((h2m.learner.root / data["incident_id"] / "meta.json").read_text())
    assert meta["trigger"]["possibly_voluntary"] is True
    assert meta["trigger"]["followed_by_flinch"] is False


def test_burst_stop_with_a_brace_too_late_after_it_proposes_no_rule(h2m, monkeypatch):
    monkeypatch.setattr(learning_mod, "BURST_FOLLOW_S", 0.3)
    burst_stop(h2m)
    time.sleep(0.4)
    h2m.log.emit("emg.brace", onset_t=now())  # after the follow window
    assert h2m.learner.run_once(timeout=1.0)
    assert kinds(h2m, "incident.explained")[-1].data["proposal"] is None


@pytest.mark.parametrize("veto", ["emg.brace", "emg.open_hand"])
def test_burst_stop_followed_by_a_flinch_proposes_as_usual(h2m, monkeypatch, veto):
    monkeypatch.setattr(learning_mod, "BURST_FOLLOW_S", 0.5)
    burst_stop(h2m)
    time.sleep(0.05)
    h2m.log.emit(veto, onset_t=now(), extensor=0.9)  # the arm is already stopped
    assert len(kinds(h2m, "incident.triggered")) == 1  # still one incident
    assert h2m.learner.run_once(timeout=1.0)
    data = kinds(h2m, "incident.explained")[-1].data
    assert data["proposal"] is not None and data["proposal"]["rule"]["approved"] is False
    text = prompt_text(h2m)
    assert "POSSIBLY VOLUNTARY" in text and "A brace or open hand followed" in text


def test_a_brace_incident_is_not_labelled_possibly_voluntary(h2m):
    flinch_and_explain(h2m)
    assert "VOLUNTARY" not in prompt_text(h2m)


# ── route choice and modifiers ───────────────────────────────────────────────
def save(h, response, tags=("liquid",), avoid="laptop"):
    r = Rule(list(tags), avoid, response, approved=True)
    h.rules.add(r)
    return r


def ask(h, text="hand me the cup"):
    h.on_utterance(text, source="typed")
    return h.last["contract"], h.last["decision"]


def test_full_loop_reroutes_after_approval(h2m):
    _, _ = ask(h2m)
    assert h2m.last["contract"]["route"] == "direct"
    h2m.cancel("test")
    flinch_and_explain(h2m)
    answer_later(h2m.learner.proposal)
    rule_id = h2m.learner.proposal.rule.id
    assert h2m.learner.approve(by="ui", expected_id=rule_id)[0]
    assert h2m.arm.state is ArmState.STOPPED  # approving never resumes motion
    assert h2m.arm.reset()[0] and h2m.arm.arm()[0]
    took_it_back(h2m)
    contract, decision = ask(h2m)
    assert decision["verdict"] == "ask"  # a carry still needs a confirmation
    assert contract["route"] == "via_left" and contract["applied_rules"] == [rule_id]
    assert h2m.confirm(by="ui")[0]
    started = kinds(h2m, "arm.started")[-1].data
    assert started["route"] == "via_left" and started["applied_rules"] == [rule_id]
    assert any(wp.label == "via.via_left" for wp in h2m.arm._plan.waypoints)
    # generalization: an untagged object is unaffected
    h2m.arm.stop("test"), h2m.arm.reset(), h2m.arm.arm()
    contract, _ = ask(h2m, "hand me the block")
    assert contract["route"] == "direct" and contract["applied_rules"] == []


def test_no_route_left_holds_and_says_why(h2m):
    r = save(h2m, "route_around")
    from dataclasses import replace

    h2m.routes = replace(h2m.routes, zones={"laptop": (-1.0, -1.0, 2.0, 2.0)})
    contract, decision = ask(h2m)
    assert decision["verdict"] == "hold" and contract["route"] is None
    assert "Every route" in decision["reason"] and h2m.reply == decision["reason"]
    assert contract["applied_rules"] == [r.id] and h2m.pending is None


def test_corrupt_rules_hold_tagged_carry_only(h2m):
    h2m.rules.path.parent.mkdir(parents=True, exist_ok=True)
    h2m.rules.path.write_text("[{]")
    _, decision = ask(h2m)
    assert decision["verdict"] == "hold" and "can't read my saved rules" in decision["reason"]
    _, decision = ask(h2m, "hand me the block")  # no rule can match an untagged object
    assert decision["verdict"] == "ask"


def test_missing_route_map_holds_when_rules_exist(h2m):
    h2m.routes = None
    _, decision = ask(h2m)
    assert decision["verdict"] == "ask"  # no rules: nothing to avoid
    save(h2m, "slow_down")
    for text in ("hand me the cup", "hand me the block"):  # tags unknown without the map
        _, decision = ask(h2m, text)
        assert decision["verdict"] == "hold" and "route map" in decision["reason"]


def test_slow_down_and_grip_gentler_only_lower(h2m):
    slow, gentle = save(h2m, "slow_down"), save(h2m, "grip_gentler")
    contract, _ = ask(h2m, "gently hand me the cup")
    assert contract["speed"] == 0.5
    assert contract["grip_effort"] <= 0.35 * 0.6 + 1e-9
    assert set(contract["applied_rules"]) == {slow.id, gentle.id}
    assert h2m.confirm(by="ui")[0]
    started = kinds(h2m, "arm.started")[-1].data
    assert started["speed"] == 0.5 and started["grip"]["effort"] <= 0.35 * 0.6 + 1e-9


def test_misconfigured_caps_never_raise_speed_or_grip(h2m):
    h2m.rules_cfg = {**h2m.rules_cfg, "slow_down_speed": 5.0, "grip_gentler": 3.0}
    save(h2m, "slow_down"), save(h2m, "grip_gentler")
    contract, _ = ask(h2m, "firmly hand me the cup")
    assert contract["speed"] <= 1.0
    assert contract["grip_effort"] <= 0.85  # the spoken "firmly" effort, not raised


def test_lift_higher_keeps_only_high_routes(h2m):
    r = save(h2m, "lift_higher")
    contract, _ = ask(h2m)
    assert contract["route"] == "via_high" and contract["applied_rules"] == [r.id]
    from dataclasses import replace

    h2m.routes = replace(h2m.routes, high_routes=[])
    _, decision = ask(h2m)
    assert decision["verdict"] == "hold"


def test_route_without_taught_waypoints_is_never_chosen(h2m):
    h2m.arm.poses.routes.pop("via_left")
    save(h2m, "route_around")
    contract, _ = ask(h2m)
    assert contract["route"] == "via_high"  # via_left is not executable


def test_squeeze_confirm_effort_is_capped_by_grip_gentler(h2m):
    save(h2m, "grip_gentler")
    ask(h2m)
    h2m.pending.announced_at = now() - 2
    h2m.log.emit("emg.squeeze", effort=1.0, held_ms=350)
    time.sleep(0.3)
    started = kinds(h2m, "arm.started")[-1].data
    assert started["grip"]["effort"] <= 0.35 * 0.6 + 1e-9


# ── HTTP ─────────────────────────────────────────────────────────────────────
def test_http_rules_and_incident_endpoints(h2m):
    from fastapi.testclient import TestClient

    from h2m.server import create_app

    client = TestClient(
        create_app(h2m, token="t0k"), base_url="http://127.0.0.1", headers={"X-H2M-Token": "t0k"}
    )
    assert client.get("/api/rules").json()["rules"] == []
    data = flinch_and_explain(h2m)
    p = client.get("/api/rules/proposal").json()["proposal"]
    assert p["rule"]["approved"] is False
    assert client.post("/api/rules/proposal/r_stale/approve").status_code == 409
    answer_later(h2m.learner.proposal)
    assert client.post(f"/api/rules/proposal/{p['rule']['id']}/approve").json()["ok"]
    rules = client.get("/api/rules").json()["rules"]
    assert [r["id"] for r in rules] == [p["rule"]["id"]]
    assert client.get("/api/incident").json()["incident"]["state"] == "explained"
    got = client.get(f"/api/incident/{data['incident_id']}").json()
    assert got["incident"]["id"] == data["incident_id"] and got["explanation"]["say"]
    assert client.get("/api/incident/..%2F..%2Fetc").status_code in (400, 404)
    assert client.get("/api/incident/nope").status_code == 404
    assert client.delete(f"/api/rules/{p['rule']['id']}").json()["ok"]
    assert client.delete("/api/rules/gone").status_code == 404
    h2m.rules.path.write_text("garbage")
    assert client.get("/api/rules").status_code == 503
    assert client.delete("/api/rules/x").status_code == 503
    assert client.post("/api/rules/proposal/x/reject").status_code == 409


def test_shaping_never_raises_an_already_lower_speed_or_grip(h2m):
    from h2m.contract import StepContract, Verb

    save(h2m, "route_around")
    c = StepContract(Verb.PICK_PLACE, "cup", "paper cup", (0.25, 0.55), grip_effort=0.1)
    c.speed = 0.3
    assert h2m._shape_carry(c) is None
    assert c.speed == 0.3 and c.grip_effort == 0.1 and c.route == "via_left"
    save(h2m, "slow_down"), save(h2m, "grip_gentler")
    for _ in range(3):  # idempotent across re-checks
        h2m._shape_carry(c)
    assert c.speed == 0.3 and c.grip_effort == 0.1


# added alongside today's changes
class ToolMuse(FakeMuse):
    """A Muse client that speaks the Responses path: a scripted list of turns, each a
    list of (tool, args); an empty turn ends with text. ``segment`` returns one box
    whose x grows with the frame's brightness (the incident frames), so track() moves."""

    explanation_source = None

    def __init__(self, script, box=(10.0, 10.0, 50.0, 50.0), fail=False):
        super().__init__()
        self.script, self.box, self.fail_respond = list(script), box, fail
        self.bodies: list[dict] = []
        self.turn = 0

    def segment(self, model, image, label):
        if self.box is None:
            return None
        shift = float(image[0, 0, 0]) if image is not None and image.size else 0.0
        x1, y1, x2, y2 = self.box
        return (x1 + shift, y1, x2 + shift, y2)

    def respond(self, body):
        import copy

        self.threads.append(threading.current_thread().name)
        self.bodies.append(copy.deepcopy(body))
        if self.fail_respond:
            raise RuntimeError("API down")
        i, self.turn = self.turn, self.turn + 1
        turn = self.script[i] if i < len(self.script) else []
        if not turn:
            return {"output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "Nothing in the clip explains it."}]}]}
        return {"output": [
            {"type": "function_call", "call_id": f"t{i}-{j}", "name": name,
             "arguments": json.dumps(args)}
            for j, (name, args) in enumerate(turn)
        ]}

SCRIPT = [
    [("track", {"phrase": "cup"}), ("track", {"phrase": "laptop"})],
    [("measure", {"a": "cup", "b": "laptop", "t": 0.0}), ("body_trace", {})],
    [("propose_rule", GOOD)],
]

WRONG = {**GOOD, "held_property": "rigid",
         "rule": {"when_holding": ["rigid"], "avoid": "laptop", "response": "route_around"}}

def with_camera(h):
    """The fixture has no camera: give the learner the simulation's virtual one so the
    clip has frames for SAM to look at (a flat grey table)."""
    import numpy as np

    h.learner.virtual_camera = lambda: np.full((240, 320, 3), 90, np.uint8)

def test_tool_loop_publishes_feed_calls_checks_and_rehearsal(h2m):
    with_camera(h2m)
    h2m.learner.muse = ToolMuse(SCRIPT)
    data = flinch_and_explain(h2m)
    assert h2m.arm.state is ArmState.STOPPED  # the loop never touches the arm
    assert data["explanation"]["source"] == "wonder" and data["investigation"]["status"] == "proposed"
    assert data["investigation"]["tool_calls"] == 5 and data["investigation"]["rejected"] == 0
    snap = h2m.snapshot()
    learning = snap["learning"]
    # the feed: [{t, tool, args, result_summary, ms}] in order
    feed = learning["investigation"]
    assert [e["tool"] for e in feed] == [
        "wonder", "sam-3.1 track", "sam-3.1 track", "wonder", "measure", "measure body_trace",
        "wonder", "wonder propose_rule", "checker",
    ]
    assert all({"t", "tool", "args", "result_summary", "ms"} <= set(e) for e in feed)
    assert "cup-laptop" in feed[4]["result_summary"] and feed[-1]["ticks"] == [True, True]
    # the same lines as incident.tool events in the snapshot's event list (the stage feed)
    kinds_seen = [e["kind"] for e in snap["events"]]
    assert kinds_seen.count("incident.tool") == len(feed) and "incident.investigated" in kinds_seen
    tool_ev = [e for e in snap["events"] if e["kind"] == "incident.tool"][-1]
    assert tool_ev["tool"] == "checker" and tool_ev["ticks"] == [True, True] and tool_ev["ok"]
    # state.calls carries the Wonder and SAM calls with their ms
    calls = [(c["model"], c["for"]) for c in snap["calls"]]
    assert calls.count(("muse-wonder-1.0", "investigate")) == 3
    assert all(isinstance(c.get("ms"), int) for c in snap["calls"])
    # the proposal: both ticks, the verifier for the card, the rehearsal for the twin
    p = learning["proposal"]
    assert p["checks"]["restrictive"] is True and p["checks"]["prevented"] is True
    assert "via_left" in p["checks"]["detail"]["prevented"]
    assert p["verifier"] == {"removes options only": True, "incident prevented": True}
    rh = p["rehearsal"]
    assert rh["old_route_name"] == "direct" and rh["new_route_name"] == "via_left"
    assert rh["old_route"][0] == [0.25, 0.55] and len(rh["new_route"]) == 3
    assert rh["state"] in ("playing", "done") and 0.0 <= rh["progress"] <= 1.0
    assert rh["source"] == {"file": "configs/routes.yaml", "measured": False}
    assert "placeholder" in rh["note"] and "twin only" in rh["note"]
    # the Wonder requests: the clip, the zones and tags it may use, then the tool outputs
    first = h2m.learner.muse.bodies[0]
    assert first["model"] == "muse-wonder-1.0" and first["input"][0]["content"][-1]["type"] == "input_video"
    assert "laptop" in first["input"][0]["content"][0]["text"]
    assert "liquid" in first["input"][0]["content"][0]["text"]
    outputs = [it for it in h2m.learner.muse.bodies[2]["input"] if it["type"] == "function_call_output"]
    seen = json.loads(outputs[0]["output"])["found_in"]
    assert 1 <= seen <= h2m.learner.inv_cfg.track_frames  # one SAM call per frame, per phrase
    assert calls.count(("sam-3.1", "track")) == 2 * seen
    # the clip was saved next to the keyframes and the explanation records the loop
    inc_dir = h2m.learner.root / data["incident_id"]
    assert (inc_dir / "clip.mp4").exists()
    saved = json.loads((inc_dir / "explanation.json").read_text())
    assert saved["checks"]["prevented"] is True and saved["investigation"]["status"] == "proposed"
    assert json.loads((inc_dir / "meta.json").read_text())["carry"]["slot_id"] == "slot_1"

def test_rehearsal_plays_in_the_twin_only_and_no_timer_approves(h2m):
    h2m.learner.cfg["rehearsal_s"] = 0.2
    h2m.learner.muse = ToolMuse(SCRIPT)
    flinch_and_explain(h2m)
    p = h2m.learner.proposal
    assert p.rehearsal_state()["state"] == "playing"
    time.sleep(0.3)
    done = p.rehearsal_state()
    assert done["state"] == "done" and done["progress"] == 1.0
    assert h2m.rules.list() == [] and h2m.learner.proposal is p  # finishing approves nothing
    assert h2m.arm.state is ArmState.STOPPED  # and moves nothing, virtual or real
    assert not kinds(h2m, "arm.started")[1:]  # only the carry's start is in the log

def test_rejected_proposal_goes_back_to_wonder_once_then_is_dropped(h2m):
    h2m.learner.muse = ToolMuse([[("propose_rule", WRONG)], [("propose_rule", GOOD)]])
    data = flinch_and_explain(h2m)
    assert data["investigation"]["rejected"] == 1 and data["proposal"]["rule"]["when_holding"] == ["liquid"]
    feed = h2m.learner.snapshot()["investigation"]
    checker = [e for e in feed if e["tool"] == "checker"]
    assert checker[0]["ok"] is False and checker[0]["ticks"] == [True, False]
    assert "rigid" in checker[0]["result_summary"] and checker[1]["ticks"] == [True, True]
    # the reason went back to Wonder as the tool's result
    out = json.loads(h2m.learner.muse.bodies[1]["input"][-1]["output"])
    assert out["ok"] is False and "not prevented" in out["reason"] and "liquid" in out["tags"]
    # two rejections: dropped, the one-shot path runs next, and the arm stays stopped
    h2m.arm.reset(), h2m.arm.arm()
    took_it_back(h2m)
    h2m.learner.muse = ToolMuse([[("propose_rule", WRONG)], [("propose_rule", WRONG)]])
    data = flinch_and_explain(h2m)
    assert data["investigation"]["status"] == "rejected" and data["investigation"]["rejected"] == 2
    assert h2m.learner.muse.turn == 2  # no third turn
    assert data["proposal"] is None and data["say"] == FALLBACK_SAY
    assert data["explanation"]["source"] == "fallback"  # no second opinion from chat()
    assert h2m.learner.muse.calls == []  # the one-shot path did not run
    assert h2m.arm.state is ArmState.STOPPED

def test_loop_failure_falls_back_to_the_one_shot_path_then_the_fixed_question(h2m):
    h2m.learner.muse = ToolMuse(SCRIPT, fail=True)  # respond raises; chat still answers GOOD
    data = flinch_and_explain(h2m)
    assert data["investigation"]["status"] == "error"
    assert data["explanation"]["source"] == "wonder" and data["proposal"]["checks"]["prevented"]
    assert h2m.arm.state is ArmState.STOPPED
    h2m.arm.reset(), h2m.arm.arm()
    took_it_back(h2m)
    muse = ToolMuse([], fail=True)
    muse.fail = True  # chat fails too
    h2m.learner.muse = muse
    data = flinch_and_explain(h2m)
    assert data["explanation"]["source"] == "fallback" and data["say"] == FALLBACK_SAY
    assert data["proposal"] is None and h2m.arm.state is ArmState.STOPPED
    # tools off in the config: the one-shot path only, no Responses call
    h2m.arm.reset(), h2m.arm.arm()
    took_it_back(h2m)
    h2m.learner.tools = False
    h2m.learner.muse = ToolMuse(SCRIPT)
    data = flinch_and_explain(h2m)
    assert data["investigation"] is None and h2m.learner.muse.bodies == []
    assert data["proposal"] is not None and h2m.learner.snapshot()["tools"] is False

def test_one_shot_proposal_is_checked_too(h2m):
    h2m.learner.muse = FakeMuse(reply=WRONG)  # chat() only: the one-shot path
    data = flinch_and_explain(h2m)
    assert data["proposal"] is None and any("not prevented" in p for p in data["problems"])
    assert data["say"] == FALLBACK_SAY

def test_duplicate_rule_that_changes_nothing_is_not_proposed(h2m):
    flinch_and_explain(h2m)
    answer_later(h2m.learner.proposal)
    assert h2m.learner.approve(by="ui")[0]
    h2m.arm.reset(), h2m.arm.arm()
    took_it_back(h2m)
    data = flinch_and_explain(h2m)  # the carry was on via_left, clear of the laptop
    assert data["proposal"] is None
    assert any("never crosses" in p for p in data["problems"])

def test_applied_rules_name_the_tag_that_matched(h2m):
    flinch_and_explain(h2m)
    answer_later(h2m.learner.proposal)
    rule_id = h2m.learner.proposal.rule.id
    assert h2m.learner.approve(by="ui")[0]
    h2m.arm.reset(), h2m.arm.arm()
    took_it_back(h2m)
    contract, _ = ask(h2m)
    assert contract["applied_rules"] == [rule_id]
    assert h2m.snapshot()["learning"]["applied_rules"] == [
        {"id": rule_id, "index": 1, "tags": ["liquid"], "response": "route_around", "avoid": "laptop"}
    ]
    h2m.cancel("test")
    ask(h2m, "hand me the block")
    assert h2m.snapshot()["learning"]["applied_rules"] == []
