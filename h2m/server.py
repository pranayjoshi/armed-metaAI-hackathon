"""HTTP + WebSocket API and the static operator UI."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from h2m.app import H2M
from h2m.core import ROOT


def create_app(h2m: H2M) -> FastAPI:
    app = FastAPI(title="Human2Machine")
    web = ROOT / "web"

    def result(ok: bool, detail: str) -> dict[str, Any]:
        if not ok:
            raise HTTPException(status_code=409, detail=detail)
        return {"ok": True, "detail": detail}

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        return h2m.snapshot()

    @app.websocket("/api/live")
    async def live(ws: WebSocket) -> None:
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
            arm.disarm()
            return {"ok": True}
        if action == "reset":
            return result(*arm.reset())
        if action == "home":
            h2m.run_typed("go home")
            return {"ok": True}
        raise HTTPException(404, "unknown arm action")

    # EMG
    @app.post("/api/emg/calibrate/{phase}")
    async def calibrate(phase: str, seconds: float = 3.0) -> dict[str, Any]:
        if h2m.emg is None:
            raise HTTPException(409, "EMG is off")
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

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(web / "index.html")

    app.mount("/web", StaticFiles(directory=web), name="web")
    return app
