"""Acting without being asked (docs/PROACTIVE.md): the clock, the room and the routines
turn into cards, under the trust ladder, with manners.

``Proposer`` sits beside the selection machinery in ``h2m.app``:

- It keeps the context packet (``h2m.context``) fresh on a cadence and publishes it
  (``state.context`` and ``state.scene.packet``), with the ``calls`` chips for the Spark
  rankings it asked for.
- ``extras()`` gives the ranker the trigger-driven options for the next menu: routines
  that are due (``h2m.routines``), a request from another speaker (``asked_by``), a sound
  or a scene event. They are a closed list built from the skills file, the catalog and
  the routines, never from model text. Spark ranks them with the rest and may write a
  one-line reason per card; code decides what is allowed.
- ``tick()`` runs on the app loop: a routine on the announce / do rung opens a single
  pre-approved card with a veto ring (``Selection.start_veto``); when the ring runs out
  untouched the card goes through the same gate and confirmation as any card
  (``Ticket.by == "veto_window"``). A brace in the window cancels it quietly and writes a
  "don't propose" entry. A trigger that arrives while an untouched menu is up supersedes
  it so the new card shows at once.

Manners (``proactive:`` in configs/h2m.yaml): no proposal while the arm moves or a stop is
latched, none within ``safety.after_stop_s`` of a brace, at most one unsolicited card per
``propose_min_gap_s`` unless the trigger is a sound, another person or an overdue routine,
nothing in quiet hours, nothing while request-only is set. The ladder
(``h2m.safety.ladder``) only ever lowers a rung: motion near a person or carrying is
always a card, a request from someone else is always a card for the wearer, and
announce-then-do needs ``trust.announce_enabled`` literally true.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from h2m.context import ROWS, build_packet, facts, for_model
from h2m.core import ROOT, Event, now
from h2m.intent import LABEL_MAX, Candidate, Option, _to_candidate, skill_option
from h2m.robot import ArmState
from h2m.routines import EVENTS, RoutineBook, in_quiet_hours, parse_quiet_hours
from h2m.rules import RulesUnavailable
from h2m.safety import RUNGS, Rung, action_rung, ladder
from h2m.selection import AUTO_RUNGS, VETOES, Ticket

SPARK_MODEL = "muse-spark-1.3"
CALL_KEEP_S = 1.5  # a landed call stays in state.calls this long (UI_RUNTIME_FIELDS.md)
MAX_CALLS = 8
NOTE_S = 4.0  # the quiet strip note lives this long
SOUND_EVENTS = (  # mic labels (sound.event) -> routine trigger names
    (("buzz", "ring", "vibrat"), "phone_buzz"),
    (("knock",), "knock"),
    (("door", "bell"), "door"),
)
ASKED_WINDOW_S = 60.0  # an "X asked" card lapses after this
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "routines_file": "configs/routines.yaml",
    "state_dir": "data/proactive",
    "profile": "desk",
    "packet_every_s": 1.0,
    "propose_min_gap_s": 120.0,
    "refuse_cooldown_min": 30.0,
    "dont_propose_hours": 24.0,
    "quiet_hours": None,
    "veto_window_s": 5.0,
    "request_only": False,
    "speak": True,
}


class CallLog:
    """Live Meta API calls for the chips: in flight (no ``ms``) and just landed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: deque[dict[str, Any]] = deque(maxlen=MAX_CALLS)

    def note(self, info: Mapping[str, Any]) -> None:
        model = str(info.get("model") or SPARK_MODEL)
        row = str(info.get("for") or "rank")
        with self._lock:
            if "ms" in info:
                for item in self._items:
                    if item["model"] == model and item["for"] == row and item.get("ms") is None:
                        item["ms"] = float(info["ms"])
                        item["landed_t"] = now()
                        return
                self._items.append({"model": model, "for": row, "ms": float(info["ms"]),
                                    "landed_t": now()})
            else:
                started = info.get("started_at_ms")
                self._items.append({"model": model, "for": row, "ms": None,
                                    "started_at_ms": started, "started_t": now()})

    def snapshot(self, t: float) -> list[dict[str, Any]]:
        out = []
        with self._lock:
            for item in list(self._items):
                if item.get("ms") is None:
                    out.append({"model": item["model"], "for": item["for"],
                                "elapsed_ms": round((t - item["started_t"]) * 1000),
                                "started_at_ms": item.get("started_at_ms")})
                elif t - item["landed_t"] <= CALL_KEEP_S:
                    out.append({"model": item["model"], "for": item["for"], "ms": round(item["ms"], 1)})
                else:
                    self._items.remove(item)
        return out


@dataclass
class Trigger:
    kind: str  # "routine" | "asked" | "sound" | "scene"
    t: float  # runtime clock when it arrived
    routine: str | None = None
    event: str | None = None  # a name from routines.EVENTS
    asked_by: str | None = None
    text: str | None = None  # the request heard
    label: str | None = None  # the sound's label
    row: str = "routines"  # packet row that produced it (the chip's ``for`` and the lit row)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    @property
    def unsolicited(self) -> bool:
        """Only routines and scene changes count against the proposal gap: a sound or
        another person asking is already an interruption."""
        return self.kind in ("routine", "scene")


def _cfg(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    out = dict(DEFAULTS)
    for k, v in (raw or {}).items():
        if k in out:
            out[k] = v
    return out


class Proposer:
    """One per app. Reads the app's sensors and selection; never touches the arm."""

    def __init__(
        self,
        app: Any,
        cfg: Mapping[str, Any],
        *,
        wall: Callable[[], float] = time.time,
        routines_path: Any = None,
        state_dir: Any = None,
    ) -> None:
        self.app = app
        self.cfg = _cfg(cfg.get("proactive"))
        self.trust: dict[str, Any] = dict(cfg.get("trust") or {})
        self.enabled = self.cfg.get("enabled") is True
        self.wall = wall
        skills = list(getattr(getattr(app, "embodied", None), "skills", {}) or {})
        path = routines_path if routines_path is not None else ROOT / str(self.cfg["routines_file"])
        state = state_dir if state_dir is not None else ROOT / str(self.cfg["state_dir"])
        self.book = RoutineBook(
            path,
            skills=skills,
            state_dir=state,
            profile=str(self.cfg.get("profile") or "desk"),
            clock=wall,
            refuse_cooldown_s=float(self.cfg["refuse_cooldown_min"]) * 60,
            dont_propose_s=float(self.cfg["dont_propose_hours"]) * 3600,
        )
        if self.book.quiet is None:
            self.book.quiet = parse_quiet_hours(self.cfg.get("quiet_hours"))
        self.book.request_only = self.cfg.get("request_only") is True
        self.calls = CallLog()
        self.packet: dict[str, Any] = {}
        self._packet_t = -math.inf
        self._last_unsolicited_t = -math.inf
        self._triggers: deque[Trigger] = deque(maxlen=16)
        self._cards: dict[str, Candidate] = {}  # card id -> the extra it came from
        self._note: tuple[str, float] | None = None
        self._lock = threading.Lock()
        self._seen_due: set[str] = set()
        self._reasons: dict[str, str] = {}  # card id -> Spark's reason for the menu that is up
        ranker = getattr(app, "ranker", None)
        if ranker is not None:
            ranker.on_call = self.calls.note
            inner = ranker.on_update

            def on_update(gen: int, result: Any) -> None:
                self._note_reasons(result)
                inner(gen, result)

            ranker.on_update = on_update
        app.log.subscribe(self._on_event)

    def _note_reasons(self, result: Any) -> None:
        try:
            for c in getattr(result, "candidates", []) or []:
                if getattr(c, "reason", None):
                    self._reasons[c.id] = str(c.reason)
        except Exception:  # noqa: BLE001
            pass

    # ── config and views ────────────────────────────────────────────────────
    @property
    def gap_s(self) -> float:
        return max(0.0, float(self.cfg.get("propose_min_gap_s", 120.0)))

    @property
    def veto_window_s(self) -> float:
        return float(self.cfg.get("veto_window_s", 5.0))

    def quiet_now(self) -> bool:
        return in_quiet_hours(self.wall(), self.book.quiet)

    def set_request_only(self, value: bool) -> None:
        self.book.request_only = bool(value)
        self.app.log.emit("proactive.request_only", value=bool(value))

    def action_rung(self, contract: Any) -> str:
        """The person's rung for what a contract does (world["rung"] for the gate)."""
        verb = str(getattr(contract, "verb", "") or "")
        obj = getattr(contract, "object_id", None)
        if verb == "skill":
            name = str(getattr(contract, "utterance", "") or "")
            aid = f"{name}:{obj}" if obj else name
            return action_rung(aid, name, self.trust.get("actions"), "ask")
        if verb == "point":
            verb = "look_at"
        aid = f"{verb}:{obj}" if obj else verb
        return action_rung(aid, verb, self.trust.get("actions"), "ask")

    def snapshot_fields(self, t: float, out: dict[str, Any] | None = None) -> dict[str, Any]:
        """The additive snapshot fields (UI_RUNTIME_FIELDS.md): context, calls, quiet,
        routines, proactive. With ``out`` (the snapshot so far) the reasons Spark gave for
        the cards that are up are added to their evidence (the state machine keeps the
        highlighted card's identity, so its reason is layered on here)."""
        packet = dict(self.refresh(t))
        packet["lit"] = self._lit()
        sel = (out or {}).get("selection")
        if isinstance(sel, dict) and self._reasons:
            for c in sel.get("cards") or []:
                reason = self._reasons.get(str(c.get("id")))
                ev = list(c.get("evidence") or [])
                if reason and not any(e.get("row") == "rank" for e in ev if isinstance(e, dict)):
                    c["evidence"] = ev + [{"row": "rank", "text": reason, "source": SPARK_MODEL}]
        out: dict[str, Any] = {
            "context": packet,
            "calls": self.calls.snapshot(t),
            "routines": self.book.snapshot(self.wall()),
            "proactive": {
                "enabled": self.enabled,
                "quiet_now": self.quiet_now(),
                "request_only": self.book.request_only,
                "announce_enabled": self.trust.get("announce_enabled") is True,
                "last_unsolicited_age_s": None if not math.isfinite(self._last_unsolicited_t)
                else round(t - self._last_unsolicited_t, 1),
                "pending_triggers": [tr.kind for tr in list(self._triggers)],
            },
        }
        note = self._note
        if note is not None and t - note[1] <= NOTE_S:
            out["quiet"] = {"text": note[0]}
        return out

    def _lit(self) -> list[str]:
        """Row ids of the packet rows that produced the highlighted card."""
        menu = self.app.selection.menu
        card = menu.highlighted if menu is not None else None
        if card is None:
            return []
        rows = []
        for e in card.evidence:
            row = e.get("row")
            if row in ROWS and row not in rows:
                rows.append(row)
        return rows

    # ── the packet ──────────────────────────────────────────────────────────
    def refresh(self, t: float, force: bool = False) -> dict[str, Any]:
        if force or t - self._packet_t >= float(self.cfg.get("packet_every_s", 1.0)):
            self.packet = self._build_packet(t)
            self._packet_t = t
        return self.packet

    def _build_packet(self, t: float) -> dict[str, Any]:
        app = self.app
        vision = app.vision.snapshot() if app.vision is not None else {
            "virtual": True, "objects": app.visible_objects()}
        scene = getattr(app.vision, "scene_snapshot", None)
        scene_data = None
        if callable(scene):
            try:
                scene_data = scene()
            except Exception:  # noqa: BLE001  # another sensor's view never breaks the packet
                scene_data = None
        speech = None
        if app.speech is not None:
            try:
                speech = app.speech.snapshot()
            except Exception:  # noqa: BLE001
                speech = None
        try:
            rules: Any = app.rules.list()
        except RulesUnavailable:
            rules = []
        held = app.gripper.state()
        return build_packet(
            t_now=t,
            wall=self.wall(),
            scene=scene_data if isinstance(scene_data, Mapping) else None,
            vision=vision,
            speech=speech,
            events=app.log.recent(200),
            rules=rules,
            refusals=self.book.refusals(),
            routines_due=self.book.due_rows(),
            holding=held.object_id if held.holding else None,
            slot_of=self._slot_of,
        )

    def _slot_of(self, xy: Any) -> str | None:
        slot = self.app.arm.slot_for((float(xy[0]), float(xy[1])))
        return None if slot is None else str(slot.slot_id).removeprefix("slot_")

    def facts(self) -> dict[str, Any]:
        held = self.app.gripper.state()
        out = facts(self.packet)
        out["holding"] = held.object_id if held.holding else None
        out["holding_known"] = held.empty or held.holding
        return out

    # ── events in ───────────────────────────────────────────────────────────
    def _on_event(self, event: Event) -> None:
        if not self.enabled:
            return
        try:
            self._handle(event)
        except Exception:  # noqa: BLE001  # bookkeeping never breaks the pipeline
            pass

    def _handle(self, event: Event) -> None:
        kind, data = event.kind, event.data
        if kind == "speech.utterance" and data.get("asked_by"):
            who = str(data.get("asked_by"))[:30]
            text = str(data.get("text") or "")[:200]
            self._triggers.append(Trigger(
                "asked", event.t, asked_by=who, text=text, row="present",
                evidence=[{"row": "present", "text": f"{who} asked: {text}"[:LABEL_MAX]}]))
        elif kind == "sound.event":
            label = str(data.get("label") or "")[:30].lower()
            name = next((n for words, n in SOUND_EVENTS if any(w in label for w in words)), None)
            if name:
                self._triggers.append(Trigger(
                    "sound", event.t, event=name, label=label, row="sounds",
                    evidence=[{"row": "sounds", "text": label, "age_s": 0}]))
        elif kind == "scene.event" and data.get("event") in EVENTS:
            what = str(data.get("what") or data.get("event")).replace("_", " ")[:40]
            self._triggers.append(Trigger(
                "scene", event.t, event=str(data["event"]), row="objects",
                evidence=[{"row": "objects", "text": what}]))
        elif kind == "selection.shown" and not data.get("upgraded"):
            self._reasons.clear()  # a new menu: last menu's reasons are not these cards'
        elif kind == "task.completed":
            card = self._cards.get(str(data.get("card")))
            if card is not None and card.routine:
                self.book.done(card.routine)
                self._packet_t = -math.inf
                self.app.log.emit("routine.done", name=card.routine, card=card.id,
                                  auto=bool(data.get("auto")))
        elif kind == "selection.dismiss" and data.get("by") in VETOES and data.get("ok"):
            card = self._cards.get(str(data.get("card")))
            if card is None:
                return
            if card.routine:
                ref = self.book.refused(card.routine)
            else:
                ref = self.book.dont_propose(card.id, what=card.label)
            self._note = (f"Noted. {card.label.rstrip('?')} stays off.", now())
            self._packet_t = -math.inf  # the rules row changed: rebuild on the next read
            self.app.log.emit("routine.refused", name=card.routine, card=card.id,
                              vetoed=bool(data.get("vetoed")),
                              dont_propose=ref.text if ref else None)

    # ── candidates ──────────────────────────────────────────────────────────
    def _rung(self, opt: Option, skill: Any, asked_by: str | None) -> Rung:
        base = opt.skill if opt.kind == "skill" else opt.kind
        aid = f"{base}:{opt.object_id}" if opt.object_id else base
        want = opt.rung or action_rung(aid, base, self.trust.get("actions"), "ask")
        motion = opt.kind != "say"
        near = bool(getattr(skill, "near_person", False))
        carries = opt.kind in ("pick_place", "place_near_user") or bool(getattr(skill, "carries", False))
        return ladder(want, motion=motion, near_person=near, carries=carries, asked_by=asked_by,
                      quiet=self.quiet_now(), cfg=self.trust)

    def _option_for(
        self, action: str, object_id: str | None, scene: Sequence[Mapping[str, Any]], **extra: Any
    ) -> Option | None:
        """A closed-list option for a routine's or a request's action, or None when the
        action cannot be offered now (object not visible, unknown skill)."""
        skills = self.app.embodied.skills
        entry = next((o for o in scene if o.get("id") == object_id), None) if object_id else None
        name = str(entry.get("name") if entry else object_id or "").strip()
        xy = tuple(entry["table_xy"]) if entry and entry.get("table_xy") is not None else None
        if object_id and (entry is None or xy is None or not self.app._card_reachable(entry)):
            return None
        if action in skills:
            skill = skills[action]
            if skill.requires_object and not object_id:
                return None
            label = extra.pop("label", None) or (
                f"{action.replace('_', ' ').capitalize()} the {name}" if object_id
                else action.replace("_", " ").capitalize()
            )
            return skill_option(action, label, object_id=object_id, object_name=name or None,
                                table_xy=xy, **extra)
        if action in ("pick_place", "look_at"):
            if not object_id:
                return None
            label = extra.pop("label", None) or (
                f"Bring {name}" if action == "pick_place" else f"Look at {name}")
            return Option((action, object_id), action, label[:LABEL_MAX], object_id=object_id,
                          object_name=name, table_xy=xy, **extra)
        if action == "say":
            phrases = self.app.selection_cfg.get("phrases") or {}
            text = phrases.get(object_id or "")
            if not isinstance(text, str) or not text.strip():
                return None
            label = extra.pop("label", None) or f"Say: {text}"
            return Option(("say", object_id), "say", label[:LABEL_MAX], text_id=object_id,
                          text=text, **extra)
        return None

    def _routine_option(self, routine: Any, scene: Sequence[Mapping[str, Any]], asked_by: str | None = None) -> Option | None:
        when = self.packet.get("clock") or ""
        why = f"routine {routine.name} is due ({routine.schedule_text})"
        label = routine.label or None
        evidence = [
            {"row": "routines", "text": f"{routine.name.replace('_', ' ')} · due · {routine.schedule_text}"},
            {"row": "clock", "text": str(when)},
        ]
        opt = self._option_for(
            routine.action, routine.object, scene, label=label, trigger=True,
            evidence=tuple(evidence), routine=routine.name, rung=routine.mode, why=why,
            asked_by=asked_by,
        )
        if opt is None:
            return None
        skill = self.app.embodied.skills.get(routine.action)
        rung = self._rung(opt, skill, asked_by)
        if rung.rung == "never":
            return None
        return replace(opt, rung=rung.rung)

    def _asked_option(self, tr: Trigger, scene: Sequence[Mapping[str, Any]]) -> Option | None:
        """Match another person's words against the closed list: a catalog object (bring
        it), a routine's name, or a skill's name. Nothing matched: no card."""
        text = (tr.text or "").lower()
        if not text:
            return None
        for routine in self.book.list():
            if routine.enabled and routine.mode != "never" and routine.name.replace("_", " ") in text:
                return self._routine_option(routine, scene, asked_by=tr.asked_by)
        for entry in scene:
            name = str(entry.get("name") or "").lower()
            oid = str(entry.get("id") or "").lower()
            if (name and name in text) or (oid and oid in text):
                opt = self._option_for("pick_place", entry.get("id"), scene,
                                       label=f"{tr.asked_by} asked: bring {name or oid}",
                                       trigger=True, evidence=tuple(tr.evidence),
                                       asked_by=tr.asked_by, why=f"{tr.asked_by} asked for the {name or oid}")
                if opt is not None:
                    rung = self._rung(opt, None, tr.asked_by)
                    return None if rung.rung == "never" else replace(opt, rung=rung.rung)
        for name, skill in self.app.embodied.skills.items():
            if not skill.requires_object and name.replace("_", " ") in text:
                opt = self._option_for(name, None, scene, label=f"{tr.asked_by} asked: {name.replace('_', ' ')}",
                                       trigger=True, evidence=tuple(tr.evidence), asked_by=tr.asked_by,
                                       why=f"{tr.asked_by} asked for {name}")
                if opt is not None:
                    rung = self._rung(opt, skill, tr.asked_by)
                    return None if rung.rung == "never" else replace(opt, rung=rung.rung)
        return None

    def _idle_for_cards(self, state: Any) -> bool:
        """No proposal while the arm moves, a stop is latched, or right after a brace."""
        if state is not ArmState.READY or self.app.arm.state is not ArmState.READY:
            return False
        return not self.app.stopped_recently(now())

    def extras(self, state: Any) -> tuple[list[Option], str, dict[str, Any] | None]:
        """Trigger-driven options for the next menu (ask rung only; announce / do go
        through ``tick``), the chip's ``for`` and the packet for the model. Marks the
        routines it offers as proposed. Honest empties: nothing due, nothing offered."""
        if not self.enabled:
            return [], "rank", None
        t = now()
        packet = self.refresh(t)
        context = for_model(packet)
        if not self._idle_for_cards(state) or self.book.request_only or self.quiet_now():
            return [], "rank", context
        scene = self.app.visible_objects()
        out: list[Option] = []
        row = "rank"
        # requests from other people and sounds first: they are interruptions already
        pending = list(self._triggers)
        self._triggers.clear()
        for tr in pending:
            if t - tr.t > ASKED_WINDOW_S:
                continue
            opt = None
            if tr.kind == "asked":
                opt = self._asked_option(tr, scene)
            elif tr.kind in ("sound", "scene") and tr.event:
                for routine in self.book.on_event(tr.event, self.facts()):
                    opt = self._routine_option(routine, scene)
                    if opt is not None:
                        opt = replace(opt, evidence=tuple(tr.evidence) + opt.evidence)
                        break
            if opt is not None and opt.rung not in AUTO_RUNGS:
                out.append(opt)
                row = tr.row
        # routines due by the clock, under the proposal gap
        for routine in self.book.due(self.facts()):
            if routine.mode in AUTO_RUNGS and self.trust.get("announce_enabled") is True:
                continue  # tick() opens those with a veto ring
            overdue = self.book.overdue_s(routine)
            if t - self._last_unsolicited_t < self.gap_s and overdue < self.gap_s and not out:
                continue
            opt = self._routine_option(routine, scene)
            if opt is None or opt.rung in AUTO_RUNGS:
                continue
            out.append(opt)
            if row == "rank":
                row = "routines"
        if out:
            self._last_unsolicited_t = max(self._last_unsolicited_t, t) if any(
                o.routine for o in out) else self._last_unsolicited_t
            for opt in out:
                if opt.routine:
                    self.book.proposed(opt.routine)
                self.app.log.emit("routine.proposed", name=opt.routine, card=opt.id,
                                  rung=opt.rung, asked_by=opt.asked_by, label=opt.label)
        return out[:3], row, context

    def remember(self, cards: Sequence[Candidate]) -> None:
        """The cards that went up: keep the extras so their outcome reaches the book."""
        for c in cards:
            if c.routine or c.asked_by or c.evidence:
                self._cards[c.id] = c
        while len(self._cards) > 32:
            self._cards.pop(next(iter(self._cards)))

    # ── the loop ────────────────────────────────────────────────────────────
    def tick(self, state: Any) -> None:
        """App loop (``_selection_tick``), after the queued events were emitted and before
        the normal menu goes up. Never on the arm or EMG thread."""
        if not self.enabled:
            return
        app, sel = self.app, self.app.selection
        t = now()
        self.refresh(t)
        # 1. a ring that ran out untouched: run the card through the gate
        ticket = sel.veto_due(t)
        if ticket is not None:
            self._run_auto(ticket)
            return
        if not self._idle_for_cards(state) or self.book.request_only or self.quiet_now():
            return
        # 2. a pre-approved routine due: one card, a veto ring
        if self.trust.get("announce_enabled") is True:
            for routine in self.book.due(self.facts()):
                if routine.mode not in AUTO_RUNGS:
                    continue
                if t - self._last_unsolicited_t < self.gap_s and self.book.overdue_s(routine) < self.gap_s:
                    continue
                if self._announce(routine, t):
                    return
        # 3. a trigger waiting while an untouched menu is up: make room for it
        if self._triggers or any(
            r.mode not in AUTO_RUNGS for r in self.book.due(self.facts())
        ):
            if sel.menu is not None and not sel.veto_open():
                app._emit_selection(sel.supersede(t, "a routine or a request came up"))

    def _announce(self, routine: Any, t: float) -> bool:
        app, sel = self.app, self.app.selection
        scene = app.visible_objects()
        opt = self._routine_option(routine, scene)
        if opt is None or opt.rung not in AUTO_RUNGS:
            return False
        card = _candidate(opt)
        if card.tool_call is None:
            return False
        if sel.menu is not None:
            app._emit_selection(sel.supersede(t, f"routine {routine.name} is due"))
            if sel.menu is not None:
                return False  # the person is on these cards; the routine waits
        with app._lock:
            blocked = app._selection_blocked()
            events = []
            if blocked is None and not sel.veto_open():
                events = sel.show([card], "routine", now(), None, app.arm.arm_count)
                if events:
                    events += sel.start_veto(card.id, now(), self.veto_window_s)
        if not events or not any(k == "selection.auto_started" for k, _ in events):
            if events:  # the menu went up but the ring did not: it is an ordinary card
                app._emit_selection(events)
                self.remember([card])
            return False
        app._emit_selection(events)
        self.remember([card])
        self.book.proposed(routine.name)
        self._last_unsolicited_t = t
        line = routine.say or f"{card.label.rstrip('?')}."
        app.log.emit("routine.announced", name=routine.name, card=card.id, rung=card.rung,
                     window_s=self.veto_window_s, say=line)
        if self.cfg.get("speak", True) is not False:
            threading.Thread(target=self._say, args=(f"{line} Brace to cancel.",),
                             name="h2m-announce-routine", daemon=True).start()
        return True

    def _say(self, text: str) -> None:
        try:
            self.app._say(text)
        except Exception:  # noqa: BLE001  # speech never breaks the loop
            pass

    def _run_auto(self, ticket: Ticket) -> None:
        """The veto window passed with no input: the card runs exactly like an accepted
        card (tool path, gate, confirmation), off the loop thread."""
        app = self.app
        card = ticket.card
        with app._lock:
            blocked = app._selection_blocked()
            epoch = app._epoch
        if blocked is not None or card.rung not in AUTO_RUNGS or app.stopped_recently(now()):
            why = blocked or "not pre-approved"
            app._emit_selection(app.selection.finish(ticket, False, why, False, now()))
            return
        app.log.emit("routine.auto_run", name=card.routine, card=card.id, rung=card.rung)
        threading.Thread(target=app._accept_card, args=(ticket, epoch), name="h2m-auto-card",
                         daemon=True).start()


def _candidate(opt: Option) -> Candidate:
    return _to_candidate(opt, "likely", "routine")


def build_proposer(app: Any, cfg: Mapping[str, Any], **kw: Any) -> Proposer | None:
    """A Proposer when ``proactive.enabled`` is literally true, else None."""
    section = cfg.get("proactive") or {}
    if section.get("enabled") is not True:
        return None
    return Proposer(app, cfg, **kw)


__all__ = ["RUNGS", "CallLog", "Proposer", "Trigger", "build_proposer"]
