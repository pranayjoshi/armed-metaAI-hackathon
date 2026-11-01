"""Spoken output via macOS ``say``. ``cutoff()`` kills it at once (stop, brace)."""

from __future__ import annotations

import re
import subprocess
import threading
from collections.abc import Callable
from typing import Any

MAX_CHARS = 400
ECHO_MIN_WORDS = 3  # a shorter match ("yes") is left to the echo window and reaction gate


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text).lower())


def is_echo_of(heard: str, said: str) -> bool:
    """``heard`` is the robot's own ``said`` coming back through the mic: the same words,
    or a run of at least ECHO_MIN_WORDS consecutive words from it."""
    h, s = words(heard), words(said)
    if not h or not s:
        return False
    if h == s:
        return True
    if len(h) < ECHO_MIN_WORDS:
        return False
    return any(s[i : i + len(h)] == h for i in range(len(s) - len(h) + 1))
Spawn = Callable[[list[str]], Any]  # returns a Popen-like with poll()/kill()


def _spawn(argv: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


class SpeechOut:
    def __init__(self, voice: str | None = None, rate_wpm: int | None = None,
                 spawn: Spawn = _spawn, enabled: bool = True):
        self.voice = voice
        self.rate_wpm = rate_wpm
        self.enabled = enabled
        self._spawn = spawn
        self._proc: Any = None
        self._lock = threading.Lock()
        self.last_text = ""  # the last thing said (echo check)

    def speak(self, text: str) -> bool:
        """Start speaking (non-blocking), replacing anything still playing.
        Returns False when disabled, empty, or ``say`` is unavailable."""
        text = " ".join(str(text).split())[:MAX_CHARS]
        if not self.enabled or not text:
            return False
        argv = ["say"]
        if self.voice:
            argv += ["-v", self.voice]
        if self.rate_wpm:
            argv += ["-r", str(int(self.rate_wpm))]
        argv += ["--", text]  # "--" so text can never be read as a flag
        with self._lock:
            self._kill_locked()
            try:
                self._proc = self._spawn(argv)
            except OSError:
                self._proc = None
                return False
            self.last_text = text
        return True

    def is_echo(self, heard: str) -> bool:
        """``heard`` matches the last thing this voice said."""
        with self._lock:
            said = self.last_text
        return is_echo_of(heard, said)

    def speaking(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def cutoff(self) -> None:
        """Silence now. Never raises; safe to call from the stop path."""
        with self._lock:
            self._kill_locked()

    def _kill_locked(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:  # noqa: BLE001  # cutoff must never fail
            pass
