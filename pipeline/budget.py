"""Spend ledger + monthly cap. Every paid call is estimated BEFORE it runs and recorded after.
If a call would push this month past MONTHLY_BUDGET_USD the job stops with a clear message
(raise the cap in .env, then press Retry)."""
import time

import config
from pipeline import store

# USD prices (fal.ai, Nov 2026) — override in .env if they change
PRICES = {
    "fal-ai/ltx-2-19b/distilled/audio-to-video": ("per_mp", 0.0008),
    "fal-ai/ltx-2-19b/audio-to-video": ("per_mp", 0.0018),
    "lightricks/ltx-2.5/audio-to-video/fast": ("per_sec", 0.13),
    "fal-ai/wan/v2.2-a14b/image-to-video/turbo": ("per_call", 0.10),
    "fal-ai/nano-banana/edit": ("per_call", 0.0398),
    "fal-ai/flux/schnell": ("per_call", 0.003),
    "fal-ai/whisper": ("per_call", 0.002),
}


class BudgetExceeded(RuntimeError):
    pass


def _init():
    with store._lock, store._conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS costs(t REAL, month TEXT, job TEXT, what TEXT, usd REAL)")


def estimate(model: str, seconds: float = 0, w: int = 720, h: int = 1280, fps: float = 25) -> float:
    kind, rate = PRICES.get(model, ("per_call", float(config.env("COST_UNKNOWN_CALL_USD", "0.10"))))
    rate = float(config.env("PRICE_" + model.replace("/", "_").replace("-", "_").replace(".", "_").upper(), str(rate)))
    if kind == "per_mp":
        return rate * w * h * max(1, seconds * fps) / 1e6
    if kind == "per_sec":
        return rate * max(2.0, seconds)
    return rate


def spent(month: str | None = None) -> float:
    _init()
    month = month or time.strftime("%Y%m")
    with store._conn() as c:
        r = c.execute("SELECT COALESCE(SUM(usd),0) FROM costs WHERE month=?", (month,)).fetchone()
    return float(r[0])


def job_spent(job_id: str) -> float:
    _init()
    with store._conn() as c:
        return float(c.execute("SELECT COALESCE(SUM(usd),0) FROM costs WHERE job=?", (job_id,)).fetchone()[0])


def cap() -> float:
    return float(config.env("MONTHLY_BUDGET_USD", "90"))


def check(est: float, what: str):
    if spent() + est > cap():
        raise BudgetExceeded(f"Monthly budget cap ${cap():.0f} reached (spent ${spent():.2f}, next step '{what}' "
                             f"≈ ${est:.2f}). Raise MONTHLY_BUDGET_USD in .env and press Retry.")


def record(job_id: str, what: str, usd: float):
    if usd <= 0:
        return
    _init()
    with store._lock, store._conn() as c:
        c.execute("INSERT INTO costs VALUES(?,?,?,?,?)", (time.time(), time.strftime("%Y%m"), job_id, what, usd))


def monthly() -> dict:
    _init()
    with store._conn() as c:
        return {m: u for m, u in c.execute("SELECT month, SUM(usd) FROM costs GROUP BY month")}
