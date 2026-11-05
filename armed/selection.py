"""Reflex selection: cards answered with one muscle (Reflex build plan items 2b and 2c).

A small state machine with its own lock. No I/O, no arm, no model and no gate here:
``armed.app`` feeds it inputs and emits the events it returns. Accepting a card only
hands a ``Ticket`` back; the app runs that card's tool call through the same
contract, safety gate and confirmation as any other tool request, then reports the
result with ``finish``.

Inputs (``INPUTS``), each counted on the open task whatever it does:

- ``twitch`` / ``ui_accept`` on the menu: SELECT the highlighted card. Only if the input
  began at least ``reaction_s`` after that card was highlighted and (``require_ack``) after
  the UI acknowledged rendering that exact card in that highlight generation (``ack``;
  no ack refuses), at least ``after_veto_s`` after the last brace or open hand (including
  the ones swallowed by the refractory; a refusal re-highlights), and only if the caller's
  pins (menu id, card id, arming) still match. A motion card is only selected: the app
  announces it and the card shows "confirm?". With ``require_ack`` the select is a new
  highlight (the ack clears: the UI acks the "confirm?" rendering). A ``say`` card is
  taken at once (it never moves the arm).
- ``twitch`` / ``ui_accept`` while a card asks "confirm?": take it, only if the input began
  at least ``reaction_s`` after the later of the announcement's end (``announced``: the
  app calls it once the speech has finished playing, never when it was started) and
  (``require_ack``) the UI's ack of the "confirm?" rendering, and within ``confirm_s`` of
  the announcement's end. Before ``announced`` nothing confirms; a question never
  announced lapses ``confirm_s`` after the select. One card at a time: while a card is
  being checked nothing else is taken. (The app answers the gate's question with it only
  when that question is the plain confirmation; ``finish(answered=False)`` records a
  request left for its own answer.)
- ``double_twitch`` / ``ui_step``: move the cursor to the next card (wraps); while a card
  asks "confirm?", go back to the menu instead (same card, highlighted afresh).
- ``brace`` / ``open_hand`` (idle arm only) / ``ui_dismiss``: remove the highlighted card.
  During motion those freeze the arm in the app as always; here they are only counted
  (from the ``arm.stopped`` reason) and the task ends. A flinch often fires a brace and
  an open hand together: within ``veto_refractory_s`` of the last one, a second veto is
  counted but never refuses a second card.

Every new highlight (shown, stepped to, dismissed onto, refused, back from "confirm?",
and with ``require_ack`` the select itself) bumps the highlight generation (``hl``) and
clears the UI acknowledgement: the reaction clock restarts from the next ``ack``. An ack
that names a generation (``highlight``) counts only for that generation, so a late ack
of an earlier rendering of the same card never acks the current one.

Veto ring (docs/PROACTIVE.md §3, the announce / do rungs): the app may open a menu with
one pre-approved routine card and ``start_veto`` it. The card then shows a draining ring
(``veto_until``, snapshot ``auto``); if the window passes with no input at all,
``veto_due`` hands back a ticket (``by="veto_window"``) that the app runs through the same
gate as any card. Any input during the ring cancels it: a brace or open hand dismisses
the card quietly (``selection.auto_cancelled``, no incident), a twitch or step makes the
card an ordinary one. The ring is only ever started by the app for a card whose rung is
announce or do; nothing here turns time into an approval for an ``ask`` card.

Modes: ``intent`` (up to three ranked cards; a Muse upgrade may reorder them only before
the person's first input on the menu, and never changes the highlighted card) and
``scanning`` (every option in the fixed scene order, the baseline). ``degrade`` switches
intent to scanning for the rest of the run. A menu belongs to one arming
(``arm_count``): after reset + arm it is dropped and rebuilt (``sync_arm``).

A task runs from the first card shown to the accepted motion completing (or its speech
being spoken): ``task.completed``. Refusing every card, a stop, a fault, a cancel,
another request moving the arm, a re-arming or a mode change end it with
``task.abandoned``. Both carry the per-input counts, ``selections`` (their sum) and the
seconds from the first card to motion start (``to_motion_s``) and completion
(``to_complete_s``).
"""

from __future__ import annotations

import itertools
import math
import threading
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from armed.intent import MAX_K, Candidate
from armed.world import APPROACH

INTENT, SCANNING = "intent", "scanning"
MODES = (INTENT, SCANNING)
INPUTS = ("twitch", "double_twitch", "brace", "open_hand", "ui_accept", "ui_step", "ui_dismiss")
VETO_WINDOW = "veto_window"  # Ticket.by when a pre-approved card's ring ran out
AUTO_RUNGS = frozenset({"announce", "do"})  # the only rungs a ring may be started for
MIN_VETO_S, MAX_VETO_S = 3.0, 30.0
VETOES = frozenset({"brace", "open_hand"})  # one flinch often fires both: one refusal
ONE_STEP = frozenset({"say"})  # kinds that never move the arm: no "confirm?" step
SELECT, ACCEPT = "select", "accept"  # Ticket.step
MAX_HISTORY = 10
# Floors and ceilings: the config can only make these stricter.
MIN_REACTION_S = 0.3
MIN_AFTER_VETO_S = 3.0
MAX_CONFIRM_S = 30.0

Events = list[tuple[str, dict[str, Any]]]


@dataclass
class Menu:
    id: int
    task_id: int
    mode: str
    source: str  # "fallback" | "muse" | "scanning"
    cards: list[Candidate]
    shown_t: float
    highlighted_t: float  # when the card under the cursor became highlighted
    ranker_gen: int | None = None
    arm_count: int | None = None  # the arming the cards went up in
    cursor: int = 0
    inputs: int = 0  # any input on this menu blocks a Muse upgrade
    taken: Candidate | None = None  # going through the gate right now
    hl: int = 1  # highlight generation: bumped on every new highlight
    ack_t: float | None = None  # when the UI acknowledged rendering the highlighted card
    ack_card: str | None = None
    confirming: Candidate | None = None  # selected motion card asking "confirm?"
    announce_t: float = math.inf  # when its announcement finished playing (inf: not yet)
    select_t: float = math.inf  # when it was selected (lapses from here if never announced)
    # veto ring (pre-approved routine): the window and the card it is for
    veto_until: float | None = None
    veto_total_s: float = 0.0
    veto_card: str | None = None

    @property
    def highlighted(self) -> Candidate | None:
        return self.cards[self.cursor] if self.cards else None


@dataclass
class Task:
    id: int
    mode: str
    shown_t: float
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(INPUTS, 0))
    card: Candidate | None = None  # the accepted card
    contract_id: str | None = None  # the contract it became (bound before the confirm)
    accepted_t: float | None = None
    motion_t: float | None = None  # first arm.started for that contract
    auto: dict[str, Any] | None = None  # {card_id, total_s}: taken by a veto window

    @property
    def selections(self) -> int:
        return sum(self.counts.values())

    def summary(self, t: float) -> dict[str, Any]:
        return {
            "task": self.id,
            "mode": self.mode,
            "selections": self.selections,
            "counts": dict(self.counts),
            "card": self.card.id if self.card else None,
            "card_kind": self.card.kind if self.card else None,
            "contract_id": self.contract_id,
            "elapsed_s": round(t - self.shown_t, 3),
            "to_motion_s": None if self.motion_t is None else round(self.motion_t - self.shown_t, 3),
            "auto": self.auto is not None,
        }


@dataclass(frozen=True)
class Ticket:
    """A card the person answered. ``step`` SELECT: a motion card was selected and the
    app announces it (``announced``). ``step`` ACCEPT: the app runs ``card.tool_call``
    through the gate and reports with ``finish``."""

    menu_id: int
    task_id: int
    card: Candidate
    by: str  # the input that took it
    step: str = ACCEPT


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def announcement(card: Candidate) -> str:
    """What the arm says when a motion card is selected (built from the card label, which
    armed.intent builds from the scene: never model text)."""
    return f"{card.label}? Twitch again to confirm, or double twitch to go back."


class Selection:
    def __init__(
        self,
        mode: str = INTENT,
        k: int = MAX_K,
        reshow_s: float = 3.0,
        reaction_s: float = 0.7,
        veto_refractory_s: float = 1.5,
        after_veto_s: float = MIN_AFTER_VETO_S,
        require_ack: bool = True,
        confirm_s: float = 8.0,
    ):
        if mode not in MODES:
            raise ValueError(f"selection.mode must be one of {', '.join(MODES)}")
        self.configured_mode = mode
        self.mode = mode
        self.k = max(1, min(int(k), MAX_K))
        self.reshow_s = max(0.0, float(reshow_s))
        self.reaction_s = max(MIN_REACTION_S, float(reaction_s))
        # A brace or open hand this soon after the last one is the same reaction: counted,
        # never a second refusal.
        self.veto_refractory_s = max(0.0, float(veto_refractory_s))
        # No accept begins this soon after any veto (a startle: refuse, then twitch).
        self.after_veto_s = max(MIN_AFTER_VETO_S, float(after_veto_s))
        self.require_ack = require_ack is not False
        self.confirm_s = min(MAX_CONFIRM_S, max(self.reaction_s, float(confirm_s)))
        self._last_veto_t = -math.inf
        self.degraded: str | None = None  # the ranker note that switched intent to scanning
        self.menu: Menu | None = None
        self.task: Task | None = None
        self.last_task: dict[str, Any] | None = None
        self.tasks_done = 0
        self.history: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY)
        self._menu_ids = itertools.count(1)
        self._task_ids = itertools.count(1)
        self._quiet_until = 0.0
        self._early: tuple[int, list[Candidate]] | None = None  # upgrade that beat show()
        self._lock = threading.Lock()

    # ── menus ────────────────────────────────────────────────────────────────
    def wants_menu(self, t: float) -> bool:
        """No cards, no task open (nothing accepted is still running) and not quiet."""
        with self._lock:
            return self.menu is None and self.task is None and t >= self._quiet_until

    def backoff(self, until: float) -> None:
        """Nothing to show (empty scene, or the app got busy): try again at ``until``."""
        with self._lock:
            self._quiet_until = max(self._quiet_until, until)

    def recent_history(self) -> list[dict[str, Any]]:
        """The last completed tasks' actions, oldest first (ranker context)."""
        with self._lock:
            return list(self.history)

    def show(
        self,
        cards: Sequence[Candidate],
        source: str,
        t: float,
        ranker_gen: int | None = None,
        arm_count: int | None = None,
    ) -> Events:
        """Open a task with these cards, cursor on the first. Intent mode keeps ``k``."""
        with self._lock:
            cards = self._cards(cards)
            if self.menu is not None or self.task is not None or not cards:
                return []
            early, self._early = self._early, None
            if early is not None and ranker_gen is not None and early[0] == ranker_gen:
                upgraded = self._cards(early[1])
                if upgraded:  # Muse answered before the first cards went up (nothing seen)
                    cards, source = upgraded, "muse"
            task = Task(next(self._task_ids), self.mode, t)
            menu = Menu(
                next(self._menu_ids), task.id, self.mode, source, cards, t, t, ranker_gen,
                arm_count,
            )
            self.task, self.menu = task, menu
            return [("selection.shown", self._shown(menu, upgraded=False))]

    def upgrade(self, ranker_gen: int, cards: Sequence[Candidate], t: float) -> Events:
        """A Muse ranking for ``ranker_gen``. Applied only to that intent menu and only
        before any input on it, and never to the highlighted card: it stays where it is
        (same card, same highlight time and acknowledgement); only the cards behind it
        are reordered."""
        del t
        with self._lock:
            menu = self.menu
            if menu is None:
                if self.task is None and self.mode == INTENT:
                    self._early = (ranker_gen, list(cards))
                return []
            if (
                menu.ranker_gen != ranker_gen
                or menu.mode != INTENT
                or self.mode != INTENT
                or menu.inputs
                or menu.taken is not None
                or menu.confirming is not None
            ):
                return []
            current = menu.highlighted
            ranked = self._cards(cards)
            if current is None or not ranked:
                return []
            rest = [c for c in ranked if c.id != current.id]
            new = self._cards([*menu.cards[: menu.cursor], current, *rest])
            if [c.id for c in new] == [c.id for c in menu.cards]:
                menu.source = "muse"
                return []
            menu.cards, menu.source = new, "muse"
            menu.cursor = new.index(current)
            return [("selection.shown", {**self._shown(menu, upgraded=True), "kept": current.id})]

    def sync_arm(self, arm_count: int, t: float) -> Events:
        """Cards belong to the arming they went up in: after reset + arm they are dropped
        (the task ends) and new ones go up at once."""
        with self._lock:
            menu = self.menu
            if menu is None or menu.arm_count is None or menu.arm_count == arm_count:
                return []
            self._early = None
            events = self._close("task.abandoned", t, "cancelled", "the arm was re-armed")
            self.menu = None
            self._quiet_until = t
            return events

    def _cards(self, cards: Sequence[Candidate]) -> list[Candidate]:
        out: list[Candidate] = []
        seen: set[str] = set()
        for c in cards:
            if isinstance(c, Candidate) and c.id not in seen:
                seen.add(c.id)
                out.append(c)
        return out[: self.k] if self.mode == INTENT else out

    # ── inputs ───────────────────────────────────────────────────────────────
    def ack(
        self, card_id: Any, t: float, menu_id: Any, highlight: Any = None
    ) -> tuple[bool, str]:
        """The UI rendered ``card_id`` (highlighted) on ``menu_id``; ``t`` is when the
        acknowledgement arrived. ``highlight`` (the snapshot's ``highlight``, when sent)
        pins the highlight generation that was drawn: an ack for an earlier generation is
        refused. Only the first ack of a highlight counts. Not an input."""
        with self._lock:
            menu = self.menu
            if not isinstance(card_id, str) or isinstance(menu_id, bool) or not isinstance(
                menu_id, int
            ):
                return False, "send the card id and the menu id you rendered"
            if highlight is not None and (
                isinstance(highlight, bool) or not isinstance(highlight, int)
            ):
                return False, "highlight must be the integer from the selection snapshot"
            why = self._menu_problem(menu, menu_id, card_id)
            if why is not None or menu is None:
                return False, str(why)
            if highlight is not None and highlight != menu.hl:
                return False, "that highlight changed; render the cards again"
            if menu.ack_t is None or menu.ack_card != card_id:
                menu.ack_t, menu.ack_card = float(t), card_id
            return True, "acknowledged"

    def count(self, name: str, t: float | None = None) -> None:
        """Count an input that did nothing here (it stopped motion or cancelled a request)."""
        with self._lock:
            self._count(name)
            if name in VETOES and t is not None:
                self._last_veto_t = t

    def refuse(self, name: str, action: str, why: str) -> Events:
        """Count an input the app refused before it reached the menu (e.g. blocked)."""
        with self._lock:
            self._count(name)
            return self._refused(action, name, why)

    def step(self, name: str, t: float, menu_id: int | None = None) -> tuple[bool, str, Events]:
        with self._lock:
            self._count(name)
            menu = self.menu
            why = self._menu_problem(menu, menu_id)
            if why is not None or menu is None:
                return False, str(why), self._refused("step", name, str(why))
            cancelled = self._cancel_veto(menu, name)
            back = menu.confirming is not None
            if back:  # "confirm?" -> back to the menu, same card highlighted afresh
                self._unselect(menu, t)
            else:
                menu.cursor = (menu.cursor + 1) % len(menu.cards)
                self._highlight(menu, t)
            card = menu.highlighted
            assert card is not None
            data = {**self._ids(menu), "by": name, "ok": True, "cursor": menu.cursor,
                    "card": card.id, "label": card.label, "back": back}
            return True, card.label, cancelled + [("selection.step", data)]

    def take(
        self,
        name: str,
        t: float,
        onset: float | None,
        card_id: str | None = None,
        menu_id: int | None = None,
        arm_count: int | None = None,
    ) -> tuple[Ticket | None, str, Events]:
        """Select (motion card) or take (a "confirm?" card, or a say card) the highlighted
        card. ``onset`` is when the input began (a twitch's burst onset, a click's
        arrival). Missing or non-finite onset fails closed."""
        with self._lock:
            self._count(name)
            menu = self.menu
            why = self._menu_problem(menu, menu_id, card_id)
            card = menu.highlighted if menu is not None else None
            cancelled = self._cancel_veto(menu, name) if menu is not None else []
            reset = False  # refuse AND restart the highlight (fresh ack + reaction)
            if why is None and menu is not None and card is not None:
                if arm_count is not None and menu.arm_count is not None and (
                    arm_count != menu.arm_count
                ):
                    why = "I was re-armed since these cards went up; look again"
                elif not _finite(onset):
                    why = "I couldn't time that input, so it doesn't count as an answer"
                elif onset - self._last_veto_t < self.after_veto_s:  # type: ignore[operator]
                    why = "too soon after a refusal; look at the card again"
                    reset = True
                elif menu.confirming is not None:
                    clock = menu.announce_t
                    if not math.isfinite(clock):
                        why = "I'm still asking; wait for the question"
                    elif onset > clock + self.confirm_s:  # type: ignore[operator]
                        why = "that question lapsed; look at the card again"
                        reset = True
                    elif self.require_ack and (
                        menu.ack_t is None or menu.ack_card != card.id
                    ):
                        why = "the screen has not shown the question yet"
                    else:
                        if self.require_ack and menu.ack_t is not None:
                            clock = max(clock, menu.ack_t)
                        if onset < clock + self.reaction_s:  # type: ignore[operator]
                            why = "too soon after I asked; twitch again to confirm"
                else:
                    clock = menu.highlighted_t
                    if self.require_ack:
                        if menu.ack_t is None or menu.ack_card != card.id:
                            why = "the screen has not shown that card yet"
                        else:
                            clock = max(clock, menu.ack_t)
                    if why is None and onset < clock + self.reaction_s:  # type: ignore[operator]
                        why = "too soon after the card appeared; twitch again to accept it"
                    elif why is None and card.tool_call is None:
                        why = f"I can't do {card.label!r} yet"
            if why is not None or menu is None or card is None:
                if reset and menu is not None:
                    self._unselect(menu, t)
                return None, str(why), cancelled + self._refused("accept", name, str(why))
            if menu.confirming is None and card.kind not in ONE_STEP:
                if self.require_ack:  # "confirm?" is a new rendering: the UI acks it again
                    self._highlight(menu, t)
                menu.confirming, menu.announce_t, menu.select_t = card, math.inf, float(t)
                data = {**self._ids(menu), "by": name, "ok": True, "card": card.id,
                        "label": card.label}
                ticket = Ticket(menu.id, menu.task_id, card, name, SELECT)
                return ticket, "selected", cancelled + [("selection.select", data)]
            menu.taken = card
            return Ticket(menu.id, menu.task_id, card, name, ACCEPT), "taken", cancelled

    def selected(self, ticket: Ticket) -> bool:
        """``ticket``'s card still asks "confirm?" and has not been announced yet."""
        with self._lock:
            menu = self.menu
            return (
                menu is not None
                and menu.id == ticket.menu_id
                and menu.confirming is ticket.card
                and not math.isfinite(menu.announce_t)
            )

    def announced(self, ticket: Ticket, t: float, text: str) -> Events:
        """The selected card's question finished playing at ``t``: the confirm clock
        starts there (the app calls this after the speech ended, never when it began)."""
        with self._lock:
            menu = self.menu
            if menu is None or menu.id != ticket.menu_id or menu.confirming is not ticket.card:
                return []
            if math.isfinite(menu.announce_t):
                return []  # announced once; a second call never moves the clock
            menu.announce_t = float(t)
            return [("selection.announced", {**self._ids(menu), "card": ticket.card.id,
                                             "text": text})]

    def unselect(self, ticket: Ticket, t: float, reason: str = "") -> Events:
        """The selection could not be announced (not spoken, failed, cancelled meanwhile):
        back to the menu, the card highlighted afresh."""
        with self._lock:
            menu = self.menu
            if menu is None or menu.id != ticket.menu_id or menu.confirming is not ticket.card:
                return []
            self._unselect(menu, t)
            return [("selection.unselected", {**self._ids(menu), "card": ticket.card.id,
                                              "reason": reason})]

    def expire(self, t: float) -> Events:
        """A "confirm?" left unanswered for ``confirm_s`` after its announcement goes back
        to the menu; one never announced lapses ``confirm_s`` after the select."""
        with self._lock:
            menu = self.menu
            if menu is None or menu.confirming is None or menu.taken is not None:
                return []
            start = menu.announce_t if math.isfinite(menu.announce_t) else menu.select_t
            # Neither time set cannot happen through take(); if it does, lapse now.
            if math.isfinite(start) and not t > start + self.confirm_s:
                return []
            card = menu.confirming
            self._unselect(menu, t)
            return [("selection.lapsed", {**self._ids(menu), "card": card.id})]

    def bind(self, ticket: Ticket, contract_id: str) -> None:
        """The taken card became ``contract_id`` (before its confirmation): motion and
        completion of that contract belong to the task."""
        with self._lock:
            task = self.task
            if task is not None and task.id == ticket.task_id:
                task.contract_id, task.card = contract_id, ticket.card

    def finish(
        self,
        ticket: Ticket,
        ok: bool,
        detail: str,
        motion: bool,
        t: float,
        answered: bool = True,
    ) -> Events:
        """The gate's answer for a taken card. ``ok`` with ``motion``: the arm started, or
        (``answered`` False) the request waits for its own squeeze / yes / click; either
        way the task now waits for the motion. ``ok`` without ``motion``: spoken, the task
        is done. Not ``ok``: back to the menu, the card highlighted afresh."""
        with self._lock:
            menu, task = self.menu, self.task
            data = {"menu": ticket.menu_id, "task": ticket.task_id, "by": ticket.by, "ok": ok,
                    "answered": ok and answered, "detail": detail, "card": ticket.card.id,
                    "card_kind": ticket.card.kind}
            events: Events = [("selection.accept", data)]
            if menu is not None and menu.id == ticket.menu_id:
                menu.taken = None
                if ok:
                    self.menu = None  # the cards go; the task waits for the motion
                else:
                    self._unselect(menu, t)
            if task is None or task.id != ticket.task_id:
                return events  # ended meanwhile (stopped, or already completed)
            if ok:
                task.card = ticket.card
                task.accepted_t = t
                if not motion:
                    events += self._close("task.completed", t, "completed")
            elif task.motion_t is None:
                # Whatever was bound did not run. If it already started (another answer
                # won the race for the card's request) the binding stays: it is our motion.
                task.contract_id, task.card = None, None
            return events

    def dismiss(
        self, name: str, t: float, card_id: str | None = None, menu_id: int | None = None
    ) -> tuple[bool, str, Events]:
        with self._lock:
            self._count(name)
            menu = self.menu
            why = self._menu_problem(menu, menu_id, card_id)
            if name in VETOES:
                if why is None and t - self._last_veto_t < self.veto_refractory_s:
                    why = "that was the same reaction; one refusal per brace"
                self._last_veto_t = t
            if why is not None or menu is None:
                return False, str(why), self._refused("dismiss", name, str(why))
            vetoed = menu.veto_until is not None and menu.veto_card == (
                menu.highlighted.id if menu.highlighted else None
            )
            events: Events = self._cancel_veto(menu, name)
            menu.confirming, menu.announce_t, menu.select_t = None, math.inf, math.inf
            card = menu.cards.pop(menu.cursor)
            data = {**self._ids(menu), "by": name, "ok": True, "card": card.id,
                    "remaining": len(menu.cards), "vetoed": vetoed}
            events.append(("selection.dismiss", data))
            if vetoed:  # a quiet no to a pre-approved routine: the task ends, no incident
                events += self._close("task.abandoned", t, "vetoed", f"vetoed by {name}")
            elif not menu.cards:
                events += self._close("task.abandoned", t, "dismissed", "every card was refused")
            else:
                menu.cursor %= len(menu.cards)
                self._highlight(menu, t)
            return True, f"dismissed {card.label}", events

    # ── veto ring: pre-approved routines ─────────────────────────────────────
    def start_veto(
        self, card_id: str, t: float, window_s: float, menu_id: int | None = None
    ) -> Events:
        """Open the veto window on the highlighted card. Only for a card whose rung is
        announce or do (anything else is refused here too), on a menu nobody has touched.
        The window is clamped to [MIN_VETO_S, MAX_VETO_S]."""
        with self._lock:
            menu = self.menu
            why = self._menu_problem(menu, menu_id, card_id)
            card = menu.highlighted if menu is not None else None
            if why is None and menu is not None and card is not None:
                if card.rung not in AUTO_RUNGS:
                    why = "only a pre-approved routine gets a veto window"
                elif menu.inputs or menu.confirming is not None or menu.veto_until is not None:
                    why = "the person already answered these cards"
                elif not _finite(window_s):
                    why = "bad veto window"
            if why is not None or menu is None or card is None:
                return self._refused("auto_started", "app", str(why))
            total = min(MAX_VETO_S, max(MIN_VETO_S, float(window_s)))
            menu.veto_until, menu.veto_total_s, menu.veto_card = t + total, total, card.id
            data = {**self._ids(menu), "card": card.id, "label": card.label, "total_s": total,
                    "until": round(t + total, 3), "rung": card.rung, "routine": card.routine}
            return [("selection.auto_started", data)]

    def veto_due(self, t: float) -> Ticket | None:
        """The ring ran out with no input: the card is taken (``by`` VETO_WINDOW) and the
        app runs it through the gate. None while the window is open or after any input."""
        with self._lock:
            menu = self.menu
            if (
                menu is None
                or menu.veto_until is None
                or menu.taken is not None
                or menu.inputs
                or menu.confirming is not None
            ):
                return None
            card = menu.highlighted
            if card is None or card.id != menu.veto_card or card.rung not in AUTO_RUNGS:
                menu.veto_until, menu.veto_card = None, None
                return None
            if t < menu.veto_until:
                return None
            menu.taken = card
            menu.veto_until = None
            if self.task is not None and self.task.id == menu.task_id:
                self.task.auto = {"card_id": card.id, "total_s": menu.veto_total_s}
            return Ticket(menu.id, menu.task_id, card, VETO_WINDOW, ACCEPT)

    def veto_open(self) -> bool:
        with self._lock:
            return self.menu is not None and self.menu.veto_until is not None

    def supersede(self, t: float, reason: str) -> Events:
        """A trigger wants new cards: drop a menu nobody has touched so the next tick
        shows them at once. A menu with any input, a card asking "confirm?" or a card
        being checked is left alone (returns [])."""
        with self._lock:
            menu = self.menu
            if menu is None or menu.inputs or menu.confirming is not None or menu.taken is not None:
                return []
            self._early = None
            events = self._cancel_veto(menu, "superseded")
            events += self._close("task.abandoned", t, "superseded", reason)
            self._quiet_until = t
            return events

    def _cancel_veto(self, menu: Menu, by: str) -> Events:
        """Any input on a ringing card ends the ring (under the lock)."""
        if menu.veto_until is None:
            return []
        data = {**self._ids(menu), "card": menu.veto_card, "by": by}
        menu.veto_until, menu.veto_card = None, None
        return [("selection.auto_cancelled", data)]

    # ── the arm and the app ──────────────────────────────────────────────────
    def on_arm_started(self, contract_id: Any, t: float) -> Events:
        """Cards never stay up while the arm moves. The accepted contract starting is
        the task's motion; anything else moving the arm ends the task."""
        with self._lock:
            self._early = None
            task = self.task
            if task is not None and task.contract_id is not None and task.contract_id == contract_id:
                self.menu = None
                if task.motion_t is None:
                    task.motion_t = t
                return []
            self.menu = None
            return self._close("task.abandoned", t, "interrupted", "another request moved the arm")

    def on_arm_completed(self, contract_id: Any, phase: Any, t: float) -> Events:
        """The accepted contract's last plan finished (not the handover's first stage)."""
        with self._lock:
            task = self.task
            if task is None or task.contract_id is None or task.contract_id != contract_id:
                return []
            if phase == APPROACH:
                return []
            return self._close("task.completed", t, "completed")

    def on_arm_stopped(self, reason: str, fault: bool, t: float) -> Events:
        """A stop or fault: cards go and the task ends. A brace or open hand that froze
        the arm is counted here (its stop reason), since it never reached the menu."""
        with self._lock:
            self._early = None
            if reason.startswith("brace"):
                self._count("brace")
                self._last_veto_t = t
            elif reason.startswith("open hand"):
                self._count("open_hand")
                self._last_veto_t = t
            self.menu = None
            return self._close("task.abandoned", t, "fault" if fault else "stopped", reason)

    def hide(self, t: float, reason: str) -> Events:
        """Cancel, disarm: the cards go and an open task ends."""
        with self._lock:
            self._early = None
            self.menu = None
            return self._close("task.abandoned", t, "cancelled", reason)

    def abandon_if_idle(self, t: float) -> Events:
        """Called only when the app is idle (under its lock): a task with no cards up is
        waiting for a motion, so if nothing is running it never completed (e.g. the
        handover's second stage was refused) and the task ends."""
        with self._lock:
            if self.task is None or self.menu is not None:
                return []
            return self._close("task.abandoned", t, "incomplete", "the motion did not complete")

    def set_mode(self, mode: str, t: float) -> tuple[bool, str, Events]:
        if mode not in MODES:
            return False, f"mode must be one of {', '.join(MODES)}", []
        with self._lock:
            old = self.mode
            self.mode, self.degraded, self._early = mode, None, None
            self.menu = None
            events = self._close("task.abandoned", t, "cancelled", "selection mode changed")
            self._quiet_until = t  # the new menu may go up straight away
            events.append(("selection.mode", {"mode": mode, "previous": old}))
            return True, mode, events

    def degrade(self, note: str, t: float) -> Events:
        """Muse could not rank (no key, timeout, error): scanning for the rest of the run.
        The cards already up stay; the next menu is the scanning menu."""
        del t
        with self._lock:
            if self.mode != INTENT:
                return []
            self.mode, self.degraded, self._early = SCANNING, str(note), None
            return [("selection.degraded", {"note": str(note), "from": INTENT, "to": SCANNING})]

    # ── views ────────────────────────────────────────────────────────────────
    def snapshot(self, t: float) -> dict[str, Any]:
        with self._lock:
            menu, task = self.menu, self.task
            cards = []
            confirming = menu.confirming if menu is not None else None
            if menu is not None:
                cards = [
                    {**c.as_dict(), "highlighted": i == menu.cursor,
                     "confirm": confirming is not None and i == menu.cursor}
                    for i, c in enumerate(menu.cards)
                ]
            highlighted = menu.highlighted if menu is not None else None
            acked = (
                menu is not None
                and highlighted is not None
                and menu.ack_t is not None
                and menu.ack_card == highlighted.id
            )
            accept_in: float | None = None
            if menu is not None and confirming is not None:
                if math.isfinite(menu.announce_t) and (acked or not self.require_ack):
                    clock = menu.announce_t
                    if self.require_ack and menu.ack_t is not None:
                        clock = max(clock, menu.ack_t)
                    accept_in = round(max(0.0, clock + self.reaction_s - t), 3)
            elif menu is not None and (acked or not self.require_ack):
                ack = menu.ack_t if menu.ack_t is not None else -math.inf
                clock = max(menu.highlighted_t, ack)
                clock = max(clock, self._last_veto_t + self.after_veto_s - self.reaction_s)
                accept_in = round(max(0.0, clock + self.reaction_s - t), 3)
            auto: dict[str, Any] | None = None
            if menu is not None and menu.veto_until is not None:
                auto = {"card_id": menu.veto_card, "total_s": menu.veto_total_s,
                        "remaining_s": round(max(0.0, menu.veto_until - t), 3)}
            elif task is not None and task.auto is not None:
                auto = {**task.auto, "remaining_s": 0.0}  # the routine is running
            return {
                "mode": self.mode,
                "configured_mode": self.configured_mode,
                "degraded": self.degraded,
                "k": self.k,
                "veto_until": None if menu is None else menu.veto_until,
                "auto": auto,
                "asked_by": highlighted.asked_by if highlighted is not None else None,
                "menu": menu.id if menu is not None else None,
                "source": menu.source if menu is not None else None,
                "cards": cards,
                "cursor": menu.cursor if menu is not None else None,
                "highlighted": highlighted.id if highlighted is not None else None,
                "highlight": menu.hl if menu is not None else None,
                "require_ack": self.require_ack,
                "acked": acked,
                "confirming": confirming.id if confirming is not None else None,
                "accept_in_s": accept_in,
                "checking": menu is not None and menu.taken is not None,
                "task": task.summary(t) if task is not None else None,
                "last_task": self.last_task,
                "tasks_done": self.tasks_done,
            }

    # ── internals (under the lock) ───────────────────────────────────────────
    def _highlight(self, menu: Menu, t: float) -> None:
        """A new highlight: the reaction clock restarts and needs a fresh UI ack."""
        menu.highlighted_t = t
        menu.hl += 1
        menu.ack_t, menu.ack_card = None, None

    def _unselect(self, menu: Menu, t: float) -> None:
        menu.confirming, menu.announce_t, menu.select_t = None, math.inf, math.inf
        self._highlight(menu, t)

    def _count(self, name: str) -> None:
        if self.task is not None and name in self.task.counts:
            self.task.counts[name] += 1
        if self.menu is not None:
            self.menu.inputs += 1

    def _menu_problem(
        self, menu: Menu | None, menu_id: int | None, card_id: str | None = None
    ) -> str | None:
        if menu is None or not menu.cards:
            return "no cards are showing"
        if menu.taken is not None:
            return "a card is being checked"
        if menu_id is not None and menu_id != menu.id:
            return "those cards changed; look again"
        highlighted = menu.highlighted
        if card_id is not None and (highlighted is None or card_id != highlighted.id):
            return "that card is not the highlighted one"
        return None

    def _ids(self, menu: Menu) -> dict[str, Any]:
        return {"menu": menu.id, "task": menu.task_id}

    def _refused(self, action: str, name: str, why: str) -> Events:
        if self.task is None:
            return []
        data = {"menu": self.menu.id if self.menu else None, "task": self.task.id,
                "by": name, "ok": False, "detail": why}
        return [(f"selection.{action}", data)]

    def _shown(self, menu: Menu, upgraded: bool) -> dict[str, Any]:
        return {
            **self._ids(menu),
            "mode": menu.mode,
            "source": menu.source,
            "cards": [c.id for c in menu.cards],
            "labels": [c.label for c in menu.cards],
            "cursor": menu.cursor,
            "upgraded": upgraded,
        }

    def _close(self, kind: str, t: float, outcome: str, reason: str = "") -> Events:
        task = self.task
        if task is None:
            return []
        self.task, self.menu = None, None
        summary = {**task.summary(t), "outcome": outcome}
        if kind == "task.completed":
            summary["to_complete_s"] = round(t - task.shown_t, 3)
            self.tasks_done += 1
            if task.card is not None:
                c = task.card
                self.history.append({"kind": c.kind, "object_id": c.object_id, "text_id": c.text_id})
        else:
            summary["reason"] = reason
            self._quiet_until = t + self.reshow_s
        self.last_task = summary
        return [(kind, summary)]


__all__ = [
    "ACCEPT",
    "INPUTS",
    "INTENT",
    "MODES",
    "ONE_STEP",
    "SCANNING",
    "SELECT",
    "VETO_WINDOW",
    "Menu",
    "Selection",
    "Task",
    "Ticket",
    "announcement",
]
