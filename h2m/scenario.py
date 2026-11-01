"""Scripted software demo on the virtual arm (REFLEX_BUILD_PLAN item 0). SIMULATION.

Runs the real runtime (h2m.app.H2M: grammar, gate, rules, routing, learner, incident
capture) with the virtual arm, the SyntheticSource EMG and a canned explanation, and
plays one story through its public paths:

  carry the cup of water -> synthetic brace -> freeze -> incident -> canned explanation
  -> scripted approval -> reset and re-arm -> same request takes the long route.

What is scripted, and only here: the typed request, the confirmations (the same
``confirm(by="ui")`` a click makes), the brace (SyntheticSource drive on both muscle
groups, detected by the real brace detector), the rule approval (``approve(by="ui")``
after the reaction-time gate), the person taking the cup back, and the operator's
reset + arm after the brace stop. It refuses to run unless the arm is the virtual one
and the EMG is synthetic. No model, no API key, no network: the Muse client is
replaced by ``CannedMuse`` and the compiler LLM is off.

An operator stop (``operator_stop``, the replay server's /api/stop) aborts the
scenario and leaves the stop latched; the script never resets a stop it did not
script, and any other unexpected stop or fault aborts it too.
"""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

import numpy as np

from h2m.core import Event, now
from h2m.learning import MIN_APPROVE_REACTION_S
from h2m.replay import CANNED_EXPLANATION, CANNED_NOTE
from h2m.robot import ArmState
from h2m.rules import RuleStore
from h2m.sensors.emg import Profile
from h2m.sim_view import TableView, jpeg
from h2m.world import GripperTracker

SIM_NOTE = (
    "SIMULATION: virtual arm, synthetic EMG, schematic table view and a canned "
    "explanation. No hardware, no model, no network."
)
STEPS: tuple[tuple[str, str], ...] = (
    ("setup", "Virtual arm armed, synthetic EMG streaming"),
    ("request", "Typed request: hand me the cup"),
    ("confirm", "Scripted confirmation (the same path as a UI click)"),
    ("carry", "Carrying water on the direct route, toward the laptop"),
    ("brace", "Synthetic brace on the EMG, detected by the brace detector"),
    ("incident", "Incident clip saved"),
    ("explain", "Canned explanation; rule proposed"),
    ("approve", "Scripted approval of the proposed rule"),
    ("rearm", "The person takes the cup back; operator resets and re-arms"),
    ("rerun_request", "Same request again"),
    ("rerun_confirm", "Scripted confirmation"),
    ("rerun_carry", "The carry takes the long way round the laptop"),
)
STEP_NAMES = tuple(name for name, _ in STEPS)
EXPECTED_STOPS = ("brace (EMG)", "open hand (EMG)")  # the scripted brace's stops
CARRYING = frozenset({"carry", "release"})  # plan stages with the object in the jaws
WATCHED = frozenset({
    "intent", "arm.started", "arm.completed", "arm.stopped", "arm.fault", "emg.brace",
    "emg.open_hand", "incident.triggered", "incident.saved", "incident.explained",
    "incident.error", "rule.approved", "rule.rejected", "rule.expired", "rule.error",
    "intent.blocked", "intent.cancelled", "intent.expired",
})


class ScenarioAborted(RuntimeError):
    """Stopped by the operator or by something the script did not plan for."""


class ScenarioFailed(RuntimeError):
    """The runtime did not do what the story needs (a real finding, not a pass)."""


@dataclass
class ScenarioSettings:
    """Pacing only (``demo:`` in configs/h2m.yaml). Nothing here reaches a gate."""

    request: str = "hand me the cup"
    beat_s: float = 1.2
    approve_after_s: float = 2.0
    brace_zone: str = "laptop"
    brace_margin: float = 0.0
    brace_fallback_s: float = 4.0
    brace_drive: float = 0.9
    brace_hold_s: float = 0.6
    step_timeout_s: float = 30.0
    loop_pause_s: float = 8.0

    def __post_init__(self) -> None:
        # The script answers like a person: never inside the reaction-time gate.
        self.approve_after_s = max(float(self.approve_after_s), MIN_APPROVE_REACTION_S + 0.2)

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> ScenarioSettings:
        raw = cfg.get("demo", {}) or {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})


class CannedMuse:
    """Stand-in for the Muse Wonder client: fixed text, no network, labelled canned."""

    explanation_source = "canned"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, model: str, messages: list[dict[str, Any]]) -> str:
        self.calls += 1
        return json.dumps(CANNED_EXPLANATION)


def scenario_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """The shipped config with every sensor and model forced to its simulated or off
    form. Gate, rule and arm thresholds are left exactly as configured."""
    out = copy.deepcopy(cfg)
    out["arm"] = {**out.get("arm", {}), "backend": "virtual"}
    out["emg"] = {**out.get("emg", {}), "source": "synthetic"}
    out["vision"] = {**out.get("vision", {}), "source": "off"}
    out["speech"] = {**out.get("speech", {}), "source": "off", "voice_out": False}
    out["compiler"] = {**out.get("compiler", {}), "llm": "off"}
    out["safety"] = {**out.get("safety", {}), "operator_release": False}
    out["selection"] = {**out.get("selection", {}), "enabled": False, "muse": False}
    return out


@dataclass
class Step:
    name: str
    label: str
    status: str = "pending"  # pending | active | done | failed | aborted
    t_start: float | None = None
    t_end: float | None = None
    detail: str = ""


class Scenario:
    def __init__(
        self,
        cfg: dict[str, Any],
        workdir: Path | None = None,
        settings: ScenarioSettings | None = None,
        until: str | None = None,
    ) -> None:
        if until is not None and until not in STEP_NAMES:
            raise ValueError(f"until must be one of {STEP_NAMES}")
        self.cfg = scenario_config(cfg)
        self.settings = settings or ScenarioSettings.from_config(cfg)
        self.until = until
        self._own_workdir = workdir is None  # a temp dir made here is removed on close()
        self.workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="h2m-demo-"))
        self.incidents_dir = self.workdir / "incidents"
        self._lock = threading.RLock()
        self.h2m: Any = None
        self.view: TableView | None = None
        self.state = "idle"  # idle | running | done | aborted | failed
        self.run_count = 0
        self.steps: list[Step] = []
        self.events: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self.error: str | None = None
        self.incident_id: str | None = None
        self.explanation: dict[str, Any] | None = None
        self.proposal: dict[str, Any] | None = None
        self.rule: dict[str, Any] | None = None
        self.brace: dict[str, Any] | None = None
        self._t0 = now()
        self._abort: str | None = None
        self._frame: bytes | None = None
        self._closed = False
        self._thread: threading.Thread | None = None
        self._run: dict[str, Any] | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self, loop: bool = False) -> threading.Thread:
        def body() -> None:
            while not self._closed:
                self.run()
                if not loop or self.state == "aborted":
                    return
                end = now() + self.settings.loop_pause_s
                while now() < end and not self._closed:
                    time.sleep(0.1)

        self._thread = threading.Thread(target=body, name="h2m-scenario", daemon=True)
        self._thread.start()
        return self._thread

    def close(self) -> None:
        self._closed = True
        with self._lock:
            h, self.h2m = self.h2m, None
        if h is not None:
            h.shutdown()
        if self._own_workdir:
            if self._thread is not None and self._thread is not threading.current_thread():
                self._thread.join(timeout=5.0)
            shutil.rmtree(self.workdir, ignore_errors=True)

    def operator_stop(self, reason: str = "ui stop") -> None:
        """Stop the virtual arm now and abort the script. The stop stays latched."""
        self._abort = reason
        h = self.h2m
        if h is not None:
            h.stop(reason)

    # ── one pass ─────────────────────────────────────────────────────────────
    def run(self) -> str:
        """One pass of the story, blocking. Returns the final state."""
        with self._lock:
            old, self.h2m = self.h2m, None
        if old is not None:
            old.shutdown()
        with self._lock:
            self.run_count += 1
            self.state = "running"
            self.steps = [Step(n, label) for n, label in STEPS]
            self.events, self.runs = [], []
            self.error = self.incident_id = None
            self.explanation = self.proposal = self.rule = self.brace = None
            self._abort = None
            self._t0 = now()
        try:
            for name in STEP_NAMES:
                self._do(name, getattr(self, f"_step_{name}"))
                if name == self.until:
                    break
            final = "done"
        except ScenarioAborted as exc:
            final, self.error = "aborted", str(exc)
        except ScenarioFailed as exc:
            final, self.error = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001  # report, never crash the server
            final, self.error = "failed", f"{type(exc).__name__}: {exc}"
        with self._lock:
            self.state = final
            for step in self.steps:
                if step.status == "active":
                    step.status = "aborted" if final == "aborted" else "failed"
                    step.detail = self.error or ""
                    step.t_end = self._t()
        return final

    def _do(self, name: str, fn: Callable[[], str | None]) -> None:
        step = next(s for s in self.steps if s.name == name)
        with self._lock:
            step.status, step.t_start = "active", self._t()
        self._check_abort()
        detail = fn() or ""
        with self._lock:
            step.status, step.t_end, step.detail = "done", self._t(), detail

    # ── steps ────────────────────────────────────────────────────────────────
    def _step_setup(self) -> str:
        from h2m.app import H2M

        h = H2M(self.cfg)
        if h.arm.backend.is_hardware or h.arm.backend.name != "virtual":
            raise ScenarioFailed("refusing to run the scripted scenario: not the virtual arm")
        source = getattr(h.emg, "source", None)
        if source is None or source.name != "synthetic" or source.is_hardware:
            raise ScenarioFailed("refusing to run the scripted scenario: EMG is not synthetic")
        h.emg.profile = Profile(name="synthetic-sim")  # never the person's saved profile
        run_dir = self.workdir / f"run-{self.run_count}"
        h.rules = RuleStore("demo", root=run_dir / "rules")  # never data/rules
        h.learner.root = self.incidents_dir
        h.learner.muse = CannedMuse()
        self.view = TableView(h.arm.poses, h.routes, self.cfg.get("objects", []))
        h.learner.virtual_camera = self._camera
        h.log.subscribe(self._on_event)
        with self._lock:  # one section with close(): never start an app it cannot stop
            if self._closed:
                raise ScenarioAborted("closed")
            self.h2m = h
            h.start()
        self._wait(lambda: h.arm.state is ArmState.DISARMED, "the virtual arm to connect")
        self._wait(lambda: h._emg_problem() is None, "the synthetic EMG to stream")
        ok, why = h.arm.arm()
        if not ok:
            raise ScenarioFailed(f"could not arm the virtual arm: {why}")
        return f"rules in {run_dir / 'rules'}; incidents in {self.incidents_dir}"

    def _ask(self) -> tuple[dict[str, Any], dict[str, Any]]:
        h = self.h2m
        self._pause()
        h.on_utterance(self.settings.request, source="typed")
        last = h.last or {}
        contract, decision = last.get("contract") or {}, last.get("decision") or {}
        if decision.get("verdict") != "ask" or h.pending is None:
            raise ScenarioFailed(
                f"expected the carry to wait for a confirmation, got "
                f"{decision.get('verdict')}: {decision.get('reason')}"
            )
        return contract, decision

    def _step_request(self) -> str:
        contract, decision = self._ask()
        self._run = {"run": 1, "route": contract.get("route"),
                     "applied_rules": contract.get("applied_rules") or [],
                     "verdict": decision.get("verdict"), "outcome": None,
                     "closest_to_zone": None}
        self.runs.append(self._run)
        if contract.get("route") != "direct":
            raise ScenarioFailed(f"first carry should be direct, got {contract.get('route')}")
        return f"route {contract.get('route')}; gate says: {decision.get('reason')}"

    def _confirm(self) -> str:
        h = self.h2m
        self._pause()
        ok, why = h.confirm(by="ui")
        if not ok:
            raise ScenarioFailed(f"confirmation refused: {why}")
        self._wait(lambda: h.arm.state is ArmState.MOVING or self._seen("arm.completed"),
                   "the arm to start")
        started = self._last("arm.started") or {}
        if self._run is not None:
            self._run["route"] = started.get("route")
            self._run["speed"] = started.get("speed")
        return f"arm started on route {started.get('route')}"

    def _step_confirm(self) -> str:
        return self._confirm()

    def _step_carry(self) -> str:
        h, s = self.h2m, self.settings
        self._wait(lambda: h.arm.snapshot().get("stage") == "carry", "the carry to begin")
        t_carry = now()

        def near() -> bool:
            dist = self._track()
            return (dist is not None and dist <= s.brace_margin) or (
                now() - t_carry >= s.brace_fallback_s
            )

        self._wait(near, f"the cup to near the {s.brace_zone}")
        dist = self._track()
        return f"{dist:.2f} from the {s.brace_zone} (schematic)" if dist is not None else ""

    def _step_brace(self) -> str:
        h, s = self.h2m, self.settings
        source = h.emg.source
        source.set_drive(s.brace_drive, s.brace_drive)
        t0 = now()
        try:
            self._wait(lambda: h.arm.state is ArmState.STOPPED, "the brace to freeze the arm",
                       allow_stop=True, timeout=5.0)
        finally:
            rest = s.brace_hold_s - (now() - t0)
            if rest > 0:
                time.sleep(rest)
            source.set_drive(0.0, 0.0)
        reason = h.arm.reason
        if reason not in EXPECTED_STOPS:
            raise ScenarioAborted(f"the arm stopped for another reason: {reason}")
        brace = self._last("emg.brace")
        with self._lock:
            self.brace = None if brace is None else {
                k: brace.get(k) for k in ("trigger", "detect_ms", "peak_flexor",
                                          "peak_extensor", "peak_brace")
            }
        if self._run is not None:
            self._run["outcome"] = f"frozen: {reason}"
            self._run["frozen_at"] = self._xy()
        return f"stopped: {reason}" + (f", detected in {brace.get('detect_ms')} ms" if brace
                                       else "")

    def _step_incident(self) -> str:
        self._wait(lambda: self._seen("incident.saved") or self._seen("incident.error"),
                   "the incident clip", allow_stop=True)
        if self._seen("incident.error") and not self._seen("incident.saved"):
            raise ScenarioFailed(f"incident not saved: {self._last('incident.error')}")
        saved = self._last("incident.saved") or {}
        with self._lock:
            self.incident_id = saved.get("incident_id")
        return f"incident {self.incident_id}"

    def _step_explain(self) -> str:
        h = self.h2m
        self._wait(lambda: self._seen("incident.explained"), "the explanation", allow_stop=True)
        data = self._last("incident.explained") or {}
        exp = dict(data.get("explanation") or {})
        exp.update({"canned": exp.get("source") == "canned", "note": CANNED_NOTE})
        proposal = h.learner.proposal
        with self._lock:
            self.explanation = exp
            self.proposal = data.get("proposal")
        if proposal is None:
            raise ScenarioFailed(f"no rule proposed: {data.get('problems')}")
        return exp.get("say") or ""

    def _step_approve(self) -> str:
        h, s = self.h2m, self.settings
        proposal = h.learner.proposal
        if proposal is None:
            raise ScenarioFailed("the proposal is gone (expired or replaced)")
        self._sleep(s.approve_after_s)  # the scripted operator reads the card
        ok, why = h.learner.approve(by="ui", expected_id=proposal.rule.id)
        if not ok:
            raise ScenarioFailed(f"approval refused: {why}")
        rules = h.rules.list()
        with self._lock:
            self.rule = next((asdict(r) for r in rules if r.id == proposal.rule.id), None)
        if h.arm.state is not ArmState.STOPPED:
            raise ScenarioFailed("approving a rule must never resume motion")
        return why

    def _step_rearm(self) -> str:
        h = self.h2m
        self._pause()
        self._check_abort()
        if h.arm.state is not ArmState.STOPPED or h.arm.reason not in EXPECTED_STOPS:
            # Only the scripted brace's stop is reset here; anything else stays latched.
            raise ScenarioAborted(f"not resetting an unscripted stop ({h.arm.reason})")
        # SIMULATION: the person lifts the cup out of the stopped gripper and puts it back
        # on its slot. The tracker restarts at UNKNOWN (never assumed empty), as after a
        # runtime restart.
        h.gripper = GripperTracker()
        ok, why = h.arm.reset()
        if not ok:
            raise ScenarioFailed(f"reset refused: {why}")
        ok, why = h.arm.arm()
        if not ok:
            raise ScenarioFailed(f"arm refused: {why}")
        return "reset and armed (operator, scripted)"

    def _step_rerun_request(self) -> str:
        contract, decision = self._ask()
        self._run = {"run": 2, "route": contract.get("route"),
                     "applied_rules": contract.get("applied_rules") or [],
                     "verdict": decision.get("verdict"), "outcome": None,
                     "closest_to_zone": None}
        self.runs.append(self._run)
        rule_id = (self.rule or {}).get("id")
        if contract.get("route") in (None, "direct") or rule_id not in self._run["applied_rules"]:
            raise ScenarioFailed(
                f"the rule did not reroute the carry: route {contract.get('route')}, "
                f"rules {self._run['applied_rules']}"
            )
        return f"route {contract.get('route')} (rule {rule_id}); still asks for a confirmation"

    def _step_rerun_confirm(self) -> str:
        return self._confirm()

    def _step_rerun_carry(self) -> str:
        h = self.h2m
        started = self._count("arm.completed")

        def finished() -> bool:
            self._track()
            return self._count("arm.completed") > started

        self._wait(finished, "the carry to finish")
        if self._run is not None:
            self._run["outcome"] = "completed"
        dist = (self._run or {}).get("closest_to_zone")
        where = f"; closest to the {self.settings.brace_zone}: {dist:.2f}" if dist else ""
        return f"placed on the tray via {(self._run or {}).get('route')}{where}" + (
            "" if h.arm.state is ArmState.READY else f" (arm {h.arm.state})"
        )

    # ── helpers ──────────────────────────────────────────────────────────────
    def _t(self) -> float:
        return round(now() - self._t0, 3)

    def _check_abort(self) -> None:
        if self._abort is not None:
            raise ScenarioAborted(f"operator stop: {self._abort}")
        if self._closed:
            raise ScenarioAborted("closed")

    def _sleep(self, seconds: float) -> None:
        end = now() + seconds
        while now() < end:
            self._check_abort()
            time.sleep(min(0.05, max(0.0, end - now())))

    def _pause(self) -> None:
        self._sleep(self.settings.beat_s)

    def _wait(
        self,
        cond: Callable[[], bool],
        what: str,
        allow_stop: bool = False,
        timeout: float | None = None,
    ) -> None:
        h = self.h2m
        deadline = now() + (timeout or self.settings.step_timeout_s)
        while True:
            self._check_abort()
            if cond():
                return
            if h is not None and h.arm.state is ArmState.FAULT:
                raise ScenarioAborted(f"arm fault: {h.arm.reason}")
            if not allow_stop and h is not None and h.arm.state is ArmState.STOPPED:
                raise ScenarioAborted(f"the arm stopped: {h.arm.reason}")
            if now() > deadline:
                raise ScenarioFailed(f"timed out waiting for {what}")
            time.sleep(0.02)

    def _on_event(self, event: Event) -> None:
        if event.kind not in WATCHED:
            return
        data = {k: v for k, v in event.data.items() if k != "trace"}
        with self._lock:
            self.events.append({"t": self._t(), "kind": event.kind, "data": data})
        view = self.view
        if event.kind == "arm.completed" and data.get("verb") == "pick_place" and view:
            view.placed.add(str(data.get("object_id")))  # drawn on the tray from now on

    def _seen(self, kind: str) -> bool:
        with self._lock:
            return any(e["kind"] == kind for e in self.events)

    def _count(self, kind: str) -> int:
        with self._lock:
            return sum(e["kind"] == kind for e in self.events)

    def _last(self, kind: str) -> dict[str, Any] | None:
        with self._lock:
            for e in reversed(self.events):
                if e["kind"] == kind:
                    return e["data"]
        return None

    def _context(self) -> tuple[dict[str, Any], str | None, str | None, str | None, bool]:
        h = self.h2m
        snap = h.arm.snapshot()
        last = h.last or {}
        route = (last.get("contract") or {}).get("route")
        slot = (last.get("decision") or {}).get("slot_id")
        held = h.gripper.state()
        holding = held.object_id if held.holding else None
        if snap.get("state") == "moving" and snap.get("stage") in CARRYING:
            holding = snap.get("target")  # drawn in the jaws between grasp and release
        stopped = snap.get("state") in ("stopped", "fault")
        return snap, route, slot, holding, stopped

    def _xy(self) -> list[float] | None:
        h, view = self.h2m, self.view
        if h is None or view is None:
            return None
        snap, route, slot, _, _ = self._context()
        xy = view.gripper_xy(snap.get("joints") or {}, snap.get("stage"), route, slot)
        return None if xy is None else [round(xy[0], 3), round(xy[1], 3)]

    def _track(self) -> float | None:
        """Distance of the schematic gripper from the brace zone; keeps the run's minimum."""
        xy, view = self._xy(), self.view
        if xy is None or view is None:
            return None
        dist = view.zone_distance((xy[0], xy[1]), self.settings.brace_zone)
        run = self._run
        if dist is not None and run is not None and self.h2m.arm.state is ArmState.MOVING:
            prev = run.get("closest_to_zone")
            run["closest_to_zone"] = round(dist if prev is None else min(prev, dist), 3)
        return dist

    def _render(self) -> np.ndarray | None:
        h, view = self.h2m, self.view
        if h is None or view is None:
            return None
        snap, route, slot, holding, stopped = self._context()
        active = next((s for s in self.steps if s.status == "active"), None)
        return view.render(snap.get("joints") or {}, snap.get("stage"), route, slot, holding,
                           stopped, active.label if active else self.state)

    def _camera(self) -> np.ndarray | None:
        """The learner's virtual camera (sampler thread): render and cache the jpeg."""
        img = self._render()
        if img is not None:
            self._frame = jpeg(img)
        return img

    def frame(self) -> bytes | None:
        return self._frame

    # ── payload ──────────────────────────────────────────────────────────────
    def snapshot(self) -> dict[str, Any]:
        h = self.h2m
        with self._lock:
            steps = [asdict(s) for s in self.steps]
            current = next((s["name"] for s in steps if s["status"] == "active"), None)
            out = {
                "simulation": True,
                "label": "SIMULATION",
                "note": SIM_NOTE,
                "state": self.state,
                "run": self.run_count,
                "step": current,
                "steps": steps,
                "t": self._t(),
                "incident_id": self.incident_id,
                "brace": self.brace,
                "explanation": self.explanation,
                "proposal": self.proposal,
                "rule": self.rule,
                "runs": [dict(r) for r in self.runs],
                "error": self.error,
                "settings": asdict(self.settings),
            }
        if h is not None:
            arm = h.arm.snapshot()
            emg = h.emg.snapshot() if h.emg is not None else None
            out["arm"] = {k: arm.get(k) for k in ("state", "reason", "backend", "hardware",
                                                  "skill", "stage", "progress", "joints")}
            out["emg"] = None if emg is None else {
                k: emg.get(k) for k in ("source", "hardware", "live", "effort", "extensor",
                                        "brace", "brace_effort")
            }
            out["gripper_xy"] = self._xy()
            out["frame_url"] = "/api/replay/scenario/frame.jpg"
        return out

    def timeline(self) -> dict[str, Any]:
        with self._lock:
            return {
                "simulation": True,
                "label": "SIMULATION",
                "steps": [asdict(s) for s in self.steps],
                "events": list(self.events),
            }


def make_sample(
    cfg: dict[str, Any],
    dest: Path,
    sample_id: str = "demo-liquid-over-laptop",
    settings: ScenarioSettings | None = None,
) -> Path:
    """Run the scenario up to the explanation and copy its incident to ``dest/<id>``,
    marked synthetic. The clip is the pipeline's own output: rendered virtual-table
    frames, SyntheticSource EMG, the canned explanation."""
    work = Path(tempfile.mkdtemp(prefix="h2m-sample-"))
    scenario = Scenario(cfg, work, settings, until="explain")
    try:
        if scenario.run() != "done" or scenario.incident_id is None:
            raise ScenarioFailed(f"scenario did not reach the explanation: {scenario.error}")
        src = scenario.incidents_dir / scenario.incident_id
        out = Path(dest) / sample_id
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(src, out)
        meta = json.loads((out / "meta.json").read_text())
        meta.update({
            "id": sample_id,
            "synthetic": True,
            "synthetic_note": (
                "SYNTHETIC sample: produced by `python scripts/make_demo_sample.py` running "
                "the scripted scenario (h2m/scenario.py) on the virtual arm. Frames are a "
                "schematic render of the virtual table, EMG is SyntheticSource, the "
                "explanation is canned. Not a recording of a person or a real arm."
            ),
            "source_incident": scenario.incident_id,
        })
        (out / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
        return out
    finally:
        scenario.close()
        shutil.rmtree(work, ignore_errors=True)
