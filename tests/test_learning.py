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
    return kinds(h, "incident.explained")[-1].data


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
