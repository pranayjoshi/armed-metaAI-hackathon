"""Local, instant command grammar.

This path never touches the network: STOP and CANCEL are recognized here
first, always. The LLM compiler (``llm.py``) handles free-form requests; this
grammar is its fallback and its safety floor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from h2m.contract import Verb

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
        for name in filter(None, names):
            phrases[name] = object_id
            for word in name.split():
                if word not in _STOPWORDS:
                    owners.setdefault(word, set()).add(object_id)
    for word, ids in owners.items():
        if len(ids) == 1:
            phrases.setdefault(word, next(iter(ids)))
    return phrases


def parse(text: str, vocabulary: dict[str, str]) -> Parsed:
    clean = normalize(text)
    if not clean:
        return Parsed(Verb.UNKNOWN, text=text)
    if phrase := _first(clean, STOP_PHRASES):
        return Parsed(Verb.STOP, grammar_confidence=1.0, text=text, matched=[phrase])
    if phrase := _first(clean, CANCEL_PHRASES):
        return Parsed(Verb.CANCEL, grammar_confidence=1.0, text=text, matched=[phrase])

    verb, confidence, matched = Verb.UNKNOWN, 0.0, []
    for candidate, phrases, score in (
        (Verb.HOME, HOME_PHRASES, 0.95),
        (Verb.POINT, POINT_PHRASES, 0.9),
        (Verb.PICK_PLACE, PICK_PHRASES, 0.9),
    ):
        if phrase := _first(clean, phrases):
            verb, confidence, matched = candidate, score, [phrase]
            break

    parsed = Parsed(verb, grammar_confidence=confidence, text=text, matched=matched)
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
