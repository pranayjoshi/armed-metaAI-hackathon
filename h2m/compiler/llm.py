"""Meta Muse Spark as the language interpreter for free-form requests.

Only called when the local grammar cannot parse a request. It returns the
same structure as the grammar (verb, object, reference, grip words); target
fusion with pointing and the safety decision stay deterministic.
STOP and CANCEL never reach this module.

``call_tools`` is the embodied path: the model picks tools from ``h2m.embodied.TOOLS``
and only (name, raw arguments) come back. Nothing here is trusted: every call is
schema-checked and gated by ``Embodied.handle_tool_call``. The tool-call wire format
(OpenAI-style ``tool_calls``) is UNVERIFIED against the live Muse API.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from h2m.compiler.grammar import Parsed
from h2m.contract import Verb

SYSTEM = """You turn a person's spoken request to a tabletop robot arm into JSON.
The arm can: pick_place (pick an object and put it in the tray), point (point at an
object), home (return to rest). Anything else is unknown.
Reply with ONLY a JSON object:
{"verb": "pick_place|point|home|unknown",
 "object_id": "<one of the given ids, or null if not named>",
 "reference": "named|deictic|ordinal|none",
 "ordinal": "left|right|middle|null",
 "grip": "gentle|firm|null",
 "confidence": 0.0-1.0,
 "reply": "<= 12 words, what you understood or what is unclear"}
Use deictic when they say this/that/it without naming. Never invent object ids."""

GRIP = {"gentle": 0.25, "firm": 0.85}
MAX_TOOL_CALLS = 4

TOOL_SYSTEM = """You are the voice of a small tabletop robot arm. Answer by calling tools.
Use only the object ids given. pick_place and place_near_user move objects and the person
will be asked to confirm. Use gesture for small expressive motions and say for short
spoken replies (one or two sentences). If the request is unclear, call say to ask.
Never describe joint angles or invent tools."""

SCENE_SYSTEM = """You look at a photo of a tabletop robot workspace and answer the person's
question in one or two short spoken sentences. Describe only what is visible in the photo.
If you cannot tell, say so."""


PROFILE_SYSTEM = """You look at a photo of a tabletop robot workspace and classify ONE named object
for the gripper. Reply with ONLY one word: rigid (hard, holds its shape: a phone, a box, a
metal tool), fragile (can be crushed or broken: paper, a thin cup, glass, food), liquid (holds
liquid that can spill: a mug, a bottle, a jar of liquid), or unknown if you cannot tell."""
PROFILE_WORDS = ("rigid", "fragile", "liquid")


def parse_profile(text: Any) -> str:
    """The one profile word of a reply; anything else (two words, none, not a string) is
    ``unknown``, the softest cap, so a rambling reply can never pick a firmer grip."""
    if not isinstance(text, str):
        return "unknown"
    found = {w for w in PROFILE_WORDS if re.search(rf"\b{w}\b", text.lower())}
    return found.pop() if len(found) == 1 else "unknown"


@dataclass
class ToolReply:
    calls: list[tuple[str, Any]]  # (tool name, raw arguments): untrusted, validated later
    text: str = ""


class MuseCompiler:
    def __init__(self, cfg: dict[str, Any]):
        self.base_url = cfg.get("base_url", "https://api.meta.ai/v1").rstrip("/")
        self.model = cfg.get("model", "muse-spark-1.3")
        self.timeout = float(cfg.get("timeout_s", 4.0))
        self.send_image = bool(cfg.get("send_image", True))
        self.key = os.environ.get("MODEL_API_KEY", "")
        self.last_ms: float | None = None
        self.last_error: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.key)

    def interpret(
        self, text: str, objects: list[dict], pointing: dict, jpeg: bytes | None = None
    ) -> tuple[Parsed, str] | None:
        scene = [{"id": o["id"], "name": o["name"], "table_xy": o.get("table_xy")} for o in objects]
        prompt = (
            f'Request: "{text}"\nVisible objects: {json.dumps(scene)}\n'
            f"Pointing at (scores): {json.dumps(pointing.get('candidates') or [])}"
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if jpeg and self.send_image:
            b64 = base64.b64encode(jpeg).decode()
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": content},
            ],
            "temperature": 0,
            "max_tokens": 1000,  # includes reasoning tokens; 200 returned empty replies
        }
        started = time.monotonic()
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                json=body,
                timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.key}"},
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))  # type: ignore[union-attr]
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            return None
        finally:
            self.last_ms = round((time.monotonic() - started) * 1000)
        self.last_error = None
        return self._to_parsed(text, data, {o["id"] for o in objects}), str(data.get("reply") or "")

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            json=body,
            timeout=self.timeout,
            headers={"Authorization": f"Bearer {self.key}"},
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]

    def call_tools(
        self,
        text: str,
        objects: list[dict],
        pointing: dict,
        tools: list[dict[str, Any]],
        jpeg: bytes | None = None,
    ) -> ToolReply | None:
        """Ask Muse to answer with tool calls. None on any failure (last_error set)."""
        scene = [{"id": o["id"], "name": o["name"]} for o in objects]
        prompt = (
            f'Person said: "{text}"\nVisible objects: {json.dumps(scene)}\n'
            f"Pointing at (scores): {json.dumps(pointing.get('candidates') or [])}"
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": TOOL_SYSTEM},
                {"role": "user", "content": self._content(prompt, jpeg)},
            ],
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0,
            "max_tokens": 1000,
        }
        started = time.monotonic()
        try:
            message = self._post(body)
            calls: list[tuple[str, Any]] = []
            for call in (message.get("tool_calls") or [])[:MAX_TOOL_CALLS]:
                fn = call.get("function") or {}
                raw = fn.get("arguments")
                try:
                    args = json.loads(raw) if isinstance(raw, str) else raw
                except ValueError:
                    args = raw  # the schema check rejects it
                calls.append((str(fn.get("name")), args))
            reply = ToolReply(calls, str(message.get("content") or "").strip())
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            return None
        finally:
            self.last_ms = round((time.monotonic() - started) * 1000)
        self.last_error = None
        return reply

    def describe(self, question: str, jpeg: bytes) -> str:
        """Answer a scene question from one frame. Raises on failure (the caller
        speaks a stock sentence instead)."""
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SCENE_SYSTEM},
                {"role": "user", "content": self._content(question, jpeg, force_image=True)},
            ],
            "temperature": 0,
            "max_tokens": 1000,
        }
        started = time.monotonic()
        try:
            return str(self._post(body).get("content") or "").strip()
        finally:
            self.last_ms = round((time.monotonic() - started) * 1000)

    def classify_profile(self, name: str, jpeg: bytes) -> str:
        """Grip profile of the object called ``name`` in one frame: rigid, fragile, liquid
        or unknown (``parse_profile``). Raises on a transport failure (the caller treats it
        as unknown)."""
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": PROFILE_SYSTEM},
                {
                    "role": "user",
                    "content": self._content(f"Object: {name}", jpeg, force_image=True),
                },
            ],
            "temperature": 0,
            "max_tokens": 20,
        }
        return parse_profile(self._post(body).get("content"))

    def _content(
        self, text: str, jpeg: bytes | None, force_image: bool = False
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if jpeg and (self.send_image or force_image):
            b64 = base64.b64encode(jpeg).decode()
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            )
        return content

    @staticmethod
    def _to_parsed(text: str, data: dict[str, Any], valid_ids: set[str]) -> Parsed:
        try:
            verb = Verb(str(data.get("verb", "unknown")))
        except ValueError:
            verb = Verb.UNKNOWN
        if verb not in (Verb.PICK_PLACE, Verb.POINT, Verb.HOME):
            verb = Verb.UNKNOWN
        object_id = data.get("object_id")
        object_id = object_id if object_id in valid_ids else None
        ordinal = (
            data.get("ordinal") if data.get("ordinal") in ("left", "right", "middle") else None
        )
        reference = (
            "named"
            if object_id
            else ("ordinal" if ordinal else str(data.get("reference") or "none"))
        )
        conf = float(data.get("confidence") or 0.0)
        return Parsed(
            verb=verb,
            object_id=object_id,
            reference=reference,
            ordinal=ordinal,
            grip_effort=GRIP.get(str(data.get("grip"))),
            # An LLM's self-reported confidence is capped below the grammar's.
            grammar_confidence=min(0.85, max(0.0, conf)) if verb is not Verb.UNKNOWN else 0.0,
            text=text,
            matched=["muse"],
        )


def build_llm(cfg: dict[str, Any]) -> MuseCompiler | None:
    mode = cfg.get("llm", "auto")
    if mode == "off":
        return None
    compiler = MuseCompiler(cfg)
    if mode == "auto" and not compiler.available:
        return None
    return compiler
