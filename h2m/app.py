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
- Reflex cards (``selection.enabled``, off by default; h2m/selection.py,
  docs/SELECTION_API.md): when the app is idle, up to three ranked cards (intent mode) or
  the scanning menu go up. A twitch SELECTS the highlighted card (only ``reaction_s``
  after the UI acknowledged rendering it, and ``after_veto_s`` after any brace or open
  hand); a motion card is then announced and a second twitch ``reaction_s`` after the
  announcement finished playing (and, with ``require_ack``, after the UI acknowledged the
  "confirm?" rendering) confirms it. An announcement that was not spoken, was cut off or
  failed undoes the select. A double twitch steps (or goes back from "confirm?"), and a
  brace or open hand on an idle arm dismisses. Confirming runs the card's tool call
  through Embodied, the gate and the pending confirm like any tool request, and the
  twitch answers only the plain confirmation (a warning the card never showed, e.g.
  gripper not proven empty, waits for a squeeze, "yes" or a click). A twitch never
  confirms any other request and never approves a rule. The brace / open-hand freeze
  runs first and unchanged; the card handling comes after the stop.
- A twitch-band burst onset (``emg.burst_onset``) acts only with ``emg.burst_freeze``
  literally true (off by default; enable after a bench session; ``selection.enabled``
  does not enable it). Then, with the arm MOVING it freezes the arm through the same
  ``stop_if_active`` as a brace (latched); with a plan in flight on a READY arm it drops
  the plan through the request epoch (no latch). On an idle arm it does nothing. A burst
  stop's incident proposes no rule unless a brace or open hand follows within 2 s.
- A squeeze, and any rule approval, that began within ``safety.after_stop_s`` (3 s, floor
  3) of the last brace, open hand or burst stop is refused.
"""

from __future__ import annotations

import hmac
import math
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from h2m.compiler.grammar import Parsed, build_vocabulary, normalize, parse
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
    skill_facts,
    skill_speed,
)
from h2m.grasp import (
    GraspConfig,
    GraspConfigError,
    GraspParams,
    JawCalibration,
    grasp_params,
    load_grasp,
    roll_for_axis,
    roll_from_mask,
    to_table,
    width_across_jaws,
)
from h2m.intent import IntentRanker, RankResult, SparkClient, scanning_candidates
from h2m.learning import Learner
from h2m.proactive import build_proposer
from h2m.recorder import Recorder
from h2m.robot import ArmState, build_arm
from h2m.robot.skills import (
    Plan,
    Pose,
    plan_home,
    plan_move,
    plan_pick_approach,
    plan_pick_place,
    plan_point,
    plan_skill,
)
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
from h2m.safety import CONFIRM, Decision, Verdict, decide
from h2m.selection import INTENT, SCANNING, SELECT, VETO_WINDOW, Selection, Ticket, announcement
from h2m.sensors.emg import build_emg
from h2m.sensors.speech import build_speech
from h2m.sensors.vision import build_vision
from h2m.speech_out import SpeechOut
from h2m.think import WAKE_RE, Thinker
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
# Voice power (on normalized text). Disarm first: "disarm" contains "arm". Disarming
# only makes the arm safer, so anyone may say it; arming is the wearer's, after one "yes".
DISARM_RE = re.compile(r"\b(disarm( yourself)?|stand down|power down|deactivate)\b")
ARM_RE = re.compile(
    r"\b(arm (yourself|up|the (robot|arm))|power up|activate yourself|start helping)\b"
)
POWER_OFFER_S = 10.0  # an "arm?" question lapses after this
NO_VISION_MODEL = "I can't look closely right now; my vision model is offline."
TOOL_MOTION = frozenset(
    {"pick_place", "place_near_user", "look_at", "gesture", "skill", "move_to_slot"}
)
SKILL_GRIP_MAX = 0.4  # a skill's close (take_this, hold_up) stays soft: it may be near a hand
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
# Ranker notes that switch intent selection to scanning for the rest of the run.
DEGRADE_NOTES = frozenset({"no_key", "timeout_or_error", "error"})
SELECTION_OFF = "selection is off (set selection.enabled: true in configs/h2m.yaml)"
BURST_STOP = "twitch-band burst while moving"
BURST_DROP = "twitch-band burst while a plan was in flight"
# No squeeze answer and no rule approval begins this soon after a brace, an open hand or
# a burst stop (safety.after_stop_s may only raise it): what a startle leaves behind in
# the flexors is not an answer.
AFTER_STOP_S = 3.0
# A selected card's question must finish playing within this (selection.announce_max_s,
# capped below); otherwise the select is undone and nothing confirms.
ANNOUNCE_MAX_S = 10.0
MAX_ANNOUNCE_S = 30.0
ANNOUNCE_POLL_S = 0.02
# Learner incident states during which no card is answered (the stop is being looked at).
INCIDENT_BUSY = frozenset({"capturing", "explaining"})


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def twitch_onset(event: Event) -> float | None:
    """When a twitch began: the earliest of the detector's sample-time onset
    (``onset_t``, or ``onset``) and the event time minus ``detect_ms``, never later than
    the event itself. Earlier only makes the reaction and after-veto gates stricter.
    None (fails closed) when neither can be read."""
    data = event.data
    found: list[float] = []
    detect = data.get("detect_ms")
    if _finite_number(detect) and detect >= 0:
        found.append(event.t - float(detect) / 1000)
    for key in ("onset_t", "onset"):
        value = data.get(key)
        if _finite_number(value):
            found.append(float(value))
    if not found:
        return None
    return min(*found, event.t)


@dataclass
class Pending:
    parsed: Parsed | None  # None for a Muse tool call (no grammar re-resolution)
    contract: StepContract
    decision: Decision
    expires_at: float
    announced_at: float = field(default_factory=now)
    plan: Plan | None = None  # embodied plan to check against at execution (gesture / place)
    popped: bool = False  # set (under the app lock) by the confirm that took it


class GraspJob(NamedTuple):
    """A pick waiting at ``slot.above`` for its measured grasp (stage 2): the request as
    the gate approved it, and the request epoch and arm_count at stage 1."""

    contract: StepContract
    decision: Decision
    meta: dict[str, Any]
    epoch: int
    armed: int
    effort: float
    via: tuple


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
        # Meta-measured grasp (h2m/grasp.py): off unless grasp.sam_width is literally true.
        # A bad grasp section fails closed to the defaults, which are off.
        self.grasp_error: str | None = None
        try:
            self.grasp_cfg, self.jaw_cal = load_grasp(cfg)
        except GraspConfigError as exc:
            self.grasp_cfg, self.jaw_cal = GraspConfig(), JawCalibration()
            self.grasp_error = str(exc)
        self.emg = build_emg(cfg.get("emg", {}), self.log)
        self.vision = build_vision(cfg.get("vision", {}), cfg.get("objects", []), self.log)
        # The mic is told when the robot is talking (echo window); read lazily because
        # the voice is built below and may be swapped.
        self.speech = build_speech(
            cfg.get("speech", {}),
            self.log,
            self.on_utterance,
            is_speaking=self._speaking,
            catalog=cfg.get("objects", []),  # keyword biasing on the object names
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
        self._power_offer: float | None = None  # when "arm?" was asked by voice
        self._lock = threading.RLock()
        self._running = False
        self._in_flight = 0  # plans being confirmed/built/started but not yet MOVING
        # Bumped by cancel, stop, brace and open hand. Work that began at an older epoch
        # (a Muse reply, a confirmed plan, the handover's second stage) is dropped.
        self._epoch = 0
        # Event time of the last brace or open hand (moving or not) or burst stop, and the
        # recent brace / open-hand times (does a burst stop have a flinch after it?).
        # Plain float / deque writes after the stop; read by the squeeze and rule gates.
        self._last_stop_t = -math.inf
        self._veto_times: deque[float] = deque(maxlen=32)
        self.after_stop_s = max(
            AFTER_STOP_S, float(self.safety_cfg.get("after_stop_s", AFTER_STOP_S))
        )
        # The burst-onset freeze is its own switch, off unless literally true (a bench
        # session first); selection.enabled does not turn it on.
        self.burst_freeze = (cfg.get("emg") or {}).get("burst_freeze") is True
        self._tls = threading.local()  # .epoch: the epoch the current request began at
        # place_near_user stage 2 waiting for stage 1 to arrive: (contract, epoch, the
        # arm's arm_count at stage 1: any re-arming since, however it came, drops it)
        self._handover_job: tuple[StepContract, int, int] | None = None
        # A pick waiting at slot.above for its measured grasp (grasp.sam_width only).
        self._grasp_job: GraspJob | None = None
        self._grasp_chip: dict[str, Any] | None = None  # last measurement, for the snapshot
        self._grasp_profiles: dict[str, str] = {}  # object id -> classified profile (cached)
        self._release_offer: ReleaseOffer | None = None  # operator release, step 1 done
        # Embodied gesture runs by contract id: (started, needs confirmation, reason).
        self._gesture_outcomes: dict[str, tuple[bool, bool, str]] = {}
        # Incident capture + rule proposals; it never subscribes to the log itself.
        self.learner = Learner(self)
        # The arm keeps its grip on a freeze unless the tracker proves the gripper empty.
        self.arm.gripper_empty = self._gripper_proven_empty
        if self.emg is not None:
            self.arm.guards.append(self._emg_problem)  # no motion without a live EMG stop
        self._build_selection(cfg)
        # Proactive cards (h2m/proactive.py, docs/PROACTIVE.md): None unless
        # proactive.enabled is literally true. It reads sensors and the selection; the
        # arm is only ever reached through the card path, gate and confirmation below.
        self.proactive = build_proposer(self, cfg)
        # Muse, always on (h2m/think.py): watches, listens, remarks and suggests; a
        # suggestion is a request through run_tool_calls, so the gate and one
        # confirmation stand between it and the arm. Off unless think.enabled is true.
        self.thinker = Thinker(self, cfg)
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
        self.thinker.start()
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
            grasp_error=self.grasp_error,
        )

    def shutdown(self) -> None:
        self._running = False
        self.thinker.stop()
        self.learner.stop()
        self.recorder.stop()
        for part in (self.speech, self.vision, self.emg):
            if part is not None:
                part.stop()
        self.arm.close()

    # ── world ────────────────────────────────────────────────────────────────
    def visible_objects(self) -> list[dict[str, Any]]:
        if self.vision is not None:
            seen = self.vision.visible_objects()
            assume = self.cfg.get("vision", {}).get("assume_slots") is True
            if assume and getattr(self.vision, "homography", None) is None:
                return self._on_assumed_slots(seen)
            return seen
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

    def _on_assumed_slots(self, seen: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Until ``h2m calibrate table`` has run, an object SAM really sees takes the taught
        slot the virtual scene gives it (catalog order), marked ``position: assumed_slot``.
        Presence, staleness and confidence stay SAM's; only the table position is assumed.
        Opt-in (``vision.assume_slots: true``), and ignored once a homography exists."""
        slots = list(self.arm.poses.slots.values())
        home = {o["id"]: slots[i].table_xy for i, o in enumerate(self.cfg.get("objects", [])[: len(slots)])}
        return [
            {**o, "table_xy": home[o["id"]], "position": "assumed_slot"}
            for o in seen
            if o.get("id") in home
        ]

    def pointing(self) -> dict[str, Any]:
        return self.vision.pointing if self.vision else {"active": 0.0, "candidates": []}

    def _world(self, contract: StepContract) -> dict[str, Any]:
        # A stale object (its SAM scan older than vision.stale_s) is not visible to the gate.
        visible_ids = {o["id"] for o in self.visible_objects() if o.get("stale") is not True}
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
        if self.proactive is not None:  # the person's trust rung for this action (never -> reject)
            world["rung"] = self.proactive.action_rung(contract)
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
        elif contract.verb is Verb.SKILL:
            skill = self.embodied.skills.get(contract.utterance)
            world.update(skill_facts(skill, self.safety_cfg))
            if skill is not None:
                contract.speed = min(contract.speed, skill_speed(skill, self.safety_cfg))
                # Moving while (maybe) holding something is a carry whatever the skill
                # says: rules shape it, a route must be proven, the question says so.
                if skill.carries or (
                    not skill.needs_empty and world["gripper_empty"] is not True
                ):
                    world["carries"] = True
                    problem = self._shape_held_move(contract, held)
                    world["route_clear"] = problem is None
                    world["route_reason"] = problem
        elif contract.verb is Verb.MOVE:
            problem = self._shape_carry(contract)
            if problem is None and (contract.applied_rules or contract.route not in (None, "direct")):
                # The route polylines are slot-to-tray; none is taught slot-to-slot, so a
                # move a rule cares about has no proof yet.
                problem = "a rule applies to that object and moves between spots have no proven route yet"
            world["route_clear"] = problem is None
            world["route_reason"] = problem
            world["destination_ok"], world["destination_reason"] = self._destination_free(
                contract, slot
            )
        return world

    def _destination_free(self, contract: StepContract, source: Any) -> tuple[bool, str | None]:
        """A move may only end on a taught slot that is not the source and that no other
        visible object sits on."""
        dest = self.arm.poses.slots.get(contract.destination or "")
        if dest is None:
            return False, f"{contract.destination or 'that'} is not a taught spot"
        if source is not None and dest.slot_id == source.slot_id:
            return False, f"the {contract.object_name or 'object'} is already there"
        for other in self.visible_objects():
            if other.get("id") == contract.object_id or not other.get("table_xy"):
                continue
            on = self.arm.slot_for(tuple(other["table_xy"]))
            if on is not None and on.slot_id == dest.slot_id:
                return False, f"{dest.slot_id} has the {other.get('name', 'other object')} on it"
        return True, None

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

    def _slot_open_pct(self, slot: Any) -> float:
        """Jaw opening for a pick at ``slot``: the value taught at its grasp pose, kept
        inside ``taught_jaws_range``; the one ``gripper_open`` when that is switched off,
        there is no slot, or the taught value is unusable."""
        default = float(self.arm.settings.gripper_open)
        arm_cfg = self.cfg.get("arm", {})
        if slot is None or arm_cfg.get("use_taught_grasp_jaws") is not True:
            return default
        taught = slot.grasp.joints.get("gripper")
        if not isinstance(taught, (int, float)) or isinstance(taught, bool) or taught != taught:
            return default
        low, high = (float(v) for v in arm_cfg.get("taught_jaws_range", (20, 60)))
        return max(low, min(high, float(taught)))

    def _touch_close_pct(self, slot: Any, grip: Any) -> float:
        """Where the jaws close to: the opening taught at the slot's ``point`` pose (the
        jaws touching the object there), less a small squeeze scaled by the grip effort, so
        a fragile object gets less. The torque limit still bounds the force. Falls back to
        the effort-mapped ``grip.close_pct`` when it is switched off or the pose is untaught."""
        arm_cfg = self.cfg.get("arm", {})
        if slot is None or arm_cfg.get("use_taught_touch_close") is not True:
            return grip.close_pct
        touch = slot.point.joints.get("gripper")
        if (
            not slot.point.taught
            or not isinstance(touch, (int, float))
            or isinstance(touch, bool)
            or not 0.0 <= touch <= 100.0
        ):
            return grip.close_pct
        squeeze = max(0.0, float(arm_cfg.get("touch_squeeze_pct", 8.0)))
        return max(float(self.arm.settings.gripper_hard_close), float(touch) - squeeze * grip.effort)

    def _tray_open_pct(self, slot: Any) -> float:
        """Opening for a pick that releases at the tray: the slot's taught jaws, never above
        ``arm.tray_release_max_pct`` (the widest opening known to clear the tray wall at
        tray_release; the release opens to max(gripper_open, this), so a wider grasp would
        open the jaws against the wall there)."""
        cap = float(self.cfg.get("arm", {}).get("tray_release_max_pct", self.arm.settings.gripper_open))
        return min(self._slot_open_pct(slot), cap)

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
        self.learner.note_applied(list(shaping.applied), tags)  # "rule 1 applied · liquid"
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
        self.learner.note_applied(
            list(shaping.applied), tags_for(held.object_id, self.routes, self.catalog)
        )
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
        clean = normalize(text)
        if DISARM_RE.search(clean):
            self._power_offer = None
            self.disarm(f"{source}: {text}")
            self._say("Disarmed. I'm still watching and listening.")
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
        if ARM_RE.search(clean):
            self._offer_power(meta)
            return
        with self._lock:
            pending = self.pending
            proposal = self.learner.proposal
        offer = self._power_offer
        if (
            parsed.verb is Verb.CONFIRM
            and offer is not None
            and pending is None
            and proposal is None
            and not meta.get("asked_by")
        ):
            self._power_offer = None
            if onset < offer or now() - offer > POWER_OFFER_S:
                self._say("That question lapsed. Say arm yourself again.")
                return
            self._arm_by_voice()
            return
        if parsed.verb is Verb.CONFIRM:
            if meta.get("asked_by"):
                # Diarized as someone other than the wearer: only the wearer answers.
                self.log.emit(
                    "speech.confirm_rejected", reason="not the wearer", asked_by=meta["asked_by"]
                )
                self.reply = f"{meta['asked_by']} said yes; waiting for you."
                return
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
        if self.thinker.enabled and source == "voice" and not self._is_command(parsed, clean):
            # Ambient speech, or addressed but not a command: Muse heard it (the thinker
            # woke on speech.utterance above) and judges whether to remark or suggest.
            # Never a chatbot turn, never "what do you need?".
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

    @staticmethod
    def _is_command(parsed: Parsed, clean: str) -> bool:
        """A spoken request the grammar can act on: a known verb with a named target or a
        taught skill, short, or addressed to the arm by name. Everything else is
        conversation for the thinker."""
        if parsed.verb is Verb.UNKNOWN:
            return False
        if parsed.verb in (Verb.PICK_PLACE, Verb.POINT, Verb.MOVE) and parsed.reference == "none":
            return False
        return bool(WAKE_RE.search(clean)) or len(clean.split()) <= 8

    def _offer_power(self, meta: dict[str, Any]) -> None:
        """"Arm yourself": ask once; the wearer's next "yes" within POWER_OFFER_S arms."""
        if meta.get("asked_by"):
            self._say("Only you can arm me.")
            return
        state = self.arm.state
        if state in (ArmState.READY, ArmState.MOVING):
            self._say("I'm already armed.")
            return
        if state not in (ArmState.DISARMED, ArmState.STOPPED):
            self._say(f"I can't arm while {state}.")
            return
        self._say("Arm and start helping? Say yes.")
        self._power_offer = now()

    def _arm_by_voice(self) -> None:
        """The confirmed "arm": a latched stop is reset first (the "yes" is the deliberate
        act the latch waits for), then armed. Nothing moves; the next motion is gated."""
        arm = self.arm
        if arm.state is ArmState.STOPPED:
            ok, why = arm.reset()
            if not ok:
                self._say(f"I couldn't reset: {why}.")
                return
        ok, why = arm.arm()
        self.log.emit("arm.voice", ok=ok, detail=why)
        self._say("Armed. I'll suggest, you confirm." if ok else f"I couldn't arm: {why}.")

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

    def _say(self, text: str) -> ToolResult:
        """Speak ``text`` through the say tool (gated, speech only) and show it. The
        result's ``spoken`` says whether speech actually started (voice off, ``say``
        missing or a refused say leave it False)."""
        result = self.embodied.handle_tool_call("say", {"text": text[:280]})
        self.log.emit("embodied.tool", **result.as_dict())
        self.reply = text
        return result

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
            pending.popped = True  # a card that raised it can tell another answer won
            self._tls.popped = pending
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
        self._selection_hide(f"cancel: {reason}")  # after the stop: the cards go too

    def stop(self, reason: str = "stop") -> None:
        with self._lock:
            self.pending = None
            self._handover_job = None  # stage 2 never follows a stop
            self._grasp_job = None
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
            self._grasp_job = None
            self._release_offer = None
            self._epoch += 1
        self.arm.disarm()
        self._silence()
        if had:
            self.log.emit("intent.cancelled", reason=reason, had_pending=True)
        self._selection_hide(reason)

    # ── operator gripper release (SAFETY REVIEW: actuation outside a plan) ────
    def operator_release_enabled(self) -> bool:
        """``safety.operator_release`` is literally true (off by default)."""
        return self.safety_cfg.get("operator_release") is True

    def _release_state_problem(self) -> str | None:
        """Why no release may start now (app state only), or None."""
        if self._in_flight > 0 or self._handover_job is not None or self._grasp_job is not None:
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

    def _mark_veto(self, t: float) -> None:
        """A brace or open hand at ``t`` (moving or not). Called after the stop."""
        self._last_stop_t = max(self._last_stop_t, t)
        self._veto_times.append(t)

    def stopped_recently(self, onset: float) -> bool:
        """An answer that began at ``onset`` is within ``after_stop_s`` of the last brace,
        open hand or burst stop (or began before it). A non-finite onset counts as recent
        (fails closed)."""
        if not _finite_number(onset):
            return True
        return onset - self._last_stop_t < self.after_stop_s

    def vetoed_between(self, start: float, end: float) -> bool:
        """A brace or open hand was seen in [start, end] (event times)."""
        return any(start <= t <= end for t in list(self._veto_times))

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
        elif contract.verb is Verb.SKILL:
            skill = self.embodied.skills.get(contract.utterance)
            if skill is None or decision.checks.get("skill_known") is not True:
                return False, f"skill {contract.utterance!r} is not defined"
            slot = None
            if skill.requires_object:
                slot = arm.poses.slots.get(decision.slot_id or "")
                if slot is None:
                    return False, "no reachable slot"
            if skill.carries and decision.checks.get("route_clear") is not True:
                return False, "route not checked"
            if skill.near_person and decision.checks.get("near_person_ok") is not True:
                return False, "near-person move not checked"
            close_pct = float(arm.settings.gripper_soft_close)
            if skill.closes:
                # A skill closes near a hand or on an unknown object: never above a soft grip.
                effort = contract.grip_effort
                if effort is None:
                    effort = float(self.safety_cfg.get("default_grip", 0.5))
                ceiling = float(self.catalog.get(contract.object_id or "", {}).get("max_grip", 1.0))
                grip = arm.grip_for(min(effort, SKILL_GRIP_MAX), ceiling)
                close_pct = self._touch_close_pct(slot, grip)
                meta["requested_effort"] = round(effort, 3)
            meta["skill_name"] = skill.name
            meta["closes"] = skill.closes
            last_jaw = next((st.gripper for st in reversed(skill.steps) if st.gripper), None)
            meta["releases"] = last_jaw == "open"
            try:
                plan = plan_skill(arm.poses, skill, self._slot_open_pct(slot), close_pct, slot)
            except KeyError as exc:
                return False, f"skill {skill.name} uses an undefined pose {exc}"
        elif contract.verb is Verb.MOVE:
            slot = arm.poses.slots.get(decision.slot_id or "")
            dest = arm.poses.slots.get(contract.destination or "")
            if slot is None or dest is None:
                return False, "no reachable slot"
            if decision.checks.get("route_clear") is not True:
                return False, "route not checked"
            if decision.checks.get("destination_ok") is not True:
                return False, "destination not checked"
            if contract.route is not None:
                route = arm.poses.routes.get(contract.route)
                if route is None:
                    return False, f"route {contract.route} has no taught waypoints"
                via = route
            meta["route"] = contract.route
            meta["applied_rules"] = list(contract.applied_rules)
            meta["destination"] = dest.slot_id
            effort = contract.grip_effort
            if effort is None:
                effort = float(self.safety_cfg.get("default_grip", 0.5))
            ceiling = float(self.catalog.get(contract.object_id or "", {}).get("max_grip", 1.0))
            grip = arm.grip_for(effort, ceiling)
            meta["requested_effort"] = round(effort, 3)
            plan = plan_move(
                arm.poses, slot, dest, self._slot_open_pct(slot), self._touch_close_pct(slot, grip), via
            )
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
                if self.grasp_cfg.sam_width is True:
                    # Two stages like the handover: the approach to slot.above now, the
                    # grasp once SAM has measured the object from there (_continue_grasp).
                    plan = plan_pick_approach(arm.poses, slot, self._tray_open_pct(slot))
                    meta["phase"] = APPROACH
                    grip = None  # the close is set by stage 2
                else:
                    plan = plan_pick_place(
                        arm.poses, slot, arm.settings.gripper_open,
                        self._touch_close_pct(slot, grip), via,
                        grasp_open_pct=self._tray_open_pct(slot),
                    )
        tags = tags_for(contract.object_id, self.routes, self.catalog)
        self.learner.set_active({
            **meta, "object_name": contract.object_name, "tags": tags,
            "slot_id": decision.slot_id,
        })
        if epoch is None:
            epoch = self._epoch
        two_stage = contract.verb is Verb.PICK_PLACE and meta.get("phase") == APPROACH
        if contract.verb is Verb.PLACE_NEAR_USER:
            with self._lock:  # set before the start: arrival may come on the next tick
                self._handover_job = (contract, epoch, arm.arm_count)
        elif two_stage:
            with self._lock:
                self._grasp_job = GraspJob(
                    contract, decision, dict(meta), epoch, arm.arm_count, effort, via
                )
        ok, reason = arm.execute(
            plan, meta, grip, speed=contract.speed, still_wanted=lambda: not self._stale(epoch)
        )
        if not ok:
            if contract.verb is Verb.PLACE_NEAR_USER:
                with self._lock:
                    self._handover_job = None
            elif two_stage:
                with self._lock:
                    self._grasp_job = None
            self.log.emit("intent.blocked", reason=reason, contract_id=contract.id)
        elif contract.verb is Verb.SKILL and (line := self.embodied.skills[contract.utterance].say):
            self._say(line)  # gated speech only; the plan is already running
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

    # ── pick stage 2: the measured grasp (grasp.sam_width) ───────────────────
    def _continue_grasp(self, contract_id: Any, arrived_t: float) -> None:
        """Stage 1 arrived at ``slot.above``. Measure the object from there, rebuild the
        pick with the clamped opening and roll, and run it, only if the request, the arm's
        arming and the gate's decision are all still the ones stage 1 started under.
        Runs on its own thread: never on the control thread, never under the arm lock,
        and a stop does not wait for it."""
        with self._lock:
            job, self._grasp_job = self._grasp_job, None
            if job is None or job.contract.id != contract_id:
                return
            self._in_flight += 1  # same section as the pop: a brace in between still stops
        try:
            ok, why = self._grasp_lower(job, arrived_t)
        finally:
            with self._lock:
                self._in_flight -= 1
        if not ok:
            self.log.emit("intent.blocked", reason=why, contract_id=job.contract.id)
            if not self._stale(job.epoch):
                name = job.contract.object_name or "it"
                self._say(f"I stopped above the {name}: {why}. Say go home to bring me back.")

    def _grasp_gate(self, job: GraspJob) -> tuple[Decision, str]:
        """The gate's answer for the same request now that the arm is above the slot, and
        why it no longer stands ("" if it does). The request was confirmed (or approved) as
        stage 1's decision, so the new decision must be that verdict or better (an ask a person
        already answered counts as met), with the same checks and slot. The object under the arm may be hidden from the overhead camera, so
        its visibility is carried over from stage 1."""
        cfg = {**self.safety_cfg, "always_confirm": False, "auto_approve": 0.0}
        world = self._world(job.contract)
        world["target_visible"] = job.decision.checks.get("target_visible", True)
        decision = decide(job.contract, world, cfg)
        allowed = {Verdict.APPROVE, job.decision.verdict}
        if job.meta.get("confirmed_by") != "auto":
            allowed.add(Verdict.ASK)  # a person already answered the question it asks
        if decision.verdict not in allowed:
            return decision, decision.reason
        if decision.slot_id != job.decision.slot_id:
            return decision, "the target slot changed"
        if decision.checks != job.decision.checks:
            return decision, "the checks changed while I approached"
        return decision, ""

    def _grasp_lower(self, job: GraspJob, arrived_t: float) -> tuple[bool, str]:
        contract, arm = job.contract, self.arm
        raw = self.cfg.get("grasp") or {}
        deadline = now() + float(raw.get("recheck_s", 2.0))

        def wanted() -> bool:  # no cancel, stop, brace or disarm, and not re-armed since
            return not self._stale(job.epoch) and arm.arm_count == job.armed

        while True:  # the arm is READY a tick after its completed event
            if not wanted():
                return False, "the request was cancelled"
            decision, why = self._grasp_gate(job)
            if not why:
                break
            if now() >= deadline:
                self._publish(contract, decision, stage="grasp")
                return False, why
            time.sleep(0.05)
        slot = arm.poses.slots.get(job.decision.slot_id or "")
        if slot is None:
            return False, "no reachable slot"
        ceiling = float(self.catalog.get(contract.object_id or "", {}).get("max_grip", 1.0))
        params = self._measure_grasp(contract, slot, arrived_t, ceiling)
        if params is not None and params.source == "sam":
            grasp_open, grasp_roll = params.open_pct, params.roll_deg
        else:  # the taught plan, exactly as without grasp.sam_width
            grasp_open, grasp_roll = self._tray_open_pct(slot), None
        if params is not None:
            ceiling = min(ceiling, params.close_cap)
        grip = arm.grip_for(job.effort, ceiling)
        try:
            plan = plan_pick_place(
                arm.poses, slot, arm.settings.gripper_open, grip.close_pct, job.via,
                grasp_open_pct=grasp_open,
                grasp_roll_deg=grasp_roll,
                roll_envelope_deg=self.grasp_cfg.roll_envelope_deg,
            )
        except (KeyError, ValueError) as exc:  # PoseError is a ValueError
            return False, f"could not plan the grasp: {exc}"
        self._grasp_chip = {
            "text": params.chip if params else "taught",
            "source": params.source if params else "fallback",
            "width_mm": params.width_mm if params else None,
            "open_pct": round(grasp_open, 1),
            "roll_deg": grasp_roll,
            "close_cap": round(ceiling, 3),
            "object_id": contract.object_id,
            "t": round(now(), 3),
        }
        self.log.emit("grasp.params", contract_id=contract.id, **self._grasp_chip)
        if not wanted():
            return False, "the request was cancelled"
        decision, why = self._grasp_gate(job)  # once more, right before it moves
        if why:
            self._publish(contract, decision, stage="grasp")
            return False, why
        self._publish(contract, decision, stage="grasp")
        meta = {k: v for k, v in job.meta.items() if k != "phase"}
        meta["grasp_open_pct"] = round(grasp_open, 1)
        meta["grasp_roll_deg"] = grasp_roll
        meta["requested_effort"] = round(job.effort, 3)
        return arm.execute(
            plan, meta, grip, speed=contract.speed, still_wanted=wanted
        )

    def _measure_grasp(
        self, contract: StepContract, slot: Any, arrived_t: float, catalog_max_grip: float
    ) -> GraspParams | None:
        """Opening, roll and close cap for this pick from one SAM call and one profile
        classification. Any failure (no key, no frame, timeout, no match, no homography)
        leaves the measurement out and gives the taught values; None only when even that
        cannot be built (the caller then runs the taught plan)."""
        cfg, raw = self.grasp_cfg, self.cfg.get("grasp") or {}
        taught_roll = slot.grasp.joints["wrist_roll"]
        taught_open = self._slot_open_pct(slot)
        width = roll = None
        profile = "unknown"
        try:
            frame = self._grasp_frame(arrived_t, float(raw.get("frame_wait_s", 1.0)))
            profile = self._grasp_profile(contract)
            muse = self.learner.muse
            homography = self.vision.homography if self.vision is not None else None
            if frame is not None and muse is not None and homography is not None:
                found = muse.segment_mask(
                    muse.sam_model, frame, contract.object_name,
                    timeout=float(raw.get("sam_timeout_s", 2.0)),
                )
                if found is not None:
                    box, polygon = found
                    if cfg.roll_sign_verified is True and polygon is not None:
                        axis = roll_from_mask(to_table(polygon, homography), cfg.round_aspect)
                        roll = roll_for_axis(
                            axis, cfg.jaw_axis_deg.get(slot.slot_id), taught_roll, cfg.roll_sign
                        )
                    width = width_across_jaws(
                        polygon if polygon is not None else box,
                        homography, slot.slot_id, taught_roll, roll, cfg,
                    )
        except Exception as exc:  # noqa: BLE001  # a measurement never stops a pick; it falls back
            self.log.emit("grasp.error", reason=type(exc).__name__, contract_id=contract.id)
            width = roll = None
        try:
            return grasp_params(
                width, roll, taught_roll, profile, cfg, self.jaw_cal,
                catalog_max_grip=catalog_max_grip, taught_open_pct=taught_open,
            )
        except ValueError:
            return None

    def _grasp_frame(self, arrived_t: float, wait_s: float) -> Any:
        """An overhead frame taken after the arm arrived (the arm is above the slot now),
        waiting up to ``wait_s`` for one; None if there is none."""
        if self.vision is None:
            return None
        end = now() + wait_s
        while True:
            item = self.vision.scene.latest()
            if item is not None and item[1] > arrived_t:
                return item[0]
            if now() >= end:
                return None
            time.sleep(0.05)

    def _grasp_profile(self, contract: StepContract) -> str:
        """Muse Spark's grip profile for the target (rigid / fragile / liquid), cached per
        object. ``unknown`` (no model, no frame, an error, an unclear reply) is not cached
        and takes the softest cap."""
        oid = contract.object_id or ""
        cached = self._grasp_profiles.get(oid)
        if cached is not None:
            return cached
        if self.llm is None or self.vision is None:
            return "unknown"
        jpeg = self.vision.scene.jpeg()
        if jpeg is None:
            return "unknown"
        try:
            profile = self.llm.classify_profile(contract.object_name or oid, jpeg)
        except Exception:  # noqa: BLE001
            return "unknown"
        if profile in ("rigid", "fragile", "liquid") and oid:
            self._grasp_profiles[oid] = profile
        return profile

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

    # ── selection: Reflex cards (h2m/selection.py, docs/SELECTION_API.md) ────
    # Lock order: app lock, then the Selection's own lock (it never calls out). Handlers
    # that run under the arm lock (arm.*) only change state and queue their events; the
    # loop (or the next non-arm caller) emits them, so no disk I/O sits under the arm lock.
    def _build_selection(self, cfg: dict[str, Any]) -> None:
        sel = cfg.get("selection") or {}
        self.selection_cfg: dict[str, Any] = sel
        # Only the literal true turns cards (and the twitch as their answer) on.
        self.selection_enabled = sel.get("enabled") is True
        # The Selection clamps these so the config can only make them stricter: reaction
        # at least MIN_CONFIRM_REACTION_S, no accept within 3 s of a veto, and the UI
        # render acknowledgement required unless require_ack is literally false
        # (headless tests / scanning runs without a UI).
        self.selection = Selection(
            str(sel.get("mode", INTENT)),
            k=int(sel.get("cards", 3)),
            reshow_s=float(sel.get("reshow_s", 3.0)),
            reaction_s=max(MIN_CONFIRM_REACTION_S, float(sel.get("reaction_s", 0.7))),
            veto_refractory_s=float(sel.get("veto_refractory_s", 1.5)),
            after_veto_s=float(sel.get("after_veto_s", 3.0)),
            require_ack=sel.get("require_ack", True) is not False,
            confirm_s=float(sel.get("confirm_s", 8.0)),
        )
        client = None
        if self.selection_enabled and sel.get("muse", True) is not False:
            base = (cfg.get("compiler") or {}).get("base_url")
            client = SparkClient({**({"base_url": base} if base else {}), **(sel.get("spark") or {})})
        # Muse only ranks a closed option list off-thread; it never touches the arm.
        self.ranker = IntentRanker(client, self._on_ranked, on_fallback=self._on_rank_fallback)
        self._selection_out: deque[tuple[str, dict[str, Any]]] = deque()

    def _emit_selection(self, events: list[tuple[str, dict[str, Any]]]) -> None:
        for kind, data in events:
            self.log.emit(kind, **data)

    def _drain_selection(self) -> None:
        """Emit the events the arm handlers queued. Never call it under the arm lock."""
        while True:
            try:
                kind, data = self._selection_out.popleft()
            except IndexError:
                return
            self.log.emit(kind, **data)

    def _selection_blocked(self) -> str | None:
        """Under the app lock: why the cards cannot be answered now, or None."""
        if self.pending is not None:
            return "a request is waiting: squeeze, say yes or click to answer it"
        if self.learner.proposal is not None:
            return "a rule is waiting: squeeze, say yes or click to approve it"
        arm = self.arm
        if (
            self._in_flight > 0
            or self._handover_job is not None
            or self._grasp_job is not None
            or arm.state is ArmState.MOVING
            or arm.releasing
        ):
            return "I'm busy moving"
        if self._release_offer is not None:
            return "an operator gripper release is open"
        learner = self.learner
        incident = learner.incident
        # A stop just happened and is being captured or explained (or is still queued for
        # the learner): nothing is answered from the cards until that is done.
        if (isinstance(incident, dict) and incident.get("state") in INCIDENT_BUSY) or (
            not learner._queue.empty()
        ):
            return "I'm still looking at what just happened"
        return None

    def _selection_tick(self) -> None:
        """Loop thread: emit queued events; when the app is idle, end a task whose motion
        is over and put up new cards. The ranker gets ``arm_stopped=True`` only with the
        arm READY or STOPPED (nothing moving, nothing in flight)."""
        self._drain_selection()
        if not self.selection_enabled:
            return
        state = self.arm.state
        if state in (ArmState.DISCONNECTED, ArmState.FAULT):
            return
        with self._lock:
            t = now()
            # Cards from an earlier arming go (rebuilt below); an unanswered "confirm?"
            # lapses back to the menu. Both run even while blocked.
            rearmed = self.selection.sync_arm(self.arm.arm_count, t)
            events = rearmed + self.selection.expire(t)
            blocked = self._selection_blocked()
            if blocked is None:
                events += self.selection.abandon_if_idle(t)
        if rearmed:
            self.ranker.cancel()
        self._emit_selection(events)
        if blocked is None and self.proactive is not None:
            self.proactive.tick(state)  # veto rings and pre-approved routines (h2m/proactive.py)
        if blocked is not None or not self.selection.wants_menu(now()):
            return
        cards, source, gen = self._selection_cards(state)
        with self._lock:
            still_idle = self._selection_blocked() is None and self.arm.state is state
            events = (
                self.selection.show(cards, source, now(), gen, self.arm.arm_count)
                if still_idle
                else []
            )
        if not events:
            if gen is not None:
                self.ranker.cancel()  # nothing went up: drop the Muse upgrade
            self.selection.backoff(now() + 0.5)  # no re-ranking (or rules reads) at 10 Hz
        self._emit_selection(events)

    def _selection_cards(self, state: ArmState) -> tuple[list[Any], str, int | None]:
        """Cards for a new menu: the scanning menu, or the ranker's deterministic first
        ranking (a Muse upgrade may follow off-thread). Targets that are not visible or
        not on a taught slot are dropped by h2m.intent before any display."""
        scene = self.visible_objects()
        held = self.gripper.state()
        holding = held.object_id if held.holding and held.object_id else None
        phrases = self.selection_cfg.get("phrases") or {}
        if self.selection.mode == SCANNING:
            cards = scanning_candidates(
                scene, holding=holding, reachable=self._card_reachable, phrases=phrases
            )
            return cards, "scanning", None
        try:
            rules: Any = self.rules.list()
        except RulesUnavailable:
            rules = []  # context only; the gate still enforces whatever is stored
        head = getattr(self.vision, "head_third", None)
        table_x = self.selection_cfg.get("table_x")
        extra, call_for, context = (
            self.proactive.extras(state) if self.proactive is not None else ([], "rank", None)
        )
        gen, first = self.ranker.request(
            scene,
            self.selection.recent_history(),
            rules,
            head if isinstance(head, str) else None,
            self._recent_speech(),
            self.selection.k,
            arm_stopped=state in (ArmState.READY, ArmState.STOPPED),
            holding=holding,
            reachable=self._card_reachable,
            phrases=phrases,
            table_x=tuple(table_x) if isinstance(table_x, (list, tuple)) and len(table_x) == 2
            else None,
            deadline_s=float(self.selection_cfg.get("deadline_s", 2.5)),
            extra=extra,
            context=context,
            call_for=call_for,
        )
        if self.proactive is not None:
            self.proactive.remember(first.candidates)
        return first.candidates, "fallback", gen

    def _recent_speech(self, window_s: float = 120.0) -> list[dict[str, str]]:
        """What was said near the arm lately, for the ranker: the wearer as "user", anyone
        else by name. Context only; the ranker treats it as untrusted."""
        t = now()
        out = []
        for e in self.log.recent(300, {"speech.utterance"}):
            if t - e.t <= window_s and e.data.get("text") and e.data.get("echo") is not True:
                who = e.data.get("asked_by") or "user"
                out.append({"speaker": str(who), "text": str(e.data["text"])})
        return out

    def _card_reachable(self, entry: Any) -> bool:
        xy = entry.get("table_xy")
        return self.arm.slot_for((float(xy[0]), float(xy[1]))) is not None

    def _on_ranked(self, gen: int, result: RankResult) -> None:
        """Ranker thread: a valid Muse ranking. Applied only before the first input."""
        self._emit_selection(self.selection.upgrade(gen, result.candidates, now()))

    def _on_rank_fallback(self, gen: int, result: RankResult) -> None:
        """Ranker thread: Muse could not rank. No key, a timeout or an error switch to
        scanning for the rest of the run; an invalid reply keeps the deterministic cards."""
        del gen
        if result.note in DEGRADE_NOTES:
            self._emit_selection(self.selection.degrade(result.note, now()))

    def _selection_arm_event(self, event: Event) -> None:
        """Under the arm lock (control or caller thread): state changes and a queue
        append only. The ranker is cancelled whenever the arm moves or stops."""
        if not self.selection_enabled:
            return
        try:
            data, t = event.data, event.t
            if event.kind == "arm.started":
                self.ranker.cancel()
                events = self.selection.on_arm_started(data.get("contract_id"), t)
            elif event.kind == "arm.completed":
                events = self.selection.on_arm_completed(
                    data.get("contract_id"), data.get("phase"), t
                )
            else:
                self.ranker.cancel()
                events = self.selection.on_arm_stopped(
                    str(data.get("reason", "")), event.kind == "arm.fault", t
                )
            self._selection_out.extend(events)
        except Exception:  # noqa: BLE001  # bookkeeping must never break an arm event
            pass

    def _selection_veto(self, name: str, stopped: bool, consumed: bool) -> None:
        """A brace or open hand, AFTER the freeze logic ran. If it stopped the arm it was
        counted from the stop reason; if it cancelled a waiting request or an in-flight
        plan it only counts; on an idle arm it dismisses the highlighted card."""
        if not self.selection_enabled:
            return
        try:
            if consumed and not stopped:
                self.selection.count(name, now())
            elif not stopped:
                self._emit_selection(self.selection.dismiss(name, now())[2])
            self._drain_selection()
        except Exception:  # noqa: BLE001  # the stop already happened; never raise past it
            pass

    def _selection_hide(self, reason: str) -> None:
        if not self.selection_enabled:
            return
        self.ranker.cancel()
        self._emit_selection(self.selection.hide(now(), reason))
        self._drain_selection()

    def _on_twitch(self, event: Event) -> None:
        """EMG thread: a twitch selects (motion card) or confirms / takes (the card asking
        "confirm?", a say card) the highlighted card, nothing else. Its onset is the
        earliest of the detector's ``onset_t`` and the event time minus ``detect_ms``
        (earlier only makes every gate stricter); a missing onset fails closed. The
        announcement and the gate run on their own thread."""
        if not self.selection_enabled:
            return
        ticket, _why, epoch = self._selection_take("twitch", twitch_onset(event))
        if ticket is None:
            return
        if ticket.step == SELECT:
            target, name = self._announce_card, "h2m-announce"
        else:
            target, name = self._accept_card, "h2m-select"
        threading.Thread(target=target, args=(ticket, epoch), name=name, daemon=True).start()

    def _selection_take(
        self,
        name: str,
        onset: float | None,
        card_id: str | None = None,
        menu_id: int | None = None,
    ) -> tuple[Ticket | None, str, int]:
        """Take the highlighted card, atomically with the blocked check (app lock), the
        arming the cards belong to and the request epoch it starts at: a later cancel,
        stop, brace or open hand drops it."""
        with self._lock:
            epoch = self._epoch
            blocked = self._selection_blocked()
            if blocked is not None:
                ticket, why = None, blocked
                events = self.selection.refuse(name, "accept", blocked)
            else:
                ticket, why, events = self.selection.take(
                    name, now(), onset, card_id, menu_id, self.arm.arm_count
                )
        self._emit_selection(events)
        return ticket, why, epoch

    def _announce_card(self, ticket: Ticket, epoch: int) -> tuple[bool, str]:
        """A motion card was selected: say what it does and ask for the confirming twitch.
        The confirm clock starts only once the question has finished playing. If it was
        not spoken (voice off, ``say`` missing), was cut off or replaced, took too long, or
        anything here failed, the select is undone (back to the menu, the card highlighted
        afresh) and nothing can confirm it. Blocks until the speech ends: never call it on
        the EMG or event thread. Nothing moves here."""
        try:
            ok, why = self._announce(ticket, epoch)
        except Exception as exc:  # noqa: BLE001  # never leave a card asking "confirm?"
            ok, why = False, f"I couldn't ask about that card ({type(exc).__name__})"
        if not ok:
            try:
                self._emit_selection(self.selection.unselect(ticket, now(), why))
            except Exception:  # noqa: BLE001  # the expiry still lapses it from select_t
                pass
            self.reply = why
        return ok, why

    def _announce(self, ticket: Ticket, epoch: int) -> tuple[bool, str]:
        text = announcement(ticket.card)
        if self._stale(epoch):
            return False, "cancelled meanwhile"
        result = self._say(text)
        if not getattr(result, "spoken", False):
            return False, "I couldn't say the question aloud, so a twitch can't confirm it"
        speech = self.embodied.speech
        said = getattr(speech, "last_text", None)
        cap = min(MAX_ANNOUNCE_S, float(self.selection_cfg.get("announce_max_s", ANNOUNCE_MAX_S)))
        deadline = time.monotonic() + cap
        while True:
            if self._stale(epoch):
                return False, "cancelled meanwhile"
            if not self.selection.selected(ticket):
                return False, "that question was closed"
            try:
                playing = bool(speech.speaking())
            except Exception:  # noqa: BLE001  # cannot tell: keep waiting, up to the cap
                playing = True
            if not playing:
                break
            if time.monotonic() >= deadline:
                return False, "the question took too long to say"
            time.sleep(ANNOUNCE_POLL_S)
        end = now()
        if self._stale(epoch):
            return False, "cancelled meanwhile"
        if getattr(speech, "last_text", None) != said:
            return False, "the question was cut off"  # something else spoke over it
        events = self.selection.announced(ticket, end, text)
        if not events:
            return False, "that question was closed"
        self._emit_selection(events)
        self.reply = text
        return True, text

    def _accept_card(self, ticket: Ticket, epoch: int) -> tuple[bool, str]:
        """Run a taken card through the same path as any tool request, then answer the
        question it raised with this accept. Never on the EMG or event thread."""
        by = ticket.by if ticket.by in ("twitch", VETO_WINDOW) else "ui_card"
        self._tls.epoch = epoch  # _set_pending drops the request if anything bumped since
        try:
            ok, why, motion, answered = self._run_card(ticket, epoch, by)
        except Exception as exc:  # noqa: BLE001  # the card fails; nothing was confirmed
            ok, why, motion, answered = False, f"that card failed ({type(exc).__name__})", False, False
        finally:
            self._tls.epoch = None
        if not ok or not answered:
            self.reply = why
        self._emit_selection(self.selection.finish(ticket, ok, why, motion, now(), answered))
        self._drain_selection()
        return ok, why

    def _run_card(self, ticket: Ticket, epoch: int, by: str) -> tuple[bool, str, bool, bool]:
        """(ok, why, motion, answered). The gate decides: a card's motion is always asked
        about (the tool path never auto-runs model-chosen motion), and this accept answers
        exactly the request this card just raised, pinned by identity, and only when the
        gate asks the plain confirmation the card stood for. A question with a warning the
        card never showed (gripper not proven empty, ...) is left waiting for a squeeze,
        "yes" or a click (``answered`` False); a twitch cannot answer it."""
        card = ticket.card
        if card.tool_call is None:
            return False, f"I can't do {card.label!r} yet", False, False
        name, args = card.tool_call
        if self._stale(epoch):
            return False, "cancelled meanwhile", False, False
        results = self.run_tool_calls([(name, dict(args))])
        if not results:
            return False, "that card did not run", False, False
        result = results[0]
        contract = result.contract
        if not result.ok or contract is None or result.decision is None:
            return False, result.error or self.reply or "I couldn't do that", False, False
        if contract.verb is Verb.SAY:
            if result.spoken:
                return True, contract.utterance, False, True
            return False, self.reply or "I couldn't say that", False, False
        if card.object_id is not None and contract.object_id != card.object_id:
            return False, "that card no longer matches the request", False, False
        self.selection.bind(ticket, contract.id)  # before the confirm: arm.started is ours
        with self._lock:
            pending = self.pending
        if pending is None or pending.contract is not contract:
            return False, self.reply or result.decision.reason, False, False
        plain = {CONFIRM}
        if contract.verb is Verb.SKILL:  # the card named the skill: its plain question is "<skill>? confirm"
            plain.add(f"{contract.utterance.replace('_', ' ')}? {CONFIRM}")
        if pending.decision.reason not in plain or pending.decision.clarify:
            return True, pending.decision.reason, True, False  # waits for its own answer
        self._tls.popped = None
        ok, why = self.confirm(by=by, expected=pending)
        if not ok and pending.popped and getattr(self._tls, "popped", None) is not pending:
            # Another answer (squeeze, yes, click) took this card's request first: that
            # confirm runs the card's contract, so the task keeps it and waits for it.
            return True, "confirmed by another answer", True, True
        return ok, why, True, True

    # public selection API (server endpoints; the UI equivalents of the EMG inputs)
    def selection_snapshot(self) -> dict[str, Any]:
        with self._lock:
            blocked = self._selection_blocked() if self.selection_enabled else SELECTION_OFF
        return {"enabled": self.selection_enabled, "blocked": blocked,
                **self.selection.snapshot(now())}

    def selection_step(self, by: str = "ui", menu_id: int | None = None) -> tuple[bool, str]:
        """Next card (wraps). ``by="emg"`` is a double twitch."""
        if not self.selection_enabled:
            return False, SELECTION_OFF
        name = "double_twitch" if by == "emg" else "ui_step"
        with self._lock:
            blocked = self._selection_blocked()
        if blocked is not None:
            self._emit_selection(self.selection.refuse(name, "step", blocked))
            return False, blocked
        ok, why, events = self.selection.step(name, now(), menu_id)
        self._emit_selection(events)
        return ok, why

    def selection_dismiss(
        self, card_id: str | None = None, menu_id: int | None = None
    ) -> tuple[bool, str]:
        """UI refuse of the highlighted card (only removes a card; never moves anything)."""
        if not self.selection_enabled:
            return False, SELECTION_OFF
        ok, why, events = self.selection.dismiss("ui_dismiss", now(), card_id, menu_id)
        self._emit_selection(events)
        return ok, why

    def selection_ack(
        self, card_id: Any, menu_id: Any, highlight: Any = None
    ) -> tuple[bool, str]:
        """The UI rendered ``card_id`` highlighted on ``menu_id``. With ``require_ack`` the
        reaction clock of that highlight starts here (server arrival time). ``highlight``
        (the snapshot's ``highlight``) pins the generation that was drawn."""
        if not self.selection_enabled:
            return False, SELECTION_OFF
        return self.selection.ack(card_id, now(), menu_id, highlight)

    def selection_accept(self, card_id: Any, menu_id: Any) -> tuple[bool, str]:
        """UI accept: same pins as a twitch plus the card and menu the person saw, and the
        same gates on its arrival time (UI ack + reaction_s; after a veto; for a card asking
        "confirm?", reaction_s after the announcement). The first accept of a motion card
        only selects it. Blocking (speech, gate, rules on disk): call it off the event
        loop."""
        if not self.selection_enabled:
            return False, SELECTION_OFF
        if not isinstance(card_id, str) or isinstance(menu_id, bool) or not isinstance(
            menu_id, int
        ):
            return False, "send the card id and the menu id you are accepting"
        ticket, why, epoch = self._selection_take("ui_accept", now(), card_id, menu_id)
        if ticket is None:
            return False, why
        if ticket.step == SELECT:
            return self._announce_card(ticket, epoch)
        return self._accept_card(ticket, epoch)

    def selection_set_mode(self, mode: Any) -> tuple[bool, str]:
        if mode not in (INTENT, SCANNING):
            return False, f"mode must be {INTENT} or {SCANNING}"
        self.ranker.cancel()
        ok, why, events = self.selection.set_mode(mode, now())
        self._emit_selection(events)
        return ok, why

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
            self._mark_veto(event.t)  # after the stop: plain writes, no lock, no I/O
            self._silence()  # after the stop, and on any brace (moving or not)
            if stopped and not releasing:
                self.log.emit(
                    "incident.triggered", source="brace", onset_t=event.data.get("onset_t")
                )
            if had_pending:
                self.log.emit("intent.cancelled", reason="brace (EMG)", had_pending=True)
            self._selection_veto("brace", stopped, had_pending or in_flight)  # after the stop
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
            self._mark_veto(event.t)
            self._silence()
            if stopped and not releasing:  # "that bothered me" learns the same as a flinch
                self.log.emit("incident.triggered", source="open_hand")
            if had_pending:
                self.log.emit("intent.cancelled", reason="open hand (EMG)", had_pending=True)
            self._selection_veto("open_hand", stopped, had_pending or in_flight)
        elif event.kind == "emg.burst_onset":
            # A twitch-band burst began. Off unless emg.burst_freeze is literally true
            # (selection.enabled does not turn it on). With the arm MOVING (or an
            # operator release running) it freezes now, through the brace's
            # check-and-stop, before any twitch is decided; nothing runs before the stop
            # but attribute reads (no lock wait, model, network or disk). A plan in
            # flight on a READY arm (compile, gate, handover recheck) is dropped through
            # the request epoch instead of latching a stop: a "did it register?" re-twitch
            # must not need reset + arm. The arm checks the epoch under its lock right
            # before a start, so a plan that started between the check and the bump is
            # MOVING and the second check-and-stop freezes it. Idle: nothing at all, so a
            # squeeze or twitch that follows still answers what it answers.
            if not self.burst_freeze:
                return
            releasing = self.arm.releasing
            stopped = self.arm.stop_if_active(BURST_STOP)
            dropped = False
            if not stopped and self._in_flight > 0:
                with self._lock:
                    self._epoch += 1
                dropped = True
                stopped = self.arm.stop_if_active(BURST_STOP)  # it started meanwhile
            if stopped:
                self._last_stop_t = max(self._last_stop_t, event.t)  # a burst stop
                with self._lock:  # after the stop: drop what waited on the old motion
                    had_pending = self.pending is not None
                    self.pending = None
                    self._epoch += 1
                self._silence()
                if not releasing:  # the brace that follows finds the arm already stopped
                    self.log.emit(
                        "incident.triggered", source="burst", onset_t=event.data.get("onset_t")
                    )
                if had_pending:
                    self.log.emit("intent.cancelled", reason=BURST_STOP, had_pending=True)
            elif dropped:
                self.log.emit("intent.cancelled", reason=BURST_DROP, had_pending=False)
        elif event.kind == "emg.twitch":
            self._on_twitch(event)
        elif event.kind == "emg.double_twitch":
            if self.selection_enabled:
                self.selection_step(by="emg")
        elif event.kind == "arm.started":
            self.gripper.started(event.data)
            self._selection_arm_event(event)
        elif event.kind == "arm.completed":
            self.gripper.completed(event.data)
            self._selection_arm_event(event)
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
            elif data.get("verb") == str(Verb.PICK_PLACE) and data.get("phase") == APPROACH:
                threading.Thread(  # the pick's measured-grasp stage 2, same reasons
                    target=self._continue_grasp,
                    args=(data.get("contract_id"), event.t),
                    name="h2m-grasp",
                    daemon=True,
                ).start()
        elif event.kind == "arm.gripper_released":
            self.gripper.released_by_operator()  # the measured opening reached open
        elif event.kind in ("arm.stopped", "arm.fault"):
            # Emitted after the freeze write, under the arm lock: plain assignments (no app
            # lock) void stage 2 and any release offer; the tracker takes its own lock.
            self._handover_job = None
            self._grasp_job = None
            self._release_offer = None
            self.gripper.interrupted(
                event.data.get("stage"), event.kind == "arm.fault", str(event.data.get("reason"))
            )
            self._selection_arm_event(event)  # after the freeze write: bookkeeping only
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
            # A squeeze that began within after_stop_s of a brace, an open hand or a burst
            # stop is what the startle left in the flexors, not an answer (nor a grip).
            if self.stopped_recently(onset):
                self.log.emit("emg.squeeze_rejected", reason="too soon after a stop")
                return
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
            try:
                self._selection_tick()
            except Exception as exc:  # noqa: BLE001  # cards must never stop expiry below
                self.log.emit("selection.error", detail=type(exc).__name__)
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
                "incident.tool",
                "incident.investigated",
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
                "emg.twitch",
                "emg.double_twitch",
                "selection.shown",
                "selection.step",
                "selection.accept",
                "selection.dismiss",
                "selection.degraded",
                "selection.mode",
                "selection.error",
                "task.completed",
                "task.abandoned",
                "grasp.params",
                "grasp.error",
                "vision.scan",
                "selection.auto_started",
                "selection.auto_cancelled",
                "routine.proposed",
                "routine.announced",
                "routine.auto_run",
                "routine.refused",
                "routine.done",
                "sound.event",
                "scene.event",
                "think.started",
                "think.note",
                "think.said",
                "think.suggested",
                "think.failed",
                "arm.voice",
            },
        )
        out = {
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
            "transcript": self.speech.transcript() if self.speech else None,
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
            "grasp": {
                "enabled": self.grasp_cfg.sam_width is True,
                "calibrated": self.jaw_cal.calibrated is True,
                "chip": (self._grasp_chip or {}).get("text"),
                "last": self._grasp_chip,
                "error": self.grasp_error,
            },
            "operator_release": self._release_snapshot(),
            "selection": self.selection_snapshot(),
            "session": self.recorder.snapshot(),
            "objects": [
                {"id": o["id"], "name": o["name"], "max_grip": o.get("max_grip", 1.0)}
                for o in self.cfg.get("objects", [])
            ],
            "events": [e.as_dict() for e in events],
            "think": self.thinker.snapshot(),
            **self._scene_fields(),
        }
        return self._with_proactive(out)

    def _with_proactive(self, out: dict[str, Any]) -> dict[str, Any]:
        """Additive snapshot fields from the proactive layer (UI_RUNTIME_FIELDS.md):
        ``context`` (the packet, with ``lit``), ``scene.packet`` (merged into whatever
        ``scene`` another sensor published), ``calls``, ``quiet``, ``routines``."""
        if self.proactive is None:
            return out
        try:
            fields = self.proactive.snapshot_fields(now(), out)
        except Exception as exc:  # noqa: BLE001  # the snapshot must always render
            out["proactive"] = {"error": type(exc).__name__}
            return out
        calls = [*(out.get("calls") or []), *(fields.get("calls") or [])]  # vision, speech, Wonder, Spark
        out.update(fields)
        out["calls"] = calls[-24:]
        scene = out.get("scene")
        out["scene"] = {**(scene if isinstance(scene, dict) else {}), "packet": fields["context"]}
        return out

    def _scene_fields(self) -> dict[str, Any]:
        """Additive snapshot fields for the Card and Context views (docs/UI_RUNTIME_FIELDS.md):
        ``scene`` = {masks, scan_age_s, scan_ms, stale, limit_s} from the SAM 3.1 scans and
        ``calls`` = [{model: "sam-3.1", for: "scan", ms}], newest last. None / [] without a camera."""
        scene_fn = getattr(self.vision, "scene_snapshot", None)
        calls_fn = getattr(self.vision, "calls", None)
        calls = list(calls_fn()) if callable(calls_fn) else []
        if self.speech is not None:
            # {model: "muse-voice-transcribe-1.0", for: "speech", ms}, one per final utterance
            calls.extend(self.speech.calls())
            calls.sort(key=lambda c: c.get("t") or 0)
        return {
            "scene": scene_fn() if callable(scene_fn) else None,
            # plus the learner's Muse Wonder / SAM calls from the investigation tool loop
            "calls": (calls + self.learner.snapshot()["calls"])[-24:],
        }
