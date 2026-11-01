"""Brace (flinch) detector: co-contraction with a fast onset, and its priority over
open hand and squeeze. Driven by SyntheticSource on a simulated clock, so every
test is deterministic and takes milliseconds."""

from __future__ import annotations

import threading
import time

import pytest

import h2m.sensors.emg as emg
from h2m.core import EventLog, load_config

HOP_S = 0.05


class Rig:
    """An EmgService fed synchronously, one 50 ms hop at a time, on a fake clock."""

    def __init__(self, monkeypatch):
        cfg = dict(load_config()["emg"])
        cfg["source"] = "synthetic"
        cfg["brace_feature"] = "cocontraction"  # flexor_spike has its own test below
        self.clock = 1000.0
        monkeypatch.setattr(emg, "now", lambda: self.clock)
        self.log = EventLog()
        self.src = emg.SyntheticSource()
        self.src.flexor_ch = tuple(cfg["flexor_channels"])  # same wiring as build_emg
        self.src.extensor_ch = tuple(cfg["extensor_channels"])
        self.svc = emg.EmgService(cfg, self.src, self.log)
        self.svc.profile = emg.Profile()  # defaults, never the user's saved profile
        self.run(1.5)  # settle the filters and fill the history with calm

    def run(self, seconds: float, flexor: float | None = None, extensor: float | None = None):
        self.src.set_drive(flexor, extensor)
        for _ in range(round(seconds / HOP_S)):
            self.svc._ingest(self.svc.filter.process(self.src.generate(10)))
            self.clock += HOP_S

    def ramp(self, seconds: float, to: float):
        """Raise both groups linearly from rest to ``to``."""
        steps = round(seconds / HOP_S)
        for i in range(1, steps + 1):
            self.run(HOP_S, flexor=to * i / steps, extensor=to * i / steps)

    def kinds(self) -> list[str]:
        return [e.kind for e in self.log.recent(200)]

    def events(self, kind: str):
        return [e for e in self.log.recent(200) if e.kind == kind]


@pytest.fixture
def rig(monkeypatch):
    return Rig(monkeypatch)


def test_rest_is_silent(rig):
    rig.run(20.0)
    assert rig.kinds() == []
    assert rig.svc.snapshot()["brace"] < 0.05


def test_sudden_co_contraction_fires_brace(rig):
    rig.run(0.5, 0.0, 0.0)
    rig.src.set_drive(0.9, 0.9)
    rig.run(1.0)
    [event] = rig.events("emg.brace")
    data = event.data
    assert data["peak_flexor"] > 0.5 and data["peak_extensor"] > 0.5
    assert data["peak_brace"] >= rig.svc.brace_thr
    assert data["onset_t"] <= data["cross_t"] <= event.t
    assert data["detect_ms"] <= 250  # software latency from the calm hop to the event
    assert len(data["trace"]) >= 10 and len(data["trace"][0]) == 4  # t, flexor, extensor, brace


def test_brace_fires_once_per_brace(rig):
    rig.run(3.0, 0.9, 0.9)
    assert len(rig.events("emg.brace")) == 1
    rig.run(1.0, 0.0, 0.0)  # relax: calm again
    rig.run(1.0, 0.9, 0.9)  # a second flinch
    assert len(rig.events("emg.brace")) == 2


def test_flexor_only_squeeze_is_not_a_brace(rig):
    rig.run(1.0, 0.9, 0.0)
    assert rig.events("emg.brace") == []
    assert len(rig.events("emg.squeeze")) == 1


def test_extensor_only_open_hand_is_not_a_brace(rig):
    rig.run(1.0, 0.0, 0.9)
    assert rig.events("emg.brace") == []
    assert len(rig.events("emg.open_hand")) == 1


@pytest.mark.parametrize("seconds", [1.0, 2.0, 4.0])
def test_slow_ramp_to_co_contraction_is_not_a_brace(rig, seconds):
    rig.ramp(seconds, 0.9)
    rig.run(2.0, 0.9, 0.9)  # and holding it there does not make it a brace either
    assert rig.events("emg.brace") == []


def test_already_busy_baseline_is_not_a_brace(rig):
    rig.run(1.0, 0.25, 0.25)  # moderate tension, above the calm level but below threshold
    assert rig.svc.brace > rig.svc.brace_calm
    rig.run(1.0, 0.9, 0.9)
    assert rig.events("emg.brace") == []


def test_brace_beats_squeeze_but_never_suppresses_open_hand(rig):
    rig.run(4.0, 0.95, 0.95)
    assert rig.kinds().count("emg.brace") == 1
    assert "emg.squeeze" not in rig.kinds()
    # the stop gesture is never swallowed, even while the brace is still held
    assert len(rig.events("emg.open_hand")) == 1


def test_open_hand_after_brace_fires_through_what_was_the_lockout(rig):
    rig.run(0.5, 0.0, 0.0)
    rig.run(0.5, 0.9, 0.9)
    assert len(rig.events("emg.brace")) == 1
    # Relax, then open the hand inside the old 1.5 s lockout: it must fire.
    rig.run(0.3, 0.0, 0.0)
    assert rig.clock < rig.svc._brace_until
    before = len(rig.events("emg.open_hand"))
    rig.run(0.5, 0.0, 0.9)
    assert len(rig.events("emg.open_hand")) == before + 1
    # And an open hand held straight through the lockout fires too, exactly once.
    rig.run(3.0, 0.0, 0.0)
    rig.run(0.5, 0.9, 0.9)
    rig.run(0.2, 0.0, 0.0)
    n = len(rig.events("emg.open_hand"))
    rig.run(4.0, 0.0, 0.9)
    assert len(rig.events("emg.open_hand")) == n + 1


def test_squeeze_needs_quiet_extensors(rig):
    # Moderate co-contraction: flexors past the confirm level, extensors above the
    # co-contraction level but nowhere near the open-hand level. Never a squeeze.
    cocontraction = rig.svc.cfg["cocontraction_effort"]
    assert cocontraction < rig.svc.cfg["stop_effort"]
    rig.ramp(3.0, 0.5)
    rig.run(2.0, 0.5, 0.5)
    assert rig.svc.extensor > cocontraction
    assert "emg.squeeze" not in rig.kinds()


def test_hard_fist_with_extensor_crosstalk_still_squeezes(rig):
    # Extensors above the absolute co-contraction level but well under the flexors.
    rig.svc.profile.calibrated = True
    rig.run(1.0, 0.9, 0.25)
    assert rig.svc.extensor > rig.svc.cfg["cocontraction_effort"]
    assert "emg.brace" not in rig.kinds()
    assert "emg.squeeze" in rig.kinds()


def _spike_rig(rig):
    rig.svc.brace_feature = "flexor_spike"
    rig.run(1.0)  # settle the brace history on the new feature
    return rig


def test_flexor_spike_fires_on_a_sudden_squeeze(rig):
    _spike_rig(rig).run(0.5, 0.9, 0.0)
    assert len(rig.events("emg.brace")) == 1


def test_flexor_spike_ignores_a_slow_squeeze_which_can_still_confirm(rig):
    _spike_rig(rig)
    steps = 40
    for i in range(1, steps + 1):
        rig.run(0.1, 0.9 * i / steps, 0.0)
    rig.run(1.0, 0.9, 0.0)
    assert not rig.events("emg.brace")
    assert "emg.squeeze" in rig.kinds()


def test_flexor_spike_never_confirms(rig):
    _spike_rig(rig).run(1.5, 0.9, 0.0)
    assert rig.events("emg.brace")
    assert "emg.squeeze" not in rig.kinds()


def test_flexor_spike_mode_keeps_cocontraction_from_a_busy_flexor(rig):
    _spike_rig(rig).run(1.0, 0.3, 0.0)  # flexor already busy: no spike possible
    rig.run(0.5, 0.7, 0.45)
    assert [e.data["trigger"] for e in rig.events("emg.brace")] == ["cocontraction"]
    assert "emg.squeeze" not in rig.kinds()


def test_flinch_from_a_light_grip_does_not_confirm(rig):
    _spike_rig(rig).svc.profile.calibrated = True
    rig.run(1.0, 0.25, 0.0)
    rig.log._tail.clear()
    rig.run(1.0, 0.95, 0.4)
    assert rig.events("emg.brace")
    assert "emg.squeeze" not in rig.kinds()


def test_one_reaction_fires_one_brace(rig):
    _spike_rig(rig).run(0.5, 0.9, 0.9)
    rig.run(0.5, 0.0, 0.0)
    assert len(rig.events("emg.brace")) == 1


def test_flexor_spike_ignores_an_open_hand(rig):
    _spike_rig(rig).run(0.5, 0.0, 0.9)
    assert not rig.events("emg.brace")
    assert "emg.open_hand" in rig.kinds()


def test_uncalibrated_profile_gets_no_crosstalk_allowance(rig):
    rig.run(1.0, 0.9, 0.25)
    assert rig.svc.extensor > rig.svc.cfg["cocontraction_effort"]
    assert "emg.squeeze" not in rig.kinds()


def test_extensor_flicker_does_not_rearm_a_held_squeeze(rig):
    rig.svc.profile.calibrated = True
    rig.run(1.0, 0.9, 0.1)
    assert rig.kinds().count("emg.squeeze") == 1
    rig.run(0.5, 0.9, 0.9)  # extensors jump while the flexors never release
    rig.run(1.0, 0.9, 0.1)
    assert rig.kinds().count("emg.squeeze") == 1


def test_squeeze_after_brace_waits_for_release(rig):
    rig.run(0.5, 0.0, 0.0)
    rig.run(0.5, 0.9, 0.9)
    assert len(rig.events("emg.brace")) == 1
    # Extensors let go but the flexors stay tense, well past the lockout: still no confirm.
    rig.run(4.0, 0.9, 0.0)
    assert rig.events("emg.squeeze") == []
    # A deliberate squeeze after relaxing confirms normally.
    rig.run(0.5, 0.0, 0.0)
    rig.run(1.0, 0.9, 0.0)
    assert len(rig.events("emg.squeeze")) == 1


def test_snapshot_exposes_brace_for_the_meter(rig):
    rig.run(1.0, 0.9, 0.9)
    snap = rig.svc.snapshot()
    assert snap["brace"] > 0.6
    assert snap["brace_effort"] == rig.svc.brace_thr
    assert snap["brace_calibrated"] is False
    assert snap["brace_trace"] and snap["brace_trace"][-1][1] == snap["brace"]


def test_brace_calibration_phase(rig, monkeypatch, tmp_path):
    monkeypatch.setattr(emg, "PROFILE_DIR", tmp_path)
    svc = rig.svc

    def record(phase: str, flexor: float, extensor: float):
        rig.src.set_drive(flexor, extensor)
        stop = threading.Event()

        def feed():
            while not stop.is_set():
                svc._ingest(svc.filter.process(rig.src.generate(10)))
                time.sleep(0.002)

        thread = threading.Thread(target=feed)
        thread.start()
        try:
            return svc.calibrate(phase, seconds=0.4)
        finally:
            stop.set()
            thread.join()

    assert record("rest", 0.0, 0.0)["ok"]
    assert record("squeeze", 1.0, 0.0)["ok"]
    assert record("open", 0.0, 1.0)["ok"]
    assert record("brace", 0.7, 0.7)["ok"]
    result = svc.calibrate("finish")
    assert result["ok"] and result["profile"]["brace_max"] is not None
    assert svc.profile.calibrated and (tmp_path / "default.json").exists()
    assert emg.Profile.load("default").brace_max == pytest.approx(svc.profile.brace_max)

    rig.run(1.0, 0.0, 0.0)
    rig.log._tail.clear()
    rig.run(1.0, 0.7, 0.7)
    assert len(rig.events("emg.brace")) == 1
    assert rig.svc.snapshot()["brace_calibrated"] is True


def _recorded(rig, **phases):
    rig.svc._pending = {k: [*v, 2.0, 2.0] for k, v in phases.items()}
    return rig.svc


def test_good_calibration_steps_have_no_problems(rig):
    svc = _recorded(
        rig, rest=[5.0, 6.0], squeeze=[180.0, 70.0], open=[50.0, 110.0], brace=[150.0, 90.0]
    )
    for phase in ("rest", "squeeze", "open", "brace"):
        assert svc.phase_problems(phase) == []


def test_tense_rest_is_flagged(rig):
    assert _recorded(rig, rest=[60.0, 6.0]).phase_problems("rest")


def test_swapped_wires_are_flagged(rig):
    svc = _recorded(rig, rest=[5.0, 5.0], squeeze=[60.0, 180.0], open=[110.0, 50.0])
    assert "swapped" in svc.phase_problems("squeeze")[0]
    assert "swapped" in svc.phase_problems("open")[0]


def test_weak_brace_is_flagged(rig):
    svc = _recorded(
        rig, rest=[3.0, 3.0], squeeze=[177.0, 71.0], open=[55.0, 111.0], brace=[48.0, 47.0]
    )
    assert any("under half" in p for p in svc.phase_problems("brace"))


def test_weak_brace_cannot_shrink_the_scale_below_a_fist(rig, monkeypatch, tmp_path):
    monkeypatch.setattr(emg, "PROFILE_DIR", tmp_path)
    svc = _recorded(
        rig, rest=[3.0, 3.0], squeeze=[177.0, 71.0], open=[55.0, 111.0], brace=[48.0, 47.0]
    )
    svc.flexor_ch, svc.extensor_ch = [0], [1]
    result = svc.calibrate("finish")
    assert result["ok"]
    assert result["profile"]["brace_max"][:2] == [177.0, 111.0]


def test_calibration_without_brace_still_finishes(rig, monkeypatch, tmp_path):
    monkeypatch.setattr(emg, "PROFILE_DIR", tmp_path)
    rig.svc._pending = {
        "rest": [4.0] * 4,
        "squeeze": [60.0] * 4,
        "open": [60.0] * 4,
    }
    result = rig.svc.calibrate("finish")
    assert result["ok"] and result["profile"]["brace_max"] is None
    with pytest.raises(ValueError):
        rig.svc.calibrate("flinch")


def test_service_thread_with_synthetic_source():
    """End to end on the real clock: the service thread reads SyntheticSource."""
    cfg = dict(load_config()["emg"])
    cfg["source"] = "synthetic"
    cfg["brace_feature"] = "cocontraction"
    log = EventLog()
    src = emg.SyntheticSource()
    src.flexor_ch = tuple(cfg["flexor_channels"])
    src.extensor_ch = tuple(cfg["extensor_channels"])
    svc = emg.EmgService(cfg, src, log)
    svc.profile = emg.Profile()
    svc.start()
    try:
        time.sleep(1.5)  # calm baseline
        src.set_drive(flexor=0.9)  # flexor-only: squeeze, not brace
        time.sleep(0.8)
        src.set_drive(flexor=0.0, extensor=0.0)
        time.sleep(1.0)
        assert not log.recent(50, {"emg.brace"})
        src.set_drive(flexor=0.9, extensor=0.9)  # sudden co-contraction
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not log.recent(50, {"emg.brace"}):
            time.sleep(0.05)
        assert len(log.recent(50, {"emg.brace"})) == 1
    finally:
        svc.stop()


@pytest.mark.parametrize("feature", ["cocontraction", "flexor_spike"])
@pytest.mark.parametrize("extensor_drive", [0.5, 0.55, 0.65])
def test_slow_whole_forearm_co_contraction_never_confirms(rig, feature, extensor_drive):
    # A ~1 s ramp is too slow to be a brace, but with the extensors past brace_effort it is
    # still a co-contraction, and the hard-fist ratio allowance must not turn it into a squeeze.
    rig.svc.brace_feature = feature
    rig.svc.profile.calibrated = True
    rig.run(1.0)
    for i in range(1, 21):
        rig.run(HOP_S, flexor=i / 20, extensor=extensor_drive * i / 20)
    rig.run(1.0)
    assert min(rig.svc.fast_flexor, rig.svc.fast_extensor) > rig.svc.brace_thr
    assert "emg.brace" not in rig.kinds()
    assert "emg.squeeze" not in rig.kinds()
