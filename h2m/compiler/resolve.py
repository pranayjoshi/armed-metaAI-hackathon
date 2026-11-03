"""Fuse the parsed request with what the cameras see into a step contract.

Target evidence per visible object (additive, then normalized):
  named in speech      +0.90
  pointed at           +0.80 x pointing score x hand-is-pointing
  ordinal ("left one") +0.70
  only object in view  +0.50 (when nothing else disambiguates)

Confidence = action confidence x target confidence. The top target must beat
the runner-up by ``target_margin`` or the target stays unresolved, so the
system asks instead of guessing.
"""

from __future__ import annotations

from typing import Any

from h2m.compiler.grammar import Parsed
from h2m.contract import TARGETED_VERBS, Candidate, StepContract, Verb
from h2m.core import clamp

W_NAMED, W_POINT, W_ORDINAL, W_ONLY = 0.9, 0.8, 0.7, 0.5


def _ordinal_pick(ordinal: str, visible: list[dict]) -> str | None:
    if not visible:
        return None
    ordered = sorted(visible, key=lambda o: o["table_xy"][0])
    index = {
        "left": 0,
        "first": 0,
        "right": -1,
        "last": -1,
        "middle": len(ordered) // 2,
        "second": 1,
        "third": 2,
    }.get(ordinal)
    if index is None or index >= len(ordered):
        return None
    return ordered[index]["id"]


def compile_contract(
    parsed: Parsed,
    visible: list[dict[str, Any]],
    pointing: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    margin_required: float = 0.2,
    source: str = "grammar",
) -> StepContract:
    contract = StepContract(
        verb=parsed.verb,
        utterance=parsed.text,
        source=source,
        grip_effort=parsed.grip_effort,
        destination=parsed.destination,
    )
    if parsed.verb is Verb.SKILL:
        contract.utterance = parsed.skill or ""  # the skill name, as gestures do
    action_conf = parsed.grammar_confidence
    contract.evidence["speech_action"] = round(action_conf, 3)
    if parsed.verb not in TARGETED_VERBS and not parsed.skill_needs_object:
        contract.confidence = round(action_conf, 3)
        if parsed.verb is Verb.UNKNOWN:
            contract.unresolved.append("action")
        return contract

    by_id = {o["id"]: o for o in visible}
    scores: dict[str, float] = {o["id"]: 0.0 for o in visible}
    parts: dict[str, dict[str, float]] = {o["id"]: {} for o in visible}

    def add(object_id: str, kind: str, value: float) -> None:
        if object_id in scores and value > 0:
            scores[object_id] += value
            parts[object_id][kind] = round(parts[object_id].get(kind, 0.0) + value, 3)

    if parsed.object_id:
        if parsed.object_id in scores:
            add(parsed.object_id, "speech_target", W_NAMED)
        else:
            name = catalog.get(parsed.object_id, {}).get("name", parsed.object_id)
            contract.unresolved.append(f"{name} is not visible")
    hand = float(pointing.get("active") or 0.0)
    for cand in pointing.get("candidates") or []:
        add(cand["id"], "pointing", W_POINT * float(cand["score"]) * hand)
    if parsed.ordinal and (pick := _ordinal_pick(parsed.ordinal, visible)):
        add(pick, "ordinal", W_ORDINAL)
    if len(visible) == 1 and not parsed.object_id and sum(scores.values()) == 0:
        add(visible[0]["id"], "only_object", W_ONLY)

    total = sum(scores.values())
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    contract.candidates = [
        Candidate(oid, by_id[oid]["name"], round(s / total, 3) if total else 0.0)
        for oid, s in ranked[:3]
    ]
    if total <= 0:
        contract.unresolved.append("target")
        contract.confidence = round(action_conf * 0.3, 3)
        return contract

    top_id, top_score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = (top_score - second) / total
    contract.target_margin = round(margin, 3)
    target_conf = clamp(top_score)
    if margin < margin_required:
        contract.unresolved.append("target")
        target_conf *= 0.6
    else:
        obj = by_id[top_id]
        contract.object_id, contract.object_name = top_id, obj["name"]
        contract.table_xy = tuple(obj["table_xy"])
    contract.evidence.update(parts[top_id])
    contract.confidence = round(action_conf * target_conf, 3)
    return contract


def resolve_choice(contract: StepContract, object_id: str, visible: list[dict]) -> StepContract:
    """Operator picked a candidate (UI click or by name): the target is now explicit."""
    obj = next((o for o in visible if o["id"] == object_id), None)
    if obj is None:
        return contract
    contract.object_id, contract.object_name = obj["id"], obj["name"]
    contract.table_xy = tuple(obj["table_xy"])
    contract.unresolved = [u for u in contract.unresolved if u != "target"]
    contract.evidence["operator_choice"] = 1.0
    contract.confidence = round(
        max(contract.confidence, contract.evidence.get("speech_action", 0.9)), 3
    )
    return contract
