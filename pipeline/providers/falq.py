"""Minimal fal.ai queue client (submit → poll → result). Needs FAL_KEY."""
import base64
import time

import httpx

import config


def data_uri(img: bytes, mime="image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(img).decode()}"


def run(model: str, payload: dict, timeout_s: int = 900) -> dict:
    if not config.FAL_KEY:
        raise RuntimeError("FAL_KEY is not set in .env")
    h = {"Authorization": f"Key {config.FAL_KEY}"}
    r = httpx.post(f"https://queue.fal.run/{model}", json=payload, headers=h, timeout=60)
    r.raise_for_status()
    sub = r.json()
    status_url, response_url = sub["status_url"], sub["response_url"]
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        s = httpx.get(status_url, headers=h, timeout=30).json()
        if s.get("status") == "COMPLETED":
            res = httpx.get(response_url, headers=h, timeout=60)
            res.raise_for_status()
            return res.json()
        if s.get("status") in ("FAILED", "ERROR"):
            raise RuntimeError(f"fal job failed: {s}")
        time.sleep(5)
    raise TimeoutError("fal job timed out")
