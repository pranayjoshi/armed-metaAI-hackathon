"""Decision-layer invariants: never guess a target, stop never waits on a model."""

from __future__ import annotations

from armed.compiler.grammar import build_vocabulary, parse
from armed.compiler.resolve import compile_contract
from armed.contract import Verb
from armed.core import load_config
from armed.safety import Verdict, decide

CFG = load_config()
CATALOG = {o["id"]: o for o in CFG["objects"]}
VOCAB = build_vocabulary(CFG["objects"])
SCENE = [
    {"id": "cup", "name": "paper cup", "table_xy": (0.25, 0.55)},
    {"id": "block", "name": "wooden block", "table_xy": (0.5, 0.55)},
]
READY = {"arm_ready": True, "slot_id": "slot_1", "target_visible": True, "route_clear": True}


def test_ambiguous_pointing_asks_instead_of_guessing():
    pointing = {
        "active": 1.0,
        "candidates": [{"id": "cup", "score": 0.8}, {"id": "block", "score": 0.75}],
    }
    contract = compile_contract(parse("give me that", VOCAB), SCENE, pointing, CATALOG)
    assert contract.object_id is None
    decision = decide(contract, READY, CFG["safety"])
    assert decision.verdict is Verdict.ASK and decision.clarify


def test_vague_request_without_evidence_holds():
    contract = compile_contract(parse("grab that one", VOCAB), SCENE, {}, CATALOG)
    assert decide(contract, READY, CFG["safety"]).verdict is Verdict.HOLD


def test_stop_and_cancel_parse_locally_first():
    for text in ("stop", "STOP grab the cup", "never mind, hand me the cup"):
        assert parse(text, VOCAB).verb in (Verb.STOP, Verb.CANCEL)


def test_motion_needs_confirmation_by_default():
    contract = compile_contract(parse("hand me the cup", VOCAB), SCENE, {}, CATALOG)
    world = {**READY, "target_visible": True}
    assert decide(contract, world, CFG["safety"]).verdict is Verdict.ASK
