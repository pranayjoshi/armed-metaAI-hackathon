"""Deterministic safety gate: approve, ask, hold or reject a step contract.

Pure function of (contract, world, config). No model sits in this decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from h2m.contract import CARRY_VERBS, TARGETED_VERBS, StepContract, Verb


class Verdict(StrEnum):
    APPROVE = "approve"
    ASK = "ask"  # needs a confirmation, or a choice between candidates
    HOLD = "hold"  # not enough to act on; nothing happens
    REJECT = "reject"  # understood, but not allowed


@dataclass
class Decision:
    verdict: Verdict
    reason: str
    checks: dict[str, bool] = field(default_factory=dict)
    clarify: bool = False  # ASK because the target is ambiguous (needs a choice)
    slot_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": str(self.verdict),
            "reason": self.reason,
            "checks": self.checks,
            "clarify": self.clarify,
            "slot_id": self.slot_id,
        }


HELD_MOVE_VERBS = frozenset({Verb.HOME, Verb.POINT})  # carries whenever not proven empty
CONFIRM = "squeeze, say yes, or click to confirm"


def decide(contract: StepContract, world: dict[str, Any], cfg: dict[str, Any]) -> Decision:
    """``world``: arm_ready (bool), slot_id (str|None, nearest taught slot to the
    target), target_visible (bool), and for pick_place route_clear (bool, must be
    exactly True: missing means unproven, which holds) with route_reason (str).

    Also: gripper_empty / holding (proven facts) and gripper_loaded (a load reading
    above the empty threshold: blocks a pick); emg_required + emg_age_s +
    emg_calibrating (motion needs a live EMG stop when EMG is configured: fresh samples
    and calibrating exactly False, since brace / open hand are off while recording);
    spot_watchable + watch_reason for place_near_user (stage 2 could run: camera sees
    past the arm and a hand tracker runs); confirmed (True only on the re-check
    after a person confirmed). Anything that carries (pick_place, place_near_user,
    and home / point with a gripper not proven empty) always asks first."""
    checks: dict[str, bool] = {}
    if contract.verb is Verb.UNKNOWN:
        return Decision(Verdict.HOLD, "no robot action recognized", checks)

    # A skill is targeted when its definition takes an object (world["targeted"], set
    # from the skills file, never from the model).
    targeted = contract.verb in TARGETED_VERBS or world.get("targeted") is True
    checks["action_understood"] = True
    checks["arm_ready"] = bool(world.get("arm_ready"))
    if targeted:
        checks["target_resolved"] = contract.object_id is not None
        checks["target_visible"] = bool(world.get("target_visible"))
        checks["target_reachable"] = world.get("slot_id") is not None
    if contract.verb is Verb.SAY:
        checks["confident"] = contract.confidence >= cfg.get("ask_floor", 0.45)
        if not checks["confident"]:
            return Decision(Verdict.HOLD, "confidence is too low to speak", checks)
        return Decision(Verdict.APPROVE, "speech only, no motion", checks)

    held_move = contract.verb in HELD_MOVE_VERBS and world.get("gripper_empty") is not True
    carries = contract.verb in CARRY_VERBS or held_move or world.get("carries") is True
    if contract.needs_motion and world.get("emg_required") is True:
        age = world.get("emg_age_s")
        max_age = float(cfg.get("emg_max_age_ms", 300)) / 1000
        checks["emg_live"] = (
            isinstance(age, (int, float))
            and not isinstance(age, bool)
            and 0 <= age < max_age  # NaN fails
            and world.get("emg_calibrating") is False  # missing = not proven live
        )
    if carries:
        checks["route_clear"] = world.get("route_clear") is True
    if contract.verb in (Verb.PICK_PLACE, Verb.MOVE):
        # Unknown contents are asked about; a known object or a load reading blocks.
        checks["hand_free"] = (
            world.get("holding") is not True and world.get("gripper_loaded") is not True
        )
    if world.get("needs_empty") is True:
        # The skill opens the jaws first: anything held would drop, so empty must be proven.
        checks["gripper_empty"] = world.get("gripper_empty") is True
    if contract.verb is Verb.MOVE:
        checks["destination_ok"] = world.get("destination_ok") is True
    if contract.verb is Verb.SKILL:
        checks["skill_known"] = world.get("skill_known") is True
        if world.get("near_person") is True:
            checks["near_person_ok"] = world.get("near_person_ok") is True
        if world.get("needs_holding") is True:
            checks["holding"] = world.get("holding") is True
    if contract.verb is Verb.PLACE_NEAR_USER:
        checks["holding"] = world.get("holding") is True
        checks["spot_watchable"] = world.get("spot_watchable") is True  # stage 2 can run
        checks["spot_clear"] = world.get("spot_clear") is True  # no hand over the spot
    checks["confident"] = contract.confidence >= cfg.get("ask_floor", 0.45)

    if targeted and not checks["target_resolved"]:
        if len([c for c in contract.candidates if c.score > 0]) >= 2:
            names = " or ".join(c.name for c in contract.candidates[:3] if c.score > 0)
            return Decision(Verdict.ASK, f"which one: {names}?", checks, clarify=True)
        why = next((u for u in contract.unresolved if u != "target"), "point at it or name it")
        return Decision(Verdict.HOLD, f"no target: {why}", checks)
    if targeted and not checks["target_visible"]:
        return Decision(Verdict.HOLD, f"{contract.object_name} is not in view", checks)
    if targeted and not checks["target_reachable"]:
        return Decision(
            Verdict.REJECT, f"{contract.object_name} is outside the taught slots", checks
        )
    if checks.get("emg_live") is False:
        why = (
            "my muscle sensor is calibrating, so I won't move"
            if world.get("emg_calibrating") is True
            else "my muscle sensor is not streaming, so I won't move"
        )
        return Decision(Verdict.HOLD, why, checks)
    if checks.get("hand_free") is False:
        why = (
            "I'm already holding something. Place it first."
            if world.get("holding") is True
            else "My gripper feels loaded, so I may be holding something. Place it first."
        )
        return Decision(Verdict.HOLD, why, checks, slot_id=world.get("slot_id"))
    if contract.verb is Verb.SKILL:
        if not checks["skill_known"]:
            return Decision(Verdict.HOLD, f"I don't know how to {contract.utterance}", checks)
        if checks.get("gripper_empty") is False:
            return Decision(
                Verdict.HOLD, "I can't prove my gripper is empty, and that move opens it", checks
            )
        if checks.get("near_person_ok") is False:
            return Decision(
                Verdict.HOLD, "moves near a person are switched off (near_person_enabled)", checks
            )
        if checks.get("holding") is False:
            return Decision(Verdict.HOLD, "I'm not holding anything for that", checks)
    if contract.verb is Verb.MOVE and not checks["destination_ok"]:
        why = world.get("destination_reason") or "that is not a free taught spot"
        return Decision(Verdict.HOLD, str(why), checks, slot_id=world.get("slot_id"))
    if carries and not checks["route_clear"]:
        why = world.get("route_reason") or "I can't prove a route is clear"
        return Decision(Verdict.HOLD, str(why), checks, slot_id=world.get("slot_id"))
    if contract.verb is Verb.PLACE_NEAR_USER:
        if not checks["holding"]:
            return Decision(Verdict.HOLD, "I am not holding anything to place", checks)
        if not checks["spot_watchable"]:
            why = world.get("watch_reason") or "I can't watch the spot while lowering"
            return Decision(Verdict.HOLD, str(why), checks)
        if not checks["spot_clear"]:
            return Decision(Verdict.HOLD, "the marked spot is not proven clear of a hand", checks)
    if not checks["confident"]:
        return Decision(Verdict.HOLD, f"confidence {contract.confidence:.0%} is too low", checks)
    if not checks["arm_ready"]:
        return Decision(Verdict.HOLD, "arm is not armed", checks, slot_id=world.get("slot_id"))
    if contract.verb is Verb.GESTURE:
        decision = _gesture(contract, world, cfg, checks)
        if decision is not None:
            return decision
    # Skills and moves never run unconfirmed, whatever always_confirm says.
    unconfirmed_carry = (
        carries or contract.verb in (Verb.SKILL, Verb.MOVE)
    ) and world.get("confirmed") is not True
    if (
        unconfirmed_carry
        or contract.confidence < cfg.get("auto_approve", 0.85)
        or cfg.get("always_confirm", True)
    ):
        return Decision(
            Verdict.ASK, _question(contract, world), checks, slot_id=world.get("slot_id")
        )
    return Decision(Verdict.APPROVE, "all checks passed", checks, slot_id=world.get("slot_id"))


def _question(contract: StepContract, world: dict[str, Any]) -> str:
    """The confirm question. It names a skill or a move (the phrase that triggered it
    may have been ambiguous) and says so when the gripper is not proven empty."""
    if contract.verb is Verb.SKILL:
        name = contract.utterance.replace("_", " ")
        return f"{name}? {_question_body(contract, world)}"
    if contract.verb is Verb.MOVE:
        what = contract.object_name or "it"
        where = (contract.destination or "there").replace("_", " ")
        return f"move the {what} to {where}? {_question_body(contract, world)}"
    return _question_body(contract, world)


def _question_body(contract: StepContract, world: dict[str, Any]) -> str:
    if world.get("gripper_empty") is True:
        return CONFIRM
    if contract.verb is Verb.PICK_PLACE:
        return (
            "I can't tell if my gripper is empty; if it holds something, it drops when "
            f"I open. {CONFIRM[0].upper()}{CONFIRM[1:]}."
        )
    if contract.verb in HELD_MOVE_VERBS or contract.verb is Verb.SKILL:
        what = "I'm holding something" if world.get("holding") is True else (
            "I may be holding something"
        )
        return f"{what}, so I'll keep my grip as I move. {CONFIRM[0].upper()}{CONFIRM[1:]}."
    return CONFIRM


def _gesture(
    contract: StepContract, world: dict[str, Any], cfg: dict[str, Any], checks: dict[str, bool]
) -> Decision | None:
    """GATE RELAXATION (needs safety-reviewer sign-off): the only path that skips
    per-motion confirmation. Reaches here armed and confident. Every condition must
    be proven True/numeric; anything missing or malformed fails closed. ``world``
    needs gesture_max_dev_deg (taught steps vs home), gesture_start_dev_deg (the
    measured pose now vs home) and gripper_empty.

    Returns REJECT (outside envelope or too fast), APPROVE (all proven), or None to
    fall through to the normal confirm path (gripper not proven empty)."""
    envelope = cfg.get("gesture_envelope_deg", 15.0)
    speed_cap = cfg.get("gesture_speed_cap", 0.3)

    def within(dev: Any) -> bool:
        return (
            isinstance(dev, (int, float))
            and not isinstance(dev, bool)
            and dev == dev
            and 0 <= dev <= envelope
        )

    # The steps and the pose the arm starts from (measured now) must both be near
    # home: otherwise the move from where the arm is to the first step is unchecked.
    ok_dev = within(world.get("gesture_max_dev_deg"))
    ok_start = within(world.get("gesture_start_dev_deg"))
    ok_speed = (
        isinstance(contract.speed, (int, float))
        and not isinstance(contract.speed, bool)
        and 0 < contract.speed <= speed_cap
    )
    checks["gesture_in_envelope"] = bool(ok_dev)
    checks["gesture_start_near_home"] = bool(ok_start)
    checks["gesture_speed_ok"] = bool(ok_speed)
    checks["gripper_empty"] = world.get("gripper_empty") is True
    if not ok_dev:
        return Decision(Verdict.REJECT, "gesture leaves the small envelope around home", checks)
    if not ok_start:
        return Decision(Verdict.REJECT, "I only gesture near home; go home first", checks)
    if not ok_speed:
        return Decision(Verdict.REJECT, f"gesture speed is above the {speed_cap} cap", checks)
    if not checks["gripper_empty"]:
        return None  # holding or unknown: confirm like any carrying motion
    return Decision(Verdict.APPROVE, "gesture: armed, gripper empty, inside envelope", checks)
