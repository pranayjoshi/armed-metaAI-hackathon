"""HTTP + WebSocket API and the static operator UI.

``create_app`` serves the live runtime. ``create_replay_app`` serves recorded incidents
and sessions and the scripted simulation, read-only (docs/REPLAY_API.md).

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
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from h2m.app import H2M, RELEASE_DISABLED
from h2m.core import ROOT
from h2m.replay import ReplayError, ReplayLibrary, source_label
from h2m.robot import ArmState
from h2m.routines import RoutineError
from h2m.routing import RoutesUnavailable, catalog_tags, load_routes
from h2m.rules import RulesUnavailable
from h2m.scenario import SIM_NOTE, Scenario

INCIDENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # folder names only: no path traversal
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
TOKEN_HEADER = "X-H2M-Token"
TOKEN_META = "h2m-token"
MODE_META = "h2m-mode"  # "replay" on the replay / demo server
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Stopping is always safe: the STOP button must work even from a page without the token.
TOKENLESS = frozenset({"/api/stop"})
PROACTIVE_OFF = "the proactive layer is off (set proactive.enabled: true in configs/h2m.yaml)"


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


def _local_only(app: FastAPI, token: str | None) -> None:
    """Host, Origin and token checks (docs/LOCAL_API.md) on every HTTP request."""
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
        response = await call_next(request)
        if request.url.path.startswith("/web/"):
            # Revalidate the console's scripts on every load (ETag keeps it cheap): a cached
            # module from before an edit otherwise survives even a hard reload.
            response.headers["Cache-Control"] = "no-cache"
        return response


def _page(web: Path, token: str, mode: str | None = None, rel: str = "index.html") -> HTMLResponse:
    # The token goes into the page itself: readable only by this origin's scripts.
    html = (web / rel).read_text()
    meta = f'<meta name="{TOKEN_META}" content="{token}" />'
    if mode is not None:
        meta += f'\n  <meta name="{MODE_META}" content="{mode}" />'
    html = html.replace("<head>", f"<head>\n  {meta}", 1) if "<head>" in html else meta + html
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


async def _json_object(request: Request) -> dict[str, Any]:
    """The body as a JSON object (empty body = {}), else 400."""
    raw = await request.body()
    try:
        body = json.loads(raw) if raw.strip() else {}
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    return body


def _pins(body: dict[str, Any], required: bool) -> tuple[str | None, int | None]:
    """``card_id`` (string) and ``menu`` (integer) from a selection body, else 400."""
    card_id, menu = body.get("card_id"), body.get("menu")
    if required and (card_id is None or menu is None):
        raise HTTPException(400, "send the card_id and menu of the highlighted card")
    if card_id is not None and not isinstance(card_id, str):
        raise HTTPException(400, "card_id must be a string")
    if menu is not None and (isinstance(menu, bool) or not isinstance(menu, int)):
        raise HTTPException(400, "menu must be an integer")
    return card_id, menu


def create_app(h2m: H2M, token: str | None = None) -> FastAPI:
    app = FastAPI(title="Human2Machine")
    web = ROOT / "web"
    _local_only(app, token)

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

    # selection: Reflex cards (docs/SELECTION_API.md). The POSTs are the UI equivalents
    # of double twitch (step), twitch (accept) and brace (dismiss), tokened like any POST.
    @app.get("/api/selection")
    def selection() -> dict[str, Any]:
        return h2m.selection_snapshot()

    @app.post("/api/selection/step")
    async def selection_step(request: Request) -> dict[str, Any]:
        _, menu = _pins(await _json_object(request), required=False)
        return result(*h2m.selection_step(by="ui", menu_id=menu))

    @app.post("/api/selection/ack")
    async def selection_ack(request: Request) -> dict[str, Any]:
        """The UI rendered this highlighted card. With ``selection.require_ack`` the
        reaction clock for accepting it starts at this request's arrival (no ack: every
        accept of that card is refused). Never moves anything."""
        body = await _json_object(request)
        card_id, menu = _pins(body, required=True)
        return result(*h2m.selection_ack(card_id, menu, body.get("highlight")))

    @app.post("/api/selection/accept")
    async def selection_accept(request: Request) -> dict[str, Any]:
        """Accept the highlighted card. It must be pinned (card_id and menu), and the
        click passes the same gates as a twitch (ack + reaction, after a veto). A motion
        card's first accept only selects it ("confirm?"); the second confirms, and the
        card's request then goes through the gate like any tool request (blocking:
        worker thread)."""
        card_id, menu = _pins(await _json_object(request), required=True)
        return result(*await asyncio.to_thread(h2m.selection_accept, card_id, menu))

    @app.post("/api/selection/dismiss")
    async def selection_dismiss(request: Request) -> dict[str, Any]:
        card_id, menu = _pins(await _json_object(request), required=False)
        return result(*h2m.selection_dismiss(card_id, menu))

    @app.post("/api/selection/mode")
    async def selection_mode(request: Request) -> dict[str, Any]:
        mode = (await _json_object(request)).get("mode")
        if mode not in ("intent", "scanning"):
            raise HTTPException(400, "mode must be intent or scanning")
        return result(*h2m.selection_set_mode(mode))

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

    @app.post("/api/gripper/check")
    def gripper_check() -> dict[str, Any]:
        """Prove the gripper empty from the wrist camera (Muse Spark); nothing moves."""
        return result(*h2m.check_gripper_by_wrist("ui"))

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

    @app.post("/api/emg/source")
    def emg_source(source: str = Body(..., embed=True)) -> dict:
        """Switch between the real band (ganglion) and the synthetic source (mock dock)."""
        try:
            return h2m.set_emg_source(source)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/emg/mock/{gesture}")
    def emg_mock(gesture: str) -> dict:
        """One twitch, double twitch or brace from the UI's mock dock (synthetic only)."""
        if h2m.emg is None:
            raise HTTPException(409, "EMG is off")
        try:
            return h2m.emg.mock(gesture)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/sound/mock")
    def sound_mock(label: str = Body(..., embed=True)) -> dict:
        """A sound the runtime has no detector for (phone buzz, knock), from the Context
        view's trigger strip. It only ever becomes a card, through the proactive layer."""
        if h2m.proactive is None:
            raise HTTPException(409, PROACTIVE_OFF)
        h2m.log.emit("sound.event", label=str(label)[:30], mock=True)
        return {"ok": True}

    # speech
    @app.post("/api/speech/mute")
    def mute(muted: bool = Body(..., embed=True)) -> dict[str, Any]:
        if h2m.speech is not None:
            h2m.speech.muted = muted
        return {"ok": True}

    @app.post("/api/speaker/claim")
    async def speaker_claim(request: Request) -> dict[str, Any]:
        """Make a diarization label the wearer: ``{label?, name?}``; no label = the last
        speaker heard. Only the wearer's "yes" confirms; others show as "asked by X"."""
        if h2m.speech is None:
            raise HTTPException(409, "speech is off")
        body = await _json_object(request)
        label, name = body.get("label"), body.get("name")
        if label is not None and not isinstance(label, str):
            raise HTTPException(400, "label must be a string")
        if name is not None and not isinstance(name, str):
            raise HTTPException(400, "name must be a string")
        ok, detail = h2m.speech.claim(label, name)
        return {**result(ok, detail), "speech": h2m.speech.snapshot()}

    # cameras
    @app.get("/api/camera/{name}.jpg")
    def camera(name: str) -> Response:
        vision = h2m.vision
        if name == "scan":  # the frame the last SAM scan ran on
            data = None if vision is None else getattr(vision, "scan_jpeg", None)
        else:
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

    # routines (docs/PROACTIVE.md §5): the person's schedule, editable at runtime.
    # Editing a routine never moves anything: a routine only ever becomes a card.
    @app.get("/api/routines")
    def routines_list() -> dict[str, Any]:
        if h2m.proactive is None:
            return {"enabled": False, "routines": [], "detail": PROACTIVE_OFF}
        return {"enabled": True, **h2m.proactive.book.snapshot()}

    @app.post("/api/routines")
    async def routines_edit(request: Request) -> dict[str, Any]:
        """``{"name": ..., <fields>}`` edits or adds one routine; ``{"name", "delete": true}``
        removes it; ``{"request_only": bool}`` toggles "stop proposing"; ``{"run_now": name}``
        makes one due (it still only becomes a card). Saved to the routines file."""
        if h2m.proactive is None:
            raise HTTPException(409, PROACTIVE_OFF)
        body = await _json_object(request)
        book = h2m.proactive.book
        try:
            if "request_only" in body:
                h2m.proactive.set_request_only(body["request_only"] is True)
            elif "run_now" in body:
                if book.get(str(body["run_now"])) is None:
                    raise HTTPException(404, "no such routine")
                book.force_due(str(body["run_now"]))
            else:
                name = body.get("name")
                if not isinstance(name, str):
                    raise HTTPException(400, "send the routine's name")
                if body.get("delete") is True:
                    if not book.remove(name):
                        raise HTTPException(404, "no such routine")
                else:
                    fields = {k: v for k, v in body.items() if k not in ("name", "delete")}
                    book.update(name, fields)
                book.save()
        except RoutineError as exc:
            raise HTTPException(400, str(exc)) from exc
        h2m.log.emit("routine.edited", **{k: v for k, v in body.items() if k != "token"})
        return {"ok": True, **book.snapshot()}

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
        return _page(web, app.state.token)

    @app.get("/card")
    def card_page() -> HTMLResponse:
        """The Card: one page for the desktop window, the bedside screen and the hosted replay."""
        return _page(web, app.state.token, rel="card/index.html")

    app.mount("/web", StaticFiles(directory=web), name="web")
    return app


# ── replay / software demo ───────────────────────────────────────────────────
def create_replay_app(
    library: ReplayLibrary,
    cfg: dict[str, Any],
    scenario: Scenario | None = None,
    token: str | None = None,
) -> FastAPI:
    """Read-only server for recorded incidents and sessions, plus the scripted
    simulation (docs/REPLAY_API.md). Same Host / Origin / token rules as the live API.

    It has no actuation endpoint at all: no request, confirm, choose, arm, gripper,
    EMG drive, calibration, rule approval or rule deletion route exists here, with or
    without the token. The only POST is ``/api/stop`` (always safe): it stops the
    simulation's virtual arm and aborts the script; the stop stays latched."""
    app = FastAPI(title="Human2Machine replay")
    web = ROOT / "web"
    _local_only(app, token)
    emg_cfg = cfg.get("emg", {}) or {}
    brace_effort = float(emg_cfg.get("brace_effort", 0.35))
    brace_calm = float(emg_cfg.get("brace_calm", 0.12))
    try:
        routes = load_routes(ROOT / (cfg.get("rules", {}) or {}).get("routes_file",
                                                                       "configs/routes.yaml"))
        routes_error = None
    except RoutesUnavailable as exc:  # proposals then show as invalid, never as fine
        routes, routes_error = None, str(exc)
    zones = sorted(routes.zones) if routes else []
    tags = sorted(catalog_tags(routes, cfg.get("objects", []))) if routes else []

    def labels() -> dict[str, Any]:
        if scenario is not None:
            return {"mode": "replay", "label": "SIMULATION", "simulation": True,
                    "note": SIM_NOTE}
        return {"mode": "replay", "label": "REPLAY", "simulation": None,
                "note": "Replay of saved recordings. Nothing here moves an arm."}

    def incident(iid: str) -> Any:
        if not INCIDENT_ID.fullmatch(iid):
            raise HTTPException(400, "bad incident id")
        try:
            return library.incident(iid)
        except KeyError as exc:
            raise HTTPException(404, "no such incident") from exc
        except ReplayError as exc:
            raise HTTPException(503, f"incident unreadable: {exc}") from exc

    def need_scenario() -> Scenario:
        if scenario is None:
            raise HTTPException(404, "no simulation running (start it with `h2m demo`)")
        return scenario

    def replay_state() -> dict[str, Any]:
        h = scenario.h2m if scenario is not None else None
        out = {
            **labels(),
            "replay": {
                "incidents": len(library.incident_ids()),
                "sessions": len(library.session_ids()),
                "routes_error": routes_error,
            },
            "scenario": scenario.snapshot() if scenario is not None else None,
        }
        if h is None:
            return out
        snap = {**h.snapshot(), **out}
        fields = scenario.scene_fields() if scenario is not None else None
        if fields is not None:  # the simulation's own boxes, merged over the packet
            base = snap.get("scene") if isinstance(snap.get("scene"), dict) else {}
            snap["scene"] = {**base, **fields}
        return snap

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        return replay_state()

    @app.websocket("/api/live")
    async def live(ws: WebSocket) -> None:
        if origin_problem(ws.headers.get("host"), ws.headers.get("origin")) is not None:
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            while True:
                await ws.send_json(replay_state())
                await asyncio.sleep(0.1 if scenario is not None else 1.0)
        except (WebSocketDisconnect, RuntimeError):
            return

    @app.post("/api/stop")
    def stop() -> dict[str, Any]:
        if scenario is None or scenario.h2m is None:
            return {"ok": True, "detail": "replay: nothing is moving"}
        scenario.operator_stop("ui stop")
        return {"ok": True, "detail": "stopped the simulated arm; the scenario is aborted"}

    @app.get("/api/replay")
    def replay_index() -> dict[str, Any]:
        return {**labels(), **library.summaries(), "scenario": scenario is not None,
                "zones": zones, "routes_error": routes_error}

    @app.get("/api/replay/incidents")
    def replay_incidents() -> dict[str, Any]:
        return {**labels(), "incidents": library.summaries()["incidents"]}

    @app.get("/api/replay/incident/{incident_id}")
    def replay_incident(incident_id: str) -> dict[str, Any]:
        inc = incident(incident_id)
        return inc.payload(f"/api/replay/incident/{inc.id}", zones, tags, brace_effort,
                           brace_calm)

    @app.get("/api/replay/incident/{incident_id}/emg")
    def replay_emg(incident_id: str) -> dict[str, Any]:
        inc = incident(incident_id)
        return {"id": inc.id, **source_label(inc.meta),
                **inc.emg_series(brace_effort, brace_calm)}

    @app.get("/api/replay/incident/{incident_id}/timeline")
    def replay_timeline(incident_id: str) -> dict[str, Any]:
        inc = incident(incident_id)
        return {"id": inc.id, **source_label(inc.meta),
                "timeline": inc.timeline(zones, tags, brace_effort, brace_calm)}

    @app.get("/api/replay/incident/{incident_id}/frame/{index}/{cam}.jpg")
    def replay_frame(incident_id: str, index: int, cam: str) -> Response:
        path = incident(incident_id).frame_path(index, cam)
        if path is None:
            raise HTTPException(404, "no such frame")
        return Response(path.read_bytes(), media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/replay/session/{session_id}")
    def replay_session(session_id: str) -> dict[str, Any]:
        if not INCIDENT_ID.fullmatch(session_id):
            raise HTTPException(400, "bad session id")
        try:
            return library.session(session_id).payload()
        except KeyError as exc:
            raise HTTPException(404, "no such session") from exc
        except ReplayError as exc:
            raise HTTPException(503, f"session unreadable: {exc}") from exc

    @app.get("/api/replay/scenario")
    def replay_scenario() -> dict[str, Any]:
        return need_scenario().snapshot()

    @app.get("/api/replay/scenario/timeline")
    def replay_scenario_timeline() -> dict[str, Any]:
        return need_scenario().timeline()

    @app.get("/api/replay/scenario/frame.jpg")
    def replay_scenario_frame() -> Response:
        data = need_scenario().frame()
        if data is None:
            raise HTTPException(404, "no frame yet")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # Read-only mirrors of live GET routes, so the operator views can render the simulation.
    @app.get("/api/camera/{name}.jpg")
    def camera(name: str) -> Response:
        data = scenario.frame() if scenario is not None and name == "scene" else None
        if data is None:
            raise HTTPException(404, "no frame")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/rules")
    def rules_list() -> dict[str, Any]:
        h = scenario.h2m if scenario is not None else None
        if h is None:
            return {"rules": [], "zones": zones, **labels()}
        try:
            rules = h.rules.list()
        except RulesUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"rules": [asdict(r) for r in rules], "zones": zones, **labels()}

    @app.get("/api/routines")
    def routines_list() -> dict[str, Any]:
        h = scenario.h2m if scenario is not None else None
        if h is None or getattr(h, "proactive", None) is None:
            return {**labels(), "enabled": False, "routines": []}
        return {**labels(), "enabled": True, **h.proactive.book.snapshot()}

    @app.get("/api/rules/proposal")
    def proposal() -> dict[str, Any]:
        h = scenario.h2m if scenario is not None else None
        return {"proposal": h.learner.snapshot()["proposal"] if h is not None else None,
                **labels()}

    @app.get("/api/incident")
    def incident_latest() -> dict[str, Any]:
        h = scenario.h2m if scenario is not None else None
        return {"incident": h.learner.snapshot()["incident"] if h is not None else None,
                **labels()}

    @app.get("/api/incident/{incident_id}")
    def incident_get(incident_id: str) -> dict[str, Any]:
        inc = incident(incident_id)
        return {"incident": inc.meta, "explanation": inc.explanation_payload(),
                **source_label(inc.meta)}

    @app.get("/")
    def index() -> HTMLResponse:
        return _page(web, app.state.token, mode="replay")

    @app.get("/card")
    def card_page() -> HTMLResponse:
        return _page(web, app.state.token, mode="replay", rel="card/index.html")

    app.mount("/web", StaticFiles(directory=web), name="web")
    return app
