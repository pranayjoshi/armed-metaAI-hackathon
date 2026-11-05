"""Incident -> explanation -> rule proposal -> approval (plan sections 3.2 and 3.3).

Nothing here sits on the freeze path. The brace handler stops the arm first and
then only enqueues an ``IncidentTrigger`` (a non-blocking put). A worker thread
freezes the ring buffer, waits out the after-window, saves the clip, asks Muse
Wonder (or falls back) and holds a rule proposal. A proposal is saved only on an
explicit approval (squeeze, "yes" or a click) after the reaction-time gate, and
only if it validates against known zones and catalog tags. Nothing here moves
the arm, resets a stop or arms it.

The investigation runs as a tool loop (``armed.incident.Investigator``) when the
client speaks the Responses API; its feed is ``snapshot()["investigation"]`` and
each model call lands in ``snapshot()["calls"]``. A rule reaches the proposal only
through ``check_rule``: ``validate_rule`` and then the checker's two ticks
(``armed.routing.check_proposal``). The proposal carries the twin's rehearsal
(old and new polylines, played by the twin only; no arm is driven by it).
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

from armed.core import ROOT, now
from armed.incident import (
    FALLBACK_SAY,
    Explanation,
    Incident,
    IncidentBuffer,
    InvestigationConfig,
    MuseClient,
    explain,
    fallback_explanation,
    investigate,
)
from armed.robot.backend import JOINTS
from armed.routing import applied_detail, check_proposal, rehearsal_routes, tags_for
from armed.rules import Rule, RulesUnavailable, validate_rule

if TYPE_CHECKING:
    from armed.app import Armed

MIN_APPROVE_REACTION_S = 0.3  # same human reaction-time floor as the squeeze confirm
APPROVERS = frozenset({"emg", "voice", "typed", "ui"})  # never "brace" or "auto"
HOLD_STAGES = frozenset({"grasp", "carry", "release"})  # stages where the gripper holds it
# A burst stop (a twitch-band rise while moving) may be voluntary (gripping a cup). Its
# incident proposes a rule only if a brace or open hand follows within this.
BURST_FOLLOW_S = 2.0
NO_FOLLOW = "burst stop with no brace or open hand after it (possibly voluntary): no rule"


@dataclass(frozen=True)
class IncidentTrigger:
    t: float
    source: str  # brace | open_hand | burst
    onset_t: float | None = None


REHEARSAL_S = 4.0  # the twin plays the new route over this long (low speed, empty gripper)


@dataclass
class RuleProposal:
    rule: Rule  # approved=False until a person approves it
    incident_id: str | None
    say: str
    announced_at: float
    expires_at: float
    checks: dict[str, Any] | None = None  # {restrictive, prevented, detail}: the two ticks
    rehearsal: dict[str, Any] | None = None  # {old_route, new_route, source}: polylines
    rehearsal_s: float = REHEARSAL_S

    def rehearsal_state(self) -> dict[str, Any] | None:
        """The twin's playback: ``ready`` before the announcement, ``playing`` with
        ``progress`` 0..1 after it, ``done`` at the end. Data for the stage view's
        picture-in-picture only: nothing here drives the virtual or the real arm."""
        if self.rehearsal is None:
            return None
        out = dict(self.rehearsal)
        if self.announced_at <= 0:
            out.update(state="ready", progress=0.0)
        else:
            frac = (now() - self.announced_at) / max(0.1, self.rehearsal_s)
            out.update(
                state="done" if frac >= 1.0 else "playing",
                progress=round(min(1.0, max(0.0, frac)), 3),
            )
        out["note"] = ("twin only" + ("" if out.get("source", {}).get("measured")
                                      else " · placeholder routes, unmeasured"))
        return out

    def as_dict(self) -> dict[str, Any]:
        out = {
            "rule": asdict(self.rule),
            "incident_id": self.incident_id,
            "say": self.say,
            "expires_in_s": round(max(0.0, self.expires_at - now()), 1),
            "checks": self.checks,
            "rehearsal": self.rehearsal_state(),
        }
        if self.checks is not None:
            out["verifier"] = {
                "removes options only": bool(self.checks.get("restrictive")),
                "incident prevented": bool(self.checks.get("prevented")),
            }
        return out


def build_muse(cfg: dict[str, Any]) -> MuseClient | None:
    """Muse Wonder client when MODEL_API_KEY is set, else None (explain falls back)."""
    inc = cfg.get("incident", {}) or {}
    client = MuseClient(
        {
            "base_url": cfg.get("compiler", {}).get("base_url", "https://api.meta.ai/v1"),
            "timeout_s": inc.get("timeout_s", 20.0),
            "sam_model": inc.get("sam_model"),
            "sam_timeout_s": inc.get("track_timeout_s", 4.0),
        }
    )
    return client if client.available else None


class Learner:
    def __init__(self, app: Armed) -> None:
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
        self.tools = self.cfg.get("tools", True) is not False
        self.inv_cfg = InvestigationConfig.from_cfg(self.cfg)
        self.proposal: RuleProposal | None = None
        self.incident: dict[str, Any] | None = None  # latest incident status
        self.investigation: list[dict[str, Any]] = []  # the feed of the latest investigation
        self.calls: list[dict[str, Any]] = []  # Meta API calls: {model, for, started_at_ms, ms?}
        self.applied: list[dict[str, Any]] = []  # rules applied to the latest shaped carry
        self._queue: queue.Queue[IncidentTrigger] = queue.Queue()
        self._active: dict[str, Any] | None = None
        self._last_held: tuple[float, dict[str, Any]] | None = None
        self._emg_seen = float("-inf")
        self._running = False
        self.error: str | None = None
        # Simulation only (armed.scenario): a rendered top-down view of the virtual table,
        # sampled into the clip when there is no real camera. Never set with a camera.
        self.virtual_camera: Callable[[], np.ndarray | None] | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        self._running = True
        threading.Thread(target=self._sample_loop, name="armed-incident-sample", daemon=True).start()
        threading.Thread(target=self._work_loop, name="armed-incident", daemon=True).start()

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
        c = self._carry(t)
        if c is None:
            return None
        tags = c.get("tags") or []
        name = c.get("object_name") or c.get("object_id")
        return f"{name} (tags: {', '.join(tags)})" if tags else name

    def _carry(self, t: float) -> dict[str, Any] | None:
        """The carry in the jaws within a second of ``t``: object, tags, route, pick slot."""
        last = self._last_held
        if last is None or not 0.0 <= t - last[0] <= 1.0:
            return None
        c = last[1]
        return {
            "object_id": c.get("object_id"),
            "object_name": c.get("object_name"),
            "tags": list(c.get("tags") or []),
            "route": c.get("route") or "direct",
            "slot_id": c.get("slot_id"),
        }

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
        burst = trig.source == "burst"
        wait = max(after, BURST_FOLLOW_S) if burst else after
        delay = trig.t + wait - now()
        if delay > 0:
            time.sleep(delay)  # the sampler keeps recording the after-window meanwhile
        # A burst stop counts as a flinch only if a brace or open hand followed it.
        followed = burst and self._flinch_followed(trig.t)
        inc: Incident | None
        try:
            trigger: dict[str, Any] = {
                "source": trig.source, "t": trig.t, "onset_t": trig.onset_t
            }
            if burst:
                trigger["possibly_voluntary"] = True
                trigger["followed_by_flinch"] = followed
            extra = {
                "trigger": trigger,
                "provenance": self._provenance(),
                "carry": self._carry(trig.t),
            }
            inc = self.buffer.save(
                self.root, self._objects(), self._held(trig.t), reset=True, extra=extra
            )
        except Exception as exc:
            self.buffer.reset()
            self.log.emit("incident.error", detail=f"incident save failed: {exc}")
            inc = None
        checks: dict[str, Any] | None = None
        investigation: dict[str, Any] | None = None
        if inc is not None:
            self._status(id=inc.id, state="explaining", path=str(inc.path))
            self.log.emit("incident.saved", incident_id=inc.id, path=str(inc.path), held=inc.held)
            exp, checks, investigation = self._investigate(inc)
            if exp is None:
                exp = explain(inc, self.muse)  # the one-shot path, then its own fallback
        else:
            exp = fallback_explanation()
        if burst and not followed:
            proposal, problems = None, [NO_FOLLOW]  # explained, never proposed
        else:
            proposal, problems = self._propose(exp, inc, checks)
        say = exp.say if proposal is not None or exp.source == "fallback" else FALLBACK_SAY
        if inc is not None and inc.path is not None:
            try:
                (Path(inc.path) / "explanation.json").write_text(
                    json.dumps(
                        {**exp.to_dict(), "problems": problems,
                         "checks": proposal.checks if proposal else checks,
                         "investigation": investigation},
                        indent=2, default=str,
                    )
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
        self._status(
            state="explained", explanation=exp.to_dict(), say=say, problems=problems,
            investigation=investigation,
        )
        self.log.emit(
            "incident.explained",
            incident_id=inc.id if inc else None,
            source=trig.source,
            explanation=exp.to_dict(),
            say=say,
            proposal=proposal.as_dict() if proposal else None,
            problems=problems,
            investigation=investigation,
        )

    # ── the investigation (worker thread) ────────────────────────────────────
    def _investigate(
        self, inc: Incident
    ) -> tuple[Explanation | None, dict[str, Any] | None, dict[str, Any] | None]:
        """Wonder with tools. (explanation, checks, summary); explanation None means the
        loop was unavailable, failed or ran out of budget and the caller falls back to
        the one-shot path. The feed and the calls are published as they land."""
        client = self.muse
        if not self.tools or client is None or not hasattr(client, "respond"):
            return None, None, None
        with self.lock:
            self.investigation = []
            self.calls = []
            calls = self.calls

        def on_event(ev: dict[str, Any]) -> None:
            with self.lock:
                self.investigation.append(ev)
            self.log.emit(
                "incident.tool", incident_id=inc.id, tool=ev["tool"], args=ev.get("args"),
                result=ev["result_summary"], ms=ev["ms"], ok=ev.get("ok", True),
                ticks=ev.get("ticks"),
            )

        app = self.app
        vision = getattr(app, "vision", None)
        grasp_cfg = app.cfg.get("grasp", {}) or {}
        try:
            unit = float(grasp_cfg.get("table_unit_mm", 1000.0))
        except (TypeError, ValueError):
            unit = 1000.0
        result = investigate(
            inc, client, self.inv_cfg,
            check=lambda rule: self.check_rule(rule, inc),
            homography=getattr(vision, "homography", None) if vision is not None else None,
            capture_size=getattr(vision, "capture_size", None) if vision is not None else None,
            table_unit_mm=unit,
            fps=float(self.cfg.get("fps", 5.0)),
            on_event=on_event,
            calls=calls,
            zones=sorted(app.zone_names()),
            tags=sorted(app.catalog_tags()),
        )
        summary = {
            "status": result.status, "tool_calls": result.tool_calls, "turns": result.turns,
            "elapsed_s": result.elapsed_s, "rejected": len(result.rejected),
            "text": result.text[:300],
        }
        self.log.emit("incident.investigated", incident_id=inc.id, **summary)
        exp = result.explanation
        if exp is not None:
            label = getattr(client, "explanation_source", None)
            exp.source = label if isinstance(label, str) and label else "wonder"
        elif result.status in ("rejected", "no_proposal"):
            # Wonder had its say (twice, for a rejection): the fixed question, not a
            # second opinion from the one-shot path.
            exp = fallback_explanation()
        checks = result.checks
        if exp is None or exp.rule is None:
            found = self._route_rule(inc)
            if found is not None:
                exp, checks = found
                self.log.emit("incident.route_rule", incident_id=inc.id, rule=exp.rule)
        return exp, checks, summary

    def _route_rule(self, inc: Incident) -> tuple[Explanation, dict[str, Any]] | None:
        """When Wonder ends without a rule: a liquid frozen on a measured route that crosses
        an avoid zone gives "lift it higher over that zone", if the checker passes it (removes
        options only; incident prevented on replay). Geometry from the taught routes, not a guess."""
        carry = (inc.extra.get("carry") if inc is not None else None) or {}
        tags = list(carry.get("tags") or [])
        tag = next((t for t in ("liquid", "drink") if t in tags), None)
        routes = self.app.routes
        if tag is None or routes is None:
            return None
        for zone in sorted(self.app.zone_names()):
            rule = {"when_holding": [tag], "avoid": zone, "response": "lift_higher"}
            problems, checks = self.check_rule(rule, inc)
            if not problems and checks is not None:
                name = carry.get("object_name") or "the drink"
                say = (f"I carried the {name} over the {zone}. "
                       f"Keep liquids off the {zone} from now on?")
                return Explanation(zone, "over", tag, say, 0.6, rule, source="route"), checks
        return None

    def check_rule(
        self, rule: dict[str, Any], inc: Incident | None
    ) -> tuple[list[str], dict[str, Any] | None]:
        """``validate_rule`` and then the checker's two ticks for a proposed rule, against
        the incident's carry. (problems, checks): checks is None when validation failed
        (the checker has nothing to replay); problems is empty only when both ticks pass."""
        try:
            r = Rule(
                when_holding=list(rule["when_holding"]), avoid=rule["avoid"],
                response=rule["response"], approved=False,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return [str(exc)], None
        app = self.app
        problems = validate_rule(r, app.zone_names(), app.catalog_tags())
        if problems:
            return problems, None
        try:
            rules: list[Rule] = app.rules.list()
        except RulesUnavailable as exc:
            return [f"rules unavailable: {exc}"], None
        carry = (inc.extra.get("carry") if inc is not None else None) or {}
        objects_tags = {
            str(o["id"]): tags_for(str(o["id"]), app.routes, app.catalog)
            for o in app.cfg.get("objects", []) if o.get("id")
        }
        checks = check_proposal(
            r, rules, app._proof_routes(), app.executable_routes(carry.get("route")), objects_tags,
            list(carry.get("tags") or []), carry.get("slot_id"), carry.get("route"),
            slow_speed=float(self.rules_cfg.get("slow_down_speed", 0.5)),
            gentle_grip=float(self.rules_cfg.get("grip_gentler", 0.6)),
        )
        return checks.problems(), checks.as_dict()

    def _rehearsal(self, rule: Rule, inc: Incident | None) -> dict[str, Any] | None:
        """The twin's old and new polylines for the incident's pick slot."""
        carry = (inc.extra.get("carry") if inc is not None else None) or {}
        slot, old = carry.get("slot_id"), carry.get("route")
        if slot is None:
            return None
        app = self.app
        from armed.routing import plan_carry

        try:
            rules: Any = app.rules.list()
        except RulesUnavailable:
            rules = []
        shaped = plan_carry(
            list(carry.get("tags") or []), [*[x for x in rules if x.approved],
                                            replace(rule, approved=True)],
            app._proof_routes(), app.executable_routes(old), slot_id=slot,
        )
        new = shaped.route if shaped.problem is None else None
        return rehearsal_routes(app._proof_routes(), slot, old, new)

    def note_applied(self, applied: list[str], held_tags: list[str]) -> None:
        """Record why rules shaped the latest carry (``snapshot()["applied_rules"]``)."""
        try:
            rules = self.app.rules.list()
        except RulesUnavailable:
            rules = []
        detail = applied_detail(applied, rules, held_tags)
        with self.lock:
            self.applied = detail

    def _flinch_followed(self, t: float) -> bool:
        """A brace or open hand came within BURST_FOLLOW_S after ``t``. Unknown: no."""
        check = getattr(self.app, "vetoed_between", None)
        if check is None:
            return False
        try:
            return bool(check(t, t + BURST_FOLLOW_S))
        except Exception:  # noqa: BLE001  # cannot tell: no rule from it
            return False

    def _propose(
        self, exp: Explanation, inc: Incident | None, checks: dict[str, Any] | None = None
    ) -> tuple[RuleProposal | None, list[str]]:
        """A proposal from an explanation. ``checks`` are the ticks the tool loop already
        ran for this exact rule; the one-shot path runs them here. Either way a rule
        that fails validation or a tick is never proposed."""
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
        if checks is None or not (checks.get("restrictive") and checks.get("prevented")):
            problems, checks = self.check_rule(exp.rule, inc)
            if problems:
                return None, problems
        rehearsal = self._rehearsal(rule, inc)
        return RuleProposal(
            rule, rule.incident_id, exp.say, now(), now(), checks=checks, rehearsal=rehearsal,
            rehearsal_s=float(self.cfg.get("rehearsal_s", REHEARSAL_S)),
        ), []

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
        at least MIN_APPROVE_REACTION_S after the question, and not within the app's
        ``after_stop_s`` of a brace, open hand or burst stop (a startle is no answer).
        ``expected``/``expected_id`` pin the proposal the answer was given to."""
        if by not in APPROVERS:
            return False, f"{by} cannot approve a rule"
        onset = now() if onset is None else onset
        recent = getattr(self.app, "stopped_recently", None)
        if recent is None or recent(onset):  # cannot tell: fail closed
            self.log.emit("rule.approve_rejected", by=by, reason="too soon after a stop")
            return False, "too soon after a stop; answer again in a moment"
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
            feed = [dict(e) for e in self.investigation]
            calls = [dict(c) for c in self.calls]
            applied = [dict(a) for a in self.applied]
        return {
            "proposal": p.as_dict() if p else None,
            "incident": dict(inc) if inc else None,
            "investigation": feed,
            "calls": calls,
            "applied_rules": applied,
            "muse": self.muse is not None,
            "tools": self.tools and self.muse is not None and hasattr(self.muse, "respond"),
            "error": self.error,
        }
