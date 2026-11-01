"""Twitch detector: a short flexor-only burst (emg.twitch) and two of them within the
double window (emg.double_twitch). Driven by SyntheticSource on a simulated clock, one
50 ms hop at a time. Brace stays the veto, and the squeeze path is not touched."""

from __future__ import annotations

import pytest

import h2m.sensors.emg as emg
from h2m.core import EventLog, load_config

HOP_S = 0.05
TWITCH_KINDS = {"emg.twitch", "emg.double_twitch"}


class Rig:
    def __init__(self, monkeypatch, feature: str = "cocontraction", **overrides):
        cfg = dict(load_config()["emg"])
        cfg["source"] = "synthetic"
        cfg["brace_feature"] = feature
        cfg.update(overrides)
        self.clock = 1000.0
        monkeypatch.setattr(emg, "now", lambda: self.clock)
        self.log = EventLog()
        self.src = emg.SyntheticSource()
        self.src.flexor_ch = tuple(cfg["flexor_channels"])
        self.src.extensor_ch = tuple(cfg["extensor_channels"])
        self.svc = emg.EmgService(cfg, self.src, self.log)
        self.svc.profile = emg.Profile()  # defaults, never the user's saved profile
        self.run(1.5)  # settle the filters and fill the history with calm

    def run(self, seconds: float, flexor: float | None = None, extensor: float | None = None):
        self.src.set_drive(flexor, extensor)
        for _ in range(round(seconds / HOP_S)):
            self.svc._ingest(self.svc.filter.process(self.src.generate(10)))
            self.clock += HOP_S

    def burst(self, seconds: float = 0.15, flexor: float = 0.9, extensor: float = 0.0):
        self.run(seconds, flexor, extensor)
        self.run(HOP_S, 0.0, 0.0)

    def kinds(self) -> list[str]:
        return [e.kind for e in self.log.recent(200)]

    def events(self, kind: str):
        return [e for e in self.log.recent(200) if e.kind == kind]

    def twitches(self) -> list[str]:
        return [k for k in self.kinds() if k in TWITCH_KINDS]


@pytest.fixture
def rig(monkeypatch):
    return Rig(monkeypatch)


def test_rest_is_silent(rig):
    rig.run(20.0)
    assert rig.kinds() == []


def test_short_burst_fires_one_twitch_after_the_double_window(rig):
    rig.burst(0.15)
    rig.run(0.3, 0.0, 0.0)
    assert rig.twitches() == []  # held back: it could still become a double
    rig.run(0.6, 0.0, 0.0)
    [event] = rig.events("emg.twitch")
    assert rig.twitches() == ["emg.twitch"]
    assert 150 <= event.data["held_ms"] <= 400
    assert event.data["detect_ms"] >= rig.svc.cfg["twitch_double_ms"]
    assert "effort" not in event.data  # a twitch selects, it never sets grip


def test_double_twitch_within_the_window(rig):
    rig.burst(0.15)
    rig.run(0.25, 0.0, 0.0)  # the signal needs ~200 ms of quiet to re-arm
    rig.burst(0.15)
    rig.run(1.0, 0.0, 0.0)
    assert rig.twitches() == ["emg.double_twitch"]  # no single emitted for the first of a pair
    [event] = rig.events("emg.double_twitch")
    assert event.data["interval_ms"] <= rig.svc.cfg["twitch_double_ms"]


def test_second_twitch_too_late_is_two_singles(rig):
    rig.burst(0.15)
    rig.run(0.9, 0.0, 0.0)  # well past 600 ms after the first onset
    rig.burst(0.15)
    rig.run(1.0, 0.0, 0.0)
    assert rig.twitches() == ["emg.twitch", "emg.twitch"]


def test_burst_that_is_too_slow_is_a_squeeze_not_a_twitch(rig):
    rig.run(1.2, 0.9, 0.0)  # held: a squeeze
    rig.run(1.5, 0.0, 0.0)
    assert rig.twitches() == []
    assert len(rig.events("emg.squeeze")) == 1  # squeeze path unchanged


def test_slow_ramp_is_not_a_twitch(rig):
    for i in range(1, 21):  # 1 s rise, then let go within the max hold
        rig.run(HOP_S, flexor=0.9 * i / 20)
    rig.run(1.5, 0.0, 0.0)
    assert rig.twitches() == []


def test_burst_from_a_busy_baseline_is_not_a_twitch(rig):
    rig.run(1.0, 0.25, 0.0)  # moderate tension: above calm, below twitch_effort
    assert rig.svc.fast_spike > rig.svc.twitch_calm
    rig.burst(0.15)
    rig.run(1.5, 0.0, 0.0)
    assert rig.twitches() == []


def test_burst_too_short_is_noise(rig):
    rig.run(HOP_S, 0.9, 0.0)  # a single hop
    rig.run(1.5, 0.0, 0.0)
    assert rig.twitches() == []


@pytest.mark.parametrize("feature", ["cocontraction", "flexor_spike"])
def test_brace_is_not_a_twitch(monkeypatch, feature):
    rig = Rig(monkeypatch, feature)
    rig.run(0.2, 0.9, 0.9)  # sudden whole-forearm flinch, short enough to look like a burst
    rig.run(2.0, 0.0, 0.0)
    assert len(rig.events("emg.brace")) == 1
    assert rig.twitches() == []


def test_flexor_spike_brace_beats_a_twitch_shaped_burst(monkeypatch):
    # In flexor_spike mode a sudden flexor burst past brace_effort IS the brace.
    rig = Rig(monkeypatch, "flexor_spike")
    rig.burst(0.2, 0.9, 0.0)
    rig.run(1.5, 0.0, 0.0)
    assert len(rig.events("emg.brace")) == 1
    assert rig.twitches() == []


@pytest.mark.parametrize("feature", ["cocontraction", "flexor_spike"])
@pytest.mark.parametrize("extensor_drive", [0.3, 0.5, 0.65])
def test_slow_co_contraction_is_never_a_twitch(monkeypatch, feature, extensor_drive):
    # The case the squeeze path guards: a ~1 s ramp of both groups, then let go.
    rig = Rig(monkeypatch, feature)
    rig.svc.profile.calibrated = True
    rig.run(1.0)
    for i in range(1, 21):
        rig.run(HOP_S, flexor=i / 20, extensor=extensor_drive * i / 20)
    rig.run(0.2, 1.0, extensor_drive)
    rig.run(2.0, 0.0, 0.0)
    assert rig.twitches() == []
    assert "emg.squeeze" not in rig.kinds()


def test_moderate_co_contraction_burst_is_not_a_twitch(rig):
    # Extensors above twitch_extensor_max but under the brace level: no brace, no twitch.
    rig.burst(0.15, flexor=0.9, extensor=0.35)
    rig.run(1.5, 0.0, 0.0)
    assert rig.events("emg.brace") == []
    assert rig.twitches() == []


def test_brace_right_after_a_first_twitch_voids_it(rig):
    rig.burst(0.15)
    rig.run(0.25, 0.0, 0.0)
    rig.run(0.2, 0.9, 0.9)  # a flinch inside the double window
    rig.run(2.0, 0.0, 0.0)
    assert len(rig.events("emg.brace")) == 1
    assert rig.twitches() == []


def test_no_twitch_during_the_brace_lockout(rig):
    rig.run(0.5, 0.9, 0.9)
    rig.run(0.3, 0.0, 0.0)
    assert rig.clock < rig.svc._brace_until
    rig.burst(0.15)  # a burst inside the old lockout
    rig.run(1.0, 0.0, 0.0)
    assert rig.twitches() == []


def test_open_hand_is_not_a_twitch(rig):
    rig.burst(0.15, flexor=0.0, extensor=0.9)
    rig.run(1.0, 0.0, 0.0)
    assert rig.twitches() == []


def test_refractory_ignores_a_burst_right_after_an_emitted_twitch(rig):
    rig.burst(0.15)
    rig.run(0.6, 0.0, 0.0)  # the single is emitted about here
    assert rig.twitches() == ["emg.twitch"]
    rig.burst(0.15)  # inside twitch_refractory_ms of the emission
    rig.run(1.0, 0.0, 0.0)
    assert rig.twitches() == ["emg.twitch"]
    rig.run(1.0, 0.0, 0.0)
    rig.burst(0.15)  # after the refractory it fires again
    rig.run(1.0, 0.0, 0.0)
    assert rig.twitches() == ["emg.twitch", "emg.twitch"]


def test_third_twitch_right_after_a_double_is_ignored(rig):
    rig.burst(0.15)
    rig.run(0.25, 0.0, 0.0)
    rig.burst(0.15)
    rig.run(0.2, 0.0, 0.0)  # inside twitch_refractory_ms of the double
    rig.burst(0.15)
    rig.run(1.5, 0.0, 0.0)
    assert rig.twitches() == ["emg.double_twitch"]


def test_twitch_can_be_disabled(monkeypatch):
    rig = Rig(monkeypatch, twitch=False)
    rig.burst(0.15)
    rig.run(1.5, 0.0, 0.0)
    assert rig.twitches() == []


def test_no_twitch_while_calibrating(rig):
    rig.svc._calib = {"phase": "squeeze", "windows": []}
    rig.burst(0.15)
    rig.run(1.5, 0.0, 0.0)
    assert rig.twitches() == []


# ── calibration profiles ─────────────────────────────────────────────────────
def test_profile_overrides_apply_by_name():
    cfg = {"profile": "default", "twitch_max_ms": 400, "profiles": {"jaw": {"twitch_max_ms": 250}}}
    assert emg.resolve_profile_cfg(cfg)["twitch_max_ms"] == 400
    jaw = emg.resolve_profile_cfg(cfg, "jaw")
    assert jaw["twitch_max_ms"] == 250 and jaw["profile"] == "jaw"
    assert emg.resolve_profile_cfg({**cfg, "profile": "jaw"})["twitch_max_ms"] == 250


def test_profile_cannot_override_its_own_selector():
    cfg = {"profile": "default", "profiles": {"jaw": {"profile": "default", "profiles": {}}}}
    merged = emg.resolve_profile_cfg(cfg, "jaw")
    assert merged["profile"] == "jaw" and "jaw" in merged["profiles"]


@pytest.mark.parametrize("name", ["../x", "a/b", "", "x" * 40, "j aw"])
def test_profile_name_is_validated(name):
    with pytest.raises(ValueError):
        emg.resolve_profile_cfg({"profile": "default"}, name)


def test_service_selects_profile_by_name_and_applies_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(emg, "PROFILE_DIR", tmp_path)
    emg.Profile(name="jaw", rest=[1.0] * 4, calibrated=True).save()
    cfg = dict(load_config()["emg"])
    cfg.update(source="synthetic", profiles={"jaw": {"twitch_max_ms": 250}})
    svc = emg.build_emg(cfg, EventLog(), profile="jaw")
    assert svc is not None
    assert svc.profile.name == "jaw" and svc.profile.calibrated
    assert svc.twitch_max_ms == 250
    forearm = emg.build_emg({**cfg, "profile": "default"}, EventLog())
    assert forearm is not None and forearm.twitch_max_ms == 400
