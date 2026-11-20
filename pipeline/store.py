"""Job storage: SQLite row per video + a folder per job for files."""
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from config import DB_PATH, JOBS_DIR

_lock = threading.Lock()

# Status flow (matches the team workflow chart)
STATUSES = {
    "queued": "Queued",
    "scripting": "2 · Writing script & storyboard",
    "await_script": "3 · Waiting for script approval",
    "keyframes": "4 · Generating keyframe images",
    "await_keyframes": "5 · Waiting for keyframe approval",
    "clips": "6 · Generating video clips",
    "qc": "7 · Quality check",
    "voice": "8 · Voice, music, subtitles",
    "assembly": "9 · Assembly & branding",
    "await_final": "10 · Waiting for final approval",
    "published": "11 · Published",
    "failed": "Failed",
}
WAITING = {"await_script", "await_keyframes", "await_final"}


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, created REAL, updated REAL, status TEXT,
                brief TEXT, storyboard TEXT, state TEXT, history TEXT, error TEXT)"""
        )


def job_dir(job_id: str) -> Path:
    d = JOBS_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def create(brief: dict) -> str:
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    now = time.time()
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",
            (job_id, now, now, "queued", json.dumps(brief, ensure_ascii=False),
             "null", "{}", "[]", ""),
        )
    job_dir(job_id)
    log(job_id, "Brief submitted")
    return job_id


def get(job_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not r:
        return None
    j = dict(r)
    for k in ("brief", "storyboard", "state", "history"):
        j[k] = json.loads(j[k]) if j[k] else None
    j["status_label"] = STATUSES.get(j["status"], j["status"])
    return j


def all_jobs() -> list[dict]:
    with _conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM jobs ORDER BY created DESC")]
    return [get(i) for i in ids]


def update(job_id: str, **fields):
    sets, vals = [], []
    for k, v in fields.items():
        if k in ("brief", "storyboard", "state", "history"):
            v = json.dumps(v, ensure_ascii=False)
        sets.append(f"{k}=?")
        vals.append(v)
    sets.append("updated=?")
    vals.append(time.time())
    with _lock, _conn() as c:
        c.execute(f"UPDATE jobs SET {','.join(sets)} WHERE id=?", (*vals, job_id))


def set_status(job_id: str, status: str, note: str = ""):
    update(job_id, status=status, error="" if status != "failed" else note)
    log(job_id, STATUSES.get(status, status) + (f" — {note}" if note else ""))


def log(job_id: str, msg: str):
    j = get(job_id)
    hist = (j or {}).get("history") or []
    hist.append({"t": time.strftime("%d %b %H:%M:%S"), "msg": msg})
    update(job_id, history=hist)
