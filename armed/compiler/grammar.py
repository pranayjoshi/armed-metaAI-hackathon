"""Local, instant command grammar.

This path never touches the network: STOP and CANCEL are recognized here
first, always. The LLM compiler (``llm.py``) handles free-form requests; this
grammar is its fallback and its safety floor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from armed.contract import Verb

STOP_PHRASES = ("emergency stop", "full stop", "stop", "halt", "freeze")
CANCEL_PHRASES = (
    "never mind",
    "nevermind",
    "don't do that",
    "dont do that",
    "scratch that",
    "forget it",
    "cancel",
    "abort",
    "no no",
    "wait",
)
CONFIRM_PHRASES = (
    "yes please",
    "go ahead",
    "do it",
    "that's right",
    "thats right",
    "correct",
    "confirm",
    "yes",
    "yeah",
    "yep",
    "okay",
    "ok",
    "sure",
    "done",  # "I'm done": the answer to the chips held up (stow_held waiting)
)
HOME_PHRASES = ("go home", "home position", "reset position", "go back")
POINT_PHRASES = ("point at", "point to", "show me", "which one is", "highlight", "point")
PICK_PHRASES = (
    "give me",
    "hand me",
    "pass me",
    "bring me",
    "get me",
    "can i have",
    "i need",
    "i want",
    "pick up",
    "pick",
    "grab",
    "take",
    "fetch",
    "put away",
    "move",
    "give",
    "pass",
    "bring",
    "get",
)
# Asking for something to be handed over. Where the tray is not taught (the bed,
# arm.give_hands_over), these hand the named object over instead of clearing it.
GIVE_PHRASES = ("give me", "hand me", "pass me", "bring me", "get me", "can i have", "i need", "i want")
# Taught skills (configs/skills.yaml): phrase -> (skill, takes an object). Checked before
# the verbs so "hold up the cup" is a skill, while "show me the cup" stays a point.
SKILL_PHRASES: tuple[tuple[str, str, bool, str | None], ...] = (
    ("wave", "wave", False, None),
    ("say hi", "wave", False, None),
    ("say hello", "wave", False, None),
    ("bow", "bow", False, None),
    ("celebrate", "celebrate", False, None),
    ("say sorry", "sorry", False, None),
    ("apologize", "sorry", False, None),
    ("take this", "take_this", False, None),
    # The desk phone (id `ball`): "bring" it means up to you at eye level, never the tray.
    ("bring me the phone", "hold_up", True, "ball"),
    ("bring the phone", "hold_up", True, "ball"),
    ("bring my phone", "hold_up", True, "ball"),
    ("get me the phone", "hold_up", True, "ball"),
    ("get my phone", "hold_up", True, "ball"),
    ("hold up", "hold_up", True, None),
    ("lift up", "hold_up", True, None),
    ("hold the jar", "hold_jar", False, None),
    ("hold this jar", "hold_jar", False, None),
    ("hold my jar", "hold_jar", False, None),
    ("sip", "sip", False, None),
    ("press the button", "press_button", False, None),
    # The bed: the pill cup, handed over (the phrase names the object, so it needs no pointing).
    ("my pills", "hand_over", True, "pill_cup"),
    ("pills", "hand_over", True, "pill_cup"),
)
# "move the cup to slot 2": a pick that ends on another taught slot instead of the tray.
DEST_RE = re.compile(
    r"\b(?:to|into|onto|at|on) (?:slot|spot|place|position) ?(\d|one|two|three)\b"
)
_WORD_DIGITS = {"one": "1", "two": "2", "three": "3"}
GENTLE_WORDS = ("gently", "gentle", "softly", "soft", "carefully", "careful", "lightly")
FIRM_WORDS = ("firmly", "firm", "tightly", "tight", "hard", "securely")
DEICTIC_RE = re.compile(r"\b(that one|this one|that|this|it|there)\b")
ORDINAL_RE = re.compile(r"\b(left|right|middle|first|second|third|last)\b")

GENTLE_EFFORT = 0.25
FIRM_EFFORT = 0.85
_STOPWORDS = frozenset({"the", "a", "an", "of", "one", "thing", "object"})


@dataclass
class Parsed:
    verb: Verb
    object_id: str | None = None
    reference: str = "none"  # named | deictic | ordinal | none
    ordinal: str | None = None
    grip_effort: float | None = None
    grammar_confidence: float = 0.0
    text: str = ""
    matched: list[str] = field(default_factory=list)
    skill: str | None = None  # verb SKILL: the skill name
    skill_needs_object: bool = False
    destination: str | None = None  # verb MOVE: the slot to set down on


def normalize(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^a-z0-9'\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _has(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9']){re.escape(phrase)}(?![a-z0-9'])", text) is not None


def _first(text: str, phrases: tuple[str, ...]) -> str | None:
    return next((phrase for phrase in phrases if _has(text, phrase)), None)


def build_vocabulary(objects: list[dict]) -> dict[str, str]:
    """Spoken phrase -> object id: names, prompts, and words no other object shares."""
    phrases: dict[str, str] = {}
    owners: dict[str, set[str]] = {}
    for item in objects:
        object_id = str(item["id"])
        names = {normalize(str(item.get(key) or "")) for key in ("name", "prompt", "id")}
        names |= {normalize(str(a)) for a in item.get("aliases") or []}  # "pills" for the pill cup
        for name in filter(None, names):
            phrases[name] = object_id
            for word in name.split():
                if word not in _STOPWORDS:
                    owners.setdefault(word, set()).add(object_id)
    for word, ids in owners.items():
        if len(ids) == 1:
            phrases.setdefault(word, next(iter(ids)))
    return phrases


def as_hand_over(parsed: Parsed) -> Parsed:
    """"Hand me the water bottle" as the hand_over skill for that object (the bed), when the
    request is a give phrase naming an object; anything else is returned unchanged."""
    if parsed.verb is Verb.PICK_PLACE and parsed.object_id and parsed.matched[:1] and parsed.matched[0] in GIVE_PHRASES:
        parsed.verb, parsed.skill, parsed.skill_needs_object = Verb.SKILL, "hand_over", True
    return parsed


def parse(text: str, vocabulary: dict[str, str]) -> Parsed:
    clean = normalize(text)
    if not clean:
        return Parsed(Verb.UNKNOWN, text=text)
    if phrase := _first(clean, STOP_PHRASES):
        return Parsed(Verb.STOP, grammar_confidence=1.0, text=text, matched=[phrase])
    if phrase := _first(clean, CANCEL_PHRASES):
        return Parsed(Verb.CANCEL, grammar_confidence=1.0, text=text, matched=[phrase])

    verb, confidence, matched = Verb.UNKNOWN, 0.0, []
    skill: tuple[str, bool] | None = None
    skill_object: str | None = None
    for phrase, name, needs_object, fixed_object in SKILL_PHRASES:
        if _has(clean, phrase):
            verb, confidence, matched, skill = Verb.SKILL, 0.9, [phrase], (name, needs_object)
            if fixed_object in vocabulary.values():  # only where the catalog has that object
                skill_object = fixed_object
            break
    if skill is None:
        for candidate, phrases, score in (
            (Verb.HOME, HOME_PHRASES, 0.95),
            (Verb.POINT, POINT_PHRASES, 0.9),
            (Verb.PICK_PLACE, PICK_PHRASES, 0.9),
        ):
            if phrase := _first(clean, phrases):
                verb, confidence, matched = candidate, score, [phrase]
                break
    destination = None
    if verb is Verb.PICK_PLACE and (dest := DEST_RE.search(clean)):
        n = _WORD_DIGITS.get(dest.group(1), dest.group(1))
        verb, destination = Verb.MOVE, f"slot_{n}"
        matched.append(dest.group(0))

    parsed = Parsed(verb, grammar_confidence=confidence, text=text, matched=matched)
    if skill is not None:
        parsed.skill, parsed.skill_needs_object = skill
    parsed.destination = destination
    if skill_object is not None:
        parsed.object_id, parsed.reference = skill_object, "named"
    for name in sorted(vocabulary, key=len, reverse=True):  # "red cup" beats "cup"
        if _has(clean, name):
            parsed.object_id, parsed.reference = vocabulary[name], "named"
            parsed.matched.append(name)
            break
    if parsed.object_id is None:
        if match := ORDINAL_RE.search(clean):
            parsed.reference, parsed.ordinal = "ordinal", match.group(1)
        elif DEICTIC_RE.search(clean):
            parsed.reference = "deictic"

    if _first(clean, GENTLE_WORDS):
        parsed.grip_effort = GENTLE_EFFORT
    elif _first(clean, FIRM_WORDS):
        parsed.grip_effort = FIRM_EFFORT

    if verb is Verb.UNKNOWN and _first(clean, CONFIRM_PHRASES):
        parsed.verb, parsed.grammar_confidence = Verb.CONFIRM, 0.9
    return parsed
