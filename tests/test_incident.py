"""Incident capture and explanation: ring buffer, clip saving, schema, fallbacks. No network."""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest

import h2m.incident as incident_mod
from h2m.incident import (  # noqa: E402
    FALLBACK_SAY,
    SAM_MODEL,
    TOOLS,
    WONDER_MODEL,
    IncidentBuffer,
    Investigation,
    InvestigationConfig,
    Investigator,
    MuseClient,
    encode_clip,
    explain,
    investigate,
    parse_sam_box,
    sam_label,
    select_keyframes,
    validate_explanation,
)

GOOD = {
    "cause_object": "laptop",
    "relation": "carried_over",
    "held_property": "liquid",
    "say": "You braced as I carried water over your laptop. Should I keep liquids away from it?",
    "confidence": 0.8,
    "rule": {"when_holding": ["liquid"], "avoid": "laptop", "response": "route_around"},
}


def make_incident(tmp_path, n=40, fps=10):
    buf = IncidentBuffer(seconds=6.0, fps=5.0)
    for i in range(n):
        t = i / fps
        buf.add_frame(
            t,
            np.full((480, 640, 3), i, np.uint8),
            joints=[0.0, i * 0.01],
            contract={"verb": "pick_place"},
        )
        buf.add_emg(t, [0.1, 0.2])
    buf.freeze(t=n / fps - 0.1, after_s=1.0)
    return buf


class FakeClient:
    def __init__(self, reply=None, exc=None, box=None):
        self.reply, self.exc, self.box, self.calls = reply, exc, box, 0

    def chat(self, model, messages):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.reply

    def segment(self, model, image, label):
        return self.box


def test_ring_buffer_bounds_and_downsample():
    buf = IncidentBuffer(seconds=2.0, fps=5.0, downscale=100)
    for i in range(200):
        buf.add_frame(i * 0.05, np.zeros((480, 640, 3), np.uint8))
    assert len(buf._frames) <= 11
    assert buf._frames[-1].t - buf._frames[0].t <= 2.0
    assert max(buf._frames[0].overhead.shape[:2]) == 100


def test_freeze_keeps_after_window_and_saves_clip(tmp_path):
    buf = make_incident(tmp_path)
    t_freeze = buf._freeze_at
    for i in range(40, 60):  # 2 s more, only 1 s should be kept
        buf.add_frame(i / 10, np.zeros((480, 640, 3), np.uint8))
        buf.add_emg(i / 10, [0.9, 0.9])
    inc = buf.save(tmp_path, objects=[{"id": "laptop"}], held="cup")
    assert max(f.t for f in inc.frames) <= t_freeze + 1.0
    d = tmp_path / inc.id
    meta = json.loads((d / "meta.json").read_text())
    assert 6 <= len(meta["keyframes"]) <= 8
    assert all((d / k["overhead"]).exists() for k in meta["keyframes"])
    assert np.load(d / "emg.npy").shape[1] == 3
    assert meta["held"] == "cup"


def test_save_before_freeze_raises(tmp_path):
    with pytest.raises(RuntimeError):
        IncidentBuffer().save(tmp_path)


def test_keyframes_labeled_and_end_at_freeze(tmp_path):
    inc = make_incident(tmp_path).save(tmp_path)
    keys = select_keyframes(inc.frames, inc.t_freeze, n=7)
    assert len(keys) == 7
    assert all(label.startswith("t") for label, _ in keys)
    assert keys[0][1].t <= keys[-1][1].t


def test_schema_accepts_good_and_rejects_bad():
    assert validate_explanation(GOOD).rule["response"] == "route_around"
    bad_resp = {**GOOD, "rule": {**GOOD["rule"], "response": "teleport"}}
    assert validate_explanation(bad_resp) is None
    for conf in (1.5, -0.1, "high", True):
        assert validate_explanation({**GOOD, "confidence": conf}) is None
    assert validate_explanation({**GOOD, "say": ""}) is None
    assert validate_explanation({k: v for k, v in GOOD.items() if k != "rule"}) is None
    assert validate_explanation("nope") is None


def test_explain_success_with_sam_and_box_fallback(tmp_path):
    inc = make_incident(tmp_path).save(tmp_path, objects=[{"id": "laptop", "box": [1, 2, 3, 4]}])
    exp = explain(inc, FakeClient("sure: " + json.dumps(GOOD), box=[5, 6, 7, 8]))
    assert exp.source == "wonder" and exp.mask_source == "sam" and exp.mask_box == (5, 6, 7, 8)
    exp = explain(inc, FakeClient(json.dumps(GOOD), box=None))
    assert exp.mask_source == "box" and exp.mask_box == (1, 2, 3, 4)


def test_explain_falls_back(tmp_path):
    for client in [
        FakeClient(exc=TimeoutError("boom")),
        FakeClient(reply="not json"),
        FakeClient(reply=json.dumps({**GOOD, "rule": {**GOOD["rule"], "response": "x"}})),
        None,
    ]:
        inc = make_incident(tmp_path).save(tmp_path)
        exp = explain(inc, client)
        assert exp.source == "fallback" and exp.say == FALLBACK_SAY and exp.rule is None


def test_schema_rejects_empty_when_holding_and_low_confidence():
    empty = {**GOOD, "rule": {**GOOD["rule"], "when_holding": []}}
    assert validate_explanation(empty) is None
    blank = {**GOOD, "rule": {**GOOD["rule"], "when_holding": [""]}}
    assert validate_explanation(blank) is None
    assert validate_explanation({**GOOD, "confidence": 0.29}) is None
    assert validate_explanation({**GOOD, "confidence": 0.3}) is not None


def test_explain_low_confidence_falls_back(tmp_path):
    inc = make_incident(tmp_path).save(tmp_path)
    exp = explain(inc, FakeClient(json.dumps({**GOOD, "confidence": 0.1})))
    assert exp.source == "fallback" and exp.rule is None


def test_explain_survives_locate_failure(tmp_path):
    class Exploding(FakeClient):
        def segment(self, model, image, label):
            raise RuntimeError("sam down")

    inc = make_incident(tmp_path).save(tmp_path, objects=[{"id": "laptop", "box": "garbage"}])
    exp = explain(inc, Exploding(json.dumps(GOOD)))
    assert exp.source == "fallback" and exp.say == FALLBACK_SAY


def test_save_copies_under_lock_and_freeze_can_be_reset(tmp_path):
    buf = make_incident(tmp_path)
    assert buf.frozen
    inc = buf.save(tmp_path, reset=True)
    assert inc.frames and inc.path.exists()
    assert not buf.frozen and not buf._frames and not buf._after
    # recording resumes normally and a second incident can be frozen and saved
    for i in range(10):
        buf.add_frame(100 + i * 0.2, np.zeros((10, 10, 3), np.uint8))
    assert len(buf._frames) > 0 and not buf._after
    buf.freeze(t=101.8, after_s=0.5)
    again = buf.save(tmp_path, incident_id="second")
    assert again.id == "second" and again.t_freeze == 101.8
    # the saved snapshot is independent of later writes to the buffer
    n = len(again.frames)
    buf.add_frame(101.9, np.zeros((10, 10, 3), np.uint8))
    assert len(again.frames) == n


def test_buffer_is_thread_safe():
    import threading

    buf = IncidentBuffer(seconds=1.0, fps=100.0)
    errors = []

    def writer(offset):
        try:
            for i in range(300):
                buf.add_frame(offset + i * 0.01, np.zeros((8, 8, 3), np.uint8))
                buf.add_emg(offset + i * 0.01, [0.1, 0.2])
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(k * 0.001,)) for k in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors


# --- SAM segment() against a fake HTTP layer (no network) -------------------------------

MASK = "<|mask;x=0;y=0;c=0.9;data=4,4,!a|b~<>c|>"  # payload with awkward characters
IMG = np.zeros((100, 200, 3), np.uint8)  # h=100, w=200


def sam_reply(text):
    return {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ]
    }


class FakeResp:
    def __init__(self, data, status=200):
        self.data, self.status = data, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self.data


@pytest.fixture
def sam_http(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "sk-secret-123")
    monkeypatch.delenv("H2M_SAM_MODEL", raising=False)
    calls = []
    state = {"resp": FakeResp(sam_reply("")), "exc": None}

    def post(url, json=None, timeout=None, headers=None):
        calls.append({"url": url, "json": json, "timeout": timeout, "headers": headers})
        if state["exc"]:
            raise state["exc"]
        return state["resp"]

    monkeypatch.setattr(incident_mod.httpx, "post", post)
    return calls, state


def test_segment_request_shape_and_box(sam_http):
    calls, state = sam_http
    state["resp"] = FakeResp(
        sam_reply(f"<0f>0<|box;x1=10;y1=20;x2=59;y2=69;w=200;h=100;c=0.7|>{MASK}")
    )
    client = MuseClient({"base_url": "https://example.test/v1/", "sam_timeout_s": 3})
    assert client.segment(SAM_MODEL, IMG, "laptop") == (10.0, 20.0, 60.0, 70.0)
    (call,) = calls
    assert call["url"] == "https://example.test/v1/responses" and call["timeout"] == 3.0
    assert call["headers"] == {"Authorization": "Bearer sk-secret-123"}
    body = call["json"]
    assert body["model"] == "sam-3.1"
    parts = body["input"][0]["content"]
    assert parts[0] == {"type": "input_text", "text": "laptop"}
    assert parts[1]["type"] == "input_image"
    assert parts[1]["image_url"].startswith("data:image/jpeg;base64,")


def test_segment_picks_most_confident_and_rescales(sam_http):
    _, state = sam_http
    # SAM saw a 400x200 image; ours is 200x100, so coordinates halve
    text = (
        "<0f>0<|box;x1=0;y1=0;x2=39;y2=39;w=400;h=200;c=0.4|>"
        + MASK
        + ",1<|box;x1=100;y1=40;x2=199;y2=139;w=400;h=200;c=0.9|>"
        + MASK
    )
    state["resp"] = FakeResp(sam_reply(text))
    assert MuseClient().segment(SAM_MODEL, IMG, "cup") == (50.0, 20.0, 100.0, 70.0)


def test_segment_model_comes_from_config_or_env(sam_http, monkeypatch):
    assert MuseClient().sam_model == "sam-3.1"
    assert MuseClient({"sam_model": "sam-x"}).sam_model == "sam-x"
    monkeypatch.setenv("H2M_SAM_MODEL", "sam-env")
    assert MuseClient().sam_model == "sam-env"
    assert MuseClient({"sam_model": "sam-x"}).sam_model == "sam-x"


def test_segment_rejects_bad_replies(sam_http):
    for reply in [
        "",  # no match is a valid empty result
        "garbage",
        "<0f>0<|box;x1=50;y1=50;x2=10;y2=10;w=200;h=100|>",  # inverted
        "<0f>0<|box;x1=500;y1=500;x2=600;y2=600;w=200;h=100|>",  # outside the image
        "<0f>0<|box;x1=1;y1=1;x2=9;y2=9;w=0;h=0|>",
        "<0f>0<|box;x1=1;y1=1;x2=9;y2=9;w=200;h=100;c=7|>",  # confidence out of range
    ]:
        _, state = sam_http
        state["resp"] = FakeResp(sam_reply(reply))
        assert MuseClient().segment(SAM_MODEL, IMG, "laptop") is None


@pytest.mark.parametrize(
    "failure",
    [
        {"exc": TimeoutError("slow")},
        {"exc": ConnectionError("down")},
        {"resp": FakeResp({}, status=500)},
        {"resp": FakeResp("not a dict")},
        {"resp": FakeResp({"output": "bad"})},
    ],
)
def test_segment_never_raises(sam_http, failure):
    _, state = sam_http
    state.update(failure)
    assert MuseClient().segment(SAM_MODEL, IMG, "laptop") is None


def test_segment_without_key_or_bad_input_makes_no_call(sam_http, monkeypatch):
    calls, _ = sam_http
    c = MuseClient()
    assert c.segment(SAM_MODEL, IMG, None) is None
    assert c.segment(SAM_MODEL, IMG, "") is None
    assert c.segment(SAM_MODEL, IMG, "<0f>1<|box;1|>") is None  # would switch SAM prompt mode
    assert c.segment(SAM_MODEL, None, "laptop") is None
    assert c.segment(SAM_MODEL, np.zeros((0, 0, 3), np.uint8), "laptop") is None
    monkeypatch.delenv("MODEL_API_KEY")
    assert MuseClient().segment(SAM_MODEL, IMG, "laptop") is None
    assert calls == []


def test_segment_error_state_does_not_leak_key(sam_http):
    _, state = sam_http
    state["exc"] = RuntimeError("Bearer sk-secret-123 rejected")
    c = MuseClient()
    assert c.segment(SAM_MODEL, IMG, "laptop") is None
    assert "sk-secret-123" not in (c.last_error or "")


def test_sam_helpers():
    assert sam_label("  red   cup ") == "red cup"
    assert sam_label("a" * 200) is None and sam_label(5) is None
    assert parse_sam_box("", (10, 10)) is None


class RealClientWithFakeHttp(MuseClient):
    """MuseClient whose chat and segment are real code paths except the HTTP layer."""

    def chat(self, model, messages):
        return json.dumps(GOOD)


def test_explain_uses_segment_then_falls_back_to_detector_box(tmp_path, sam_http):
    calls, state = sam_http
    inc = make_incident(tmp_path).save(tmp_path, objects=[{"id": "laptop", "box": [1, 2, 3, 4]}])
    state["resp"] = FakeResp(sam_reply(f"<0f>0<|box;x1=10;y1=20;x2=59;y2=69;w=320;h=240|>{MASK}"))
    client = RealClientWithFakeHttp({"sam_model": "sam-cfg"})
    exp = explain(inc, client)
    assert exp.mask_source == "sam" and exp.source == "wonder"
    assert calls[0]["json"]["model"] == "sam-cfg"  # model id comes from the client config
    assert calls[0]["json"]["input"][0]["content"][0]["text"] == "laptop"
    # SAM times out: the arm still gets an explanation and the detector box
    state["exc"] = TimeoutError("slow")
    exp = explain(inc, client)
    assert exp.source == "wonder" and exp.mask_source == "box" and exp.mask_box == (1, 2, 3, 4)

# added alongside today's changes
OK_CHECKS = {"restrictive": True, "prevented": True,
             "detail": {"restrictive": "routes 9 -> 6, none added", "prevented": "via_left clears"}}

def passing_check(rule):
    return [], OK_CHECKS

def fcall(name, args, i=0):
    return {"type": "function_call", "call_id": f"c{i}-{name}", "name": name,
            "arguments": json.dumps(args)}

def wonder_reply(calls=(), text=""):
    out = list(calls)
    if text:
        out.append({"type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": text}]})
    return {"output": out}

SAM_BOX = "<0f>0<|box;x1=100;y1=100;x2=139;y2=139;w=640;h=480;c=0.9|>"

@pytest.fixture
def wonder_http(monkeypatch):
    """httpx.post fake: /responses with `tools` in the body is a Wonder turn (scripted);
    any other /responses call is SAM, answered with one box."""
    monkeypatch.setenv("MODEL_API_KEY", "sk-secret-123")
    state = {"turns": [], "sam": FakeResp(sam_reply(SAM_BOX)), "exc": None}
    calls = {"wonder": [], "sam": []}

    def post(url, json=None, timeout=None, headers=None):
        assert headers == {"Authorization": "Bearer sk-secret-123"}
        if state["exc"] is not None:
            raise state["exc"]
        json = copy.deepcopy(json)  # a real transport serializes: no shared mutable state
        if "tools" in (json or {}):
            calls["wonder"].append(json)
            n = len(calls["wonder"])
            return FakeResp(state["turns"][n - 1] if n <= len(state["turns"]) else wonder_reply())
        calls["sam"].append(json)
        return state["sam"]

    monkeypatch.setattr(incident_mod.httpx, "post", post)
    return calls, state

def test_encode_clip_makes_an_mp4_and_handles_nothing(tmp_path):
    inc = make_incident(tmp_path).save(tmp_path)
    data = encode_clip(inc.frames, 5.0, tmp_path / "clip.mp4")
    assert data and data[4:8] == b"ftyp" and (tmp_path / "clip.mp4").read_bytes() == data
    assert encode_clip([], 5.0) is None

def test_tool_loop_request_shape_tools_feed_and_calls(tmp_path, wonder_http):
    calls, state = wonder_http
    inc = make_incident(tmp_path).save(tmp_path, objects=[{"id": "laptop", "box": [1, 2, 3, 4]}],
                                       held="paper cup (tags: liquid, fragile)")
    state["turns"] = [
        wonder_reply([fcall("track", {"phrase": "cup"}, 0), fcall("track", {"phrase": "laptop"}, 1)]),
        wonder_reply([fcall("measure", {"a": "cup", "b": "laptop", "t": 0}, 2),
                      fcall("arm_trace", {}, 3), fcall("body_trace", {}, 4)]),
        wonder_reply([fcall("propose_rule", GOOD, 5)]),
    ]
    feed = []
    client = MuseClient({"timeout_s": 7})
    inv = Investigator(inc, client, InvestigationConfig(track_frames=2), check=passing_check,
                       on_event=feed.append, zones=["laptop"], tags=["fragile", "liquid"])
    result = inv.run()
    assert isinstance(result, Investigation) and result.status == "proposed"
    assert result.explanation.source == "wonder" and result.checks == OK_CHECKS
    assert result.tool_calls == 6 and result.turns == 3
    # request shape: Responses API, flat function tools, the clip as video, the system text
    first = calls["wonder"][0]
    assert first["model"] == WONDER_MODEL and first["tools"] == TOOLS
    assert first["tool_choice"] == "auto" and first["reasoning"] == {"effort": "low"}
    assert [t["name"] for t in first["tools"]] == ["track", "measure", "arm_trace", "body_trace",
                                                  "propose_rule"]
    content = first["input"][0]["content"]
    assert content[0]["type"] == "input_text" and "laptop" in content[0]["text"]
    assert content[-1]["type"] == "input_video"
    assert content[-1]["video_url"].startswith("data:video/mp4;base64,")
    # the second turn carries the function calls and their outputs, call ids matched
    second = calls["wonder"][1]["input"]
    assert [it["type"] for it in second[1:]] == ["function_call", "function_call_output",
                                                "function_call", "function_call_output"]
    assert second[2]["call_id"] == "c0-track" and second[4]["call_id"] == "c1-track"
    assert json.loads(second[2]["output"])["found_in"] == 2
    # SAM was asked per tracked frame, two phrases x two frames, never past the loop
    assert len(calls["sam"]) == 4
    assert {c["input"][0]["content"][0]["text"] for c in calls["sam"]} == {"cup", "laptop"}
    # the feed: one line per tool and per Wonder turn, in order, with the fields the UI reads
    tools = [e["tool"] for e in feed]
    assert tools == ["wonder", "sam-3.1 track", "sam-3.1 track", "wonder", "measure",
                     "measure arm_trace", "measure body_trace", "wonder", "wonder propose_rule",
                     "checker"]
    for e in feed:
        assert {"t", "tool", "args", "result_summary", "ms"} <= set(e)
    assert "uncalibrated" in feed[4]["result_summary"]  # no homography
    assert feed[-1]["ticks"] == [True, True] and feed[-1]["ok"] is True
    assert "removes options only" in feed[-1]["result_summary"]
    assert "propose: holding liquid, route around the laptop" == feed[-2]["result_summary"]
    # calls: every model call landed with ms, labelled for the UI chips
    models = [(c["model"], c["for"]) for c in inv.calls]
    assert models.count((WONDER_MODEL, "investigate")) == 3 and models.count(("sam-3.1", "track")) == 4
    assert all(isinstance(c["ms"], int) and "started_at_ms" in c for c in inv.calls)

def test_tool_loop_sends_keyframes_when_video_is_off(tmp_path, wonder_http):
    calls, state = wonder_http
    inc = make_incident(tmp_path).save(tmp_path)
    state["turns"] = [wonder_reply(text="The person probably moved on purpose.")]
    result = investigate(inc, MuseClient(), InvestigationConfig(video=False), check=passing_check)
    assert result.status == "no_proposal" and result.explanation is None
    assert "moved on purpose" in result.text
    content = calls["wonder"][0]["input"][0]["content"]
    assert [c["type"] for c in content if c["type"] == "input_image"]
    assert not [c for c in content if c["type"] == "input_video"]

def test_measure_uses_the_table_homography_when_calibrated(tmp_path, wonder_http):
    _, state = wonder_http
    inc = make_incident(tmp_path).save(tmp_path)
    # The buffer keeps 320x240 frames; SAM's 640x480 boxes are rescaled to them. Table units
    # = image x/640, y/480 here (a plain scale), and a table unit is 1000 mm.
    H = [[1 / 640, 0, 0], [0, 1 / 480, 0], [0, 0, 1]]
    inv = Investigator(inc, MuseClient(), InvestigationConfig(track_frames=3), check=passing_check,
                       homography=H, table_unit_mm=1000.0)
    state["sam"] = FakeResp(sam_reply(SAM_BOX))
    cup = inv.tool_track("cup")
    assert cup["found_in"] == 3 and cup["frames"][0]["center"] == [60.0, 60.0]
    state["sam"] = FakeResp(sam_reply("<0f>0<|box;x1=200;y1=100;x2=239;y2=139;w=640;h=480|>"))
    m = inv.tool_measure("cup", "laptop", 0)
    assert m["calibrated"] is True and m["distance_cm"] == pytest.approx(7.8, abs=0.1)
    assert m["a_speed_cm_s"] == 0.0 and m["a_over_b"] is False  # the fake box never moves
    bad = inv.tool_measure("cup", "laptop", "soon")
    assert "error" in bad
    state["sam"] = FakeResp(sam_reply(""))
    assert "not seen" in inv.tool_measure("cup", "ghost", 0)["error"]
    assert inv.tool_arm_trace()["trace"] and inv.tool_body_trace()["peak"]["v"] == [0.1, 0.2]

def test_a_rejected_proposal_goes_back_to_wonder_once_then_the_loop_drops_it(tmp_path, wonder_http):
    calls, state = wonder_http
    inc = make_incident(tmp_path).save(tmp_path)
    wrong = {**GOOD, "rule": {**GOOD["rule"], "when_holding": ["rigid"]}}
    state["turns"] = [wonder_reply([fcall("propose_rule", wrong, 0)]),
                      wonder_reply([fcall("propose_rule", wrong, 1)]),
                      wonder_reply([fcall("propose_rule", GOOD, 2)])]
    seen = []

    def check(rule):
        seen.append(rule)
        if rule["when_holding"] == ["rigid"]:
            return ["checker: incident not prevented (the rule needs rigid)"], {
                "restrictive": True, "prevented": False, "detail": {}}
        return [], OK_CHECKS

    feed = []
    result = investigate(inc, MuseClient(), None, check=check, on_event=feed.append)
    assert result.status == "rejected" and result.explanation is None
    assert len(result.rejected) == 2 and len(calls["wonder"]) == 2  # dropped: no third turn
    # the first rejection reached Wonder as the tool's result, with the reason
    out = json.loads(calls["wonder"][1]["input"][-1]["output"])
    assert out["ok"] is False and "not prevented" in out["reason"]
    assert out["checks"]["prevented"] is False
    checker = [e for e in feed if e["tool"] == "checker"]
    assert [c["ok"] for c in checker] == [False, False] and checker[0]["ticks"] == [True, False]
    # a schema miss is a rejection too, with no checker run
    state["turns"] = [wonder_reply([fcall("propose_rule", {"say": "hm"}, 0)]),
                      wonder_reply([fcall("propose_rule", GOOD, 1)])]
    calls["wonder"].clear()
    seen.clear()
    result = investigate(inc, MuseClient(), None, check=check)
    assert result.status == "proposed" and len(result.rejected) == 1 and len(seen) == 1
    assert "schema" in json.loads(calls["wonder"][1]["input"][-1]["output"])["reason"]

def test_tool_loop_is_capped_and_never_raises(tmp_path, wonder_http):
    calls, state = wonder_http
    inc = make_incident(tmp_path).save(tmp_path)
    # tool-call cap: the third tool gets a budget error and the loop ends without a proposal
    state["turns"] = [wonder_reply([fcall("track", {"phrase": "cup"}, 0),
                                    fcall("track", {"phrase": "laptop"}, 1),
                                    fcall("arm_trace", {}, 2)])] + [wonder_reply(
        [fcall("arm_trace", {}, i)]) for i in range(3, 9)]
    result = investigate(inc, MuseClient(), InvestigationConfig(max_tool_calls=2, track_frames=1),
                         check=passing_check)
    assert result.status == "budget" and result.tool_calls == 2
    assert "budget" in json.loads(calls["wonder"][1]["input"][-1]["output"])["error"]
    # wall-clock budget: nothing is sent once it is spent
    calls["wonder"].clear()
    result = investigate(inc, MuseClient(), InvestigationConfig(budget_s=1.0), check=passing_check,
                         )
    assert result.status in ("budget", "no_proposal")
    calls["wonder"].clear()
    inv = Investigator(inc, MuseClient(), InvestigationConfig(budget_s=1.0), check=passing_check)
    inv.t0 -= 5.0
    assert inv.run().status == "budget" and calls["wonder"] == []
    # transport failure: error, not an exception; the arm stays whatever it was
    state["exc"] = TimeoutError("slow")
    result = investigate(inc, MuseClient(), None, check=passing_check)
    assert result.status == "error" and result.events[-1]["ok"] is False
    state["exc"] = None
    # a client without the Responses path is "unavailable" (the caller falls back)
    assert investigate(inc, FakeClient(json.dumps(GOOD)), None, check=passing_check).status == "unavailable"
    # a check callback that blows up is an error too, never a crash
    state["turns"] = [wonder_reply([fcall("propose_rule", GOOD, 0)])]

    def boom(rule):
        raise RuntimeError("no routes")

    assert investigate(inc, MuseClient(), None, check=boom).status == "error"

def test_tool_loop_config_from_yaml_clamps():
    cfg = InvestigationConfig.from_cfg({"max_tool_calls": 0, "budget_s": -1, "track_frames": 0,
                                        "video": "yes", "reasoning_effort": None,
                                        "max_output_tokens": 1})
    assert (cfg.max_tool_calls, cfg.budget_s, cfg.track_frames) == (1, 1.0, 1)
    assert cfg.video is False and cfg.reasoning_effort is None and cfg.max_output_tokens == 16
    assert InvestigationConfig.from_cfg(None) == InvestigationConfig()
