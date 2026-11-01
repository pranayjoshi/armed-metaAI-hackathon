"""H2M runtime: sensors -> step contract -> safety gate -> confirmation -> arm.

Interaction rules:
- "stop" (voice, UI) or an open hand (EMG) freezes the arm immediately.
- A request the gate approves runs; one it asks about waits for a squeeze
  (EMG), "yes", or a click. A squeeze also sets the grip strength.
- An ambiguous target ("which one?") re-resolves live from pointing, or from
  a spoken name or a click on a candidate.
- A brace or open hand that stops a moving arm starts an incident; the learner
  explains it off the freeze path and holds a rule proposal until a squeeze,
  "yes" or a click approves it. Approved rules shape later carries (route,
  speed, grip) and can only restrict.
- Muse tool calls (h2m.embodied) go through the same contract, gate and
  confirmation. Only taught gestures near home with an empty gripper skip the
  confirmation; everything that carries or grasps waits for one. Stop, brace and
  open hand also cut speech off, after the arm stop.
- Spoken and typed confirmations pass the same reaction-time gate as a squeeze, and
  nothing heard while the robot speaks (or that repeats what it just said) confirms.
- Every cancel, stop, brace and open hand bumps a request epoch: a Muse reply or a
  confirmed plan that began before the latest bump is dropped, never run.
- place_near_user runs in two plans: carry to above the spot, then (after a fresh,
  unoccluded look at the spot) lower and release under a hand watchdog. Stage 1 is
  refused (and the reason spoken) unless stage 2 could run: the camera is known to see
  the spot past the arm and a hand tracker is running.
- With EMG configured, motion needs a live EMG stream (fresh samples, not calibrating).
- Disarm goes through the app and bumps the request epoch like a stop.
- Operator gripper release (flagged for safety review) is off unless
  ``safety.operator_release`` is true. Then it opens the gripper alone, only with the
  arm READY at a taught release-safe pose (never a handover pose), after a two-step
  confirm (``offer_release`` returns an id; ``release_gripper`` with that id commits,
  pinned to the arming, the pose, the request epoch and what is held). Brace, open hand,
  cancel, stop and the arm guards halt it.
"""

from __future__ import annotations

import hmac
import re
import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from h2m.compiler.grammar import Parsed, build_vocabulary, parse
from h2m.compiler.llm import build_llm
from h2m.compiler.resolve import compile_contract, resolve_choice
from h2m.contract import StepContract, Verb
from h2m.core import ROOT, Event, EventLog, now
from h2m.embodied import (
    Embodied,
    ToolResult,
    gesture_deviation,
    plan_gesture,
    plan_place_lower,
    plan_place_near_user,
)
from h2m.learning import Learner
from h2m.recorder import Recorder
from h2m.robot import ArmState, build_arm
from h2m.robot.skills import Plan, Pose, plan_home, plan_pick_place, plan_point
from h2m.routing import (
    RouteConfig,
    RoutesUnavailable,
    catalog_tags,
    load_routes,
    plan_carry,
    plan_handover,
    plan_held_move,
    tags_for,
)
from h2m.rules import RuleStore, RulesUnavailable
from h2m.safety import Decision, Verdict, decide
from h2m.sensors.emg import build_emg
from h2m.sensors.speech import build_speech
from h2m.sensors.vision import build_vision
from h2m.speech_out import SpeechOut
from h2m.world import (
    APPROACH,
    GripperState,
    GripperTracker,
    gripper_facts,
    pose_deviation,
    spot_is_clear,
)

MIN_CONFIRM_REACTION_S = 0.3
REJECT_RE = re.compile(r"^\s*(no|nope|no thanks|don'?t|do not)\b", re.I)
NO_VISION_MODEL = "I can't look closely right now; my vision model is offline."
TOOL_MOTION = frozenset({"pick_place", "place_near_user", "look_at", "gesture"})
HANDOVER_WATCH_STAGES = frozenset({"lower", "release"})  # the hand watchdog runs here
# Never release-safe for the operator release, whatever the config lists: the spot near
# the user is only let go over under the stage-2 hand watchdog.
HANDOVER_POSES = frozenset({"handover_above", "handover_release"})
RELEASE_DISABLED = (
    "the operator gripper release is off (set safety.operator_release: true in "
    "configs/h2m.yaml to enable it)"
)
BRING_TO_TRAY = (
    "bring it to the tray first: I only open my gripper alone over the tray or a table slot"
)


@dataclass
class Pending:
    parsed: Parsed | None  # None for a Muse tool call (no grammar re-resolution)
    contract: StepContract
    decision: Decision
    expires_at: float
    announced_at: float = field(default_factory=now)
    plan: Plan | None = None  # embodied plan to check against at execution (gesture / place)


@dataclass(frozen=True)
class ReleaseOffer:
    """An operator gripper release shown to the operator, waiting for the second step.
    The commit must match all of it: same id, in time, same arming, same pose, same
    request epoch (no cancel / stop / brace since) and the same gripper contents."""

    release_id: str
    pose: str
    joints: dict[str, float]
    arm_count: int
    epoch: int
    held: GripperState
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
        # The mic is told when the robot is talking (echo window); read lazily because
        # the voice is built below and may be swapped.
        self.speech = build_speech(
            cfg.get("speech", {}), self.log, self.on_utterance, is_speaking=self._speaking
        )
        self.llm = build_llm(cfg.get("compiler", {}))
        self.rules_cfg = cfg.get("rules", {}) or {}
        self.routes: RouteConfig | None = None
        self.routes_error: str | None = None
        try:
            self.routes = load_routes(
                ROOT / self.rules_cfg.get("routes_file", "configs/routes.yaml")
            )
        except RoutesUnavailable as exc:  # fail closed: tagged carries hold (see routing.py)
            self.routes_error = str(exc)
        self.rules = RuleStore(str(self.rules_cfg.get("user", "default")))
        self.gripper = GripperTracker()  # what is in the gripper, proven from plan events
        self.use_tools = bool(cfg.get("compiler", {}).get("tools", True))
        speech_cfg = cfg.get("speech", {}) or {}
        # Gestures load from the poses file; a malformed one raises here, like poses do.
        self.embodied = Embodied(
            self.arm.poses,
            cfg,
            self._world,
            scene_fn=self.visible_objects,
            run_plan=self._run_gesture,
            speech=SpeechOut(
                voice=speech_cfg.get("voice"),
                rate_wpm=speech_cfg.get("rate_wpm"),
                enabled=bool(speech_cfg.get("voice_out", True)),
            ),
            vision=self._scene_answer,
        )
        self.embodied.safety_cfg = self.safety_cfg  # one gate config for both paths
        self.pending: Pending | None = None
        self.last: dict[str, Any] | None = None
        self.reply = ""
        self._lock = threading.RLock()
        self._running = False
        self._in_flight = 0  # plans being confirmed/built/started but not yet MOVING
        # Bumped by cancel, stop, brace and open hand. Work that began at an older epoch
        # (a Muse reply, a confirmed plan, the handover's second stage) is dropped.
        self._epoch = 0
        self._tls = threading.local()  # .epoch: the epoch the current request began at
        # place_near_user stage 2 waiting for stage 1 to arrive: (contract, epoch, the
        # arm's arm_count at stage 1: any re-arming since, however it came, drops it)
        self._handover_job: tuple[StepContract, int, int] | None = None
        self._release_offer: ReleaseOffer | None = None  # operator release, step 1 done
        # Embodied gesture runs by contract id: (started, needs confirmation, reason).
        self._gesture_outcomes: dict[str, tuple[bool, bool, str]] = {}
        # Incident capture + rule proposals; it never subscribes to the log itself.
        self.learner = Learner(self)
        # The arm keeps its grip on a freeze unless the tracker proves the gripper empty.
        self.arm.gripper_empty = self._gripper_proven_empty
        if self.emg is not None:
            self.arm.guards.append(self._emg_problem)  # no motion without a live EMG stop
        # The stop handler subscribes before the Recorder so the freeze runs before
        # any disk I/O on the same event.
        self.log.subscribe(self._on_event)
        self.recorder = Recorder(self.log)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        self.arm.connect()
        for part in (self.emg, self.vision, self.speech):
            if part is not None:
                part.start()
        self._running = True
        self.learner.start()
        threading.Thread(target=self._loop, name="h2m-loop", daemon=True).start()
        self.log.emit(
            "h2m.started",
            arm=self.arm.backend.name,
            emg=self.emg.source.name if self.emg else "off",
            vision="camera" if self.vision else "virtual",
            speech="mic" if self.speech else "off",
            llm=bool(self.llm),
            muse_wonder=self.learner.muse is not None,
            routes_error=self.routes_error,
        )

    def shutdown(self) -> None:
        self._running = False
        self.learner.stop()
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
        world = {
            "arm_ready": self.arm.state is ArmState.READY,
            "slot_id": slot.slot_id if slot else None,
            "target_visible": contract.object_id in visible_ids,
        }
        held = self.gripper.state()
        # Proven-only: unknown gripper contents are neither empty nor holding.
        world.update(
            gripper_facts(
                held, self.arm.load, float(self.safety_cfg.get("gripper_empty_load_max", 0.15))
            )
        )
        world.update(self._emg_facts())
        if contract.verb is Verb.PICK_PLACE:
            problem = self._shape_carry(contract)
            world["route_clear"] = problem is None
            world["route_reason"] = problem
        elif contract.verb in (Verb.HOME, Verb.POINT) and world["gripper_empty"] is not True:
            # Moving with something (maybe) in the gripper is a carry: rules apply.
            problem = self._shape_held_move(contract, held)
            world["route_clear"] = problem is None
            world["route_reason"] = problem
        elif contract.verb is Verb.GESTURE:
            steps = self.embodied.gestures.get(contract.utterance)
            home = self.arm.poses.poses.get("home")
            world["gesture_max_dev_deg"] = (
                gesture_deviation(steps, home) if steps and home else None
            )
            world["gesture_start_dev_deg"] = (
                pose_deviation(self.arm.measured(), home.joints) if home else None
            )
        elif contract.verb is Verb.PLACE_NEAR_USER:
            problem = self._shape_handover(contract, held)
            world["route_clear"] = problem is None
            world["route_reason"] = problem
            world["spot_watchable"], world["watch_reason"] = self._spot_watchable()
            world["spot_clear"], world["spot_reason"] = self._spot_clear()
        return world

    def _emg_facts(self) -> dict[str, Any]:
        """``emg_required`` when EMG is configured, with the newest sample's age and
        whether a calibration is recording (brace / open hand are not detected then)."""
        if self.emg is None:
            return {}
        t = getattr(self.emg, "last_sample_t", None)
        age = now() - t if isinstance(t, (int, float)) and not isinstance(t, bool) else None
        # Unreadable counts as calibrating: the stop gestures are not proven live.
        calibrating = getattr(self.emg, "_calib", True) is not None
        return {"emg_required": True, "emg_age_s": age, "emg_calibrating": calibrating}

    def _emg_problem(self) -> str | None:
        """Arm guard (control thread, under the arm lock): attribute reads only."""
        facts = self._emg_facts()
        if facts.get("emg_calibrating") is not False:
            return "EMG is calibrating (brace and open hand are off)"
        age = facts.get("emg_age_s")
        max_age = float(self.safety_cfg.get("emg_max_age_ms", 300)) / 1000
        if not (isinstance(age, (int, float)) and 0 <= age < max_age):
            return "EMG stopped streaming (no fresh samples)"
        return None

    def _gripper_proven_empty(self) -> bool:
        """Tracker EMPTY and no load reading against it. Small lock, no I/O: the arm
        calls it on the freeze path."""
        load_max = float(self.safety_cfg.get("gripper_empty_load_max", 0.15))
        return gripper_facts(self.gripper.state(), self.arm.load, load_max)["gripper_empty"]

    def _speaking(self) -> bool:
        return self.embodied.speech.speaking()

    def _spot_clear(self) -> tuple[bool, str]:
        """No tracked hand near the marked spot, on a fresh pass. Fails closed with no
        camera, no hand tracker, a stale pass, no taught spot or the arm hiding it."""
        handover = self._proof_routes().handover if self.routes else None
        hands = getattr(self.vision, "hands_seen", None) if self.vision is not None else None
        stale_s = float(self.cfg.get("vision", {}).get("stale_ms", 800)) / 1000
        return spot_is_clear(
            hands,
            now(),
            stale_s,
            handover.spot if handover else None,
            handover.clear_radius if handover else 0.0,
            hidden=self._arm_hides_spot(),
        )

    def _spot_watchable(self) -> tuple[bool, str]:
        """Could stage 2 (lower + release under the hand watchdog) ever run? Needs the
        camera checked at teach time to see the spot past the arm, and a running hand
        tracker. Fails closed: missing or unreadable means not watchable."""
        handover = self.routes.handover if self.routes else None
        if handover is None:
            return False, "I have no taught path to the marked spot"
        if handover.camera_sees_past_arm is not True:
            return False, (
                "my camera isn't checked to see the spot past my arm, so I couldn't watch "
                "it while lowering"
            )
        vision = self.vision
        if vision is None or getattr(vision, "hands", None) is None:
            return False, "I have no hand tracking, so I couldn't watch the spot while lowering"
        return True, "spot watchable"

    def _arm_hides_spot(self) -> bool:
        """The arm is near the handover poses (between the scene camera and the spot),
        unless the camera was checked at teach time to see past it. Unknown = hidden."""
        handover = self.routes.handover if self.routes else None
        if handover is None:
            return True
        if handover.camera_sees_past_arm:
            return False
        tol = float(self.safety_cfg.get("handover_occlusion_deg", 30.0))
        measured = self.arm.measured()
        for name in ("handover_above", "handover_release"):
            pose = self.arm.poses.poses.get(name)
            if pose is None:
                return True
            dev = pose_deviation(measured, pose.joints)
            if dev is None or dev <= tol:
                return True
        return False

    def _proof_routes(self) -> RouteConfig | None:
        """Routes that may prove a rule is kept: placeholders are dropped on hardware."""
        if self.routes is None:
            return None
        return self.routes.for_proof(self.arm.backend.is_hardware)

    # ── rules ────────────────────────────────────────────────────────────────
    def zone_names(self) -> set[str]:
        return set(self.routes.zones) if self.routes else set()

    def catalog_tags(self) -> set[str]:
        return catalog_tags(self.routes, self.cfg.get("objects", []))

    def _shape_carry(self, contract: StepContract) -> str | None:
        """Apply matching approved rules to a carry: set route and applied_rules, and
        lower (never raise) speed and grip. Returns a reason to HOLD, or None.
        Idempotent, so the re-check at confirm time can run it again."""
        obj = self.catalog.get(contract.object_id or "", {})
        name = contract.object_name or obj.get("name") or "object"
        tags = tags_for(contract.object_id, self.routes, self.catalog)
        try:
            rules: Any = self.rules.list()
        except RulesUnavailable as exc:
            rules = exc
        slot = self.arm.slot_for(contract.table_xy) if contract.table_xy else None
        shaping = plan_carry(
            tags,
            rules,
            self._proof_routes(),
            self.arm.poses.routes.keys(),
            float(self.rules_cfg.get("slow_down_speed", 0.5)),
            float(self.rules_cfg.get("grip_gentler", 0.6)),
            name,
            slot_id=slot.slot_id if slot else None,
        )
        contract.route = shaping.route
        contract.applied_rules = list(shaping.applied)
        contract.speed = min(contract.speed, shaping.speed_cap)
        if shaping.grip_cap < 1.0:
            ceiling = float(obj.get("max_grip", 1.0)) * shaping.grip_cap
            effort = contract.grip_effort
            if effort is None:
                effort = float(self.safety_cfg.get("default_grip", 0.5))
            contract.grip_effort = min(effort, ceiling)
        return shaping.problem

    def _shape_held_move(self, contract: StepContract, held: GripperState) -> str | None:
        """home / point with a gripper not proven empty: rules shape it like a carry
        (speed caps; anything needing a route proof holds). Returns a reason to HOLD."""
        known = held.holding and held.object_id is not None
        obj = self.catalog.get(held.object_id or "", {}) if known else {}
        name = (obj.get("name") or held.object_id) if known else "what I may be holding"
        try:
            rules: Any = self.rules.list()
        except RulesUnavailable as exc:
            rules = exc
        where = "home" if contract.verb is Verb.HOME else f"to the {contract.object_name}"
        shaping = plan_held_move(
            tags_for(held.object_id, self.routes, self.catalog) if known else None,
            rules,
            self._proof_routes(),
            float(self.rules_cfg.get("slow_down_speed", 0.5)),
            str(name),
            where,
        )
        contract.applied_rules = list(shaping.applied)
        contract.speed = min(contract.speed, shaping.speed_cap)
        return shaping.problem

    def _shape_handover(
        self, contract: StepContract, held: GripperState, check_start: bool = True
    ) -> str | None:
        """place_near_user: the held object, rules and the taught handover path, from
        the taught start pose only (``check_start``; stage 2 starts above the spot).
        Returns a reason to HOLD, or None. Idempotent."""
        if not held.holding or held.object_id is None:
            return "I am not holding anything to place"
        obj = self.catalog.get(held.object_id, {})
        name = obj.get("name") or held.object_id
        contract.object_id, contract.object_name = held.object_id, name
        try:
            rules: Any = self.rules.list()
        except RulesUnavailable as exc:
            rules = exc
        shaping = plan_handover(
            tags_for(held.object_id, self.routes, self.catalog),
            rules,
            self._proof_routes(),
            float(self.rules_cfg.get("slow_down_speed", 0.5)),
            float(self.rules_cfg.get("grip_gentler", 0.6)),
            name,
        )
        contract.route = shaping.route
        contract.applied_rules = list(shaping.applied)
        contract.speed = min(contract.speed, shaping.speed_cap)
        if shaping.problem is not None:
            return shaping.problem
        # The path is only taught from one pose: from anywhere else it is unproven.
        handover = self.routes.handover if self.routes else None
        if handover is None:
            return "I have no taught path to the marked spot."
        if not check_start:
            return None
        start = handover.start_pose
        try:
            start_joints = self.arm.poses.pose(start).joints
        except (KeyError, AttributeError, ValueError):
            return f"the handover start pose {start} is not defined"
        dev = pose_deviation(self.arm.measured(), start_joints)
        tol = float(self.safety_cfg.get("handover_start_tol_deg", 5.0))
        if dev is None or not dev <= tol:
            return f"I only take things to the spot from {start}. Ask me to go {start} first."
        return None

    # ── inputs ───────────────────────────────────────────────────────────────
    def on_utterance(
        self, text: str, meta: dict[str, Any] | None = None, source: str = "voice"
    ) -> None:
        t0 = now()
        meta = meta or {}
        parsed = parse(text, self.vocab)
        self.log.emit("speech.utterance", text=text, source=source, verb=str(parsed.verb), **meta)
        if parsed.verb is Verb.STOP:
            self.stop(f"{source}: {text}")
            return
        if parsed.verb is Verb.CANCEL:
            self.cancel(f"{source}: {text}")
            return
        # Past this point the words could start or confirm something. Nothing heard while
        # the robot was talking counts, nor anything repeating what it just said.
        # Fails closed: only an explicit False echo tag counts as not echo.
        if source == "voice" and (meta.get("echo") is not False or self._is_echo(text)):
            self.log.emit("speech.echo_dropped", text=text, source=source)
            return
        # Answer onset: back out ASR time and the utterance length (errs early, which
        # only makes the reaction-time gate stricter). Typed text: when it arrived.
        onset = t0 - float(meta.get("asr_ms", 0)) / 1000 - float(meta.get("audio_s", 0))
        with self._lock:
            pending = self.pending
            proposal = self.learner.proposal
        if parsed.verb is Verb.CONFIRM:
            if pending is not None and proposal is not None:
                self.log.emit("speech.ambiguous", reason="a request and a rule are both waiting")
                self.reply = "Do you mean the request or the rule? Click the one you mean."
            elif proposal is not None:
                ok, why = self.learner.approve(by=source, expected=proposal, onset=onset)
                self.reply = "Saved. I'll remember that." if ok else why
            elif pending is not None and onset < pending.announced_at + MIN_CONFIRM_REACTION_S:
                # Began before (or within a reaction time of) the question: not an answer.
                self.log.emit("speech.confirm_rejected", reason="too soon after request")
                self.reply = "I didn't catch that as an answer. Say yes again to confirm."
            else:
                self.confirm(by=source, expected=pending)
            return
        if proposal is not None and parsed.verb is Verb.UNKNOWN and REJECT_RE.match(text):
            self.learner.reject(by=source, expected_id=proposal.rule.id, reason=text)
            self.reply = "Okay, I won't save that."
            return
        if pending and pending.decision.clarify and parsed.object_id:
            self.choose(parsed.object_id, by=source)
            return
        uses_llm = bool(self.llm) and (
            parsed.verb is Verb.UNKNOWN
            or (parsed.reference == "none" and parsed.verb in (Verb.PICK_PLACE, Verb.POINT))
        )
        epoch = self._epoch  # the request starts now: a later stop or cancel drops it
        if uses_llm and source == "voice":
            # Network (and a possible wait for a gesture) must never sit on the speech
            # thread: the next utterance may be "stop".
            threading.Thread(
                target=self._compile_and_run,
                args=(text, parsed, t0, True, epoch),
                name="h2m-muse",
                daemon=True,
            ).start()
            return
        self._compile_and_run(text, parsed, t0, uses_llm, epoch)

    def _is_echo(self, text: str) -> bool:
        try:
            return bool(self.embodied.speech.is_echo(text))
        except Exception:  # noqa: BLE001  # cannot tell: let the other gates decide
            return False

    def _stale(self, epoch: int | None) -> bool:
        """A cancel, stop, brace or open hand happened since ``epoch``."""
        return epoch is not None and epoch != self._epoch

    def _compile_and_run(
        self, text: str, parsed: Parsed, t0: float, uses_llm: bool, epoch: int | None = None
    ) -> None:
        epoch = self._epoch if epoch is None else epoch
        self._tls.epoch = epoch  # _set_pending / _execute drop work from an older epoch
        try:
            self._compile_and_run_at(text, parsed, t0, uses_llm, epoch)
        finally:
            self._tls.epoch = None

    def _compile_and_run_at(
        self, text: str, parsed: Parsed, t0: float, uses_llm: bool, epoch: int
    ) -> None:
        # In flight from here (compile, gate, auto-approve) so a brace while the
        # request is being compiled latches a stop before the plan can start.
        with self._plan_in_flight():
            compiled_by = "grammar"
            if uses_llm and self.llm:
                jpeg = self.vision.scene.jpeg() if self.vision else None
                if self.use_tools:
                    reply = self.llm.call_tools(
                        text, self.visible_objects(), self.pointing(), self.embodied.tools, jpeg
                    )
                    self.log.emit(
                        "compiler.llm",
                        ok=reply is not None,
                        ms=self.llm.last_ms,
                        error=self.llm.last_error,
                        tools=[c[0] for c in reply.calls] if reply else None,
                    )
                    if self._stale(epoch):
                        self.log.emit("compiler.dropped", reason="cancelled or stopped meanwhile")
                        return
                    if reply is not None and (reply.calls or reply.text):
                        self.run_tool_calls(reply.calls, reply.text)
                        return
                    # No usable reply: the grammar's parse goes to the gate (holds if unknown).
                    self._evaluate(parsed, compiled_by, t0)
                    return
                result = self.llm.interpret(text, self.visible_objects(), self.pointing(), jpeg)
                self.log.emit(
                    "compiler.llm",
                    ok=result is not None,
                    ms=self.llm.last_ms,
                    error=self.llm.last_error,
                )
                if self._stale(epoch):
                    self.log.emit("compiler.dropped", reason="cancelled or stopped meanwhile")
                    return
                if result is not None:
                    parsed, self.reply = result
                    compiled_by = "muse"
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
        if decision.checks.get("route_clear") is False:
            self.reply = decision.reason  # spoken: why it won't carry, and what to do
        self._set_pending(parsed, contract, decision)
        if decision.verdict is Verdict.APPROVE:
            self._execute(contract, decision, confirmed_by="auto")

    def _set_pending(
        self,
        parsed: Parsed | None,
        contract: StepContract,
        decision: Decision,
        plan: Plan | None = None,
    ) -> None:
        """Replace the pending request; an ASK waits for a confirmation. A request from
        an older epoch (stopped or cancelled while it compiled) is not queued."""
        dropped = None
        with self._lock:  # one section: a cancel cannot land between check and set
            stale = self._stale(getattr(self._tls, "epoch", None))
            if not stale:
                self.pending = None
            if not stale and decision.verdict is Verdict.ASK:
                timeout = float(self.safety_cfg.get("confirm_timeout_s", 8.0))
                self.pending = Pending(parsed, contract, decision, now() + timeout, plan=plan)
                # A new question supersedes an unanswered rule: "yes" must be unambiguous.
                dropped = self.learner.pop_proposal()
        if stale:
            self.log.emit("intent.dropped", contract_id=contract.id, reason="cancelled meanwhile")
            return
        if dropped is not None:
            self.log.emit(
                "rule.rejected", rule_id=dropped.rule.id, by="system", reason="new request"
            )

    # ── embodied (Muse tool calls) ───────────────────────────────────────────
    def run_tool_calls(self, calls: list[tuple[str, Any]], text: str = "") -> list[ToolResult]:
        """Route tool calls through Embodied (schema check + gate). At most one gesture
        runs and at most one request waits per reply; a gesture after a waiting request
        is skipped so it cannot hold the arm busy when the confirmation comes. A request
        after a gesture is gated once the gesture is over (blocking: utterance thread)."""
        results: list[ToolResult] = []
        ran = queued = False
        for name, args in calls:
            if name in TOOL_MOTION and (queued or (ran and name == "gesture")):
                self.log.emit("embodied.skipped", tool=name, reason="one motion per reply")
                continue
            if name in TOOL_MOTION and ran:
                self._wait_while_moving(float(self.safety_cfg.get("gesture_wait_s", 8.0)))
            if name in TOOL_MOTION:
                with self._plan_in_flight():  # a brace while gating latches a stop
                    result = self.embodied.handle_tool_call(name, args)
            else:
                result = self.embodied.handle_tool_call(name, args)
            ran_now, queued_now = self._after_tool(result)
            ran, queued = ran or ran_now, queued or queued_now
            results.append(result)
        if not calls and text:
            result = self.embodied.handle_tool_call("say", {"text": text[:280]})
            self._after_tool(result)
            results.append(result)
        return results

    def _wait_while_moving(self, timeout_s: float) -> None:
        """Poll (no lock held) until the arm leaves MOVING or the timeout passes. The
        gate then sees the real state: still moving or stopped both hold."""
        deadline = now() + timeout_s
        while self.arm.state is ArmState.MOVING and now() < deadline:
            time.sleep(0.05)

    def _after_tool(self, result: ToolResult) -> tuple[bool, bool]:
        """Publish a tool result and queue anything that needs a confirmation.
        Returns (a gesture was started, a request is now waiting)."""
        self.log.emit("embodied.tool", **result.as_dict())
        contract, decision = result.contract, result.decision
        if not result.ok or contract is None or decision is None:
            self.reply = result.error or "I couldn't do that."
            return False, False
        gesture_ran = False
        if contract.verb is Verb.GESTURE and decision.verdict is Verdict.APPROVE:
            with self._lock:
                outcome = self._gesture_outcomes.pop(contract.id, None)
            # No recorded run (or not proven empty) falls through to a confirmation.
            started, needs_confirm, why = outcome or (False, True, "")
            gesture_ran = started
            if not started and not needs_confirm:
                self._publish(contract, decision, tool=result.tool)
                self.reply = why  # blocked at execution (e.g. a stop latched meanwhile)
                return False, False
        if decision.verdict is Verdict.APPROVE and contract.needs_motion and not gesture_ran:
            # Model-chosen motion other than a gesture always confirms (restrict-only).
            decision = Decision(
                Verdict.ASK,
                "squeeze, say yes, or click to confirm",
                dict(decision.checks),
                slot_id=decision.slot_id,
            )
        self._publish(contract, decision, tool=result.tool)
        if contract.verb is Verb.SAY:
            if result.spoken:
                self.reply = contract.utterance
            return False, False
        if gesture_ran:
            return result.executed, False  # the one reviewed exemption: already run
        if decision.verdict is Verdict.ASK and contract.needs_motion:
            self._set_pending(None, contract, decision, plan=result.plan)
            self.reply = decision.reason
            return False, True
        self.reply = decision.reason
        if contract.verb is Verb.PLACE_NEAR_USER:
            self._say(f"I won't take it to the spot: {decision.reason}.")
        return False, False

    def _say(self, text: str) -> None:
        """Speak ``text`` through the say tool (gated, speech only) and show it."""
        result = self.embodied.handle_tool_call("say", {"text": text[:280]})
        self.log.emit("embodied.tool", **result.as_dict())
        self.reply = text

    def _run_gesture(self, contract: StepContract, plan: Plan) -> None:
        """Embodied's run_plan: called only on an APPROVE verdict for a gesture. The gate
        runs again here on this app's world, then the normal executor bookkeeping. An
        unconfirmed run needs the whole exemption (gripper proven empty), even when
        ``always_confirm`` is off and the gate's fall-through would approve."""
        decision = decide(contract, self._world(contract), self.safety_cfg)
        exempt = decision.verdict is Verdict.APPROVE and all(
            decision.checks.get(k) is True
            for k in ("gripper_empty", "gesture_in_envelope", "gesture_start_near_home")
        )
        if contract.verb is not Verb.GESTURE or not exempt:
            needs_confirm = decision.verdict is Verdict.APPROVE  # not proven empty: confirm
            with self._lock:
                self._gesture_outcomes[contract.id] = (False, needs_confirm, decision.reason)
            if not needs_confirm:
                self.log.emit("intent.blocked", reason=decision.reason, contract_id=contract.id)
            return
        ok, why = self._execute(contract, decision, confirmed_by="auto", plan=plan)
        with self._lock:
            self._gesture_outcomes[contract.id] = (ok, False, why)

    def _scene_answer(self, question: str, frame: Any) -> str:
        if self.llm is None:
            return NO_VISION_MODEL
        return self.llm.describe(question, frame)

    def ask(self, question: str) -> ToolResult:
        """Scene question: Muse answers from the current frame (overhead, else wrist) and
        the reply is spoken through the say tool. Blocking (network): never call it on
        the EMG or event thread."""
        frame = None
        if self.vision is not None:
            frame = self.vision.scene.jpeg()
            if frame is None and self.vision.wrist is not None:
                frame = self.vision.wrist.jpeg()
        result = self.embodied.answer_scene(question, frame)
        self._after_tool(result)
        return result

    def _publish(self, contract: StepContract, decision: Decision, **extra: Any) -> None:
        self.last = {"contract": contract.as_dict(), "decision": decision.as_dict()}
        self.log.emit("intent", contract=contract.as_dict(), decision=decision.as_dict(), **extra)

    # ── operator / gesture actions ───────────────────────────────────────────
    def confirm(
        self, by: str = "ui", effort: float | None = None, expected: Pending | None = None
    ) -> tuple[bool, str]:
        """Confirm the pending request. ``expected`` pins the exact request the caller
        gated on (e.g. the squeeze reaction-time check); if it was replaced since,
        nothing is confirmed."""
        with self._lock:
            pending = self.pending
            if pending is None:
                return False, "nothing to confirm"
            if expected is not None and pending is not expected:
                return False, "request changed"
            if pending.decision.clarify:
                return False, "choose which object first"
            self.pending = None
            self._in_flight += 1  # same critical section as the pop: no gap for a brace
            self._tls.confirm_epoch = self._epoch  # a cancel after this pop drops the plan
        try:
            return self._confirm(pending, by, effort)
        finally:
            with self._lock:
                self._in_flight -= 1

    def _confirm(self, pending: Pending, by: str, effort: float | None) -> tuple[bool, str]:
        epoch = getattr(self._tls, "confirm_epoch", None)
        contract = pending.contract
        if effort is not None:
            contract.grip_effort = effort
            contract.evidence["emg_grip"] = round(effort, 3)
        contract.evidence[f"confirmed_{by}"] = 1.0
        # Re-check the world: things may have changed while we waited. ``confirmed``
        # is a world fact only this path sets: a person answered this request.
        world = {**self._world(contract), "confirmed": True}
        decision = decide(
            contract, world, {**self.safety_cfg, "always_confirm": False, "auto_approve": 0.0}
        )
        self._publish(contract, decision, confirmed_by=by)
        if decision.verdict is not Verdict.APPROVE:
            return False, decision.reason
        return self._execute(contract, decision, confirmed_by=by, plan=pending.plan, epoch=epoch)

    def choose(self, object_id: str, by: str = "ui") -> tuple[bool, str]:
        with self._lock:
            pending = self.pending
        if pending is None:
            return False, "nothing pending"
        if not pending.decision.clarify:  # a click on an object is not a confirmation
            return False, "nothing to choose; confirm or cancel instead"
        contract = resolve_choice(pending.contract, object_id, self.visible_objects())
        decision = decide(contract, self._world(contract), self.safety_cfg)
        approve = decision.verdict is Verdict.APPROVE
        with self._lock:
            if self.pending is not pending:  # cancelled/replaced while resolving
                return False, "request changed"
            self.pending = (
                Pending(pending.parsed, contract, decision, pending.expires_at)
                if decision.verdict is Verdict.ASK
                else None
            )
            if approve:  # same critical section as the pop: no gap for a brace (R1)
                self._in_flight += 1
            epoch = self._epoch
        try:
            self._publish(contract, decision, chosen_by=by)
            if approve:
                return self._execute(contract, decision, confirmed_by=by, epoch=epoch)
            return True, decision.reason
        finally:
            if approve:
                with self._lock:
                    self._in_flight -= 1

    def cancel(self, reason: str = "cancel") -> None:
        with self._lock:
            had = self.pending is not None
            self.pending = None
            self._release_offer = None
            self._epoch += 1  # drops a Muse reply or confirmed plan not yet started
        self.arm.stop_if_active(reason)  # atomic check-and-stop of a moving arm
        self._silence()
        self.log.emit("intent.cancelled", reason=reason, had_pending=had)
        self.learner.reject(by="cancel", reason=reason)  # "never mind" also declines a rule

    def stop(self, reason: str = "stop") -> None:
        with self._lock:
            self.pending = None
            self._handover_job = None  # stage 2 never follows a stop
            self._release_offer = None
            self._epoch += 1
        self.arm.stop(reason)
        self._silence()  # after the arm stop: speech can never delay the freeze

    def disarm(self, reason: str = "disarm") -> None:
        """Operator disarm. Bumps the request epoch like a stop, so a request or the
        handover's stage 2 that began before it is dropped even after arming again."""
        with self._lock:
            had = self.pending is not None
            self.pending = None
            self._handover_job = None
            self._release_offer = None
            self._epoch += 1
        self.arm.disarm()
        self._silence()
        if had:
            self.log.emit("intent.cancelled", reason=reason, had_pending=True)

    # ── operator gripper release (SAFETY REVIEW: actuation outside a plan) ────
    def operator_release_enabled(self) -> bool:
        """``safety.operator_release`` is literally true (off by default)."""
        return self.safety_cfg.get("operator_release") is True

    def _release_state_problem(self) -> str | None:
        """Why no release may start now (app state only), or None."""
        if self._in_flight > 0 or self._handover_job is not None:
            return "a request is in flight; stop first"
        state = self.arm.state
        if state is not ArmState.READY:
            how = "reset and arm me first" if state is ArmState.STOPPED else "arm me first"
            return f"I only release the gripper when ready, not {state}: {how}"
        if self.arm.releasing:
            return "the gripper is already being released"
        return None

    def _release_pose(self) -> tuple[str | None, dict[str, float] | None, str]:
        """The allowlisted release-safe taught pose (``operator_release_poses``) the arm
        is at now, within ``operator_release_tol_deg``: (name, joints, ""). Handover poses
        never count, and being at one refuses. On hardware only taught poses count.
        Fails closed: unreadable joints or no match is (None, None, why)."""
        poses = self.arm.poses
        tol = float(self.safety_cfg.get("operator_release_tol_deg", 5.0))
        measured = self.arm.measured()
        for name in sorted(HANDOVER_POSES):
            pose = poses.poses.get(name)
            if pose is None:
                continue
            dev = pose_deviation(measured, pose.joints)
            if dev is None:
                return None, None, "I can't read my joints, so I won't open the gripper"
            if dev <= tol:
                return None, None, f"I'm at the handover spot ({name}); {BRING_TO_TRAY}"
        raw = self.safety_cfg.get("operator_release_poses") or []
        names: list[str] = []
        for entry in raw if isinstance(raw, (list, tuple)) else []:
            if entry == "*.grasp":
                names.extend(f"{slot_id}.grasp" for slot_id in poses.slots)
            elif isinstance(entry, str):
                names.append(entry)
        best: tuple[float, str, dict[str, float]] | None = None
        for name in names:
            if name in HANDOVER_POSES:
                continue
            try:
                pose = poses.pose(name)
            except (KeyError, AttributeError, ValueError):
                continue
            if not isinstance(pose, Pose) or (self.arm.backend.is_hardware and not pose.taught):
                continue
            dev = pose_deviation(measured, pose.joints)
            if dev is not None and dev <= tol and (best is None or dev < best[0]):
                best = (dev, name, dict(pose.joints))
        if best is None:
            return None, None, BRING_TO_TRAY
        return best[1], best[2], ""

    def _held_summary(self, held: GripperState) -> dict[str, Any]:
        name = None
        if held.holding and held.object_id:
            name = self.catalog.get(held.object_id, {}).get("name") or held.object_id
        return {"status": held.status, "object_id": held.object_id, "name": name, "why": held.why}

    def offer_release(self, by: str = "ui") -> tuple[bool, str, dict[str, Any] | None]:
        """Step 1 of the operator gripper release: check it could run now and return a
        release id with what is held and where. Nothing moves. Only one offer is open;
        a new one replaces it, and cancel / stop / brace / open hand / disarm void it."""
        if not self.operator_release_enabled():
            return False, RELEASE_DISABLED, None
        with self._lock:
            problem = self._release_state_problem()
            arm_count, epoch = self.arm.arm_count, self._epoch
        if problem is None:
            problem = self.arm.guard_problem()
        pose = joints = None
        if problem is None:
            pose, joints, why = self._release_pose()
            problem = why or None
        if problem is not None or pose is None or joints is None:
            self.log.emit("operator.release_refused", step="offer", detail=problem, by=by)
            return False, str(problem), None
        held = self.gripper.state()
        window = float(self.safety_cfg.get("operator_release_confirm_s", 10.0))
        offer = ReleaseOffer(
            secrets.token_urlsafe(12), pose, joints, arm_count, epoch, held, now() + window
        )
        with self._lock:
            if self._epoch != epoch:  # a cancel or stop landed while checking
                return False, "cancelled meanwhile", None
            self._release_offer = offer
        summary = self._held_summary(held)
        what = (
            f"the {summary['name']}"
            if summary["name"]
            else ("nothing, as far as I know" if held.empty else "whatever I may be holding")
        )
        detail = (
            f"This opens my gripper at {pose}: {what} will drop. Send this release id "
            f"again within {window:.0f} s to confirm."
        )
        self.log.emit("operator.release_offered", pose=pose, held=summary, by=by)
        info = {
            "release_id": offer.release_id,
            "pose": pose,
            "holding": summary,
            "expires_in_s": round(window, 1),
        }
        return True, detail, info

    def release_gripper(self, release_id: Any, by: str = "ui") -> tuple[bool, str]:
        """Step 2: commit the open offer with its ``release_id``. Single use: any attempt
        consumes the offer. Refused unless it is in time and nothing changed since the
        offer (arming, epoch, gripper contents; the arm re-checks READY, the arming, the
        guards and the pose under its lock). Opens the gripper only. The tracker is
        UNKNOWN before the arm is told and EMPTY once the arm reports it open."""
        if not self.operator_release_enabled():
            return False, RELEASE_DISABLED
        with self._lock:
            offer, self._release_offer = self._release_offer, None
            problem = self._release_commit_problem(offer, release_id)
            if problem is None:
                self.pending = None  # anything asked about the old gripper contents is void
                self._epoch += 1
                epoch = self._epoch
                # In flight from here: a brace before the arm call stops the READY arm, so
                # the release cannot start after it (same section as the checks).
                self._in_flight += 1
                prior, marked = self.gripper.releasing(by)  # before the arm is told (H2)
        if problem is not None or offer is None:
            self.log.emit("operator.release_refused", step="commit", detail=problem, by=by)
            return False, str(problem)
        try:
            ok, why = self.arm.release_gripper(
                offer.arm_count,
                offer.joints,
                float(self.safety_cfg.get("operator_release_tol_deg", 5.0)),
                still_wanted=lambda: not self._stale(epoch),
            )
        finally:
            with self._lock:
                self._in_flight -= 1
        if not ok:
            self.gripper.restore(prior, marked)  # the gripper never moved
        self.log.emit("operator.release_gripper", ok=ok, detail=why, by=by, pose=offer.pose)
        return ok, why

    def _release_commit_problem(self, offer: ReleaseOffer | None, release_id: Any) -> str | None:
        """Under the app lock: why the offer cannot be committed, or None."""
        if offer is None:
            return "nothing to release: ask for a release first"
        if not isinstance(release_id, str) or not hmac.compare_digest(
            release_id.encode(), offer.release_id.encode()
        ):
            return "that release id is not the open offer; ask again"
        if now() > offer.expires_at:
            return "the release offer expired; ask again"
        if offer.epoch != self._epoch:
            return "something was cancelled or stopped since the offer; ask again"
        if offer.arm_count != self.arm.arm_count:
            return "the arm was re-armed since the offer; ask again"
        if self.gripper.state() != offer.held:
            return "what I hold changed since the offer; ask again"
        return self._release_state_problem()

    def _silence(self) -> None:
        """Cut speech off. Never raises; only ever called after the arm stop."""
        try:
            self.embodied.cutoff()
        except Exception:  # noqa: BLE001  # speech must never break a stop path
            pass

    def run_typed(self, text: str) -> None:
        threading.Thread(target=self.on_utterance, args=(text, {}, "typed"), daemon=True).start()

    # ── execution ────────────────────────────────────────────────────────────
    @contextmanager
    def _plan_in_flight(self) -> Iterator[None]:
        """Mark a plan as in flight: it counts as moving for a brace/stop even though
        the arm is not yet in MOVING (the plan is still being built or started)."""
        with self._lock:
            self._in_flight += 1
        try:
            yield
        finally:
            with self._lock:
                self._in_flight -= 1

    def _execute(
        self,
        contract: StepContract,
        decision: Decision,
        confirmed_by: str,
        plan: Plan | None = None,
        epoch: int | None = None,
    ) -> tuple[bool, str]:
        """``plan`` (gesture / place_near_user from Embodied) is never run as given: the
        plan is rebuilt here from the taught poses and must match it exactly. ``epoch``
        (default: the current request's) must still be current when the arm starts."""
        if epoch is None:
            epoch = getattr(self._tls, "epoch", None)
        with self._plan_in_flight():
            return self._execute_plan(contract, decision, confirmed_by, plan, epoch)

    def _embodied_plan(
        self, contract: StepContract, decision: Decision, given: Plan | None
    ) -> tuple[Plan | None, str]:
        arm = self.arm
        if decision.verdict is not Verdict.APPROVE:
            return None, decision.reason
        if contract.verb is Verb.GESTURE:
            steps = self.embodied.gestures.get(contract.utterance)
            if not steps or "home" not in arm.poses.poses:
                return None, f"gesture {contract.utterance!r} is not taught"
            plan = plan_gesture(contract.utterance, steps, arm.poses.pose("home"))
        else:
            checks = decision.checks
            proven = ("route_clear", "spot_watchable", "spot_clear", "holding")
            if not all(checks.get(k) is True for k in proven):
                return None, "handover not checked"
            try:
                plan = plan_place_near_user(arm.poses, float(arm.settings.gripper_open))
            except KeyError:
                return None, "the handover spot is not defined"
        if given is not None and given != plan:
            return None, "plan does not match the taught poses"
        return plan, ""

    def _execute_plan(
        self,
        contract: StepContract,
        decision: Decision,
        confirmed_by: str,
        given: Plan | None = None,
        epoch: int | None = None,
    ) -> tuple[bool, str]:
        arm = self.arm
        meta: dict[str, Any] = {
            "contract_id": contract.id,
            "object_id": contract.object_id,
            "verb": str(contract.verb),
            "confirmed_by": confirmed_by,
        }
        grip = None
        via: tuple = ()
        # home / point only command the gripper when it is proven empty (it may hold
        # something otherwise), and then only on a decision that checked the rules.
        empty = self._gripper_proven_empty()
        if contract.verb in (Verb.HOME, Verb.POINT) and not empty:
            if decision.checks.get("route_clear") is not True:
                return False, "moving with a held object was not checked"
        if contract.verb is Verb.HOME:
            plan = plan_home(arm.poses, gripper=empty)
        elif contract.verb in (Verb.GESTURE, Verb.PLACE_NEAR_USER):
            built, why = self._embodied_plan(contract, decision, given)
            if built is None:
                self.log.emit("intent.blocked", reason=why, contract_id=contract.id)
                return False, why
            plan = built
            if contract.verb is Verb.PLACE_NEAR_USER:
                meta["route"] = contract.route
                meta["applied_rules"] = list(contract.applied_rules)
                meta["phase"] = APPROACH  # stops above the spot; lowering is stage 2
        elif contract.verb not in (Verb.POINT, Verb.PICK_PLACE):
            return False, f"{contract.verb} is not a motion"
        else:
            slot = arm.poses.slots.get(decision.slot_id or "")
            if slot is None:
                return False, "no reachable slot"
            if contract.verb is Verb.POINT:
                plan = plan_point(arm.poses, slot, gripper=empty)
            else:
                # Belt and braces: a carry only runs on a decision that proved its route.
                if decision.checks.get("route_clear") is not True:
                    return False, "route not checked"
                if contract.route is not None:
                    route = arm.poses.routes.get(contract.route)
                    if route is None:
                        return False, f"route {contract.route} has no taught waypoints"
                    via = route
                meta["route"] = contract.route
                meta["applied_rules"] = list(contract.applied_rules)
                effort = contract.grip_effort
                if effort is None:
                    effort = float(self.safety_cfg.get("default_grip", 0.5))
                ceiling = float(self.catalog.get(contract.object_id or "", {}).get("max_grip", 1.0))
                grip = arm.grip_for(effort, ceiling)
                meta["requested_effort"] = round(effort, 3)
                plan = plan_pick_place(
                    arm.poses, slot, arm.settings.gripper_open, grip.close_pct, via
                )
        tags = tags_for(contract.object_id, self.routes, self.catalog)
        self.learner.set_active({**meta, "object_name": contract.object_name, "tags": tags})
        if epoch is None:
            epoch = self._epoch
        if contract.verb is Verb.PLACE_NEAR_USER:
            with self._lock:  # set before the start: arrival may come on the next tick
                self._handover_job = (contract, epoch, arm.arm_count)
        ok, reason = arm.execute(
            plan, meta, grip, speed=contract.speed, still_wanted=lambda: not self._stale(epoch)
        )
        if not ok:
            if contract.verb is Verb.PLACE_NEAR_USER:
                with self._lock:
                    self._handover_job = None
            self.log.emit("intent.blocked", reason=reason, contract_id=contract.id)
        return ok, reason

    # ── place_near_user stage 2 ──────────────────────────────────────────────
    def _continue_handover(self, contract_id: Any, arrived_t: float) -> None:
        """Stage 1 arrived above the spot. Look again on a frame taken after arrival;
        lower and release only if that look proves the spot clear. Otherwise hold the
        object there and say why. Runs on its own thread (never the control thread)."""
        with self._lock:
            job, self._handover_job = self._handover_job, None
            if job is None or job[0].id != contract_id:
                return
            # In flight in the same section as the pop: a brace in between still stops.
            self._in_flight += 1
        contract, epoch, armed = job
        try:
            ok, why = self._handover_lower(contract, epoch, arrived_t, armed)
        finally:
            with self._lock:
                self._in_flight -= 1
        if not ok:
            self.log.emit("intent.blocked", reason=why, contract_id=contract.id)
            name = contract.object_name or "it"
            self._say(
                f"I'm holding the {name} above the spot: {why}. Say go home to bring it back."
            )

    def _handover_world(self, contract: StepContract, arrived_t: float) -> dict[str, Any]:
        """Gate facts for lowering onto the spot (the arm is above it now)."""
        held = self.gripper.state()
        world: dict[str, Any] = {"arm_ready": self.arm.state is ArmState.READY}
        load_max = float(self.safety_cfg.get("gripper_empty_load_max", 0.15))
        world.update(gripper_facts(held, self.arm.load, load_max))
        world.update(self._emg_facts())
        if held.object_id != contract.object_id:
            world["holding"] = False  # not the object this request was for
        problem = self._shape_handover(contract, held, check_start=False)
        above = self.arm.poses.poses.get("handover_above")
        dev = pose_deviation(self.arm.measured(), above.joints) if above else None
        tol = float(self.safety_cfg.get("handover_start_tol_deg", 5.0))
        if problem is None and (dev is None or not dev <= tol):
            problem = "I'm not above the spot"
        world["route_clear"], world["route_reason"] = problem is None, problem
        world["spot_watchable"], world["watch_reason"] = self._spot_watchable()
        handover = self._proof_routes().handover if self.routes else None
        hands = getattr(self.vision, "hands_seen", None) if self.vision is not None else None
        margin = float(self.safety_cfg.get("handover_watch_margin", 0.05))
        world["spot_clear"], world["spot_reason"] = spot_is_clear(
            hands,
            now(),
            float(self.cfg.get("vision", {}).get("stale_ms", 800)) / 1000,
            handover.spot if handover else None,
            (handover.clear_radius + margin) if handover else 0.0,
            hidden=not (handover and handover.camera_sees_past_arm),
            newer_than=arrived_t,
        )
        world["confirmed"] = True  # stage 2 of a request a person confirmed
        return world

    def _handover_lower(
        self, contract: StepContract, epoch: int, arrived_t: float, armed: int | None = None
    ) -> tuple[bool, str]:
        cfg = {**self.safety_cfg, "always_confirm": False, "auto_approve": 0.0}
        deadline = now() + float(self.safety_cfg.get("handover_recheck_s", 2.0))

        def wanted() -> bool:  # no cancel, stop, brace or disarm, and not re-armed since
            return not self._stale(epoch) and (armed is None or self.arm.arm_count == armed)

        while True:
            if not wanted():
                return False, "the request was cancelled"
            world = self._handover_world(contract, arrived_t)
            decision = decide(contract, world, cfg)
            if decision.verdict is Verdict.APPROVE:
                break
            if now() >= deadline:
                why = world.get("spot_reason") if world.get("spot_clear") is False else None
                self._publish(contract, decision, stage="lower")
                return False, str(why or decision.reason)
            time.sleep(0.05)
        self._publish(contract, decision, stage="lower")
        try:
            plan = plan_place_lower(self.arm.poses, float(self.arm.settings.gripper_open))
        except KeyError:
            return False, "the handover spot is not defined"
        meta = {
            "contract_id": contract.id,
            "object_id": contract.object_id,
            "verb": str(contract.verb),
            "confirmed_by": "handover",
            "phase": "lower",
            "route": contract.route,
            "applied_rules": list(contract.applied_rules),
        }
        return self.arm.execute(
            plan,
            meta,
            speed=contract.speed,
            watchdog=self._handover_watchdog(),
            still_wanted=wanted,
        )

    def _handover_watchdog(self):
        """Per-tick check while lowering and releasing (control thread, under the arm
        lock): attribute reads and arithmetic only. Stops on a hand within clear_radius
        + margin of the spot, a stale or missing hand pass, or a hidden spot."""
        handover = self._proof_routes().handover if self.routes else None
        spot = handover.spot if handover else None
        radius = (handover.clear_radius if handover else 0.0) + float(
            self.safety_cfg.get("handover_watch_margin", 0.05)
        )
        hidden = not (handover and handover.camera_sees_past_arm)
        stale_s = float(self.safety_cfg.get("handover_watch_stale_ms", 500)) / 1000

        def check(stage: str | None) -> str | None:
            if stage not in HANDOVER_WATCH_STAGES:
                return None
            hands = getattr(self.vision, "hands_seen", None) if self.vision is not None else None
            ok, why = spot_is_clear(hands, now(), stale_s, spot, radius, hidden=hidden)
            return None if ok else f"handover stopped: {why}"

        return check

    # ── events and loop ──────────────────────────────────────────────────────
    def _on_event(self, event: Event) -> None:
        if event.kind == "emg.brace":
            # Freeze while moving or while a plan is in flight (check-and-stop is
            # atomic under the arm lock); when idle the detector event is just
            # logged, which is what false-positive counting needs.
            with self._lock:
                in_flight = self._in_flight > 0
                had_pending = self.pending is not None
                self.pending = None  # a brace cancels any request waiting for a squeeze
                self._epoch += 1  # and drops any reply or plan that began before it
            releasing = self.arm.releasing  # bookkeeping only: a release is no incident
            stopped = self.arm.stop_if_active("brace (EMG)", plan_in_flight=in_flight)
            self._silence()  # after the stop, and on any brace (moving or not)
            if stopped and not releasing:
                self.log.emit(
                    "incident.triggered", source="brace", onset_t=event.data.get("onset_t")
                )
            if had_pending:
                self.log.emit("intent.cancelled", reason="brace (EMG)", had_pending=True)
        elif event.kind == "emg.open_hand":
            # Same single critical section as the brace: no gap for a confirm to pop
            # the pending request between reading _in_flight and stopping.
            with self._lock:
                in_flight = self._in_flight > 0
                had_pending = self.pending is not None
                self.pending = None
                self._epoch += 1
            releasing = self.arm.releasing
            stopped = self.arm.stop_if_active("open hand (EMG)", plan_in_flight=in_flight)
            self._silence()
            if stopped and not releasing:  # "that bothered me" learns the same as a flinch
                self.log.emit("incident.triggered", source="open_hand")
            if had_pending:
                self.log.emit("intent.cancelled", reason="open hand (EMG)", had_pending=True)
        elif event.kind == "arm.started":
            self.gripper.started(event.data)
        elif event.kind == "arm.completed":
            self.gripper.completed(event.data)
            data = event.data
            if data.get("verb") == str(Verb.PLACE_NEAR_USER) and data.get("phase") == APPROACH:
                # Emitted under the arm lock on the control thread: hand stage 2 to its
                # own thread (it waits for a fresh look and takes the app lock).
                threading.Thread(
                    target=self._continue_handover,
                    args=(data.get("contract_id"), event.t),
                    name="h2m-handover",
                    daemon=True,
                ).start()
        elif event.kind == "arm.gripper_released":
            self.gripper.released_by_operator()  # the measured opening reached open
        elif event.kind in ("arm.stopped", "arm.fault"):
            # Emitted after the freeze write, under the arm lock: plain assignments (no app
            # lock) void stage 2 and any release offer; the tracker takes its own lock.
            self._handover_job = None
            self._release_offer = None
            self.gripper.interrupted(
                event.data.get("stage"), event.kind == "arm.fault", str(event.data.get("reason"))
            )
        elif event.kind == "incident.triggered":
            # The arm is already stopped (this event is emitted after the stop). Only a
            # non-blocking enqueue here: freeze, save and Muse run on the learner's worker.
            self.learner.trigger(event.t, str(event.data.get("source")), event.data.get("onset_t"))
        elif event.kind == "emg.squeeze":
            with self._lock:
                pending = self.pending
                proposal = self.learner.proposal
            if pending is None and proposal is None:
                return
            if pending is not None and proposal is not None:
                self.log.emit(
                    "emg.squeeze_rejected", reason="ambiguous: a request and a rule are waiting"
                )
                return
            # A squeeze that began before (or within a human reaction time of) the
            # request or question being announced is not an answer to it.
            onset = event.t - float(event.data.get("held_ms", 0.0)) / 1000
            announced = pending.announced_at if pending is not None else proposal.announced_at
            if onset < announced + MIN_CONFIRM_REACTION_S:
                self.log.emit("emg.squeeze_rejected", reason="too soon after request")
                return
            if pending is None:
                # Saving a rule writes to disk: never on the EMG thread.
                threading.Thread(
                    target=self.learner.approve,
                    kwargs={"by": "emg", "expected": proposal, "onset": onset},
                    daemon=True,
                ).start()
                return
            threading.Thread(
                target=self.confirm,
                kwargs={
                    "by": "emg",
                    "effort": float(event.data.get("effort", 0.5)),
                    "expected": pending,
                },
                daemon=True,
            ).start()

    def _loop(self) -> None:
        while self._running:
            time.sleep(0.1)
            self.learner.expire()
            with self._lock:
                pending = self.pending
            if pending is None:
                continue
            if now() > pending.expires_at:
                with self._lock:
                    if self.pending is not pending:
                        continue
                    self.pending = None
                self.log.emit("intent.expired", contract_id=pending.contract.id)
                continue
            if pending.decision.clarify and pending.parsed is not None and self.vision is not None:
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
                    approve = decision.verdict is Verdict.APPROVE
                    with self._lock:
                        if self.pending is not pending:  # cancelled/replaced meanwhile
                            continue
                        self.pending = (
                            Pending(pending.parsed, contract, decision, pending.expires_at)
                            if decision.verdict is Verdict.ASK
                            else None
                        )
                        if approve:  # same critical section as the pop (R1)
                            self._in_flight += 1
                        epoch = self._epoch
                    try:
                        self._publish(contract, decision, resolved_by="pointing")
                        if approve:
                            self._execute(contract, decision, "pointing", epoch=epoch)
                    finally:
                        if approve:
                            with self._lock:
                                self._in_flight -= 1

    def _release_snapshot(self) -> dict[str, Any]:
        offer = self._release_offer
        return {
            "enabled": self.operator_release_enabled(),
            "offer": None
            if offer is None
            else {
                "pose": offer.pose,
                "holding": self._held_summary(offer.held),
                "expires_in_s": round(max(0.0, offer.expires_at - now()), 1),
            },
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            pending = self.pending
        held = self.gripper.state()
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
                "arm.gripper_releasing",
                "arm.gripper_released",
                "operator.release_gripper",
                "operator.release_offered",
                "operator.release_refused",
                "emg.squeeze",
                "emg.squeeze_rejected",
                "emg.open_hand",
                "emg.brace",
                "incident.triggered",
                "incident.saved",
                "incident.explained",
                "incident.error",
                "rule.approved",
                "rule.rejected",
                "rule.expired",
                "rule.error",
                "speech.ambiguous",
                "emg.calibrated",
                "compiler.llm",
                "embodied.tool",
                "embodied.skipped",
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
                "plan": pending.plan.skill if pending.plan else None,
            },
            "embodied": {
                "gripper": {"status": held.status, "object_id": held.object_id, "why": held.why},
                "speaking": self.embodied.speech.speaking(),
                "tools": self.use_tools and bool(self.llm),
            },
            "last": self.last,
            "reply": self.reply,
            "learning": {**self.learner.snapshot(), "routes_error": self.routes_error},
            "operator_release": self._release_snapshot(),
            "session": self.recorder.snapshot(),
            "objects": [
                {"id": o["id"], "name": o["name"], "max_grip": o.get("max_grip", 1.0)}
                for o in self.cfg.get("objects", [])
            ],
            "events": [e.as_dict() for e in events],
        }
