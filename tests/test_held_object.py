"""A held object stays held and stays checked (virtual arm, mocked speech and vision).

H2: place_near_user lowers only after a fresh, unoccluded look at the spot, under a
hand watchdog. M1: home / point never command the gripper unless it is proven empty,
and moving with a held object is a carry (rules, confirmation). M2: no pick while
holding; an unknown gripper is said in the question."""

from __future__ import annotations

import time

from armed.core import now
from armed.robot import ArmState
from armed.robot.backend import GRIPPER
from armed.rules import Rule
from armed.safety import Verdict
from armed.world import HOLDING, HandsSeen
from tests import test_app_embodied as base
from tests.test_app_embodied import (
    _carry_then_stop,
    _go_home,
    _hands,
    _sees_past_arm,
    prove_empty,
    run,
    run_live,
    set_cfg,
    started,
)

armed = base.armed  # the shared app fixture
FAR = (0.9, 0.1)


def events(h, kind):
    return [e.data for e in h.log.recent(400, {kind})]


def wait_for(cond, seconds=3.0):
    deadline = now() + seconds
    while now() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


def held_at_home(h):
    """Holding the cup at home with a clear, fresh view of the spot."""
    _carry_then_stop(h)
    grip_cmd = h.arm._cmd[GRIPPER]
    _go_home(h)
    _hands(h, [FAR])
    assert h.gripper.state().holding
    return grip_cmd


def confirm_place(h):
    h.run_tool_calls([("place_near_user", {})])
    assert h.pending is not None, h.reply
    ok, why = h.confirm(by="ui")
    assert ok, why
    assert started(h)[-1]["phase"] == "approach"


def arrive_above(h, points=(FAR,)):
    """Run stage 1 to above the spot, with a fresh hand pass on each tick."""
    assert run_live(h, 30, until=lambda: h.arm.state is not ArmState.MOVING, points=points)
    assert h.arm.state is ArmState.READY


def lower_started(h):
    return any(s["skill"] == "place_near_user.lower" for s in started(h))


# ── H2: place_near_user ──────────────────────────────────────────────────────
def test_hand_arriving_during_the_carry_means_no_release(armed):
    """Review repro: a hand lands on the spot during the multi-second carry.
    The arm stops above the spot and never lowers or opens."""
    grip_cmd = held_at_home(armed)
    _sees_past_arm(armed)
    set_cfg(armed, handover_recheck_s=0.3)
    confirm_place(armed)
    spot = tuple(armed.routes.handover.spot)
    arrive_above(armed, points=(spot,))  # a hand right on the spot, on fresh passes
    armed.vision.hands_seen = HandsSeen(now(), (spot,))
    assert wait_for(lambda: events(armed, "intent.blocked"))
    assert not lower_started(armed)
    assert armed.gripper.state().status == HOLDING
    assert armed.arm.backend._goal[GRIPPER] == grip_cmd  # never opened
    assert "hand" in armed.reply and "above the spot" in armed.reply


def place_started(h):
    return any(s["skill"].startswith("place_near_user") for s in started(h))


def test_spot_hidden_by_the_arm_refuses_stage_one_and_says_why(armed):
    """Shipped config: the camera is not known to see past the arm, so stage 2 could
    never lower. F2: stage 1 is refused at the gate (it would only strand the object
    above the spot), and the reason is spoken, not only shown."""
    held_at_home(armed)
    assert armed.routes.handover.camera_sees_past_arm is False
    armed.spoken.clear()
    [r] = armed.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and r.decision.checks["spot_watchable"] is False
    assert armed.pending is None and not place_started(armed)
    assert armed.spoken and "past my arm" in armed.spoken[-1] and armed.reply == armed.spoken[-1]
    assert armed.gripper.state().holding
    # the occlusion check itself is unchanged: over the spot the arm hides it
    armed.arm.backend.set_measured(armed.arm.poses.pose("handover_above").joints)
    run(armed, 0.1)
    assert armed._arm_hides_spot()
    assert armed._spot_clear() == (False, "my arm hides the spot from the camera")


def test_no_hand_tracker_refuses_stage_one_and_says_why(armed):
    held_at_home(armed)
    _sees_past_arm(armed)
    armed.vision.hands = None  # camera up, but the hand model did not load
    armed.spoken.clear()
    [r] = armed.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and "hand tracking" in r.decision.reason
    assert armed.pending is None and not place_started(armed)
    assert armed.spoken and "hand tracking" in armed.spoken[-1]
    armed.vision = None  # no camera at all
    [r] = armed.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and not r.decision.checks["spot_watchable"]


def test_hand_tracker_lost_before_the_confirm_refuses_stage_one(armed):
    held_at_home(armed)
    _sees_past_arm(armed)
    armed.run_tool_calls([("place_near_user", {})])
    assert armed.pending is not None
    armed.vision.hands = None
    ok, why = armed.confirm(by="ui")
    assert not ok and "hand tracking" in why and not place_started(armed)


def test_stage_two_spoken_reason_when_it_cannot_lower(armed):
    held_at_home(armed)
    _sees_past_arm(armed)
    set_cfg(armed, handover_recheck_s=0.3)
    confirm_place(armed)
    spot = tuple(armed.routes.handover.spot)
    armed.spoken.clear()
    arrive_above(armed, points=(spot,))
    armed.vision.hands_seen = HandsSeen(now(), (spot,))
    assert wait_for(lambda: events(armed, "intent.blocked"))
    assert wait_for(lambda: armed.spoken and "above the spot" in armed.spoken[-1])


def test_lowering_needs_a_hand_pass_newer_than_the_arrival(armed):
    held_at_home(armed)
    _sees_past_arm(armed)
    set_cfg(armed, handover_recheck_s=0.3)
    confirm_place(armed)
    seen_before = HandsSeen(now(), (FAR,))  # clear, and fresh by the stale rule ...
    armed.vision.hands_seen = seen_before
    assert run(armed, 30, until=lambda: armed.arm.state is not ArmState.MOVING)
    armed.vision.hands_seen = seen_before  # ... but taken before the arm got there
    assert wait_for(lambda: events(armed, "intent.blocked"))
    assert not lower_started(armed) and armed.gripper.state().holding


def test_hand_during_the_lowering_stops_the_arm_before_it_opens(armed):
    grip_cmd = held_at_home(armed)
    _sees_past_arm(armed)
    confirm_place(armed)
    arrive_above(armed)
    assert run_live(armed, 10, until=lambda: lower_started(armed))
    # A hand comes in just outside clear_radius but inside the watchdog margin.
    spot = armed.routes.handover.spot
    near = (spot[0] + armed.routes.handover.clear_radius + 0.02, spot[1])
    assert run_live(armed, 10, until=lambda: armed.arm.state is not ArmState.MOVING, points=(near,))
    assert armed.arm.state is ArmState.STOPPED and "handover stopped" in armed.arm.reason
    assert armed.arm.backend._goal[GRIPPER] == grip_cmd  # the gripper never opened
    assert armed.gripper.state().holding  # stopped while lowering: still holding


def test_stale_hand_pass_during_the_lowering_stops_the_arm(armed):
    held_at_home(armed)
    _sees_past_arm(armed)
    confirm_place(armed)
    arrive_above(armed)
    assert run_live(armed, 10, until=lambda: lower_started(armed))
    armed.vision.hands_seen = HandsSeen(now() - 5.0, (FAR,))  # the hand tracker stalled
    run(armed, 0.2)
    assert armed.arm.state is ArmState.STOPPED and "stale" in armed.arm.reason
    armed.arm.reset(), armed.arm.arm()
    armed.vision.hands_seen = None  # and when it vanishes altogether
    assert not armed._spot_clear()[0]


def test_cancel_while_above_the_spot_drops_the_lowering(armed):
    held_at_home(armed)
    _sees_past_arm(armed)
    confirm_place(armed)
    real = armed._handover_world

    def world_then_cancel(contract, arrived_t):
        armed.cancel("never mind")  # lands while stage 2 is being checked
        return real(contract, arrived_t)

    armed._handover_world = world_then_cancel
    arrive_above(armed)
    assert wait_for(lambda: events(armed, "intent.blocked"))
    run_live(armed, 1.0, until=lambda: False)
    assert not lower_started(armed) and armed.gripper.state().holding


# ── M1: home / point with something in the gripper ──────────────────────────
def test_home_while_holding_keeps_the_grip(armed):
    _carry_then_stop(armed)
    held = armed.arm._cmd[GRIPPER]
    home_grip = armed.arm.poses.pose("home").joints[GRIPPER]
    assert held != home_grip
    armed.on_utterance("go home", source="typed")
    assert armed.pending is not None and "holding" in armed.pending.decision.reason
    assert armed.confirm(by="ui")[0]
    assert all(GRIPPER not in wp.joints for wp in armed.arm._plan.waypoints)
    assert run(armed, 30, until=lambda: armed.arm.state is ArmState.READY)
    assert armed.arm._cmd[GRIPPER] == held and armed.arm.backend._goal[GRIPPER] == held
    assert armed.gripper.state().holding


def test_home_taught_open_cannot_drop_a_held_object(armed):
    _carry_then_stop(armed)
    held = armed.arm._cmd[GRIPPER]
    home = armed.arm.poses.poses["home"]
    armed.arm.poses.poses["home"] = type(home)(home.name, {**home.joints, GRIPPER: 50.0}, home.taught)
    armed.on_utterance("go home", source="typed")
    assert armed.confirm(by="ui")[0]
    assert run(armed, 30, until=lambda: armed.arm.state is ArmState.READY)
    assert armed.arm.backend._goal[GRIPPER] == held != 50.0


def test_home_while_holding_obeys_rules_like_a_carry(armed):
    _carry_then_stop(armed)
    armed.rules.add(Rule(["liquid"], "laptop", "slow_down", approved=True))
    armed.on_utterance("go home", source="typed")
    assert armed.pending is not None and armed.pending.contract.speed <= 0.5
    armed.cancel("test")
    avoid = Rule(["liquid"], "laptop", "route_around", approved=True)
    armed.rules.add(avoid)
    armed.on_utterance("go home", source="typed")
    assert armed.pending is None and armed.last["decision"]["verdict"] == "hold"
    assert "laptop" in armed.last["decision"]["reason"]
    assert avoid.id in armed.last["contract"]["applied_rules"]


def test_home_with_unknown_contents_holds_when_any_rule_exists(armed):
    assert armed.gripper.state().status == "unknown"
    armed.rules.add(Rule(["liquid"], "laptop", "route_around", approved=True))
    armed.on_utterance("go home", source="typed")
    assert armed.pending is None and armed.last["decision"]["verdict"] == "hold"


def test_home_while_holding_always_confirms(armed):
    _carry_then_stop(armed)
    set_cfg(armed, always_confirm=False)  # would auto-run an empty-handed home
    armed.on_utterance("go home", source="typed")
    assert armed.pending is not None and started(armed)[-1]["skill"] == "pick_place"


def test_point_while_holding_keeps_the_grip(armed):
    _carry_then_stop(armed)
    held = armed.arm._cmd[GRIPPER]
    armed.on_utterance("point at the block", source="typed")
    assert armed.pending is not None, armed.last
    assert armed.confirm(by="ui")[0]
    assert all(GRIPPER not in wp.joints for wp in armed.arm._plan.waypoints)
    assert run(armed, 30, until=lambda: armed.arm.state is ArmState.READY)
    assert armed.arm.backend._goal[GRIPPER] == held


def test_home_with_a_proven_empty_gripper_still_commands_it(armed):
    prove_empty(armed)
    armed.on_utterance("go home", source="typed")
    assert armed.confirm(by="ui")[0]
    assert GRIPPER in armed.arm._plan.waypoints[0].joints


# ── M2: pick while holding ───────────────────────────────────────────────────
def test_pick_while_holding_holds(armed):
    _carry_then_stop(armed)
    [r] = armed.run_tool_calls([("pick_place", {"object_id": "block"})])
    assert r.decision.verdict is Verdict.HOLD and "Place it first" in r.decision.reason
    assert armed.pending is None
    armed.on_utterance("pick up the block", source="typed")
    assert armed.pending is None and armed.last["decision"]["checks"]["hand_free"] is False


def test_pick_with_unknown_gripper_says_so_in_the_question(armed):
    [r] = armed.run_tool_calls([("pick_place", {"object_id": "cup"})])
    assert r.decision.verdict is Verdict.ASK
    assert "can't tell if my gripper is empty" in armed.pending.decision.reason
    armed.cancel("test")
    prove_empty(armed)
    armed.run_tool_calls([("pick_place", {"object_id": "cup"})])
    assert armed.pending.decision.reason == "squeeze, say yes, or click to confirm"


# ── F3: a fault while carrying keeps the object known; a load blocks a pick ───
def _pick_to(h, stage, what="cup"):
    prove_empty(h)
    h.on_utterance(f"pick up the {what}", source="typed")
    assert h.confirm(by="ui")[0]
    assert run(h, 30, until=lambda: h.arm.snapshot()["stage"] == stage)


def test_fault_mid_carry_means_holding_and_the_next_pick_holds(armed):
    """Re-review repro: a stall mid-carry kept the grip but forgot the
    object, so the next pick was allowed and its first waypoint opened the gripper."""
    _pick_to(armed, "carry")
    run(armed, 0.3)
    grip = armed.arm._cmd[GRIPPER]
    armed.arm._fault("stalled before tray_above: arm blocked or slipping")  # what _step does
    held = armed.gripper.state()
    assert held.status == HOLDING and held.object_id == "cup" and "fault" in held.why
    assert armed.arm._cmd[GRIPPER] == grip  # the grip was kept
    armed.arm.reset(), armed.arm.arm()
    n = len(started(armed))
    armed.on_utterance("pick up the block", source="typed")
    assert armed.pending is None and armed.last["decision"]["verdict"] == "hold"
    assert armed.last["decision"]["checks"]["hand_free"] is False
    assert not armed.confirm(by="ui")[0] and len(started(armed)) == n


def test_fault_while_lowering_onto_the_spot_means_still_holding(armed):
    held_at_home(armed)
    _sees_past_arm(armed)
    confirm_place(armed)
    arrive_above(armed)
    assert run_live(armed, 10, until=lambda: lower_started(armed))
    run_live(armed, 0.2, until=lambda: False)
    assert armed.arm.snapshot()["stage"] == "lower"
    armed.arm._fault("tracking error: arm blocked or slipping")
    held = armed.gripper.state()
    assert held.status == HOLDING and held.object_id == "cup"


def test_fault_in_grasp_or_release_is_still_unknown(armed):
    _pick_to(armed, "grasp")
    armed.arm._fault("stalled")
    assert armed.gripper.state().status == "unknown"


def test_gripper_load_above_the_empty_threshold_blocks_a_pick(armed):
    armed.arm.load = 0.4  # something is in the jaws, whatever the tracker says
    armed.on_utterance("pick up the cup", source="typed")
    d = armed.last["decision"]
    assert d["verdict"] == "hold" and d["checks"]["hand_free"] is False and armed.pending is None
    assert "loaded" in d["reason"]
    prove_empty(armed)  # even a tracker that says EMPTY
    [r] = armed.run_tool_calls([("pick_place", {"object_id": "cup"})])
    assert r.decision.verdict is Verdict.HOLD and armed.pending is None
    armed.arm.load = 0.05  # below the threshold: asked about as before
    armed.on_utterance("pick up the cup", source="typed")
    assert armed.pending is not None


def test_load_rising_before_the_confirm_blocks_the_pick(armed):
    prove_empty(armed)
    armed.on_utterance("pick up the cup", source="typed")
    assert armed.pending is not None
    armed.arm.load = 0.4
    ok, why = armed.confirm(by="ui")
    assert not ok and "loaded" in why and armed.arm.state is ArmState.READY


# ── F5: a stop while closing on an object never keeps squeezing ──────────────
def test_brace_mid_grasp_freezes_the_gripper_at_its_measured_opening(armed):
    """Re-review repro: a brace during the grasp kept the closing goal."""
    prove_empty(armed)
    armed.on_utterance("pick up the block firmly", source="typed")
    assert armed.confirm(by="ui")[0]
    assert run(armed, 30, until=lambda: armed.arm.snapshot()["stage"] == "grasp")
    assert run(armed, 5, until=lambda: (armed.arm.load or 0) > 0.15)  # squeezing the block
    writes = []
    real = armed.arm.backend.write_goal
    armed.arm.backend.write_goal = lambda j: (writes.append(dict(j)), real(j))[1]
    measured = armed.arm.backend._measured[GRIPPER]
    assert armed.arm._cmd[GRIPPER] < measured  # commanded past contact
    armed.log.emit("emg.brace", onset_t=now())
    assert armed.arm.state is ArmState.STOPPED
    assert writes and writes[0][GRIPPER] == measured  # the freeze write sets the opening
    assert armed.arm._cmd[GRIPPER] == measured == armed.arm.backend._goal[GRIPPER]
    run(armed, 1.0)
    assert (armed.arm.load or 0) <= 0.15  # no longer squeezing
    armed.arm.reset()
    assert armed.arm._cmd[GRIPPER] == measured  # reset keeps it (contents unknown)


def test_stop_in_carry_still_keeps_the_grip(armed):
    _pick_to(armed, "carry")
    grip = armed.arm._cmd[GRIPPER]
    armed.stop("voice: stop")
    assert armed.arm._cmd[GRIPPER] == grip == armed.arm.backend._goal[GRIPPER]


# ── F6 and H2: stage 2 after disarm / stop / brace / a hand ──────────────────
def _above_and_waiting(h):
    """Stage 1 done above the spot; stage 2 is polling for a pass newer than arrival."""
    held_at_home(h)
    _sees_past_arm(h)
    confirm_place(h)
    arrive_above(h)
    h.vision.hands_seen = HandsSeen(now() - 1.0, (FAR,))  # older than arrival: it waits
    time.sleep(0.1)
    assert h._in_flight > 0 and not lower_started(h), "stage 2 should be polling"


def _fresh(h):
    h.vision.hands_seen = HandsSeen(now(), (FAR,))


def test_disarm_and_arm_during_the_recheck_drops_stage_two(armed):
    _above_and_waiting(armed)
    armed.disarm()
    armed.arm.arm()
    _fresh(armed)
    assert not wait_for(lambda: lower_started(armed), 1.0)
    assert armed.gripper.state().holding


def test_ui_disarm_endpoint_goes_through_the_app(armed):
    from fastapi.testclient import TestClient

    from armed.server import create_app

    _above_and_waiting(armed)
    c = TestClient(create_app(armed, token="t"), base_url="http://127.0.0.1")
    assert c.post("/api/arm/disarm", headers={"X-Armed-Token": "t"}).status_code == 200
    assert c.post("/api/arm/arm", headers={"X-Armed-Token": "t"}).status_code == 200
    _fresh(armed)
    assert not wait_for(lambda: lower_started(armed), 1.0)


def test_stop_reset_and_arm_during_the_recheck_drops_stage_two(armed):
    _above_and_waiting(armed)
    armed.stop("voice: stop")
    armed.arm.reset(), armed.arm.arm()
    _fresh(armed)
    assert not wait_for(lambda: lower_started(armed), 1.0)


def test_brace_during_the_recheck_latches_and_drops_stage_two(armed):
    _above_and_waiting(armed)
    armed.log.emit("emg.brace", onset_t=now())
    assert armed.arm.state is ArmState.STOPPED
    armed.arm.reset(), armed.arm.arm()
    _fresh(armed)
    assert not wait_for(lambda: lower_started(armed), 1.0)


def test_hand_arriving_mid_lower_stops_and_keeps_the_grip(armed):
    _above_and_waiting(armed)
    _fresh(armed)
    assert wait_for(lambda: lower_started(armed), 2.0)
    grip = armed.arm._cmd[GRIPPER]
    spot = armed.routes.handover.spot
    run_live(armed, 0.3, until=lambda: False)
    near = ((spot[0], spot[1] + 0.12),)
    run_live(armed, 5, until=lambda: armed.arm.state is not ArmState.MOVING, points=near)
    assert armed.arm.state is ArmState.STOPPED and "hand" in armed.arm.reason
    assert armed.arm._cmd[GRIPPER] == grip and armed.gripper.state().holding


# The operator gripper release (F2) is covered in tests/test_operator_release.py.


def test_direct_arm_disarm_and_arm_during_the_recheck_also_drops_stage_two(armed):
    """Re-review repro: even a disarm that bypasses the app (no epoch bump)
    cannot let stage 2 continue, because the arm was re-armed since stage 1."""
    _above_and_waiting(armed)
    armed.arm.disarm()
    armed.arm.arm()
    _fresh(armed)
    assert not wait_for(lambda: lower_started(armed), 1.0)


def test_disarm_drops_a_pending_request_and_a_muse_reply_in_flight(armed):
    from armed.compiler.llm import ToolReply
    from tests.test_app_embodied import FakeLLM

    armed.on_utterance("pick up the cup", source="typed")
    assert armed.pending is not None
    armed.disarm()
    assert armed.pending is None
    armed.arm.arm()
    prove_empty(armed)

    def disarm_and_rearm():
        armed.disarm()
        armed.arm.arm()

    armed.llm = FakeLLM(ToolReply([("gesture", {"name": "shake"})]), hook=disarm_and_rearm)
    armed.on_utterance("shake your head", source="typed")
    assert not started(armed) and armed.arm.state is ArmState.READY
