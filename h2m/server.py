"""HTTP + WebSocket API and the static operator UI.

Local-only, same-origin (see docs/LOCAL_API.md):
- Any Host other than 127.0.0.1 / localhost (any port) is refused (DNS rebinding).
- A request or WebSocket with an Origin that is not this same local origin is refused,
  so another web page in the browser cannot drive the arm.
- POST / PUT / PATCH / DELETE (except ``/api/stop``: stopping is always safe) need the
  per-process token in ``X-H2M-Token``. The token
  is generated at start-up and written into the UI page served at ``/`` as
  ``<meta name="h2m-token">``; only a page from this origin can read it.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import re
import secrets
from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from h2m.app import H2M, RELEASE_DISABLED
from h2m.core import ROOT
from h2m.robot import ArmState
from h2m.rules import RulesUnavailable

INCIDENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # folder names only: no path traversal
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
TOKEN_HEADER = "X-H2M-Token"
TOKEN_META = "h2m-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Stopping is always safe: the STOP button must work even from a page without the token.
TOKENLESS = frozenset({"/api/stop"})


def _host_name(host: str | None) -> str | None:
    """Hostname of a Host header (port stripped), or None if malformed."""
    if not host:
        return None
    try:
        return urlsplit(f"//{host}").hostname
    except ValueError:
        return None


def origin_problem(host: str | None, origin: str | None) -> str | None:
    """Why a request with these Host / Origin headers is refused, or None."""
    if _host_name(host) not in LOCAL_HOSTS:
        return "host not allowed"
    if origin is None:
        return None  # not a browser cross-site request (curl, same-origin GET)
    try:
        parts = urlsplit(origin)
    except ValueError:
        return "origin not allowed"
    if parts.scheme not in ("http", "https") or parts.netloc.lower() != str(host).lower():
        return "origin not allowed"
    return None


def create_app(h2m: H2M, token: str | None = None) -> FastAPI:
    app = FastAPI(title="Human2Machine")
    web = ROOT / "web"
    app.state.token = token or secrets.token_urlsafe(32)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        problem = origin_problem(request.headers.get("host"), request.headers.get("origin"))
        tokened = request.method not in SAFE_METHODS and request.url.path not in TOKENLESS
        if problem is None and tokened:
            sent = request.headers.get(TOKEN_HEADER, "")
            if not hmac.compare_digest(sent.encode(), app.state.token.encode()):
                problem = "missing or wrong session token"
        if problem is not None:
            return JSONResponse({"detail": problem}, status_code=403)
        return await call_next(request)

    def result(ok: bool, detail: str) -> dict[str, Any]:
        if not ok:
            raise HTTPException(status_code=409, detail=detail)
        return {"ok": True, "detail": detail}

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        return h2m.snapshot()

    @app.websocket("/api/live")
    async def live(ws: WebSocket) -> None:
        if origin_problem(ws.headers.get("host"), ws.headers.get("origin")) is not None:
            await ws.close(code=1008)  # policy violation: not this local origin
            return
        await ws.accept()
        try:
            while True:
                await ws.send_json(h2m.snapshot())
                await asyncio.sleep(0.1)
        except (WebSocketDisconnect, RuntimeError):
            return

    # requests
    @app.post("/api/say")
    def say(text: str = Body(..., embed=True)) -> dict[str, Any]:
        h2m.run_typed(text)
        return {"ok": True}

    @app.post("/api/ask")
    async def ask(question: str = Body(..., embed=True)) -> dict[str, Any]:
        """Scene question: Muse answers from the current frame and the reply is spoken.
        Runs in a worker thread (network); never on the event or EMG thread."""
        question = question.strip()[:280]
        if not question:
            raise HTTPException(400, "empty question")
        result = await asyncio.to_thread(h2m.ask, question)
        return {"ok": result.ok, "reply": h2m.reply, "result": result.as_dict()}

    @app.post("/api/confirm")
    def confirm() -> dict[str, Any]:
        return result(*h2m.confirm(by="ui"))

    @app.post("/api/choose/{object_id}")
    def choose(object_id: str) -> dict[str, Any]:
        return result(*h2m.choose(object_id, by="ui"))

    @app.post("/api/cancel")
    def cancel() -> dict[str, Any]:
        h2m.cancel("ui")
        return {"ok": True}

    @app.post("/api/stop")
    def stop() -> dict[str, Any]:
        h2m.stop("ui stop")
        return {"ok": True}

    # arm
    @app.post("/api/arm/{action}")
    def arm_action(action: str) -> dict[str, Any]:
        arm = h2m.arm
        if action == "arm":
            return result(*arm.arm())
        if action == "disarm":
            h2m.disarm("ui disarm")  # through the app: bumps the request epoch
            return {"ok": True}
        if action == "reset":
            return result(*arm.reset())
        if action == "home":
            h2m.run_typed("go home")
            return {"ok": True}
        raise HTTPException(404, "unknown arm action")

    @app.post("/api/gripper/release")
    async def release_gripper(request: Request) -> dict[str, Any]:
        """SAFETY REVIEW: operator-only gripper release, two steps (token required, like
        every POST). Off unless ``safety.operator_release`` is true: 403. A POST without
        ``release_id`` (empty body or ``{}``) only offers: it returns a ``release_id``,
        the pose and what is held, and moves nothing. A POST with ``{"release_id": id}``
        within ``operator_release_confirm_s`` commits that offer (single use). See
        docs/LOCAL_API.md."""
        if not h2m.operator_release_enabled():
            raise HTTPException(status_code=403, detail=RELEASE_DISABLED)
        raw = await request.body()
        try:
            body = json.loads(raw) if raw.strip() else {}
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        if "release_id" not in body:
            ok, detail, offer = await asyncio.to_thread(h2m.offer_release, "ui")
            if not ok or offer is None:
                raise HTTPException(status_code=409, detail=detail)
            return {"ok": True, "detail": detail, **offer}
        release_id = body["release_id"]
        return result(*await asyncio.to_thread(h2m.release_gripper, release_id, "ui"))

    # EMG
    @app.post("/api/emg/calibrate/{phase}")
    async def calibrate(phase: str, seconds: float = 3.0) -> dict[str, Any]:
        """Brace and open hand are not detected while a phase records, so only with the
        arm DISARMED or STOPPED (the arm guard also refuses motion meanwhile), and for
        a bounded time (``emg.calibrate_min_s`` .. ``emg.calibrate_max_s``)."""
        if h2m.emg is None:
            raise HTTPException(409, "EMG is off")
        if h2m.arm.state not in (ArmState.DISARMED, ArmState.STOPPED):
            raise HTTPException(409, f"disarm or stop the arm first (it is {h2m.arm.state})")
        emg_cfg = h2m.cfg.get("emg", {}) or {}
        lo = float(emg_cfg.get("calibrate_min_s", 1.0))
        hi = float(emg_cfg.get("calibrate_max_s", 10.0))
        seconds = min(max(seconds, lo), hi) if seconds == seconds else lo  # NaN: shortest
        if phase not in ("rest", "squeeze", "open", "brace", "finish"):
            raise HTTPException(400, "phase must be rest, squeeze, open, brace or finish")
        return await asyncio.to_thread(h2m.emg.calibrate, phase, seconds)

    @app.post("/api/emg/synthetic")
    def synthetic(flexor: float | None = Body(None), extensor: float | None = Body(None)) -> dict:
        source = getattr(h2m.emg, "source", None)
        if source is None or source.name != "synthetic":
            raise HTTPException(409, "only available with --emg synthetic")
        source.set_drive(flexor, extensor)
        return {"ok": True}

    # speech
    @app.post("/api/speech/mute")
    def mute(muted: bool = Body(..., embed=True)) -> dict[str, Any]:
        if h2m.speech is not None:
            h2m.speech.muted = muted
        return {"ok": True}

    # cameras
    @app.get("/api/camera/{name}.jpg")
    def camera(name: str) -> Response:
        vision = h2m.vision
        cam = None if vision is None else (vision.scene if name == "scene" else vision.wrist)
        data = cam.jpeg() if cam is not None else None
        if data is None:
            raise HTTPException(404, "no frame")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # sessions and trials
    @app.post("/api/session/start")
    def session_start(participant: str = Body("anon"), notes: str = Body("")) -> dict[str, Any]:
        return {"ok": True, "session_id": h2m.recorder.start(participant, notes)}

    @app.post("/api/session/stop")
    def session_stop() -> dict[str, Any]:
        h2m.recorder.stop()
        return {"ok": True}

    @app.post("/api/trial/start")
    def trial_start(
        expected_action: str = Body("pick_place"),
        expected_object: str | None = Body(None),
        condition: str = Body(""),
        instruction: str = Body(""),
    ) -> dict[str, Any]:
        try:
            return {
                "ok": True,
                "trial": h2m.recorder.start_trial(
                    expected_action, expected_object, condition, instruction
                ),
            }
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/trial/end")
    def trial_end(outcome: str = Body(..., embed=True)) -> dict[str, Any]:
        trial = h2m.recorder.end_trial(outcome)
        if trial is None:
            raise HTTPException(409, "no trial running")
        return {"ok": True}

    # rules and incidents
    @app.get("/api/rules")
    def rules_list() -> dict[str, Any]:
        try:
            rules = h2m.rules.list()
        except RulesUnavailable as exc:  # corrupt store: say so, never show "no rules"
            raise HTTPException(503, str(exc)) from exc
        return {"rules": [asdict(r) for r in rules], "zones": sorted(h2m.zone_names())}

    @app.delete("/api/rules/{rule_id}")
    def rules_delete(rule_id: str) -> dict[str, Any]:
        try:
            deleted = h2m.rules.delete(rule_id)
        except RulesUnavailable as exc:  # never overwrite a store we cannot read
            raise HTTPException(503, str(exc)) from exc
        if not deleted:
            raise HTTPException(404, "no such rule")
        h2m.log.emit("rule.deleted", rule_id=rule_id, by="ui")
        return {"ok": True}

    @app.get("/api/rules/proposal")
    def proposal() -> dict[str, Any]:
        return {"proposal": h2m.learner.snapshot()["proposal"]}

    @app.post("/api/rules/proposal/{rule_id}/approve")
    def proposal_approve(rule_id: str) -> dict[str, Any]:
        # The id pins the proposal the person saw: a stale click cannot approve a newer one.
        return result(*h2m.learner.approve(by="ui", expected_id=rule_id))

    @app.post("/api/rules/proposal/{rule_id}/reject")
    def proposal_reject(rule_id: str) -> dict[str, Any]:
        return result(*h2m.learner.reject(by="ui", expected_id=rule_id, reason="ui"))

    @app.get("/api/incident")
    def incident_latest() -> dict[str, Any]:
        return {"incident": h2m.learner.snapshot()["incident"]}

    @app.get("/api/incident/{incident_id}")
    def incident_get(incident_id: str) -> dict[str, Any]:
        if not INCIDENT_ID.fullmatch(incident_id):
            raise HTTPException(400, "bad incident id")
        folder = h2m.learner.root / incident_id
        try:
            meta = json.loads((folder / "meta.json").read_text())
        except FileNotFoundError as exc:
            raise HTTPException(404, "no such incident") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(503, f"incident unreadable: {exc}") from exc
        try:
            explanation = json.loads((folder / "explanation.json").read_text())
        except (OSError, ValueError):
            explanation = None
        return {"incident": meta, "explanation": explanation}

    @app.get("/")
    def index() -> HTMLResponse:
        # The token goes into the page itself: readable only by this origin's scripts.
        html = (web / "index.html").read_text()
        meta = f'<meta name="{TOKEN_META}" content="{app.state.token}" />'
        html = html.replace("<head>", f"<head>\n  {meta}", 1) if "<head>" in html else meta + html
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    app.mount("/web", StaticFiles(directory=web), name="web")
    return app
