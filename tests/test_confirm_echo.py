"""Confirmations must come from the person, now (virtual arm, mocked Muse and speech).

H1: the robot's own voice through the mic never confirms; spoken and typed confirms
pass the reaction-time gate. M7: a cancel, stop, brace or open hand drops any Muse
reply or confirmed plan that began before it."""

from __future__ import annotations

import numpy as np
import pytest

import h2m.app as app_mod
from h2m.compiler.llm import ToolReply
from h2m.core import now
from h2m.robot import ArmState
from h2m.sensors.speech import SpeechService
from h2m.speech_out import is_echo_of
from tests import test_app_embodied as base
from tests.test_app_embodied import FakeLLM, prove_empty, started

h2m = base.h2m  # the shared app fixture

SAID = "Okay, squeeze or say yes to confirm."
LATE = 5.0  # seconds: well past the reaction-time gate


def ask_cup(h, said=None):
    calls = [("pick_place", {"object_id": "cup"})]
    if said:
        calls.append(("say", {"text": said}))
    h.run_tool_calls(calls)
    assert h.pending is not None and not started(h)
    return h.pending


def heard(h, text, *, echo=False, asr_ms=250, audio_s=0.4):
    h.on_utterance(text, {"asr_ms": asr_ms, "audio_s": audio_s, "echo": echo}, "voice")


# ── H1: echo ─────────────────────────────────────────────────────────────────
def test_robot_voice_heard_back_never_confirms(h2m):
    """Review repro: the TTS line comes back through the mic."""
    p = ask_cup(h2m, said=SAID)
    assert h2m.spoken == [SAID]
    p.announced_at = now() - LATE  # even past the reaction gate
    heard(h2m, SAID, audio_s=1.5)  # the mic tagged nothing (e.g. heard after the tail)
    assert not started(h2m) and h2m.pending is p
    assert [e for e in h2m.log.recent(50, {"speech.echo_dropped"})]
    heard(h2m, "say yes to confirm")  # a run of the robot's words
    assert not started(h2m)


def test_anything_heard_while_the_robot_talks_can_only_stop(h2m):
    p = ask_cup(h2m, said=SAID)
    p.announced_at = now() - LATE
    heard(h2m, "yes", echo=True)
    heard(h2m, "pick up the block", echo=True)
    assert not started(h2m) and h2m.pending is p
    heard(h2m, "stop", echo=True)  # a spoken stop over the robot's voice still freezes
    assert h2m.arm.state is ArmState.STOPPED and h2m.pending is None


def test_cancel_over_the_robot_voice_still_cancels(h2m):
    ask_cup(h2m, said=SAID)
    heard(h2m, "never mind", echo=True)
    assert h2m.pending is None


def test_a_real_yes_after_the_robot_spoke_confirms(h2m):
    p = ask_cup(h2m, said=SAID)
    p.announced_at = now() - LATE
    heard(h2m, "yes")  # short: not an echo by text, not in the echo window
    assert started(h2m) and started(h2m)[-1]["confirmed_by"] == "voice"


def test_echo_text_match_needs_a_real_run_of_words():
    assert is_echo_of(SAID, SAID) and is_echo_of("okay squeeze or say", SAID)
    assert not is_echo_of("yes", SAID) and not is_echo_of("yes please", SAID)
    assert not is_echo_of("put the cup down", SAID) and not is_echo_of("", SAID)


# ── H1: reaction-time gate on voice and typed confirms ───────────────────────
def test_voice_confirm_that_began_before_the_question_is_rejected(h2m):
    """Review repro: "okay" whose audio began before the request existed."""
    p = ask_cup(h2m)
    heard(h2m, "okay", asr_ms=300, audio_s=1.7)
    assert not started(h2m) and h2m.pending is p
    assert [e for e in h2m.log.recent(50, {"speech.confirm_rejected"})]
    p.announced_at = now() - LATE
    heard(h2m, "okay", asr_ms=300, audio_s=1.7)
    assert started(h2m)[-1]["confirmed_by"] == "voice"


def test_typed_confirm_passes_the_same_gate(h2m):
    p = ask_cup(h2m)
    h2m.on_utterance("yes", source="typed")
    assert not started(h2m) and h2m.pending is p
    p.announced_at = now() - app_mod.MIN_CONFIRM_REACTION_S - 0.05
    h2m.on_utterance("yes", source="typed")
    assert started(h2m)[-1]["confirmed_by"] == "typed"


def test_spoken_confirm_pins_the_request_it_answered(h2m, monkeypatch):
    p = ask_cup(h2m)
    p.announced_at = now() - LATE
    seen = []
    monkeypatch.setattr(h2m, "confirm", lambda **kw: seen.append(kw) or (False, ""))
    heard(h2m, "yes")
    assert seen and seen[0]["expected"] is p


# ── H1: the mic's echo window ────────────────────────────────────────────────
def speech_service(speaking):
    cfg = {"silence_end_ms": 90, "preroll_ms": 60, "max_utterance_ms": 3000, "echo_tail_ms": 500}
    return SpeechService(cfg, log=None, on_utterance=None, is_speaking=lambda: speaking["now"])


LOUD = np.full(480, 0.2, dtype=np.float32)  # 30 ms frames at 16 kHz
QUIET = np.zeros(480, dtype=np.float32)


def feed(svc, frames, t0, speaking, talking_until):
    """Run frames through capture-time tagging and the VAD, 30 ms apart."""
    out = []
    for i, frame in enumerate(frames):
        t = t0 + i * 0.03
        speaking["now"] = t < talking_until
        audio, echo = svc.push_frame(frame, svc.in_echo_window(t))
        if audio is not None:
            out.append((len(audio), echo))
    return out


def test_speech_while_the_robot_talks_is_tagged_echo():
    speaking = {"now": False}
    svc = speech_service(speaking)
    got = feed(svc, [LOUD] * 10 + [QUIET] * 5, 100.0, speaking, talking_until=101.0)
    assert got and all(echo for _, echo in got)


def test_echo_window_has_a_tail_and_never_discards_speech_at_its_edge():
    speaking = {"now": False}
    svc = speech_service(speaking)
    assert svc.in_echo_window(0.0) is False
    speaking["now"] = True
    assert svc.in_echo_window(1.0)
    speaking["now"] = False
    assert svc.in_echo_window(1.4) and not svc.in_echo_window(1.6)
    # F1: speech that starts inside the window and runs past its end is kept whole and
    # finishes naturally, tagged echo (so it can only stop or cancel, never confirm).
    svc = speech_service(speaking)
    frames = [LOUD] * 40 + [QUIET] * 5  # 1.2 s of speech; robot talks for the first 0.3 s
    got = feed(svc, frames, 10.0, speaking, talking_until=10.3)
    # one utterance: the loud run (less the first onset frame, beyond the 60 ms
    # pre-roll) plus the 3 quiet frames that ended it, past the window's end at 0.8 s
    assert got == [((39 + 3) * 480, True)]


def test_quiet_echo_preroll_is_dropped_so_the_next_words_start_clean():
    speaking = {"now": False}
    svc = speech_service(speaking)
    # robot talks 0.3 s, then the person says something 0.6 s later (after the tail)
    frames = [LOUD] * 10 + [QUIET] * 26 + [LOUD] * 8 + [QUIET] * 5
    got = feed(svc, frames, 10.0, speaking, talking_until=10.3)
    assert [echo for _, echo in got] == [True, False]


# ── F1: a spoken "stop" around the end of the robot's voice reaches the app ─────
FR = 480  # 30 ms at 16 kHz
ROBOT = np.full(FR, 0.2, dtype=np.float32)
PERSON = np.full(FR, 0.25, dtype=np.float32) * np.sign(np.sin(np.arange(FR) + 0.5))


def _person_frames(audio):
    return int(np.sum(np.isclose(np.abs(audio.reshape(-1, FR)).max(axis=1), 0.25)))


def speak_over_robot(on_utterance, person_word, robot_until, person_from, person_to, total=4.0):
    """Re-review repro, through the real VAD + echo tagging + utterance
    callback on the shipped speech config. The fake ASR hears ``person_word`` in any
    utterance holding the person's audio (after the robot's own words)."""
    from h2m.core import load_config

    speaking = {"now": False}
    svc = SpeechService(
        load_config()["speech"], log=None, on_utterance=on_utterance,
        is_speaking=lambda: speaking["now"],
    )
    svc.backend = "fake"

    def asr(audio, rate):
        said = "okay squeeze or say yes to confirm"
        return f"{said} {person_word}" if _person_frames(audio) else said

    svc.transcriber = asr
    heard_frames = []
    real_push = svc.push_frame

    def push(frame, echo):
        audio, tagged = real_push(frame, echo)
        if audio is not None:
            heard_frames.append(_person_frames(audio))
        return audio, tagged

    svc.push_frame = push
    for i in range(int(total / 0.03)):
        t = i * 0.03
        speaking["now"] = t < robot_until
        frame = PERSON if person_from <= t < person_to else ROBOT if t < robot_until else QUIET
        svc.handle_frame(frame, svc.in_echo_window(t))
    return sum(heard_frames)


STOP_TIMINGS = {  # robot speaks 0..2.0 s; the echo window closes at 2.5 s
    "inside robot speech": (1.0, 1.3),
    "overlapping the end of robot speech": (1.9, 2.2),
    "right after the robot ends": (2.05, 2.35),
    "late in the tail": (2.35, 2.65),
    "just after the tail": (2.55, 2.85),
}


@pytest.mark.parametrize("when", sorted(STOP_TIMINGS))
def test_stop_around_the_end_of_robot_speech_reaches_the_app_as_stop(h2m, when):
    a, b = STOP_TIMINGS[when]
    heard = []

    def on_utterance(text, meta):
        heard.append(meta["echo"])
        h2m.on_utterance(text, meta, "voice")

    ask_cup(h2m)
    person = speak_over_robot(on_utterance, "stop", 2.0, a, b)
    assert person == round((b - a) / 0.03)  # every frame of the person's word delivered
    assert h2m.arm.state is ArmState.STOPPED and h2m.pending is None
    assert any(e.data["verb"] == "stop" for e in h2m.log.recent(50, {"speech.utterance"}))


@pytest.mark.parametrize("when", ["overlapping the end of robot speech", "late in the tail"])
def test_yes_glued_to_the_robot_voice_is_echo_and_never_confirms(h2m, when):
    a, b = STOP_TIMINGS[when]
    p = ask_cup(h2m)
    p.announced_at = now() - LATE
    speak_over_robot(lambda text, meta: h2m.on_utterance(text, meta, "voice"), "yes", 2.0, a, b)
    assert not started(h2m) and h2m.pending is p
    assert [e for e in h2m.log.recent(50, {"speech.echo_dropped"})]


def test_a_failing_speaking_check_counts_as_speaking():
    def boom():
        raise RuntimeError("audio device gone")

    svc = SpeechService({}, log=None, on_utterance=None, is_speaking=boom)
    assert svc.in_echo_window(0.0)


# ── M7: request epoch ────────────────────────────────────────────────────────
def test_never_mind_while_muse_answers_drops_the_reply(h2m):
    """Review repro."""
    prove_empty(h2m)
    h2m.llm = FakeLLM(
        ToolReply([("gesture", {"name": "nod"})]),
        hook=lambda: h2m.on_utterance("never mind", {}, "typed"),
    )
    h2m.on_utterance("could you nod at me", source="typed")
    assert not started(h2m) and h2m.arm.state is ArmState.READY
    assert [e for e in h2m.log.recent(50, {"compiler.dropped"})]


def test_stop_reset_and_rearm_while_muse_answers_drops_the_old_reply(h2m):
    """Review repro: re-arming must not resurrect a pre-stop request."""
    prove_empty(h2m)

    def stop_and_rearm():
        h2m.stop("voice: stop")
        h2m.arm.reset()
        h2m.arm.arm()

    h2m.llm = FakeLLM(ToolReply([("gesture", {"name": "shake"})]), hook=stop_and_rearm)
    h2m.on_utterance("shake your head", source="typed")
    assert not started(h2m) and h2m.arm.state is ArmState.READY


@pytest.mark.parametrize("bump", ["brace", "open_hand"])
def test_brace_or_open_hand_while_muse_answers_queues_nothing(h2m, bump):
    def hook():
        if bump == "brace":
            h2m.log.emit("emg.brace", onset_t=0.0)
        else:
            h2m.log.emit("emg.open_hand", extensor=0.9)
        h2m.arm.reset()
        h2m.arm.arm()

    h2m.llm = FakeLLM(ToolReply([("pick_place", {"object_id": "cup"})]), hook=hook)
    h2m.on_utterance("could you tidy the cup away", source="typed")
    assert h2m.pending is None and not started(h2m)


def test_cancel_after_the_confirm_pop_drops_the_plan(h2m):
    ask_cup(h2m)
    real = h2m._publish

    def publish_then_cancel(c, d, **kw):
        if kw.get("confirmed_by"):
            h2m.cancel("never mind")  # lands after the pop, before the arm starts
        return real(c, d, **kw)

    h2m._publish = publish_then_cancel
    ok, why = h2m.confirm(by="ui")
    assert not ok and "cancelled" in why
    assert not started(h2m) and h2m.arm.state is ArmState.READY

# imports added with the tests below
