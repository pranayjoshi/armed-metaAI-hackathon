"""Embodied assistant: Muse Spark tool calls routed through the step contract and gate.

The model only picks a tool and names its arguments. It never supplies joint values,
speed, grip or confidence: gestures come from taught poses in ``configs/poses.yaml``
(``gestures:``), speed is fixed here, and every tool is checked against a strict
schema first. Each tool builds a ``StepContract`` and goes through ``safety.decide``.
The grammar path in ``h2m.app`` is untouched and stays the offline path.

Wiring (see report): build ``Embodied(...)``, route Muse tool calls to
``handle_tool_call`` and call ``cutoff()`` wherever stop/brace freezes the arm.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from h2m.contract import StepContract, Verb
from h2m.robot.backend import GRIPPER, JOINTS
from h2m.robot.skills import Plan, Pose, PoseError, PoseLibrary, Waypoint
from h2m.safety import Decision, Verdict, decide
from h2m.speech_out import SpeechOut

GESTURES = ("nod", "tilt", "shake", "look_at_user", "thinking")
MAX_SAY_CHARS = 280
TOOL_CONFIDENCE = 0.9  # fixed: a schema-valid tool call is explicit; the model's own odds never count
GESTURE_SPEED = 0.25
USER_TARGET = "user"

WorldFn = Callable[[StepContract], dict[str, Any]]
SceneFn = Callable[[], Sequence[Mapping[str, Any]]]
RunPlanFn = Callable[[StepContract, Plan], None]
VisionFn = Callable[[str, Any], str]


def _tool(name: str, description: str, properties: dict[str, Any], required: Sequence[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


TOOLS: list[dict[str, Any]] = [
    _tool("pick_place", "Pick a catalog object and put it in the tray.",
          {"object_id": {"type": "string", "maxLength": 64}}, ["object_id"]),
    _tool("place_near_user",
          "Set the object I am holding on the marked spot near the user. Never into a hand.",
          {}, []),
    _tool("look_at", "Look at a catalog object, or at the user with target 'user'.",
          {"target": {"type": "string", "maxLength": 64}}, ["target"]),
    _tool("gesture", "Make a small expressive motion near home.",
          {"name": {"type": "string", "enum": list(GESTURES)}}, ["name"]),
    _tool("say", "Say something aloud.",
          {"text": {"type": "string", "maxLength": MAX_SAY_CHARS}}, ["text"]),
]
TOOL_NAMES = frozenset(t["function"]["name"] for t in TOOLS)


class ToolError(ValueError):
    """Rejected before anything is built: unknown tool or bad arguments."""


def validate_args(name: str, args: Any) -> dict[str, Any]:
    """Strict check against TOOLS: known tool, object args, no extras, required present,
    exact types (bool is not a number or string), enums and lengths enforced."""
    if not isinstance(name, str) or name not in TOOL_NAMES:
        raise ToolError(f"unknown tool: {name!r}")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError(f"{name}: arguments must be an object")
    schema = next(t["function"]["parameters"] for t in TOOLS if t["function"]["name"] == name)
    props = schema["properties"]
    extra = sorted(set(args) - set(props))
    if extra:
        raise ToolError(f"{name}: unexpected arguments: {', '.join(map(str, extra))}")
    for key in schema["required"]:
        if key not in args:
            raise ToolError(f"{name}: missing argument {key!r}")
    for key, value in args.items():
        spec = props[key]
        if spec["type"] == "string":
            if not isinstance(value, str):
                raise ToolError(f"{name}.{key} must be a string")
            if "enum" in spec and value not in spec["enum"]:
                raise ToolError(f"{name}.{key} must be one of {', '.join(spec['enum'])}")
            if len(value) > spec.get("maxLength", 64):
                raise ToolError(f"{name}.{key} is too long")
            if not value.strip():
                raise ToolError(f"{name}.{key} is empty")
    return dict(args)


# ── taught gestures ────────────────────────────────────────────────────────────


def load_gestures(path: Path | None) -> dict[str, tuple[Pose, ...]]:
    """Read the ``gestures:`` section of a poses file. Unknown gesture names and
    malformed poses raise PoseError (nothing is silently dropped or defaulted)."""
    if path is None:
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    out: dict[str, tuple[Pose, ...]] = {}
    for name, steps in (data.get("gestures") or {}).items():
        if name not in GESTURES:
            raise PoseError(f"unknown gesture in poses file: {name}")
        poses = []
        for i, raw in enumerate(steps or []):
            label = str((raw or {}).get("name") or f"{name}_{i}")
            joints = {str(k): float(v) for k, v in (raw or {}).get("joints", {}).items()}
            if sorted(joints) != sorted(JOINTS):
                raise PoseError(f"gesture {name}/{label} must define exactly {JOINTS}")
            poses.append(Pose(f"gesture.{name}.{label}", joints, bool((raw or {}).get("taught"))))
        if not poses:
            raise PoseError(f"gesture {name} has no steps")
        out[str(name)] = tuple(poses)
    return out


def gesture_deviation(steps: Sequence[Pose], home: Pose) -> float:
    """Largest |joint - home| over every step and arm joint (gripper is never commanded)."""
    return max(
        abs(p.joints[j] - home.joints[j]) for p in steps for j in JOINTS if j != GRIPPER
    )


def plan_gesture(name: str, steps: Sequence[Pose], home: Pose) -> Plan:
    """Visit the taught steps, then return to home. Waypoints omit the gripper joint."""
    def wp(label: str, pose: Pose) -> Waypoint:
        return Waypoint(label, {j: v for j, v in pose.joints.items() if j != GRIPPER}, "gesture")

    waypoints = [wp(p.name, p) for p in steps]
    waypoints.append(wp("home", home))
    return Plan(f"gesture.{name}", tuple(waypoints), (*steps, home))


def plan_place_near_user(lib: PoseLibrary, open_pct: float) -> Plan:
    """Stage 1 of the handover: carry the held object from the start pose to above the
    marked spot and stop there. The gripper is not commanded. Lowering and release are
    a separate plan (``plan_place_lower``) that only runs after a fresh look at the spot.
    ``open_pct`` is unused here; it is kept so both stages are built the same way."""
    del open_pct
    above = lib.pose("handover_above")
    lib.pose("handover_release")  # both stages must be defined before stage 1 may start
    lib.pose("home")
    return Plan(
        "place_near_user",
        (Waypoint("handover_above", _arm_joints(above), "carry"),),
        (above,),
    )


def plan_place_lower(lib: PoseLibrary, open_pct: float) -> Plan:
    """Stage 2: lower onto the spot, open, retreat and go home. The gripper only opens
    at the taught release pose; it never goes toward a hand."""
    above, release, home = (lib.pose(n) for n in ("handover_above", "handover_release", "home"))
    return Plan(
        "place_near_user.lower",
        (
            Waypoint("handover_release", _arm_joints(release), "lower"),
            Waypoint("open", {GRIPPER: open_pct}, "release", gripper_only=True, dwell_s=0.3),
            Waypoint("retreat", _arm_joints(above), "return"),
            Waypoint("home", dict(home.joints), "return"),
        ),
        (release, above, home),
    )


def _arm_joints(pose: Pose) -> dict[str, float]:
    return {j: v for j, v in pose.joints.items() if j != GRIPPER}


# ── dispatcher ─────────────────────────────────────────────────────────────────


@dataclass
class ToolResult:
    tool: str
    ok: bool  # false only for schema/unknown-tool rejection
    error: str | None = None
    contract: StepContract | None = None
    decision: Decision | None = None
    plan: Plan | None = None  # for gesture / place_near_user; None means the app plans it
    spoken: bool = False
    executed: bool = False  # run_plan / speech was actually started

    @property
    def needs_confirmation(self) -> bool:
        return self.decision is not None and self.decision.verdict is Verdict.ASK

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "error": self.error,
            "contract": self.contract.as_dict() if self.contract else None,
            "decision": self.decision.as_dict() if self.decision else None,
            "plan": self.plan.skill if self.plan else None,
            "spoken": self.spoken,
            "executed": self.executed,
        }


class Embodied:
    """``world_fn(contract)`` returns the gate's world dict (app._world, extended). For
    the new verbs it must carry ``gripper_empty``, ``holding``, ``spot_clear``,
    ``route_clear`` (all must be exactly True to count; missing fails closed).

    ``run_plan(contract, plan)`` is called only on an APPROVE verdict for gestures. All
    other motion is returned for the app's existing confirm-then-execute pipeline."""

    def __init__(
        self,
        lib: PoseLibrary,
        cfg: Mapping[str, Any],
        world_fn: WorldFn,
        *,
        scene_fn: SceneFn | None = None,
        run_plan: RunPlanFn | None = None,
        speech: SpeechOut | None = None,
        vision: VisionFn | None = None,
    ):
        self.lib = lib
        self.cfg = cfg
        self.safety_cfg: dict[str, Any] = dict(cfg.get("safety", {}))
        self.catalog = {str(o["id"]): o for o in cfg.get("objects", [])}
        self.world_fn = world_fn
        self.scene_fn = scene_fn or (lambda: [])
        self.run_plan = run_plan
        self.speech = speech if speech is not None else SpeechOut()
        self.vision = vision
        self.gestures = load_gestures(lib.path)

    @property
    def tools(self) -> list[dict[str, Any]]:
        return TOOLS

    def cutoff(self) -> None:
        """Silence speech immediately. Call on stop and on brace."""
        self.speech.cutoff()

    # public entry ---------------------------------------------------------------
    def handle_tool_call(self, name: str, args: Any = None) -> ToolResult:
        try:
            clean = validate_args(name, args)
        except ToolError as exc:
            return ToolResult(str(name), False, error=str(exc))
        try:
            return getattr(self, f"_do_{name}")(**clean)
        except ToolError as exc:
            return ToolResult(name, False, error=str(exc))

    def answer_scene(self, question: str, frame: Any) -> ToolResult:
        """Answer a scene question from the supplied frame (never from memory). Without a
        frame or a vision callable it says so instead of guessing. The reply goes out
        through the ``say`` tool and the gate."""
        if frame is None or self.vision is None:
            return self.handle_tool_call("say", {"text": "I can't see the scene right now."})
        try:
            text = str(self.vision(question, frame)).strip()
        except Exception:  # noqa: BLE001  # a failing model must not break the loop
            text = ""
        text = text[:MAX_SAY_CHARS] or "I'm not sure what I'm looking at."
        return self.handle_tool_call("say", {"text": text})

    # tools ----------------------------------------------------------------------
    def _contract(self, verb: Verb, **kw: Any) -> StepContract:
        return StepContract(verb=verb, confidence=TOOL_CONFIDENCE, source="llm", **kw)

    def _scene_entry(self, object_id: str) -> Mapping[str, Any] | None:
        return next((o for o in self.scene_fn() if o.get("id") == object_id), None)

    def _object_contract(self, verb: Verb, object_id: str) -> StepContract:
        item = self.catalog.get(object_id)
        if item is None:
            raise ToolError(f"unknown object: {object_id}")
        seen = self._scene_entry(object_id)
        xy = seen.get("table_xy") if seen else None
        return self._contract(
            verb,
            object_id=object_id,
            object_name=str(item.get("name", object_id)),
            table_xy=(float(xy[0]), float(xy[1])) if xy else None,
        )

    def _do_pick_place(self, object_id: str) -> ToolResult:
        contract = self._object_contract(Verb.PICK_PLACE, object_id)
        decision = decide(contract, self.world_fn(contract), self.safety_cfg)
        return ToolResult("pick_place", True, contract=contract, decision=decision)

    def _do_look_at(self, target: str) -> ToolResult:
        if target == USER_TARGET:
            return self._do_gesture("look_at_user", tool="look_at")
        contract = self._object_contract(Verb.POINT, target)  # existing confirm path
        decision = decide(contract, self.world_fn(contract), self.safety_cfg)
        return ToolResult("look_at", True, contract=contract, decision=decision)

    def _do_place_near_user(self) -> ToolResult:
        contract = self._contract(Verb.PLACE_NEAR_USER, speed=self._gesture_speed())
        open_pct = float(self.cfg.get("arm", {}).get("gripper_open", 60.0))
        try:
            plan: Plan | None = plan_place_near_user(self.lib, open_pct)
        except KeyError:
            plan = None
        decision = decide(contract, self.world_fn(contract), self.safety_cfg)
        if plan is None:
            decision = Decision(Verdict.HOLD, "the handover spot is not defined", decision.checks)
        return ToolResult("place_near_user", True, contract=contract, decision=decision, plan=plan)

    def _gesture_speed(self) -> float:
        cap = float(self.safety_cfg.get("gesture_speed_cap", 0.3))
        return min(GESTURE_SPEED, cap)

    def _do_gesture(self, name: str, tool: str = "gesture") -> ToolResult:
        contract = self._contract(Verb.GESTURE, speed=self._gesture_speed(), utterance=name)
        steps = self.gestures.get(name)
        if steps is None:
            decision = Decision(Verdict.HOLD, f"gesture {name} is not taught", {})
            return ToolResult(tool, True, contract=contract, decision=decision)
        home = self.lib.pose("home")
        plan = plan_gesture(name, steps, home)
        world = dict(self.world_fn(contract))
        # Computed here from the taught poses, never from the model or the caller.
        world["gesture_max_dev_deg"] = gesture_deviation(steps, home)
        decision = decide(contract, world, self.safety_cfg)
        result = ToolResult(tool, True, contract=contract, decision=decision, plan=plan)
        if decision.verdict is Verdict.APPROVE and self.run_plan is not None:
            self.run_plan(contract, plan)
            result.executed = True
        return result

    def _do_say(self, text: str) -> ToolResult:
        contract = self._contract(Verb.SAY, utterance=text)
        decision = decide(contract, {}, self.safety_cfg)
        result = ToolResult("say", True, contract=contract, decision=decision)
        if decision.verdict is Verdict.APPROVE:
            result.spoken = self.speech.speak(text)
            result.executed = result.spoken
        return result


__all__ = ["TOOLS", "GESTURES", "Embodied", "ToolError", "ToolResult", "validate_args",
           "load_gestures", "plan_gesture", "gesture_deviation", "plan_place_near_user",
           "plan_place_lower"]
