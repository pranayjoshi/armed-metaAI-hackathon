"""Incident -> explanation -> rule proposal -> approval (plan sections 3.2 and 3.3).

Nothing here sits on the freeze path. The brace handler stops the arm first and
then only enqueues an ``IncidentTrigger`` (a non-blocking put). A worker thread
freezes the ring buffer, waits out the after-window, saves the clip, asks Muse
Wonder (or falls back) and holds a rule proposal. A proposal is saved only on an
explicit approval (squeeze, "yes" or a click) after the reaction-time gate, and
only if it validates against known zones and catalog tags. Nothing here moves
the arm, resets a stop or arms it.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from h2m.core import ROOT, now
from h2m.incident import (
    FALLBACK_SAY,
    Explanation,
    Incident,
    IncidentBuffer,
    MuseClient,
    explain,
    fallback_explanation,
)
from h2m.robot.backend import JOINTS
from h2m.rules import Rule, RulesUnavailable

if TYPE_CHECKING:
    from h2m.app import H2M

MIN_APPROVE_REACTION_S = 0.3  # same human reaction-time floor as the squeeze confirm
APPROVERS = frozenset({"emg", "voice", "typed", "ui"})  # never "brace" or "auto"
HOLD_STAGES = frozenset({"grasp", "carry", "release"})  # stages where the gripper holds it


@dataclass(frozen=True)
class IncidentTrigger:
    t: float
    source: str  # brace | open_hand
    onset_t: float | None = None


@dataclass
class RuleProposal:
    rule: Rule  # approved=False until a person approves it
    incident_id: str | None
    say: str
    announced_at: float
    expires_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": asdict(self.rule),
            "incident_id": self.incident_id,
            "say": self.say,
            "expires_in_s": round(max(0.0, self.expires_at - now()), 1),
        }


def build_muse(cfg: dict[str, Any]) -> MuseClient | None:
    """Muse Wonder client when MODEL_API_KEY is set, else None (explain falls back)."""
    client = MuseClient(
        {
            "base_url": cfg.get("compiler", {}).get("base_url", "https://api.meta.ai/v1"),
            "timeout_s": cfg.get("incident", {}).get("timeout_s", 20.0),
            "sam_model": cfg.get("incident", {}).get("sam_model"),
        }
    )
    return client if client.available else None


class Learner:
    def __init__(self, app: H2M) -> None:
        self.app = app
        self.log = app.log
        self.lock = app._lock  # shared with the app: pending request and proposal move together
        self.cfg: dict[str, Any] = app.cfg.get("incident", {}) or {}
        self.rules_cfg: dict[str, Any] = app.cfg.get("rules", {}) or {}
        self.buffer = IncidentBuffer(
            float(self.cfg.get("seconds", 6.0)),
            float(self.cfg.get("fps", 5.0)),
            int(self.cfg.get("downscale", 320)),
        )
        self.root = ROOT / str(self.cfg.get("root", "data/incidents"))
        self.muse: Any = build_muse(app.cfg)
        self.proposal: RuleProposal | None = None
        self.incident: dict[str, Any] | None = None  # latest incident status
        self._queue: queue.Queue[IncidentTrigger] = queue.Queue()
        self._active: dict[str, Any] | None = None
        self._last_held: tuple[float, dict[str, Any]] | None = None
        self._emg_seen = float("-inf")
        self._running = False
        self.error: str | None = None
        # Simulation only (h2m.scenario): a rendered top-down view of the virtual table,
        # sampled into the clip when there is no real camera. Never set with a camera.
        self.virtual_camera: Callable[[], np.ndarray | None] | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        self._running = True
        threading.Thread(target=self._sample_loop, name="h2m-incident-sample", daemon=True).start()
        threading.Thread(target=self._work_loop, name="h2m-incident", daemon=True).start()

    def stop(self) -> None:
        self._running = False

    # ── sampling (its own thread; never the EMG or control thread) ───────────
    def set_active(self, contract: dict[str, Any] | None) -> None:
        with self.lock:
            self._active = contract

    def _sample_loop(self) -> None:
        period = 1.0 / max(1.0, float(self.cfg.get("sample_hz", 10)))
        while self._running:
            t = now()
            try:
                self.sample()
            except Exception as exc:  # a camera or bus hiccup must not end sampling
                self.error = f"incident sampling failed: {exc}"
            time.sleep(max(0.0, period - (now() - t)))

    def sample(self) -> None:
        t = now()
        overhead = wrist = None
        vision = self.app.vision
        if vision is not None:
            item = vision.scene.latest()
            overhead = item[0] if item else None
            if vision.wrist is not None:
                item = vision.wrist.latest()
                wrist = item[0] if item else None
        elif self.virtual_camera is not None:
            try:
                overhead = self.virtual_camera()
            except Exception:  # a broken render must not stop joint / EMG sampling
                overhead = None
        snap = self.app.arm.snapshot()
        q = snap.get("joints") or {}
        joints = [q[j] for j in JOINTS] if all(j in q for j in JOINTS) else None
        with self.lock:
            active = self._active
        contract = None
        if active is not None and snap.get("state") == "moving":
            contract = {**active, "stage": snap.get("stage")}
            if snap.get("stage") in HOLD_STAGES:
                self._last_held = (t, contract)
        self.buffer.add_frame(t, overhead, wrist, joints, contract)
        emg = self.app.emg
        if emg is not None:
            rows = emg.fast_trace_since(self._emg_seen)
            for row in rows:  # (flexor, extensor, brace) on the fast window
                self.buffer.add_emg(row[0], row[1:])
            if rows:
                self._emg_seen = rows[-1][0]

    # ── incidents (worker thread) ────────────────────────────────────────────
    def trigger(self, t: float, source: str, onset_t: float | None = None) -> None:
        """Called on the event thread after the arm has already stopped: enqueue only."""
        self._queue.put_nowait(IncidentTrigger(t, source, onset_t))

    def _work_loop(self) -> None:
        while self._running:
            self.run_once(timeout=0.5)

    def run_once(self, timeout: float | None = None) -> bool:
        try:
            trig = self._queue.get(timeout=timeout)
        except queue.Empty:
            return False
        try:
            self.process(trig)
        except Exception as exc:  # never let one incident kill the worker
            self.buffer.reset()
            self.log.emit("incident.error", detail=f"incident failed: {exc}")
        return True

    def _status(self, **fields: Any) -> None:
        with self.lock:
            self.incident = {**(self.incident or {}), **fields}

    def _held(self, t: float) -> str | None:
        last = self._last_held
        if last is None or not 0.0 <= t - last[0] <= 1.0:
            return None
        c = last[1]
        tags = c.get("tags") or []
        name = c.get("object_name") or c.get("object_id")
        return f"{name} (tags: {', '.join(tags)})" if tags else name

    def _objects(self) -> list[dict[str, Any]]:
        objects = []
        for o in self.app.visible_objects():
            entry = {k: o.get(k) for k in ("id", "name", "confidence", "table_xy")}
            if o.get("bbox"):
                entry["box"] = o["bbox"]
            objects.append(entry)
        routes = self.app.routes
        for name, box in routes.zones.items() if routes else []:
            objects.append({"id": name, "name": name, "kind": "avoid_zone", "table_box": box})
        return objects

    def _provenance(self) -> dict[str, Any]:
        """Where this clip came from, saved with it so a replay can say whether it was
        recorded on hardware or in the simulation."""
        app = self.app
        source = getattr(app.emg, "source", None) if app.emg is not None else None
        arm_hw = bool(app.arm.backend.is_hardware)
        emg_hw = bool(getattr(source, "is_hardware", False)) if source is not None else None
        if app.vision is not None:
            camera = "camera"
        else:
            camera = "virtual" if self.virtual_camera is not None else "off"
        return {
            "arm": app.arm.backend.name,
            "arm_hardware": arm_hw,
            "emg": getattr(source, "name", "off") if source is not None else "off",
            "emg_hardware": emg_hw,
            "camera": camera,
            "simulation": not (arm_hw and emg_hw is True and camera != "virtual"),
        }

    def process(self, trig: IncidentTrigger) -> None:
        after = float(self.cfg.get("after_s", 1.0))
        self.buffer.freeze(t=trig.t, after_s=after)
        self._status(
            id=None, state="capturing", source=trig.source, t=trig.t, path=None, explanation=None
        )
        delay = trig.t + after - now()
        if delay > 0:
            time.sleep(delay)  # the sampler keeps recording the after-window meanwhile
        inc: Incident | None
        try:
            extra = {
                "trigger": {"source": trig.source, "t": trig.t, "onset_t": trig.onset_t},
                "provenance": self._provenance(),
            }
            inc = self.buffer.save(
                self.root, self._objects(), self._held(trig.t), reset=True, extra=extra
            )
        except Exception as exc:
            self.buffer.reset()
            self.log.emit("incident.error", detail=f"incident save failed: {exc}")
            inc = None
        if inc is not None:
            self._status(id=inc.id, state="explaining", path=str(inc.path))
            self.log.emit("incident.saved", incident_id=inc.id, path=str(inc.path), held=inc.held)
            exp = explain(inc, self.muse)
        else:
            exp = fallback_explanation()
        proposal, problems = self._propose(exp, inc)
        say = exp.say if proposal is not None or exp.source == "fallback" else FALLBACK_SAY
        if inc is not None and inc.path is not None:
            try:
                (Path(inc.path) / "explanation.json").write_text(
                    json.dumps({**exp.to_dict(), "problems": problems}, indent=2, default=str)
                )
            except OSError as exc:
                self.log.emit("incident.error", detail=f"explanation not saved: {exc}")
        with self.lock:
            replaced = self.proposal
            if proposal is not None:
                proposal.announced_at = now()
                proposal.expires_at = proposal.announced_at + float(
                    self.rules_cfg.get("proposal_timeout_s", 30.0)
                )
            self.proposal = proposal if proposal is not None else replaced
            self.app.reply = say
        if replaced is not None and proposal is not None:
            self.log.emit("rule.rejected", rule_id=replaced.rule.id, by="system", reason="replaced")
        self._status(state="explained", explanation=exp.to_dict(), say=say, problems=problems)
        self.log.emit(
            "incident.explained",
            incident_id=inc.id if inc else None,
            source=trig.source,
            explanation=exp.to_dict(),
            say=say,
            proposal=proposal.as_dict() if proposal else None,
            problems=problems,
        )

    def _propose(
        self, exp: Explanation, inc: Incident | None
    ) -> tuple[RuleProposal | None, list[str]]:
        if exp.rule is None:
            return None, []
        try:
            rule = Rule(
                when_holding=list(exp.rule["when_holding"]),
                avoid=exp.rule["avoid"],
                response=exp.rule["response"],
                incident_id=inc.id if inc else None,
                clip=str(inc.path) if inc and inc.path else None,
                approved=False,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return None, [str(exc)]
        problems = rule.validate(self.app.zone_names(), self.app.catalog_tags())
        if problems:
            return None, problems
        return RuleProposal(rule, rule.incident_id, exp.say, now(), now()), []

    # ── approval ─────────────────────────────────────────────────────────────
    def pop_proposal(self) -> RuleProposal | None:
        with self.lock:
            p, self.proposal = self.proposal, None
            return p

    def approve(
        self,
        by: str,
        expected: RuleProposal | None = None,
        expected_id: str | None = None,
        onset: float | None = None,
    ) -> tuple[bool, str]:
        """Save the pending rule. ``onset`` is when the person's answer began; it must be
        at least MIN_APPROVE_REACTION_S after the question. ``expected``/``expected_id``
        pin the proposal the answer was given to."""
        if by not in APPROVERS:
            return False, f"{by} cannot approve a rule"
        onset = now() if onset is None else onset
        with self.lock:
            p = self.proposal
            if p is None:
                return False, "no rule is waiting for approval"
            if (expected is not None and p is not expected) or (
                expected_id is not None and p.rule.id != expected_id
            ):
                return False, "proposal changed"
            if now() > p.expires_at:
                self.proposal = None
                expired = True
            else:
                expired = False
                if onset < p.announced_at + MIN_APPROVE_REACTION_S:
                    return False, "too soon after the question"
                self.proposal = None
        if expired:
            self.log.emit("rule.expired", rule_id=p.rule.id)
            return False, "proposal expired"
        rule = replace(p.rule, approved=True)
        problems = rule.validate(self.app.zone_names(), self.app.catalog_tags())
        if problems:
            self.log.emit("rule.rejected", rule_id=rule.id, by="validation", reason=problems)
            return False, "; ".join(problems)
        try:
            self.app.rules.add(rule)
        except (RulesUnavailable, OSError, PermissionError, ValueError) as exc:
            self.log.emit("rule.error", rule_id=rule.id, detail=str(exc))
            return False, f"could not save the rule: {exc}"
        self.log.emit("rule.approved", rule=asdict(rule), by=by)
        return True, f"saved rule {rule.id}"

    def reject(self, by: str, expected_id: str | None = None, reason: str = "") -> tuple[bool, str]:
        with self.lock:
            p = self.proposal
            if p is None:
                return False, "no rule is waiting for approval"
            if expected_id is not None and p.rule.id != expected_id:
                return False, "proposal changed"
            self.proposal = None
        self.log.emit("rule.rejected", rule_id=p.rule.id, by=by, reason=reason)
        return True, "not saved"

    def expire(self) -> None:
        with self.lock:
            p = self.proposal
            if p is None or now() <= p.expires_at:
                return
            self.proposal = None
        self.log.emit("rule.expired", rule_id=p.rule.id)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            p, inc = self.proposal, self.incident
        return {
            "proposal": p.as_dict() if p else None,
            "incident": dict(inc) if inc else None,
            "muse": self.muse is not None,
            "error": self.error,
        }
