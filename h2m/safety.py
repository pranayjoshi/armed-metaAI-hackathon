"""Deterministic safety gate: approve, ask, hold or reject a step contract.

Pure function of (contract, world, config). No model sits in this decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from h2m.contract import TARGETED_VERBS, StepContract, Verb


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


def decide(contract: StepContract, world: dict[str, Any], cfg: dict[str, Any]) -> Decision:
    """``world``: arm_ready (bool), slot_id (str|None, nearest taught slot to the
    target), target_visible (bool)."""
    checks: dict[str, bool] = {}
    if contract.verb is Verb.UNKNOWN:
        return Decision(Verdict.HOLD, "no robot action recognized", checks)

    targeted = contract.verb in TARGETED_VERBS
    checks["action_understood"] = True
    checks["arm_ready"] = bool(world.get("arm_ready"))
    if targeted:
        checks["target_resolved"] = contract.object_id is not None
        checks["target_visible"] = bool(world.get("target_visible"))
        checks["target_reachable"] = world.get("slot_id") is not None
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
    if not checks["confident"]:
        return Decision(Verdict.HOLD, f"confidence {contract.confidence:.0%} is too low", checks)
    if not checks["arm_ready"]:
        return Decision(Verdict.HOLD, "arm is not armed", checks, slot_id=world.get("slot_id"))
    if contract.confidence < cfg.get("auto_approve", 0.85) or cfg.get("always_confirm", True):
        return Decision(
            Verdict.ASK,
            "squeeze, say yes, or click to confirm",
            checks,
            slot_id=world.get("slot_id"),
        )
    return Decision(Verdict.APPROVE, "all checks passed", checks, slot_id=world.get("slot_id"))
