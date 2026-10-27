"""Session recording: every event to JSONL, plus labeled trials for benchmarking."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from h2m.core import ROOT, Event, EventLog, now

SESSIONS = ROOT / "data" / "sessions"
SKIP = {"emg.trace"}


class Recorder:
    def __init__(self, log: EventLog):
        self.log = log
        self._lock = threading.Lock()
        self.session_id: str | None = None
        self.dir: Path | None = None
        self._events = None
        self.trial: dict[str, Any] | None = None
        self.trials: list[dict[str, Any]] = []
        self.started_t: float | None = None
        log.subscribe(self._on_event)

    def start(self, participant: str, notes: str = "") -> str:
        with self._lock:
            if self.session_id:
                return self.session_id
            self.session_id = time.strftime("%Y%m%d-%H%M%S") + f"-{participant or 'anon'}"
            self.dir = SESSIONS / self.session_id
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "meta.json").write_text(
                json.dumps(
                    {
                        "session_id": self.session_id,
                        "participant": participant,
                        "notes": notes,
                        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                    },
                    indent=2,
                )
            )
            self._events = (self.dir / "events.jsonl").open("a", encoding="utf-8")
            self.trials, self.trial, self.started_t = [], None, now()
        self.log.emit("session.started", session_id=self.session_id, participant=participant)
        return self.session_id

    def stop(self) -> None:
        if not self.session_id:
            return
        if self.trial:
            self.end_trial("abandoned")
        self.log.emit("session.stopped", session_id=self.session_id, trials=len(self.trials))
        with self._lock:
            if self._events:
                self._events.close()
            self._events, self.session_id, self.dir = None, None, None

    def start_trial(
        self,
        expected_action: str,
        expected_object: str | None,
        condition: str = "",
        instruction: str = "",
    ) -> dict[str, Any]:
        if not self.session_id:
            raise RuntimeError("start a session first")
        if self.trial:
            self.end_trial("abandoned")
        self.trial = {
            "trial": len(self.trials) + 1,
            "expected_action": expected_action,
            "expected_object": expected_object,
            "condition": condition,
            "instruction": instruction,
            "t_start": now(),
        }
        self.log.emit("trial.started", **{k: v for k, v in self.trial.items() if k != "t_start"})
        return self.trial

    def end_trial(self, outcome: str, notes: str = "") -> dict[str, Any] | None:
        """outcome: success | wrong_action | wrong_object | dropped | crushed | no_action | abandoned"""
        if not self.trial:
            return None
        trial = {**self.trial, "outcome": outcome, "notes": notes, "t_end": now()}
        self.trials.append(trial)
        self.trial = None
        if self.dir:
            with (self.dir / "trials.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(trial) + "\n")
        self.log.emit("trial.ended", trial=trial["trial"], outcome=outcome)
        return trial

    def _on_event(self, event: Event) -> None:
        if event.kind in SKIP:
            return
        with self._lock:
            if self._events is not None:
                self._events.write(json.dumps(event.as_dict(), default=str) + "\n")
                self._events.flush()

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "elapsed_s": round(now() - self.started_t, 1)
            if self.session_id and self.started_t
            else None,
            "trial": {k: v for k, v in self.trial.items() if k != "t_start"}
            if self.trial
            else None,
            "trials_done": len(self.trials),
            "outcomes": [t["outcome"] for t in self.trials[-20:]],
        }
