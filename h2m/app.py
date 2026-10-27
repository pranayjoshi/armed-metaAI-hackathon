"""H2M runtime: sensors -> step contract -> safety gate -> confirmation -> arm.

Interaction rules:
- "stop" (voice, UI) or an open hand (EMG) freezes the arm immediately.
- A request the gate approves runs; one it asks about waits for a squeeze
  (EMG), "yes", or a click. A squeeze also sets the grip strength.
- An ambiguous target ("which one?") re-resolves live from pointing, or from
  a spoken name or a click on a candidate.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from h2m.compiler.grammar import Parsed, build_vocabulary, parse
from h2m.compiler.llm import build_llm
from h2m.compiler.resolve import compile_contract, resolve_choice
from h2m.contract import StepContract, Verb
from h2m.core import Event, EventLog, now
from h2m.recorder import Recorder
from h2m.robot import ArmState, build_arm
from h2m.robot.skills import plan_home, plan_pick_place, plan_point
from h2m.safety import Decision, Verdict, decide
from h2m.sensors.emg import build_emg
from h2m.sensors.speech import build_speech
from h2m.sensors.vision import build_vision


@dataclass
class Pending:
    parsed: Parsed
    contract: StepContract
    decision: Decision
    expires_at: float


class H2M:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.log = EventLog()
        self.catalog = {o["id"]: o for o in cfg.get("objects", [])}
        self.vocab = build_vocabulary(cfg.get("objects", []))
        self.safety_cfg = cfg.get("safety", {})
        self.arm = build_arm(cfg.get("arm", {}), self.log)
        self.emg = build_emg(cfg.get("emg", {}), self.log)
        self.vision = build_vision(cfg.get("vision", {}), cfg.get("objects", []), self.log)
        self.speech = build_speech(cfg.get("speech", {}), self.log, self.on_utterance)
        self.llm = build_llm(cfg.get("compiler", {}))
        self.recorder = Recorder(self.log)
        self.pending: Pending | None = None
        self.last: dict[str, Any] | None = None
        self.reply = ""
        self._lock = threading.RLock()
        self._running = False
        self.log.subscribe(self._on_event)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        self.arm.connect()
        for part in (self.emg, self.vision, self.speech):
            if part is not None:
                part.start()
        self._running = True
        threading.Thread(target=self._loop, name="h2m-loop", daemon=True).start()
        self.log.emit(
            "h2m.started",
            arm=self.arm.backend.name,
            emg=self.emg.source.name if self.emg else "off",
            vision="camera" if self.vision else "virtual",
            speech="mic" if self.speech else "off",
            llm=bool(self.llm),
        )

    def shutdown(self) -> None:
        self._running = False
        self.recorder.stop()
        for part in (self.speech, self.vision, self.emg):
            if part is not None:
                part.stop()
        self.arm.close()

    # ── world ────────────────────────────────────────────────────────────────
    def visible_objects(self) -> list[dict[str, Any]]:
        if self.vision is not None:
            return self.vision.visible_objects()
        # Virtual scene (no camera): catalog objects sit on the taught slots.
        slots = list(self.arm.poses.slots.values())
        return [
            {
                "id": o["id"],
                "name": o["name"],
                "confidence": 1.0,
                "table_xy": slots[i].table_xy,
                "bbox": None,
                "virtual": True,
            }
            for i, o in enumerate(self.cfg.get("objects", [])[: len(slots)])
        ]

    def pointing(self) -> dict[str, Any]:
        return self.vision.pointing if self.vision else {"active": 0.0, "candidates": []}

    def _world(self, contract: StepContract) -> dict[str, Any]:
        visible_ids = {o["id"] for o in self.visible_objects()}
        slot = self.arm.slot_for(contract.table_xy) if contract.table_xy else None
        return {
            "arm_ready": self.arm.state is ArmState.READY,
            "slot_id": slot.slot_id if slot else None,
            "target_visible": contract.object_id in visible_ids,
        }

    # ── inputs ───────────────────────────────────────────────────────────────
    def on_utterance(
        self, text: str, meta: dict[str, Any] | None = None, source: str = "voice"
    ) -> None:
        t0 = now()
        parsed = parse(text, self.vocab)
        self.log.emit(
            "speech.utterance", text=text, source=source, verb=str(parsed.verb), **(meta or {})
        )
        if parsed.verb is Verb.STOP:
            self.stop(f"{source}: {text}")
            return
        if parsed.verb is Verb.CANCEL:
            self.cancel(f"{source}: {text}")
            return
        with self._lock:
            pending = self.pending
        if parsed.verb is Verb.CONFIRM:
            self.confirm(by=source)
            return
        if pending and pending.decision.clarify and parsed.object_id:
            self.choose(parsed.object_id, by=source)
            return
        compiled_by = "grammar"
        if self.llm and (
            parsed.verb is Verb.UNKNOWN
            or (parsed.reference == "none" and parsed.verb in (Verb.PICK_PLACE, Verb.POINT))
        ):
            jpeg = self.vision.scene.jpeg() if self.vision else None
            result = self.llm.interpret(text, self.visible_objects(), self.pointing(), jpeg)
            if result is not None:
                parsed, self.reply = result
                compiled_by = "muse"
            self.log.emit(
                "compiler.llm",
                ok=result is not None,
                ms=self.llm.last_ms,
                error=self.llm.last_error,
            )
        self._evaluate(parsed, compiled_by, t0)

    def _evaluate(self, parsed: Parsed, source: str, t0: float) -> None:
        contract = compile_contract(
            parsed,
            self.visible_objects(),
            self.pointing(),
            self.catalog,
            float(self.safety_cfg.get("target_margin", 0.2)),
            source,
        )
        decision = decide(contract, self._world(contract), self.safety_cfg)
        self._publish(contract, decision, compile_ms=round((now() - t0) * 1000, 1))
        with self._lock:
            self.pending = None
            if decision.verdict is Verdict.ASK:
                timeout = float(self.safety_cfg.get("confirm_timeout_s", 8.0))
                self.pending = Pending(parsed, contract, decision, now() + timeout)
        if decision.verdict is Verdict.APPROVE:
            self._execute(contract, decision, confirmed_by="auto")

    def _publish(self, contract: StepContract, decision: Decision, **extra: Any) -> None:
        self.last = {"contract": contract.as_dict(), "decision": decision.as_dict()}
        self.log.emit("intent", contract=contract.as_dict(), decision=decision.as_dict(), **extra)

    # ── operator / gesture actions ───────────────────────────────────────────
    def confirm(self, by: str = "ui", effort: float | None = None) -> tuple[bool, str]:
        with self._lock:
            pending = self.pending
            if pending is None:
                return False, "nothing to confirm"
            if pending.decision.clarify:
                return False, "choose which object first"
            self.pending = None
        contract = pending.contract
        if effort is not None:
            contract.grip_effort = effort
            contract.evidence["emg_grip"] = round(effort, 3)
        contract.evidence[f"confirmed_{by}"] = 1.0
        # Re-check the world: things may have changed while we waited.
        decision = decide(
            contract,
            self._world(contract),
            {**self.safety_cfg, "always_confirm": False, "auto_approve": 0.0},
        )
        self._publish(contract, decision, confirmed_by=by)
        if decision.verdict is not Verdict.APPROVE:
            return False, decision.reason
        return self._execute(contract, decision, confirmed_by=by)

    def choose(self, object_id: str, by: str = "ui") -> tuple[bool, str]:
        with self._lock:
            pending = self.pending
        if pending is None:
            return False, "nothing pending"
        contract = resolve_choice(pending.contract, object_id, self.visible_objects())
        decision = decide(contract, self._world(contract), self.safety_cfg)
        self._publish(contract, decision, chosen_by=by)
        with self._lock:
            self.pending = (
                Pending(pending.parsed, contract, decision, pending.expires_at)
                if decision.verdict is Verdict.ASK
                else None
            )
        if decision.verdict is Verdict.APPROVE:
            return self._execute(contract, decision, confirmed_by=by)
        return True, decision.reason

    def cancel(self, reason: str = "cancel") -> None:
        with self._lock:
            had = self.pending is not None
            self.pending = None
        if self.arm.state is ArmState.MOVING:
            self.arm.stop(reason)
        self.log.emit("intent.cancelled", reason=reason, had_pending=had)

    def stop(self, reason: str = "stop") -> None:
        with self._lock:
            self.pending = None
        self.arm.stop(reason)

    def run_typed(self, text: str) -> None:
        threading.Thread(target=self.on_utterance, args=(text, {}, "typed"), daemon=True).start()

    # ── execution ────────────────────────────────────────────────────────────
    def _execute(
        self, contract: StepContract, decision: Decision, confirmed_by: str
    ) -> tuple[bool, str]:
        arm = self.arm
        meta = {
            "contract_id": contract.id,
            "object_id": contract.object_id,
            "verb": str(contract.verb),
            "confirmed_by": confirmed_by,
        }
        grip = None
        if contract.verb is Verb.HOME:
            plan = plan_home(arm.poses)
        else:
            slot = arm.poses.slots.get(decision.slot_id or "")
            if slot is None:
                return False, "no reachable slot"
            if contract.verb is Verb.POINT:
                plan = plan_point(arm.poses, slot)
            else:
                effort = contract.grip_effort
                if effort is None:
                    effort = float(self.safety_cfg.get("default_grip", 0.5))
                ceiling = float(self.catalog.get(contract.object_id or "", {}).get("max_grip", 1.0))
                grip = arm.grip_for(effort, ceiling)
                meta["requested_effort"] = round(effort, 3)
                plan = plan_pick_place(arm.poses, slot, arm.settings.gripper_open, grip.close_pct)
        ok, reason = arm.execute(plan, meta, grip)
        if not ok:
            self.log.emit("intent.blocked", reason=reason, contract_id=contract.id)
        return ok, reason

    # ── events and loop ──────────────────────────────────────────────────────
    def _on_event(self, event: Event) -> None:
        if event.kind == "emg.open_hand":
            if self.arm.state is ArmState.MOVING:
                self.stop("open hand (EMG)")
            elif self.pending is not None:
                self.cancel("open hand (EMG)")
        elif event.kind == "emg.squeeze" and self.pending is not None:
            threading.Thread(
                target=self.confirm,
                kwargs={"by": "emg", "effort": float(event.data.get("effort", 0.5))},
                daemon=True,
            ).start()

    def _loop(self) -> None:
        while self._running:
            time.sleep(0.1)
            with self._lock:
                pending = self.pending
            if pending is None:
                continue
            if now() > pending.expires_at:
                with self._lock:
                    self.pending = None
                self.log.emit("intent.expired", contract_id=pending.contract.id)
                continue
            if pending.decision.clarify and self.vision is not None:
                # Live re-resolution from pointing while we wait for a choice.
                contract = compile_contract(
                    pending.parsed,
                    self.visible_objects(),
                    self.pointing(),
                    self.catalog,
                    float(self.safety_cfg.get("target_margin", 0.2)),
                )
                if contract.object_id:
                    decision = decide(contract, self._world(contract), self.safety_cfg)
                    self._publish(contract, decision, resolved_by="pointing")
                    with self._lock:
                        self.pending = (
                            Pending(pending.parsed, contract, decision, pending.expires_at)
                            if decision.verdict is Verdict.ASK
                            else None
                        )
                    if decision.verdict is Verdict.APPROVE:
                        self._execute(contract, decision, confirmed_by="pointing")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            pending = self.pending
        events = self.log.recent(
            40,
            kinds={
                "speech.utterance",
                "intent",
                "intent.cancelled",
                "intent.expired",
                "intent.blocked",
                "arm.started",
                "arm.completed",
                "arm.stopped",
                "arm.fault",
                "arm.state",
                "emg.squeeze",
                "emg.open_hand",
                "emg.calibrated",
                "compiler.llm",
                "trial.started",
                "trial.ended",
                "session.started",
                "session.stopped",
                "speech.error",
                "vision.error",
                "emg.error",
            },
        )
        return {
            "t": round(now(), 3),
            "arm": self.arm.snapshot(),
            "emg": self.emg.snapshot() if self.emg else None,
            "vision": self.vision.snapshot()
            if self.vision
            else {
                "live": False,
                "virtual": True,
                "objects": self.visible_objects(),
                "pointing": self.pointing(),
                "frame_size": None,
            },
            "speech": self.speech.snapshot() if self.speech else None,
            "llm": {
                "enabled": bool(self.llm),
                "model": self.llm.model if self.llm else None,
                "last_ms": self.llm.last_ms if self.llm else None,
                "error": self.llm.last_error if self.llm else None,
            },
            "pending": None
            if pending is None
            else {
                "contract": pending.contract.as_dict(),
                "decision": pending.decision.as_dict(),
                "expires_in_s": round(max(0.0, pending.expires_at - now()), 1),
            },
            "last": self.last,
            "reply": self.reply,
            "session": self.recorder.snapshot(),
            "objects": [
                {"id": o["id"], "name": o["name"], "max_grip": o.get("max_grip", 1.0)}
                for o in self.cfg.get("objects", [])
            ],
            "events": [e.as_dict() for e in events],
        }
