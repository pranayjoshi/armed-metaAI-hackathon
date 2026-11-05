"""SAM 3.1 client and scan scheduler (armed/sensors/sam.py). No network: the HTTP layer is a
fake ``post``; the key is a fake value set in the environment for the duration of a test."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import httpx
import numpy as np
import pytest

from armed.core import EventLog, now
from armed.sensors.sam import (
    _B85 as B85,
)
from armed.sensors.sam import (
    EVENT_REASONS,
    SamClient,
    ScanScheduler,
    base85_unpack,
    decode_one_bit_mask,
    parse_sam_boxes,
    parse_sam_reply,
    scene_changed,
)


def box_token(x1, y1, x2, y2, w=640, h=360, c=None):
    extra = f";c={c}" if c is not None else ""
    return f"<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={w};h={h}{extra}|>"


# ── a one_bit mask encoder, the mirror of armed.sensors.sam.decode_one_bit_mask ────────────
_TOP, _BOTTOM, _M32 = 1 << 24, 1 << 16, 0xFFFFFFFF


class RangeEncoder:
    def __init__(self):
        self.low, self.range, self.out = 0, _M32, bytearray()

    def encode(self, cum, freq, total):
        scaled = self.range // total
        self.low = (self.low + scaled * cum) & _M32
        self.range = scaled * freq
        while True:
            if (self.low ^ ((self.low + self.range) & _M32)) < _TOP:
                pass
            elif self.range < _BOTTOM:
                self.range = (-self.low) & (_BOTTOM - 1)
            else:
                break
            self.out.append((self.low >> 24) & 0xFF)
            self.low = (self.low << 8) & _M32
            self.range = (self.range << 8) & _M32

    def finish(self):
        for _ in range(4):
            self.out.append((self.low >> 24) & 0xFF)
            self.low = (self.low << 8) & _M32
        return bytes(self.out)


def base85_pack(data: bytes) -> str:
    def digits(v, n=5):
        out = ""
        for _ in range(n):
            out = B85[v % 85] + out
            v //= 85
        return out

    text = digits(len(data))
    full, rest = divmod(len(data), 4)
    for i in range(full):
        text += digits(int.from_bytes(data[4 * i : 4 * i + 4], "big"))
    if rest:
        tail = data[4 * full :] + bytes(4 - rest)
        text += digits(int.from_bytes(tail, "big"))[: rest + 1]
    return text


def encode_one_bit(raster: np.ndarray) -> str:
    """``data=`` value for a (H, W) 0/1 raster: ``H,W,!`` + base85 of the range-coded bits."""
    h, w = raster.shape
    counts = [1] * (2 * 4096)
    pad_w = w + 4
    buf = bytearray((h + 2) * pad_w)
    enc = RangeEncoder()
    for y in range(h):
        row, row1, row2 = (y + 2) * pad_w, (y + 1) * pad_w, y * pad_w
        for x in range(w):
            i = row + x + 2
            ctx = (buf[i - 1] | buf[i - 2] << 1 | buf[row1 + x] << 2 | buf[row1 + x + 1] << 3
                   | buf[row1 + x + 2] << 4 | buf[row1 + x + 3] << 5 | buf[row1 + x + 4] << 6
                   | buf[row2 + x] << 7 | buf[row2 + x + 1] << 8 | buf[row2 + x + 2] << 9
                   | buf[row2 + x + 3] << 10 | buf[row2 + x + 4] << 11)
            zero, one = counts[2 * ctx], counts[2 * ctx + 1]
            bit = 1 if raster[y, x] else 0
            enc.encode(zero if bit else 0, one if bit else zero, zero + one)
            counts[2 * ctx + bit] += 14
            if counts[2 * ctx] + counts[2 * ctx + 1] >= 4096:
                counts[2 * ctx] = (counts[2 * ctx] >> 1) or 1
                counts[2 * ctx + 1] = (counts[2 * ctx + 1] >> 1) or 1
            buf[i] = bit
    return f"{h},{w},!" + base85_pack(enc.finish())


def mask_token(h, w, fill=None, c=None):
    """A box-sized one_bit mask record: the whole box filled, or only ``fill`` = (y0, y1, x0, x1)."""
    raster = np.ones((h, w), np.uint8) if fill is None else np.zeros((h, w), np.uint8)
    if fill is not None:
        y0, y1, x0, x1 = fill
        raster[y0:y1, x0:x1] = 1
    extra = f";c={c}" if c is not None else ""
    return f"<|mask;x=0;y=0{extra};data={encode_one_bit(raster)}|>"


def reply(text: str) -> dict:
    return {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}


class FakeResp:
    def __init__(self, data, status=200):
        self.data, self.status = data, status

    def raise_for_status(self):
        if self.status >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self.data


class FakePost:
    """Replies by phrase: text, an exception to raise, or a status code. Records calls."""

    def __init__(self, by_phrase=None, delay=0.0):
        self.by_phrase, self.delay = by_phrase or {}, delay
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, url, json, timeout, headers):
        phrase = json["input"][0]["content"][0]["text"]
        with self.lock:
            self.calls.append({"url": url, "phrase": phrase, "timeout": timeout, "headers": headers, "body": json})
        if self.delay:
            time.sleep(self.delay)
        what = self.by_phrase.get(phrase, "")
        if isinstance(what, Exception):
            raise what
        if isinstance(what, int):
            return FakeResp({}, status=what)
        return FakeResp(reply(what))


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test-key-not-real")


FRAME = np.zeros((360, 640, 3), np.uint8)


# ── parsing ───────────────────────────────────────────────────────────────────
def test_every_box_is_parsed_in_order_with_confidence():
    text = "a" + box_token(10, 20, 109, 59, c=0.9) + "b" + box_token(300, 100, 399, 199, c=0.4)
    out = parse_sam_boxes(text, (640, 360))
    assert [c for c, _ in out] == [0.9, 0.4]
    assert out[0][1] == (10.0, 20.0, 110.0, 60.0)


def test_boxes_are_rescaled_and_bad_ones_dropped():
    text = box_token(5, 5, 14, 14, w=320, h=180) + box_token(0, 0, 0, 0, w=0, h=0) + box_token(700, 0, 800, 10)
    out = parse_sam_boxes(text, (640, 360))
    assert len(out) == 1 and out[0][1] == (10.0, 10.0, 30.0, 30.0)


def test_one_bit_mask_round_trips_through_the_decoder():
    rng = np.random.default_rng(3)
    blob = np.zeros((40, 57), np.uint8)
    blob[5:30, 10:45] = 1
    blob[12:20, 20:30] = 0  # a hole
    noisy = (rng.random((23, 31)) > 0.5).astype(np.uint8)
    for raster in (blob, noisy, np.zeros((3, 3), np.uint8), np.ones((1, 9), np.uint8)):
        data = encode_one_bit(raster)
        h, w, body = data.split(",", 2)
        out = decode_one_bit_mask(body[1:], int(h), int(w))
        assert out is not None and out.shape == raster.shape and np.array_equal(out, raster)


def test_base85_envelope_rejects_junk():
    assert base85_unpack("") is None and base85_unpack("!!!!!!!") is None
    assert base85_unpack("!!!!#" + "\"\"\"\"\"") is None  # characters outside the alphabet
    assert decode_one_bit_mask("!!!!!", 0, 5) is None
    assert decode_one_bit_mask("garbage", 4, 4) is None
    assert decode_one_bit_mask("!!!!!", 1000, 1000) is None  # over MASK_MAX_PIXELS: boxed only


def test_live_face_reply_fixture_decodes_to_a_face_sized_blob():
    """A reply recorded from the live API (one `face` on a 960x540 upload)."""
    text = (Path(__file__).parent / "fixtures" / "sam_face_one_bit.txt").read_text()
    [m] = parse_sam_reply(text, "face", (960, 540))
    assert m.confidence > 0.8 and 0.4 < m.bbox[0] < 0.5 and 0.6 < m.bbox[2] < 0.7
    assert m.polygon is not None and 8 <= len(m.polygon) <= 120
    xs, ys = [p[0] for p in m.polygon], [p[1] for p in m.polygon]
    assert m.bbox[0] - 0.01 <= min(xs) and max(xs) <= m.bbox[2] + 0.01
    assert m.bbox[1] - 0.01 <= min(ys) and max(ys) <= m.bbox[3] + 0.01
    from armed.sensors.sam import _mask_rasters

    [raster] = _mask_rasters(text)
    assert raster.shape == (154, 159) and 0.5 < raster.mean() < 0.9  # a face fills most of its box


def test_reply_gives_normalized_masks_with_matching_polygon():
    # box 64..319 x 36..179 inclusive -> 256 x 144 raster; the mask fills its left half
    text = box_token(64, 36, 319, 179, c=0.8) + mask_token(144, 256, fill=(0, 144, 0, 128))
    [m] = parse_sam_reply(text, "paper cup", (640, 360))
    assert m.phrase == "paper cup" and m.confidence == 0.8
    assert m.bbox == pytest.approx((0.1, 0.1, 0.5, 0.5))
    assert m.polygon is not None and 4 <= len(m.polygon) <= 8
    xs, ys = [p[0] for p in m.polygon], [p[1] for p in m.polygon]
    assert min(xs) == pytest.approx(0.1, abs=0.002) and max(xs) == pytest.approx(0.3, abs=0.002)
    assert min(ys) == pytest.approx(0.1, abs=0.002) and max(ys) == pytest.approx(0.5, abs=0.002)
    d = m.as_dict()
    assert set(d) == {"phrase", "polygon"} and len(d["polygon"][0]) == 2


def test_masks_pair_with_boxes_in_reply_order_and_unreadable_ones_are_boxes():
    text = (
        box_token(0, 0, 9, 9, c=0.9) + "<|mask;x=0;y=0;data=10,10,~notdecoded|>"
        + box_token(100, 100, 119, 109, c=0.7) + mask_token(10, 20)
    )
    a, b = parse_sam_reply(text, "phone", (640, 360))
    assert a.polygon is None and set(a.as_dict()) == {"phrase", "bbox"}
    assert b.polygon is not None and b.bbox == pytest.approx((100 / 640, 100 / 360, 120 / 640, 110 / 360))
    assert parse_sam_reply(text, "phone", (640, 360), decode_masks=False)[1].polygon is None


def test_min_confidence_drops_weak_matches():
    text = box_token(0, 0, 63, 35, c=0.1) + box_token(100, 100, 199, 199, c=0.7)
    assert len(parse_sam_reply(text, "x", (640, 360), min_confidence=0.25)) == 1


# ── client ────────────────────────────────────────────────────────────────────
def test_client_without_a_key_reports_no_key_and_invents_nothing(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    post = FakePost({"paper cup": box_token(0, 0, 63, 35)})
    scan = SamClient({}, post=post).segment_phrases(FRAME, ["paper cup", "hand"])
    assert not scan.ok and scan.error == "no key" and scan.masks == []
    assert scan.failed == ("paper cup", "hand") and post.calls == []
    assert scan.ms >= 0


def test_client_asks_each_phrase_once_and_measures_latency(key):
    post = FakePost({"paper cup": box_token(64, 36, 319, 179, c=0.9), "hand": box_token(0, 0, 31, 31)}, delay=0.02)
    client = SamClient({"sam_timeout_s": 3.0, "sam_model": "sam-3.1"}, post=post)
    scan = client.segment_phrases(FRAME, ["paper cup", "hand", "paper cup", "bad<phrase>"], frame_t=1.5, reason="test")
    assert scan.ok and scan.phrases == ("paper cup", "hand") and scan.t == 1.5 and scan.reason == "test"
    assert sorted(c["phrase"] for c in post.calls) == ["hand", "paper cup"]
    assert all(c["url"].endswith("/responses") and c["timeout"] == 3.0 for c in post.calls)
    assert all(c["headers"]["Authorization"] == "Bearer test-key-not-real" for c in post.calls)
    assert all(c["body"]["model"] == "sam-3.1" for c in post.calls)
    assert scan.ms >= 20 and scan.frame_size == (640, 360)
    assert {m.phrase for m in scan.masks} == {"paper cup", "hand"}
    assert [m for m in scan.masks if m.phrase == "paper cup"][0].bbox == pytest.approx((0.1, 0.1, 0.5, 0.5))


def test_client_downscales_wide_frames_before_upload(key):
    post = FakePost({"cup": box_token(0, 0, 95, 53, w=960, h=540)})
    scan = SamClient({"scan_width": 960}, post=post).segment_phrases(np.zeros((1080, 1920, 3), np.uint8), ["cup"])
    assert scan.frame_size == (960, 540)
    assert scan.masks[0].bbox == pytest.approx((0.0, 0.0, 0.1, 0.1))
    assert post.calls[0]["body"]["input"][0]["content"][1]["image_url"].startswith("data:image/jpeg;base64,")


def test_one_failed_phrase_is_listed_and_the_rest_still_land(key):
    post = FakePost({"cup": box_token(0, 0, 63, 35), "hand": httpx.ReadTimeout("slow")})
    scan = SamClient({}, post=post).segment_phrases(FRAME, ["cup", "hand"])
    assert scan.ok and scan.failed == ("hand",) and [m.phrase for m in scan.masks] == ["cup"]


def test_every_phrase_failing_is_a_failed_scan_with_only_the_error_type(key):
    post = FakePost({"cup": httpx.ReadTimeout("secret request echoed here"), "hand": 500})
    client = SamClient({}, post=post)
    scan = client.segment_phrases(FRAME, ["cup", "hand"])
    assert not scan.ok and scan.error == "ReadTimeout" and scan.masks == []
    assert "secret" not in str(scan.error) and client.last_error == "ReadTimeout"


def test_no_match_is_an_ok_scan_with_no_masks(key):
    scan = SamClient({}, post=FakePost({"cup": "nothing here"})).segment_phrases(FRAME, ["cup"])
    assert scan.ok and scan.masks == [] and scan.failed == ()


def test_bad_frame_is_reported_not_sent(key):
    post = FakePost({"cup": box_token(0, 0, 9, 9)})
    scan = SamClient({}, post=post).segment_phrases(np.zeros((0, 0, 3), np.uint8), ["cup"])
    assert not scan.ok and scan.error == "no frame" and post.calls == []


# ── scene change ──────────────────────────────────────────────────────────────
def test_scene_change_needs_a_real_pixel_difference():
    from armed.sensors.sam import _thumbnail

    a = _thumbnail(np.zeros((360, 640, 3), np.uint8))
    b = _thumbnail(np.zeros((360, 640, 3), np.uint8))
    moved = np.zeros((360, 640, 3), np.uint8)
    moved[100:260, 200:440] = 255
    assert scene_changed(None, a, 0.06) and not scene_changed(a, b, 0.06) and scene_changed(a, _thumbnail(moved), 0.06)
    assert not scene_changed(a, None, 0.06)


# ── scheduler ─────────────────────────────────────────────────────────────────
class Cam:
    def __init__(self, frame=None):
        self.frame = FRAME.copy() if frame is None else frame
        self.t = now()

    def latest(self):
        return self.frame, self.t


def make_scheduler(post, cam=None, cfg=None, log=None, on_scan=None):
    cam = cam or Cam()
    client = SamClient({"sam_timeout_s": 1.0}, post=post)
    cfg = {"scan_every_s": 10.0, "scan_change": 0.06, "scan_min_gap_s": 0.0, "stale_s": 15.0, **(cfg or {})}
    return ScanScheduler(client, cam.latest, lambda: ["cup", "hand"], on_scan, cfg, log), cam


def test_first_tick_scans_then_cadence_then_change_then_event(key):
    post = FakePost({"cup": box_token(0, 0, 63, 35, c=0.9)})
    seen = []
    sched, cam = make_scheduler(post, on_scan=seen.append)
    t0 = now()

    def at(dt):  # the camera's frame time moves with the clock the scheduler is given
        cam.t = t0 + dt
        return sched.step(t0 + dt)

    assert at(0.0).reason == "first scan"
    assert at(1.0) is None  # nothing changed, cadence not due
    assert at(10.5).reason == "cadence"
    cam.frame = FRAME.copy()
    cam.frame[:, :] = 200
    assert at(11.0).reason == "scene changed"
    assert at(11.5) is None
    sched.request("card accepted")
    assert at(12.0).reason == "card accepted"
    assert at(12.5) is None and len(seen) == 4 and sched.count == 4


def test_min_gap_holds_back_asked_for_scans(key):
    sched, _ = make_scheduler(FakePost(), cfg={"scan_min_gap_s": 1.0})
    t0 = now()
    first = sched.step(t0)
    assert first is not None
    sched.request("brace")
    assert sched.due(None, first.done_t + 0.5) is None
    assert sched.due(None, first.done_t + 1.5) == "brace"


def test_runtime_events_request_a_scan(key):
    log = EventLog()
    sched, _ = make_scheduler(FakePost(), log=log)
    sched.start()  # subscribes; the thread is stopped right away
    sched.stop()
    for kind, reason in EVENT_REASONS.items():
        sched._wanted = None
        log.emit(kind)
        assert sched.due(None, now()) == reason
    sched._wanted = None
    log.emit("speech.utterance")
    assert sched.due(None, now() + 100) == "first scan"  # an unrelated event asks for nothing


def test_one_scan_in_flight_at_a_time(key):
    post = FakePost({"cup": box_token(0, 0, 63, 35)}, delay=0.15)
    sched, cam = make_scheduler(post)
    results = []
    threads = [threading.Thread(target=lambda: results.append(sched.scan_now("a"))) for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len([r for r in results if r is not None]) == 1
    assert len([c for c in post.calls if c["phrase"] == "cup"]) == 1


def test_calls_ring_shows_in_flight_then_landed(key):
    post = FakePost({"cup": box_token(0, 0, 63, 35)}, delay=0.1)
    sched, cam = make_scheduler(post)
    during = {}

    def spy():
        time.sleep(0.03)
        during["calls"] = sched.calls()
        during["snapshot"] = sched.snapshot()

    th = threading.Thread(target=spy)
    th.start()
    scan = sched.scan_now("asked")
    th.join()
    [live] = during["calls"]
    assert live["model"] == "sam-3.1" and live["for"] == "scan" and live["ms"] is None
    assert live["elapsed_ms"] >= 0 and "started_at_ms" in live and during["snapshot"]["scanning"] is True
    [landed] = sched.calls()
    assert landed["ms"] == scan.ms and landed["for"] == "scan" and "landed_t" not in landed
    assert len(sched._calls) == 1


def test_snapshot_has_the_documented_scene_fields_and_goes_stale(key, monkeypatch):
    post = FakePost({"cup": box_token(64, 36, 319, 179, c=0.9), "hand": box_token(0, 0, 63, 35)})
    sched, cam = make_scheduler(post, cfg={"stale_s": 15.0})
    before = sched.snapshot()
    assert before["stale"] is True and before["scan_age_s"] is None and before["masks"] == []
    scan = sched.scan_now("asked")
    snap = sched.snapshot()
    assert {"masks", "scan_age_s", "scan_ms", "stale", "limit_s"} <= set(snap)
    assert snap["stale"] is False and snap["scan_ms"] == scan.ms and snap["limit_s"] == 15.0
    assert snap["scan_age_s"] >= 0 and snap["error"] is None
    assert sorted(m["phrase"] for m in snap["masks"]) == ["cup", "hand"]
    assert all(("bbox" in m) ^ ("polygon" in m) for m in snap["masks"])
    assert sched.stale(scan.t + 14.9) is False and sched.stale(scan.t + 15.1) is True


def test_a_failed_scan_keeps_the_last_good_masks_and_reports_the_error(key):
    post = FakePost({"cup": box_token(0, 0, 63, 35, c=0.9)})
    sched, cam = make_scheduler(post)
    good = sched.scan_now("asked")
    post.by_phrase = {"cup": httpx.ConnectError("down"), "hand": httpx.ConnectError("down")}
    bad = sched.scan_now("cadence")
    assert not bad.ok and sched.last is good
    snap = sched.snapshot()
    assert snap["error"] == "ConnectError" and [m["phrase"] for m in snap["masks"]] == ["cup"]
    assert snap["scan_ms"] == good.ms and snap["failed"] == ["cup", "hand"]


def test_a_failed_scan_backs_off_before_retrying(key):
    post = FakePost({"cup": httpx.ConnectError("down"), "hand": httpx.ConnectError("down")})
    sched, _ = make_scheduler(post, cfg={"scan_retry_s": 3.0})
    bad = sched.step(now())
    assert bad is not None and not bad.ok
    sched.request("brace")
    assert sched.due(None, bad.done_t + 2.0) is None
    assert sched.due(None, bad.done_t + 3.5) == "brace"


def test_scheduler_without_a_key_scans_nothing_and_is_stale(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    post = FakePost({"cup": box_token(0, 0, 63, 35)})
    sched, _ = make_scheduler(post)
    scan = sched.step(now())
    assert scan is not None and not scan.ok and scan.error == "no key" and post.calls == []
    snap = sched.snapshot()
    assert snap["stale"] is True and snap["masks"] == [] and snap["error"] == "no key"


def test_a_consumer_error_is_logged_and_does_not_stop_scanning(key):
    log = EventLog()

    def boom(scan):
        raise RuntimeError("consumer bug")

    sched, _ = make_scheduler(FakePost({"cup": box_token(0, 0, 63, 35)}), log=log, on_scan=boom)
    assert sched.scan_now("asked") is not None
    kinds = [e.kind for e in log.recent(10)]
    assert "vision.scan" in kinds and "vision.error" in kinds
    assert sched.scan_now("again") is not None
