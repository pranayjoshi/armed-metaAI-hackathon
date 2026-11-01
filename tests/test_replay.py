"""Replay mode and the scripted software demo (REFLEX_BUILD_PLAN item 0), on the virtual
arm. Covers: replay is read-only (no actuation route exists, with or without the token)
and keeps the local-API rules; recordings are labelled by provenance and a missing
explanation is shown as canned, never as model output; the scripted scenario runs only
on the virtual arm with synthetic EMG and no network, still confirms every carry, learns
a rule that only restricts (the re-run takes the long route), and an operator stop aborts
it with the stop latched (the script never resets a stop it did not script)."""

from __future__ import annotations

import json
import shutil
import socket
import threading
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from h2m.core import ROOT, load_config, now
from h2m.incident import Frame, Incident, explain
from h2m.replay import (
    CANNED_NOTE,
    ReplayError,
    ReplayLibrary,
    load_incident,
    load_session,
    rule_check,
    source_label,
)
from h2m.robot import ArmState
from h2m.scenario import (
    STEP_NAMES,
    CannedMuse,
    Scenario,
    ScenarioAborted,
    ScenarioSettings,
    scenario_config,
)
from h2m.server import create_replay_app
from tests import test_learning as learning

SAMPLE = ROOT / "data" / "demo" / "demo-liquid-over-laptop"
TOKEN = "t0k"
LOCAL = "http://127.0.0.1"
GOOD = {"X-H2M-Token": TOKEN}
ZONES, TAGS = ["laptop"], ["fragile", "liquid"]

h2m = learning.h2m  # the learning fixture: virtual arm, mocked Muse, tmp stores


def fast_cfg() -> dict:
    """Shipped config with a quicker virtual arm and after-window (tests only)."""
    cfg = load_config()
    cfg["arm"] = {**cfg["arm"], "max_joint_speed_deg_s": 90}
    cfg["incident"] = {**cfg["incident"], "after_s": 0.3}
    return cfg


# approve_after_s past the app's 3 s after-stop hold-off: no rule approval within 3 s of a stop.
FAST = ScenarioSettings(beat_s=0.05, approve_after_s=3.1, step_timeout_s=20.0)


def client(library: ReplayLibrary, scenario=None, base=LOCAL) -> TestClient:
    return TestClient(create_replay_app(library, load_config(), scenario, token=TOKEN),
                      base_url=base)


@pytest.fixture
def sample(tmp_path) -> Path:
    """A private copy of the bundled sample, free to break."""
    dest = tmp_path / "lib" / SAMPLE.name
    shutil.copytree(SAMPLE, dest)
    return dest


# ── bundled sample ───────────────────────────────────────────────────────────
def test_bundled_sample_is_marked_synthetic_and_complete():
    meta = json.loads((SAMPLE / "meta.json").read_text())
    assert meta["synthetic"] is True and "SYNTHETIC" in meta["synthetic_note"]
    assert meta["provenance"]["simulation"] is True and meta["provenance"]["arm"] == "virtual"
    assert meta["keyframes"] and all((SAMPLE / k["overhead"]).is_file() for k in meta["keyframes"])
    exp = json.loads((SAMPLE / "explanation.json").read_text())
    assert exp["source"] == "canned"
    size = sum(p.stat().st_size for p in SAMPLE.rglob("*") if p.is_file())
    assert size < 400_000  # small enough to commit


def test_sample_payload_is_labelled_and_shows_the_story():
    inc = load_incident(SAMPLE)
    p = inc.payload("/api/replay/incident/x", ZONES, TAGS)
    assert p["label"] == "SIMULATION" and p["simulation"] is True and p["synthetic"] is True
    assert p["explanation"]["source"] == "canned" and p["explanation"]["canned"] is True
    assert p["explanation"]["note"] == CANNED_NOTE
    rule = p["proposed_rule"]
    assert rule["approved"] is False and rule["valid"] is True
    assert rule["rule"] == {"when_holding": ["liquid"], "avoid": "laptop",
                            "response": "route_around"}
    assert p["frames"] and p["frames"][0]["cams"]["overhead"].endswith("/overhead.jpg")
    assert p["joints"]["names"][0] == "shoulder_pan" and len(p["joints"]["q"]) >= 5
    assert any(s["stage"] == "carry" and s["route"] == "direct" for s in p["stages"])
    onset = p["emg"]["onset"]
    assert onset["source"] == "recorded" and -1.0 < onset["t"] <= 0.0
    kinds = [e["kind"] for e in p["timeline"]]
    assert kinds.index("brace.onset") < kinds.index("freeze") < kinds.index("explanation")
    emg = inc.emg_series()
    assert set(emg["channels"]) == {"flexor", "extensor", "brace"}
    assert max(emg["channels"]["brace"]) >= emg["brace_effort"]  # the brace is in the trace
    assert len(emg["t"]) == len(emg["channels"]["brace"])


# ── replay loading and labels ────────────────────────────────────────────────
def test_missing_explanation_is_canned_and_labelled(sample):
    (sample / "explanation.json").unlink()
    exp = load_incident(sample).explanation_payload()
    assert exp["source"] == "canned" and exp["canned"] is True and exp["stored"] is False
    assert exp["note"] == CANNED_NOTE


def test_stored_fallback_is_shown_as_stored_not_replaced(sample):
    (sample / "explanation.json").write_text(json.dumps(
        {"say": "Something bothered you. What was it?", "source": "fallback", "rule": None}))
    p = load_incident(sample).payload("/x", ZONES, TAGS)
    assert p["explanation"]["source"] == "fallback" and p["explanation"]["canned"] is False
    assert p["proposed_rule"] is None


@pytest.mark.parametrize(
    "meta, label",
    [
        ({}, "UNKNOWN SOURCE"),  # incidents saved before provenance existed
        ({"provenance": {"simulation": False}}, "RECORDED"),
        ({"provenance": {"simulation": True}}, "SIMULATION"),
        ({"synthetic": True, "provenance": {"simulation": False}}, "SIMULATION"),
    ],
)
def test_source_label(meta, label):
    assert source_label(meta)["label"] == label


def test_proposed_rule_is_checked_like_the_runtime_would():
    bad = rule_check({"when_holding": ["liquid"], "avoid": "kitchen",
                      "response": "route_around"}, ZONES, TAGS)
    assert bad["valid"] is False and bad["approved"] is False and "zone" in bad["problems"][0]
    allow = rule_check({"when_holding": ["liquid"], "avoid": "laptop", "response": "allow"},
                       ZONES, TAGS)
    assert allow["valid"] is False  # no permissive response exists to propose


def test_corrupt_parts_degrade_without_crashing(sample):
    (sample / "emg.npy").write_bytes(b"not numpy")
    inc = load_incident(sample)
    assert inc.emg is None and "unreadable" in inc.emg_error
    assert inc.payload("/x")["emg"]["error"] and inc.emg_series()["t"] == []
    (sample / "meta.json").write_text("{nope")
    with pytest.raises(ReplayError):
        load_incident(sample)


def test_keyframe_paths_cannot_leave_the_incident_folder(sample, tmp_path):
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"\xff\xd8 not for you")
    meta = json.loads((sample / "meta.json").read_text())
    meta["keyframes"][0]["overhead"] = "../../secret.jpg"
    meta["keyframes"][1]["overhead"] = str(secret)
    (sample / "meta.json").write_text(json.dumps(meta))
    inc = load_incident(sample)
    assert inc.frame_path(0, "overhead") is None and inc.frame_path(1, "overhead") is None
    assert inc.frame_path(2, "overhead") is not None
    assert inc.frame_path(2, "../meta") is None and inc.frame_path(99, "overhead") is None


def test_session_replay_links_its_incidents(tmp_path, sample):
    sess = tmp_path / "sessions" / "20261101-000000-p01"
    sess.mkdir(parents=True)
    (sess / "meta.json").write_text(json.dumps({"session_id": sess.name, "participant": "p01"}))
    events = [
        {"seq": 1, "t": 100.0, "kind": "session.started"},
        {"seq": 2, "t": 101.0, "kind": "emg.brace", "onset_t": 100.9,
         "trace": [[100.8, 0.0, 0.0, 0.0], [100.95, 0.9, 0.8, 0.8]]},
        {"seq": 3, "t": 102.0, "kind": "incident.saved", "incident_id": sample.name,
         "path": str(sample)},
    ]
    (sess / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n{torn")
    s = load_session(sess)
    assert [p.name for p in s.incident_paths] == [sample.name]
    p = s.payload()
    assert p["timeline"][1]["t"] == 1.0 and "trace" not in p["timeline"][1]
    assert p["braces"][0]["onset_t"] == 0.9 and p["braces"][0]["trace"]["brace"] == [0.0, 0.8]
    lib = ReplayLibrary.from_path(sess)
    assert set(lib.incident_ids()) == {sample.name} and set(lib.session_ids()) == {sess.name}


# ── replay server: read-only, same local rules ───────────────────────────────
ACTUATION = [
    ("post", "/api/say"), ("post", "/api/ask"), ("post", "/api/confirm"),
    ("post", "/api/choose/cup"), ("post", "/api/cancel"), ("post", "/api/arm/arm"),
    ("post", "/api/arm/reset"), ("post", "/api/arm/home"), ("post", "/api/gripper/release"),
    ("post", "/api/emg/synthetic"), ("post", "/api/emg/calibrate/rest"),
    ("post", "/api/rules/proposal/r1/approve"), ("delete", "/api/rules/r1"),
    ("post", "/api/session/start"), ("post", "/api/trial/start"),
    ("post", "/api/replay/scenario"), ("post", "/api/replay/incident/x"),
]


@pytest.mark.parametrize("method, path", ACTUATION)
def test_replay_server_has_no_actuation_route(sample, method, path):
    c = client(ReplayLibrary(scan_dirs=[sample.parent]))
    assert getattr(c, method)(path, headers=GOOD).status_code in (404, 405)
    assert getattr(c, method)(path).status_code == 403  # the token rule still applies


def test_replay_server_serves_the_sample(sample):
    c = client(ReplayLibrary(scan_dirs=[sample.parent]))
    index = c.get("/api/replay").json()
    assert [i["id"] for i in index["incidents"]] == [sample.name]
    assert index["incidents"][0]["label"] == "SIMULATION" and index["scenario"] is False
    p = c.get(f"/api/replay/incident/{sample.name}").json()
    assert p["explanation"]["source"] == "canned" and p["proposed_rule"]["approved"] is False
    frame = c.get(p["frames"][0]["cams"]["overhead"])
    assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg"
    assert frame.content[:2] == b"\xff\xd8"
    emg = c.get(p["emg"]["url"]).json()
    assert emg["label"] == "SIMULATION" and emg["onset"]["source"] == "recorded"
    tl = c.get(f"/api/replay/incident/{sample.name}/timeline").json()["timeline"]
    assert any(e["kind"] == "freeze" for e in tl)
    assert c.get("/api/replay/incident/nope").status_code == 404
    assert c.get("/api/replay/incident/..%2F..%2Fetc").status_code in (400, 404)
    assert c.get(f"/api/replay/incident/{sample.name}/frame/0/../meta.jpg").status_code == 404
    assert c.get("/api/replay/scenario").status_code == 404
    state = c.get("/api/state").json()
    assert state["mode"] == "replay" and state["label"] == "REPLAY" and "arm" not in state
    stop = c.post("/api/stop")  # tokenless, like the live API; nothing to stop here
    assert stop.status_code == 200 and "nothing is moving" in stop.json()["detail"]


def test_replay_server_keeps_the_local_rules(sample):
    lib = ReplayLibrary(scan_dirs=[sample.parent])
    for url in ("http://evil.example", "http://192.168.1.5:8080"):
        assert client(lib, base=url).get("/api/replay").status_code == 403
    c = client(lib)
    assert c.get("/api/replay", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/stop", headers={"Origin": "https://evil.example"}).status_code == 403
    page = c.get("/")
    assert page.headers["cache-control"] == "no-store"
    assert f'<meta name="h2m-token" content="{TOKEN}" />' in page.text
    assert '<meta name="h2m-mode" content="replay" />' in page.text


def test_replay_of_a_folder_skips_unsafe_names(tmp_path, sample):
    lib_dir = sample.parent
    shutil.copytree(sample, lib_dir / "bad name!")
    lib = ReplayLibrary.from_path(lib_dir)
    assert set(lib.incident_ids()) == {sample.name}
    with pytest.raises(KeyError):
        lib.incident("../demo")
    with pytest.raises(ReplayError):
        ReplayLibrary.from_path(tmp_path / "missing")


# ── learner: provenance, trigger and the virtual camera ──────────────────────
def test_incident_meta_records_provenance_and_trigger(h2m):
    data = learning.flinch_and_explain(h2m)
    meta = json.loads((h2m.learner.root / data["incident_id"] / "meta.json").read_text())
    assert meta["trigger"]["source"] == "brace" and meta["trigger"]["onset_t"] is not None
    assert meta["provenance"] == {"arm": "virtual", "arm_hardware": False, "emg": "off",
                                  "emg_hardware": None, "camera": "off", "simulation": True}
    assert source_label(meta)["label"] == "SIMULATION"


def test_virtual_camera_is_never_used_with_a_real_camera(h2m):
    calls = []
    h2m.learner.virtual_camera = lambda: calls.append(1) or np.zeros((4, 4, 3), np.uint8)
    h2m.learner.sample()
    assert calls == [1] and h2m.learner.buffer._frames[-1].overhead is not None

    class Cam:
        def latest(self):
            return np.ones((4, 4, 3), np.uint8), now()

    class Vision:
        scene, wrist = Cam(), None

    h2m.vision = Vision()
    h2m.learner.buffer.reset()
    h2m.learner.sample()
    assert calls == [1] and h2m.learner.buffer._frames[-1].overhead.max() == 1


def test_canned_client_labels_its_explanation():
    frame = Frame(0.0, overhead=np.zeros((8, 8, 3), np.uint8))
    inc = Incident("x", 0.0, [frame], np.zeros((0, 0)), np.zeros(0), held="paper cup")
    exp = explain(inc, CannedMuse())
    assert exp.source == "canned" and exp.rule["avoid"] == "laptop"


# ── scripted scenario ────────────────────────────────────────────────────────
def test_scenario_config_forces_simulation_and_keeps_every_threshold():
    cfg = load_config()
    sim = scenario_config(cfg)
    assert sim["arm"]["backend"] == "virtual" and sim["emg"]["source"] == "synthetic"
    assert sim["vision"]["source"] == "off" and sim["speech"]["source"] == "off"
    assert sim["speech"]["voice_out"] is False and sim["compiler"]["llm"] == "off"
    assert sim["safety"]["operator_release"] is False
    assert sim["rules"] == cfg["rules"]
    assert {k: v for k, v in sim["safety"].items() if k != "operator_release"} == {
        k: v for k, v in cfg["safety"].items() if k != "operator_release"}
    assert {k: v for k, v in sim["arm"].items() if k != "backend"} == {
        k: v for k, v in cfg["arm"].items() if k != "backend"}
    assert {k: v for k, v in sim["emg"].items() if k != "source"} == {
        k: v for k, v in cfg["emg"].items() if k != "source"}
    assert ScenarioSettings(approve_after_s=0.0).approve_after_s >= 0.5  # never inside the gate


def test_scenario_refuses_a_hardware_arm(monkeypatch, tmp_path):
    from h2m.robot.backend import VirtualArm

    monkeypatch.setattr(VirtualArm, "is_hardware", True)
    sc = Scenario(fast_cfg(), tmp_path, FAST)
    assert sc.run() == "failed" and "not the virtual arm" in sc.error
    assert sc.h2m is None  # never started, never armed


def test_scenario_refuses_non_synthetic_emg(monkeypatch, tmp_path):
    import h2m.scenario as scenario_mod

    real = scenario_mod.scenario_config
    monkeypatch.setattr(scenario_mod, "scenario_config",
                        lambda cfg: {**real(cfg), "emg": {**cfg["emg"], "source": "off"}})
    sc = Scenario(fast_cfg(), tmp_path, FAST)
    assert sc.run() == "failed" and "EMG is not synthetic" in sc.error


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    """One full pass of the story with every network path refusing to connect."""
    mp = pytest.MonkeyPatch()
    mp.setenv("MODEL_API_KEY", "not-a-real-key")  # a key present must not mean a call

    def no_network(*a, **k):
        raise AssertionError("network used in the simulation")

    mp.setattr(socket.socket, "connect", no_network)
    mp.setattr(socket, "create_connection", no_network)
    mp.setattr(httpx, "post", no_network)
    mp.setattr(httpx.Client, "send", no_network)
    work = tmp_path_factory.mktemp("scenario")
    sc = Scenario(fast_cfg(), work, FAST)
    t0 = time.monotonic()
    state = sc.run()
    elapsed = time.monotonic() - t0
    mp.undo()
    yield sc, state, elapsed, work
    sc.close()


def test_scenario_plays_the_whole_story(full_run):
    sc, state, elapsed, work = full_run
    snap = sc.snapshot()
    assert state == "done", (sc.error, snap["steps"])
    assert [s["status"] for s in snap["steps"]] == ["done"] * len(STEP_NAMES)
    assert snap["simulation"] is True and snap["label"] == "SIMULATION"
    first, second = snap["runs"]
    assert first["route"] == "direct" and first["outcome"] == "frozen: brace (EMG)"
    assert first["verdict"] == "ask" and second["verdict"] == "ask"  # both carries confirmed
    assert second["route"] == "via_left" and second["outcome"] == "completed"
    assert second["applied_rules"] == [snap["rule"]["id"]] and snap["rule"]["approved"] is True
    assert snap["rule"]["response"] == "route_around" and snap["rule"]["avoid"] == "laptop"
    assert second["closest_to_zone"] > 0.05  # the long way round stays off the laptop
    assert snap["explanation"]["source"] == "canned" and snap["explanation"]["canned"] is True
    assert snap["brace"]["detect_ms"] is not None  # the real detector fired, not a stub event


def test_scenario_used_only_its_own_stores_and_the_canned_client(full_run):
    sc, _, _, work = full_run
    h = sc.h2m
    assert h.learner.muse.calls == 1 and h.llm is None
    assert Path(h.rules.path).is_relative_to(work) and h.learner.root.is_relative_to(work)
    assert h.emg.profile.name == "synthetic-sim" and h.arm.backend.is_hardware is False
    inc = load_incident(work / "incidents" / sc.incident_id)
    assert inc.meta["provenance"]["simulation"] is True and inc.meta["provenance"]["camera"] == (
        "virtual")
    assert inc.frames() and inc.explanation_payload()["source"] == "canned"
    assert inc.brace_onset(0.35, 0.12)["source"] == "recorded"


def test_scenario_is_served_read_only(full_run):
    sc, _, _, work = full_run
    c = client(ReplayLibrary(scan_dirs=[sc.incidents_dir]), sc)
    snap = c.get("/api/replay/scenario").json()
    assert snap["state"] == "done" and snap["label"] == "SIMULATION"
    assert snap["arm"]["backend"] == "virtual" and snap["emg"]["source"] == "synthetic"
    frame = c.get("/api/replay/scenario/frame.jpg")
    assert frame.status_code == 200 and frame.content[:2] == b"\xff\xd8"
    assert c.get("/api/camera/scene.jpg").status_code == 200
    tl = c.get("/api/replay/scenario/timeline").json()
    assert tl["label"] == "SIMULATION" and any(e["kind"] == "rule.approved" for e in tl["events"])
    state = c.get("/api/state").json()
    assert state["label"] == "SIMULATION" and state["arm"]["backend"] == "virtual"
    rules = c.get("/api/rules").json()["rules"]
    assert [r["id"] for r in rules] == [snap["rule"]["id"]]
    assert c.get(f"/api/replay/incident/{sc.incident_id}").json()["label"] == "SIMULATION"
    for method, path in ACTUATION:
        assert getattr(c, method)(path, headers=GOOD).status_code in (404, 405)


def test_operator_stop_aborts_the_scenario_and_stays_latched(tmp_path):
    sc = Scenario(fast_cfg(), tmp_path, FAST)
    thread = sc.start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        h = sc.h2m
        if h is not None and h.arm.state is ArmState.MOVING:
            break
        time.sleep(0.02)
    else:
        pytest.fail("the arm never started")
    c = client(ReplayLibrary(), sc)
    assert c.post("/api/stop").json()["ok"]  # the UI's STOP: tokenless, always safe
    thread.join(10)
    h = sc.h2m
    assert sc.state == "aborted" and "operator stop" in sc.error
    assert h.arm.state is ArmState.STOPPED and h.arm.reason == "ui stop"
    time.sleep(0.3)  # nothing resets or re-arms it afterwards
    assert h.arm.state is ArmState.STOPPED
    steps = {s.name: s.status for s in sc.steps}
    assert steps["rearm"] == "pending" and steps["approve"] == "pending"
    assert h.rules.list() == []
    sc.close()


def test_script_never_resets_a_stop_it_did_not_script(tmp_path):
    sc = Scenario(fast_cfg(), tmp_path, FAST, until="approve")
    assert sc.run() == "done", sc.error
    h = sc.h2m
    assert h.arm.state is ArmState.STOPPED and h.arm.reason == "brace (EMG)"
    h.stop("someone else's stop")  # latched again, for a reason the script did not cause
    with pytest.raises(ScenarioAborted):
        sc._step_rearm()
    assert h.arm.state is ArmState.STOPPED and h.arm.reason == "someone else's stop"
    sc.close()


def test_scenario_close_stops_its_threads(tmp_path):
    sc = Scenario(fast_cfg(), tmp_path, FAST)
    thread = sc.start(loop=True)
    time.sleep(0.5)
    sc.close()
    thread.join(10)
    assert not thread.is_alive() and sc.h2m is None
    assert threading.active_count() < 50
