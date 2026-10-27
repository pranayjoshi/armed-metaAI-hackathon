"""Meta Muse Spark as the language interpreter for free-form requests.

Only called when the local grammar cannot parse a request. It returns the
same structure as the grammar (verb, object, reference, grip words); target
fusion with pointing and the safety decision stay deterministic.
STOP and CANCEL never reach this module.
"""

from __future__ import annotations

import base64
import json
import os
import re
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


class MuseCompiler:
    def __init__(self, cfg: dict[str, Any]):
        self.base_url = cfg.get("base_url", "https://api.meta.ai/v1").rstrip("/")
        self.model = cfg.get("model", "muse-spark-1.1")
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
            "max_tokens": 200,
        }
        import time

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
