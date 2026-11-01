"""A held object stays held and stays checked (virtual arm, mocked speech and vision).

H2: place_near_user lowers only after a fresh, unoccluded look at the spot, under a
hand watchdog. M1: home / point never command the gripper unless it is proven empty,
and moving with a held object is a carry (rules, confirmation). M2: no pick while
holding; an unknown gripper is said in the question."""

from __future__ import annotations

import time

from h2m.core import now
from h2m.robot import ArmState
from h2m.robot.backend import GRIPPER
from h2m.rules import Rule
from h2m.safety import Verdict
from h2m.world import HOLDING, HandsSeen
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

h2m = base.h2m  # the shared app fixture
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
def test_hand_arriving_during_the_carry_means_no_release(h2m):
    """Port of the review repro: a hand lands on the spot during the multi-second carry.
    The arm stops above the spot and never lowers or opens."""
    grip_cmd = held_at_home(h2m)
    _sees_past_arm(h2m)
    set_cfg(h2m, handover_recheck_s=0.3)
    confirm_place(h2m)
    spot = tuple(h2m.routes.handover.spot)
    arrive_above(h2m, points=(spot,))  # a hand right on the spot, on fresh passes
    h2m.vision.hands_seen = HandsSeen(now(), (spot,))
    assert wait_for(lambda: events(h2m, "intent.blocked"))
    assert not lower_started(h2m)
    assert h2m.gripper.state().status == HOLDING
    assert h2m.arm.backend._goal[GRIPPER] == grip_cmd  # never opened
    assert "hand" in h2m.reply and "above the spot" in h2m.reply


def place_started(h):
    return any(s["skill"].startswith("place_near_user") for s in started(h))


def test_spot_hidden_by_the_arm_refuses_stage_one_and_says_why(h2m):
    """Shipped config: the camera is not known to see past the arm, so stage 2 could
    never lower. F2: stage 1 is refused at the gate (it would only strand the object
    above the spot), and the reason is spoken, not only shown."""
    held_at_home(h2m)
    assert h2m.routes.handover.camera_sees_past_arm is False
    h2m.spoken.clear()
    [r] = h2m.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and r.decision.checks["spot_watchable"] is False
    assert h2m.pending is None and not place_started(h2m)
    assert h2m.spoken and "past my arm" in h2m.spoken[-1] and h2m.reply == h2m.spoken[-1]
    assert h2m.gripper.state().holding
    # the occlusion check itself is unchanged: over the spot the arm hides it
    h2m.arm.backend.set_measured(h2m.arm.poses.pose("handover_above").joints)
    run(h2m, 0.1)
    assert h2m._arm_hides_spot()
    assert h2m._spot_clear() == (False, "my arm hides the spot from the camera")


def test_no_hand_tracker_refuses_stage_one_and_says_why(h2m):
    held_at_home(h2m)
    _sees_past_arm(h2m)
    h2m.vision.hands = None  # camera up, but the hand model did not load
    h2m.spoken.clear()
    [r] = h2m.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and "hand tracking" in r.decision.reason
    assert h2m.pending is None and not place_started(h2m)
    assert h2m.spoken and "hand tracking" in h2m.spoken[-1]
    h2m.vision = None  # no camera at all
    [r] = h2m.run_tool_calls([("place_near_user", {})])
    assert r.decision.verdict is Verdict.HOLD and not r.decision.checks["spot_watchable"]


def test_hand_tracker_lost_before_the_confirm_refuses_stage_one(h2m):
    held_at_home(h2m)
    _sees_past_arm(h2m)
    h2m.run_tool_calls([("place_near_user", {})])
    assert h2m.pending is not None
    h2m.vision.hands = None
    ok, why = h2m.confirm(by="ui")
    assert not ok and "hand tracking" in why and not place_started(h2m)


def test_stage_two_spoken_reason_when_it_cannot_lower(h2m):
    held_at_home(h2m)
    _sees_past_arm(h2m)
    set_cfg(h2m, handover_recheck_s=0.3)
    confirm_place(h2m)
    spot = tuple(h2m.routes.handover.spot)
    h2m.spoken.clear()
    arrive_above(h2m, points=(spot,))
    h2m.vision.hands_seen = HandsSeen(now(), (spot,))
    assert wait_for(lambda: events(h2m, "intent.blocked"))
    assert wait_for(lambda: h2m.spoken and "above the spot" in h2m.spoken[-1])


def test_lowering_needs_a_hand_pass_newer_than_the_arrival(h2m):
    held_at_home(h2m)
    _sees_past_arm(h2m)
    set_cfg(h2m, handover_recheck_s=0.3)
    confirm_place(h2m)
    seen_before = HandsSeen(now(), (FAR,))  # clear, and fresh by the stale rule ...
    h2m.vision.hands_seen = seen_before
    assert run(h2m, 30, until=lambda: h2m.arm.state is not ArmState.MOVING)
    h2m.vision.hands_seen = seen_before  # ... but taken before the arm got there
    assert wait_for(lambda: events(h2m, "intent.blocked"))
    assert not lower_started(h2m) and h2m.gripper.state().holding


def test_hand_during_the_lowering_stops_the_arm_before_it_opens(h2m):
    grip_cmd = held_at_home(h2m)
    _sees_past_arm(h2m)
    confirm_place(h2m)
    arrive_above(h2m)
    assert run_live(h2m, 10, until=lambda: lower_started(h2m))
    # A hand comes in just outside clear_radius but inside the watchdog margin.
    spot = h2m.routes.handover.spot
    near = (spot[0] + h2m.routes.handover.clear_radius + 0.02, spot[1])
    assert run_live(h2m, 10, until=lambda: h2m.arm.state is not ArmState.MOVING, points=(near,))
    assert h2m.arm.state is ArmState.STOPPED and "handover stopped" in h2m.arm.reason
    assert h2m.arm.backend._goal[GRIPPER] == grip_cmd  # the gripper never opened
    assert h2m.gripper.state().holding  # stopped while lowering: still holding


def test_stale_hand_pass_during_the_lowering_stops_the_arm(h2m):
    held_at_home(h2m)
    _sees_past_arm(h2m)
    confirm_place(h2m)
    arrive_above(h2m)
    assert run_live(h2m, 10, until=lambda: lower_started(h2m))
    h2m.vision.hands_seen = HandsSeen(now() - 5.0, (FAR,))  # the hand tracker stalled
    run(h2m, 0.2)
    assert h2m.arm.state is ArmState.STOPPED and "stale" in h2m.arm.reason
    h2m.arm.reset(), h2m.arm.arm()
    h2m.vision.hands_seen = None  # and when it vanishes altogether
    assert not h2m._spot_clear()[0]


def test_cancel_while_above_the_spot_drops_the_lowering(h2m):
    held_at_home(h2m)
    _sees_past_arm(h2m)
    confirm_place(h2m)
    real = h2m._handover_world

    def world_then_cancel(contract, arrived_t):
        h2m.cancel("never mind")  # lands while stage 2 is being checked
        return real(contract, arrived_t)

    h2m._handover_world = world_then_cancel
    arrive_above(h2m)
    assert wait_for(lambda: events(h2m, "intent.blocked"))
    run_live(h2m, 1.0, until=lambda: False)
    assert not lower_started(h2m) and h2m.gripper.state().holding


# ── M1: home / point with something in the gripper ──────────────────────────
def test_home_while_holding_keeps_the_grip(h2m):
    _carry_then_stop(h2m)
    held = h2m.arm._cmd[GRIPPER]
    home_grip = h2m.arm.poses.pose("home").joints[GRIPPER]
    assert held != home_grip
    h2m.on_utterance("go home", source="typed")
    assert h2m.pending is not None and "holding" in h2m.pending.decision.reason
    assert h2m.confirm(by="ui")[0]
    assert all(GRIPPER not in wp.joints for wp in h2m.arm._plan.waypoints)
    assert run(h2m, 30, until=lambda: h2m.arm.state is ArmState.READY)
    assert h2m.arm._cmd[GRIPPER] == held and h2m.arm.backend._goal[GRIPPER] == held
    assert h2m.gripper.state().holding


def test_home_taught_open_cannot_drop_a_held_object(h2m):
    _carry_then_stop(h2m)
    held = h2m.arm._cmd[GRIPPER]
    home = h2m.arm.poses.poses["home"]
    h2m.arm.poses.poses["home"] = type(home)(home.name, {**home.joints, GRIPPER: 50.0}, home.taught)
    h2m.on_utterance("go home", source="typed")
    assert h2m.confirm(by="ui")[0]
    assert run(h2m, 30, until=lambda: h2m.arm.state is ArmState.READY)
    assert h2m.arm.backend._goal[GRIPPER] == held != 50.0


def test_home_while_holding_obeys_rules_like_a_carry(h2m):
    _carry_then_stop(h2m)
    h2m.rules.add(Rule(["liquid"], "laptop", "slow_down", approved=True))
    h2m.on_utterance("go home", source="typed")
    assert h2m.pending is not None and h2m.pending.contract.speed <= 0.5
    h2m.cancel("test")
    avoid = Rule(["liquid"], "laptop", "route_around", approved=True)
    h2m.rules.add(avoid)
    h2m.on_utterance("go home", source="typed")
    assert h2m.pending is None and h2m.last["decision"]["verdict"] == "hold"
    assert "laptop" in h2m.last["decision"]["reason"]
    assert avoid.id in h2m.last["contract"]["applied_rules"]


def test_home_with_unknown_contents_holds_when_any_rule_exists(h2m):
    assert h2m.gripper.state().status == "unknown"
    h2m.rules.add(Rule(["liquid"], "laptop", "route_around", approved=True))
    h2m.on_utterance("go home", source="typed")
    assert h2m.pending is None and h2m.last["decision"]["verdict"] == "hold"


def test_home_while_holding_always_confirms(h2m):
    _carry_then_stop(h2m)
    set_cfg(h2m, always_confirm=False)  # would auto-run an empty-handed home
    h2m.on_utterance("go home", source="typed")
    assert h2m.pending is not None and started(h2m)[-1]["skill"] == "pick_place"


def test_point_while_holding_keeps_the_grip(h2m):
    _carry_then_stop(h2m)
    held = h2m.arm._cmd[GRIPPER]
    h2m.on_utterance("point at the block", source="typed")
    assert h2m.pending is not None, h2m.last
    assert h2m.confirm(by="ui")[0]
    assert all(GRIPPER not in wp.joints for wp in h2m.arm._plan.waypoints)
    assert run(h2m, 30, until=lambda: h2m.arm.state is ArmState.READY)
    assert h2m.arm.backend._goal[GRIPPER] == held


def test_home_with_a_proven_empty_gripper_still_commands_it(h2m):
    prove_empty(h2m)
    h2m.on_utterance("go home", source="typed")
    assert h2m.confirm(by="ui")[0]
    assert GRIPPER in h2m.arm._plan.waypoints[0].joints


# ── M2: pick while holding ───────────────────────────────────────────────────
def test_pick_while_holding_holds(h2m):
    _carry_then_stop(h2m)
    [r] = h2m.run_tool_calls([("pick_place", {"object_id": "block"})])
    assert r.decision.verdict is Verdict.HOLD and "Place it first" in r.decision.reason
    assert h2m.pending is None
    h2m.on_utterance("pick up the block", source="typed")
    assert h2m.pending is None and h2m.last["decision"]["checks"]["hand_free"] is False


def test_pick_with_unknown_gripper_says_so_in_the_question(h2m):
    [r] = h2m.run_tool_calls([("pick_place", {"object_id": "cup"})])
    assert r.decision.verdict is Verdict.ASK
    assert "can't tell if my gripper is empty" in h2m.pending.decision.reason
    h2m.cancel("test")
    prove_empty(h2m)
    h2m.run_tool_calls([("pick_place", {"object_id": "cup"})])
    assert h2m.pending.decision.reason == "squeeze, say yes, or click to confirm"


# ── F3: a fault while carrying keeps the object known; a load blocks a pick ───
def _pick_to(h, stage, what="cup"):
    prove_empty(h)
    h.on_utterance(f"pick up the {what}", source="typed")
    assert h.confirm(by="ui")[0]
    assert run(h, 30, until=lambda: h.arm.snapshot()["stage"] == stage)


def test_fault_mid_carry_means_holding_and_the_next_pick_holds(h2m):
    """Port of the re-review repro: a stall mid-carry kept the grip but forgot the
    object, so the next pick was allowed and its first waypoint opened the gripper."""
    _pick_to(h2m, "carry")
    run(h2m, 0.3)
    grip = h2m.arm._cmd[GRIPPER]
    h2m.arm._fault("stalled before tray_above: arm blocked or slipping")  # what _step does
    held = h2m.gripper.state()
    assert held.status == HOLDING and held.object_id == "cup" and "fault" in held.why
    assert h2m.arm._cmd[GRIPPER] == grip  # the grip was kept
    h2m.arm.reset(), h2m.arm.arm()
    n = len(started(h2m))
    h2m.on_utterance("pick up the block", source="typed")
    assert h2m.pending is None and h2m.last["decision"]["verdict"] == "hold"
    assert h2m.last["decision"]["checks"]["hand_free"] is False
    assert not h2m.confirm(by="ui")[0] and len(started(h2m)) == n


def test_fault_while_lowering_onto_the_spot_means_still_holding(h2m):
    held_at_home(h2m)
    _sees_past_arm(h2m)
    confirm_place(h2m)
    arrive_above(h2m)
    assert run_live(h2m, 10, until=lambda: lower_started(h2m))
    run_live(h2m, 0.2, until=lambda: False)
    assert h2m.arm.snapshot()["stage"] == "lower"
    h2m.arm._fault("tracking error: arm blocked or slipping")
    held = h2m.gripper.state()
    assert held.status == HOLDING and held.object_id == "cup"


def test_fault_in_grasp_or_release_is_still_unknown(h2m):
    _pick_to(h2m, "grasp")
    h2m.arm._fault("stalled")
    assert h2m.gripper.state().status == "unknown"


def test_gripper_load_above_the_empty_threshold_blocks_a_pick(h2m):
    h2m.arm.load = 0.4  # something is in the jaws, whatever the tracker says
    h2m.on_utterance("pick up the cup", source="typed")
    d = h2m.last["decision"]
    assert d["verdict"] == "hold" and d["checks"]["hand_free"] is False and h2m.pending is None
    assert "loaded" in d["reason"]
    prove_empty(h2m)  # even a tracker that says EMPTY
    [r] = h2m.run_tool_calls([("pick_place", {"object_id": "cup"})])
    assert r.decision.verdict is Verdict.HOLD and h2m.pending is None
    h2m.arm.load = 0.05  # below the threshold: asked about as before
    h2m.on_utterance("pick up the cup", source="typed")
    assert h2m.pending is not None


def test_load_rising_before_the_confirm_blocks_the_pick(h2m):
    prove_empty(h2m)
    h2m.on_utterance("pick up the cup", source="typed")
    assert h2m.pending is not None
    h2m.arm.load = 0.4
    ok, why = h2m.confirm(by="ui")
    assert not ok and "loaded" in why and h2m.arm.state is ArmState.READY


# ── F5: a stop while closing on an object never keeps squeezing ──────────────
def test_brace_mid_grasp_freezes_the_gripper_at_its_measured_opening(h2m):
    """Port of the re-review repro: a brace during the grasp kept the closing goal."""
    prove_empty(h2m)
    h2m.on_utterance("pick up the block firmly", source="typed")
    assert h2m.confirm(by="ui")[0]
    assert run(h2m, 30, until=lambda: h2m.arm.snapshot()["stage"] == "grasp")
    assert run(h2m, 5, until=lambda: (h2m.arm.load or 0) > 0.15)  # squeezing the block
    writes = []
    real = h2m.arm.backend.write_goal
    h2m.arm.backend.write_goal = lambda j: (writes.append(dict(j)), real(j))[1]
    measured = h2m.arm.backend._measured[GRIPPER]
    assert h2m.arm._cmd[GRIPPER] < measured  # commanded past contact
    h2m.log.emit("emg.brace", onset_t=now())
    assert h2m.arm.state is ArmState.STOPPED
    assert writes and writes[0][GRIPPER] == measured  # the freeze write sets the opening
    assert h2m.arm._cmd[GRIPPER] == measured == h2m.arm.backend._goal[GRIPPER]
    run(h2m, 1.0)
    assert (h2m.arm.load or 0) <= 0.15  # no longer squeezing
    h2m.arm.reset()
    assert h2m.arm._cmd[GRIPPER] == measured  # reset keeps it (contents unknown)


def test_stop_in_carry_still_keeps_the_grip(h2m):
    _pick_to(h2m, "carry")
    grip = h2m.arm._cmd[GRIPPER]
    h2m.stop("voice: stop")
    assert h2m.arm._cmd[GRIPPER] == grip == h2m.arm.backend._goal[GRIPPER]


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


def test_disarm_and_arm_during_the_recheck_drops_stage_two(h2m):
    _above_and_waiting(h2m)
    h2m.disarm()
    h2m.arm.arm()
    _fresh(h2m)
    assert not wait_for(lambda: lower_started(h2m), 1.0)
    assert h2m.gripper.state().holding


def test_ui_disarm_endpoint_goes_through_the_app(h2m):
    from fastapi.testclient import TestClient

    from h2m.server import create_app

    _above_and_waiting(h2m)
    c = TestClient(create_app(h2m, token="t"), base_url="http://127.0.0.1")
    assert c.post("/api/arm/disarm", headers={"X-H2M-Token": "t"}).status_code == 200
    assert c.post("/api/arm/arm", headers={"X-H2M-Token": "t"}).status_code == 200
    _fresh(h2m)
    assert not wait_for(lambda: lower_started(h2m), 1.0)


def test_stop_reset_and_arm_during_the_recheck_drops_stage_two(h2m):
    _above_and_waiting(h2m)
    h2m.stop("voice: stop")
    h2m.arm.reset(), h2m.arm.arm()
    _fresh(h2m)
    assert not wait_for(lambda: lower_started(h2m), 1.0)


def test_brace_during_the_recheck_latches_and_drops_stage_two(h2m):
    _above_and_waiting(h2m)
    h2m.log.emit("emg.brace", onset_t=now())
    assert h2m.arm.state is ArmState.STOPPED
    h2m.arm.reset(), h2m.arm.arm()
    _fresh(h2m)
    assert not wait_for(lambda: lower_started(h2m), 1.0)


def test_hand_arriving_mid_lower_stops_and_keeps_the_grip(h2m):
    _above_and_waiting(h2m)
    _fresh(h2m)
    assert wait_for(lambda: lower_started(h2m), 2.0)
    grip = h2m.arm._cmd[GRIPPER]
    spot = h2m.routes.handover.spot
    run_live(h2m, 0.3, until=lambda: False)
    near = ((spot[0], spot[1] + 0.12),)
    run_live(h2m, 5, until=lambda: h2m.arm.state is not ArmState.MOVING, points=near)
    assert h2m.arm.state is ArmState.STOPPED and "hand" in h2m.arm.reason
    assert h2m.arm._cmd[GRIPPER] == grip and h2m.gripper.state().holding


# The operator gripper release (F2) is covered in tests/test_operator_release.py.


def test_direct_arm_disarm_and_arm_during_the_recheck_also_drops_stage_two(h2m):
    """Port of the re-review repro: even a disarm that bypasses the app (no epoch bump)
    cannot let stage 2 continue, because the arm was re-armed since stage 1."""
    _above_and_waiting(h2m)
    h2m.arm.disarm()
    h2m.arm.arm()
    _fresh(h2m)
    assert not wait_for(lambda: lower_started(h2m), 1.0)


def test_disarm_drops_a_pending_request_and_a_muse_reply_in_flight(h2m):
    from h2m.compiler.llm import ToolReply
    from tests.test_app_embodied import FakeLLM

    h2m.on_utterance("pick up the cup", source="typed")
    assert h2m.pending is not None
    h2m.disarm()
    assert h2m.pending is None
    h2m.arm.arm()
    prove_empty(h2m)

    def disarm_and_rearm():
        h2m.disarm()
        h2m.arm.arm()

    h2m.llm = FakeLLM(ToolReply([("gesture", {"name": "shake"})]), hook=disarm_and_rearm)
    h2m.on_utterance("shake your head", source="typed")
    assert not started(h2m) and h2m.arm.state is ArmState.READY
