"""`armed demo`'s scripted scenario drives the real investigation tool loop with a canned
script (no model, no network): a wrong proposal the checker rejects, then the canned
one, both ticks, and a rehearsal for the twin. Everything stays labelled SIMULATION."""

from __future__ import annotations

import socket

import httpx
import pytest

from armed.core import load_config
from armed.replay import CANNED_EXPLANATION
from armed.scenario import CANNED_WRONG_PROPOSAL, CannedMuse, Scenario, ScenarioSettings

FAST = ScenarioSettings(beat_s=0.05, approve_after_s=3.1, step_timeout_s=20.0)


def fast_cfg() -> dict:
    cfg = load_config()
    cfg["arm"] = {**cfg["arm"], "max_joint_speed_deg_s": 90}
    cfg["incident"] = {**cfg["incident"], "after_s": 0.3}
    return cfg


@pytest.fixture(scope="module")
def explained(tmp_path_factory):
    """The scenario up to the explanation, with every network path blocked."""
    mp = pytest.MonkeyPatch()

    def no_network(*a, **k):
        raise AssertionError("the demo must not touch the network")

    mp.setattr(socket.socket, "connect", no_network)
    mp.setattr(socket, "create_connection", no_network)
    mp.setattr(httpx, "post", no_network)
    sc = Scenario(fast_cfg(), tmp_path_factory.mktemp("demo"), FAST, until="explain")
    state = sc.run()
    mp.undo()
    yield sc, state
    sc.close()


def test_demo_reaches_a_rejected_proposal_then_the_canned_one(explained):
    sc, state = explained
    assert state == "done", sc.error
    h = sc.armed
    snap = h.snapshot()
    learning = snap["learning"]
    feed = learning["investigation"]
    tools = [e["tool"] for e in feed]
    assert tools[:3] == ["wonder", "sam-3.1 track", "sam-3.1 track"]
    assert "measure" in tools and tools.count("wonder propose_rule") == 2
    checker = [e for e in feed if e["tool"] == "checker"]
    assert [c["ok"] for c in checker] == [False, True]
    assert checker[0]["ticks"] == [True, False] and "rigid" in checker[0]["result_summary"]
    assert checker[1]["ticks"] == [True, True]
    assert [e["tool"] for e in feed if e["tool"].startswith("wonder propose")] == ["wonder propose_rule"] * 2
    assert "holding rigid" in feed[tools.index("wonder propose_rule")]["result_summary"]
    # the stage feed: incident.tool events in the snapshot, and the scenario's timeline
    assert [e for e in snap["events"] if e["kind"] == "incident.tool"]
    assert any(e["kind"] == "incident.tool" for e in sc.timeline()["events"])
    # the proposal: the canned rule, both ticks, the rehearsal, labelled canned / simulation
    p = learning["proposal"]
    assert p["rule"]["when_holding"] == ["liquid"] and p["rule"]["approved"] is False
    assert p["verifier"] == {"removes options only": True, "incident prevented": True}
    assert p["rehearsal"]["old_route_name"] == "direct" and p["rehearsal"]["new_route_name"] == "via_left"
    assert p["rehearsal"]["source"]["measured"] is False
    assert sc.explanation["source"] == "canned" and sc.explanation["canned"] is True
    assert sc.snapshot()["label"] == "SIMULATION"
    assert learning["incident"]["investigation"]["rejected"] == 1
    # the calls the Card shows: Wonder turns and SAM tracks, all landed
    models = {(c["model"], c["for"]) for c in snap["calls"]}
    assert ("muse-wonder-1.0", "investigate") in models and ("sam-3.1", "track") in models
    assert h.arm.snapshot()["state"] == "stopped"  # the investigation moved nothing


def test_canned_script_is_the_wrong_rule_then_the_canned_one():
    m = CannedMuse()
    turns = [m.respond({}) for _ in range(5)]
    names = [[c["name"] for c in t["output"] if c["type"] == "function_call"] for t in turns]
    assert names == [["track", "track"], ["measure", "arm_trace"], ["propose_rule"],
                     ["propose_rule"], []]
    import json

    assert json.loads(turns[2]["output"][0]["arguments"]) == CANNED_WRONG_PROPOSAL
    assert json.loads(turns[3]["output"][0]["arguments"]) == CANNED_EXPLANATION
    assert CANNED_WRONG_PROPOSAL["rule"]["when_holding"] == ["rigid"]
    assert m.segment("sam-3.1", None, "cup") is None  # no SAM without a key
