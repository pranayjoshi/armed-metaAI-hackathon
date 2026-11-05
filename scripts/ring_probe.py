"""Does Muse Voice Transcribe react to a phone ringtone at all? Streams the mic to the
realtime endpoint for a fixed time and prints every event it sends back (speechStart,
partials, finals), timestamped from the start, so a ring can be lined up against what the
model did. Nothing is fed to the runtime and the arm is not involved.

    conda activate lerobot && python scripts/ring_probe.py            # 60 s
    conda activate lerobot && python scripts/ring_probe.py --seconds 90 --mode DIARIZATION

Suggested run: 0-15 s quiet, 15-35 s play the ringtone, 35-50 s say a sentence, then quiet.
The key comes from MODEL_API_KEY or .env and is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import websockets

ENDPOINT = "wss://api.meta.ai/v1/asr/realtime"
MODEL = "muse-voice-transcribe-1.0"
RATE = 16000
FRAME_S = 0.1


def api_key() -> str:
    key = os.environ.get("MODEL_API_KEY", "")
    env = Path(__file__).resolve().parents[1] / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "MODEL_API_KEY":
                key = value.strip().strip("'\"")
    if not key:
        raise SystemExit("MODEL_API_KEY is not set (env or .env)")
    return key


async def probe(seconds: float, mode: str, keywords: list[str]) -> None:
    loop = asyncio.get_running_loop()
    audio: asyncio.Queue[bytes] = asyncio.Queue()
    levels: list[float] = []

    def on_audio(data, _frames, _time, _status) -> None:
        levels.append(float(np.sqrt(np.mean(np.square(data[:, 0].astype(np.float32))))))
        loop.call_soon_threadsafe(audio.put_nowait, bytes(data))

    body = {
        "authorization": {"accessToken": f"Bearer {api_key()}"},
        "model": MODEL,
        "audioEncoding": "PCM_16KHZ",
        "mode": mode,
        "partialMode": "CUMULATIVE",
        "emitAudioProgress": False,
    }
    if keywords:
        body["keywords"] = keywords
    async with websockets.connect(ENDPOINT, max_size=2**22) as ws:
        await ws.send(json.dumps(body))
        ack = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if "sessionId" not in ack:
            raise SystemExit(f"no session ack: {str(ack)[:200]}")
        print(f"connected ({mode}); listening for {seconds:.0f} s. Play the ringtone now and then.")
        t0 = time.monotonic()
        counts: dict[str, int] = {}

        async def send() -> None:
            with sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                                blocksize=int(RATE * FRAME_S), callback=on_audio):
                while time.monotonic() - t0 < seconds:
                    await ws.send(await audio.get())
                    if len(levels) % 10 == 0 and levels:  # one level line a second
                        print(f"  {time.monotonic() - t0:5.1f}s  mic level {levels[-1]:6.0f}")
            await ws.send(json.dumps({"type": "endStream"}))

        async def recv() -> None:
            async for raw in ws:
                if not isinstance(raw, str):
                    continue
                ev = json.loads(raw)
                kind = str(ev.get("type"))
                counts[kind] = counts.get(kind, 0) + 1
                text = ev.get("transcript") or ev.get("label") or ev.get("message") or ""
                print(f"{time.monotonic() - t0:5.1f}s  {kind:15} {text}")

        sender = asyncio.create_task(send())
        try:
            await asyncio.wait_for(recv(), seconds + 15)
        except (TimeoutError, websockets.ConnectionClosed):
            pass
        await sender
        print("events:", counts or "none")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--mode", default="ENDPOINTING", choices=("ENDPOINTING", "DIARIZATION"))
    ap.add_argument("--keywords", default="", help="comma list, e.g. ringtone,ringing")
    a = ap.parse_args()
    asyncio.run(probe(a.seconds, a.mode, [k.strip() for k in a.keywords.split(",") if k.strip()]))


if __name__ == "__main__":
    main()
