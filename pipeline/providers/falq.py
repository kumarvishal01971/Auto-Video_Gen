"""fal.ai queue client (submit → poll → result) with retries, plus file upload. Needs FAL_KEY.
Errors are classified so callers know whether to retry, try another model, or stop:
  FalError.transient  – network / 429 / 5xx / timeout  → retried here automatically
  FalError.fatal      – bad key or no balance           → stop, other fal models will fail too
  otherwise           – this model rejected the input   → caller tries its next model"""
import base64
import mimetypes
import time
from pathlib import Path

import httpx

import config

REST = "https://rest.alpha.fal.ai"


class FalError(RuntimeError):
    def __init__(self, msg, transient=False, fatal=False):
        super().__init__(msg)
        self.transient, self.fatal = transient, fatal


def _h():
    if not config.FAL_KEY:
        raise FalError("FAL_KEY is not set in .env", fatal=True)
    return {"Authorization": f"Key {config.FAL_KEY}"}


def _check(r: httpx.Response, what: str):
    if r.status_code < 400:
        return
    body = r.text[:300]
    if r.status_code in (401, 403) or "balance" in body.lower() or "locked" in body.lower():
        raise FalError(f"{what}: HTTP {r.status_code} {body} (check FAL_KEY / fal balance)", fatal=True)
    raise FalError(f"{what}: HTTP {r.status_code} {body}", transient=r.status_code in (408, 425, 429, 500, 502, 503, 504))


def data_uri(data: bytes, mime="image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def upload(path: Path) -> str:
    """Upload a local file to fal storage and return its URL (falls back to a data URI)."""
    path = Path(path)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        r = httpx.post(f"{REST}/storage/upload/initiate", params={"storage_type": "fal-cdn-v3"},
                       json={"content_type": mime, "file_name": path.name}, headers=_h(), timeout=60)
        _check(r, "upload")
        info = r.json()
        p = httpx.put(info["upload_url"], content=path.read_bytes(), headers={"Content-Type": mime}, timeout=300)
        p.raise_for_status()
        return info["file_url"]
    except FalError as e:
        if e.fatal:
            raise
    except Exception:
        pass
    return data_uri(path.read_bytes(), mime)


def _once(model: str, payload: dict, timeout_s: int) -> dict:
    h = _h()
    r = httpx.post(f"https://queue.fal.run/{model}", json=payload, headers=h, timeout=120)
    _check(r, model)
    sub = r.json()
    status_url, response_url = sub["status_url"], sub["response_url"]
    t0, wait = time.time(), 3
    while time.time() - t0 < timeout_s:
        s = httpx.get(status_url, headers=h, timeout=30)
        _check(s, f"{model} status")
        st = s.json().get("status")
        if st == "COMPLETED":
            res = httpx.get(response_url, headers=h, timeout=120)
            _check(res, model)
            return res.json()
        if st in ("FAILED", "ERROR", "CANCELLED"):
            raise FalError(f"{model} job {st}: {str(s.json())[:300]}")
        time.sleep(wait)
        wait = min(10, wait + 1)
    raise FalError(f"{model} timed out after {timeout_s}s", transient=True)


def run(model: str, payload: dict, timeout_s: int = 900, retries: int = 2) -> dict:
    """Run a fal model; transient failures are retried with backoff."""
    last = None
    for attempt in range(retries + 1):
        try:
            return _once(model, payload, timeout_s)
        except FalError as e:
            last = e
            if not e.transient:
                raise
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last = FalError(f"{model}: network error {e}", transient=True)
        time.sleep(5 * 2 ** attempt)
    raise last


def download(url: str, out: Path) -> Path:
    for attempt in range(3):
        try:
            r = httpx.get(url, timeout=300, follow_redirects=True)
            r.raise_for_status()
            out.write_bytes(r.content)
            return out
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
    return out
