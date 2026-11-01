"""`h2m muse-check`: verify the Meta Model API key with one text + image call."""

from __future__ import annotations

import base64
import os
import time

import cv2
import httpx
import numpy as np

BASE = "https://api.meta.ai/v1"
MODELS = ("muse-wonder-1.0", "muse-spark-1.1")


def main() -> int:
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        print("MODEL_API_KEY is not set. Put it in .env (git-ignored) or export it.")
        return 1
    headers = {"Authorization": f"Bearer {key}"}
    try:
        listing = httpx.get(f"{BASE}/models", headers=headers, timeout=15)
        print(f"GET /models -> {listing.status_code}")
        if listing.is_success:
            print(
                "  " + ", ".join(sorted(m.get("id", "?") for m in listing.json().get("data", [])))
            )
    except httpx.HTTPError as exc:
        print(f"GET /models failed: {exc}")

    image = np.zeros((240, 320, 3), np.uint8)
    cv2.rectangle(image, (100, 60), (220, 180), (0, 0, 255), -1)
    jpeg = base64.b64encode(cv2.imencode(".jpg", image)[1].tobytes()).decode()
    ok = False
    for model in MODELS:
        body = {
            "model": model,
            "max_tokens": 30,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What colour is the square? One word."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{jpeg}"},
                        },
                    ],
                }
            ],
        }
        started = time.monotonic()
        try:
            resp = httpx.post(f"{BASE}/chat/completions", json=body, headers=headers, timeout=60)
            ms = round((time.monotonic() - started) * 1000)
            if resp.is_success:
                print(
                    f"{model}: OK in {ms} ms -> {resp.json()['choices'][0]['message']['content']!r}"
                )
                ok = True
            else:
                print(f"{model}: HTTP {resp.status_code} {resp.text[:200]}")
        except httpx.HTTPError as exc:
            print(f"{model}: {exc}")
    return 0 if ok else 1
