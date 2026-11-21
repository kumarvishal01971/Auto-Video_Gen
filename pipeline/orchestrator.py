"""Runs the steps in order and stops at the three approval gates.
One background worker processes jobs one at a time (safe for API rate limits)."""
import json
import queue
import shutil
import threading
import time
import traceback

import config
from pipeline import assemble, media, script, store

_q: "queue.Queue[tuple]" = queue.Queue()
MAX_REGEN = 2


def start_worker():
    store.init()
    # resume anything that was mid-run when the server stopped
    for j in store.all_jobs():
        if j["status"] not in store.WAITING and j["status"] not in ("published", "failed"):
            enqueue(j["id"])
    threading.Thread(target=_loop, daemon=True).start()


def enqueue(job_id: str):
    _q.put((job_id,))


def _loop():
    while True:
        (job_id,) = _q.get()
        try:
            advance(job_id)
        except Exception as e:
            store.set_status(job_id, "failed", f"{type(e).__name__}: {e}")
            store.log(job_id, traceback.format_exc()[-1500:])


def advance(job_id: str):
    """Run automatic steps from the job's current status until the next gate."""
    j = store.get(job_id)
    if not j or (j["state"] or {}).get("deleted"):
        return  # deleted or in Trash: don't spend API credits on it
    st, state, sb = j["status"], j["state"] or {}, j["storyboard"]

    if st in ("queued", "scripting"):
        store.set_status(job_id, "scripting")
        sb = script.generate(j["brief"], state.pop("comments", ""), sb)
        store.update(job_id, storyboard=sb, state=state)
        store.set_status(job_id, "await_script")
        return

    if st == "keyframes":
        only = state.pop("redo_shots", None)
        sb = media.keyframes(job_id, sb, only, state.pop("shot_notes", None))
        store.update(job_id, storyboard=sb, state=state)
        store.set_status(job_id, "await_keyframes")
        return

    if st == "clips":
        sb = media.clips(job_id, sb)
        store.update(job_id, storyboard=sb)
        st = "qc"
        store.set_status(job_id, "qc")

    if st == "qc":
        for attempt in range(MAX_REGEN + 1):
            sb, failed = media.quality_check(job_id, sb)
            if not failed or attempt == MAX_REGEN:
                break
            store.log(job_id, f"QC failed shots {failed}; regenerating (try {attempt + 1})")
            for s in sb["shots"]:
                if s["id"] in failed:
                    s["seed"] += 101
            sub = {**sb, "shots": [s for s in sb["shots"] if s["id"] in failed]}
            media.clips(job_id, sub)
        if failed:
            store.log(job_id, f"⚠ Shots {failed} still failed QC — using animated keyframe instead. Check at final approval.")
        store.update(job_id, storyboard=sb)
        st = "voice"
        store.set_status(job_id, "voice")

    if st == "voice":
        sb = media.voice(job_id, sb)
        store.update(job_id, storyboard=sb)
        st = "assembly"
        store.set_status(job_id, "assembly")

    if st == "assembly":
        sb = assemble.assemble(job_id, sb)
        store.update(job_id, storyboard=sb)
        store.set_status(job_id, "await_final")
        return


# ---------- Approval actions (called by the dashboard / API) ----------
def _go(job_id, status, **state_updates):
    j = store.get(job_id)
    state = j["state"] or {}
    state.update(state_updates)
    store.update(job_id, state=state)
    store.set_status(job_id, status)
    enqueue(job_id)


def script_decision(job_id: str, approve: bool, comments: str = "", edited: dict | None = None, who: str = ""):
    if edited:
        brief = store.get(job_id)["brief"]
        store.update(job_id, storyboard=script.normalise(edited, brief, config.load_brand()))
        store.log(job_id, f"Storyboard edited by {who or 'reviewer'}")
    if approve:
        store.log(job_id, f"✔ Script approved by {who or 'reviewer'}")
        _go(job_id, "keyframes")
    else:
        store.log(job_id, f"✘ Script rejected by {who or 'reviewer'}: {comments}")
        _go(job_id, "scripting", comments=comments)


def keyframe_decision(job_id: str, approve: bool, redo: list[int] | None = None,
                      notes: dict | None = None, who: str = ""):
    if approve:
        store.log(job_id, f"✔ Keyframes approved by {who or 'reviewer'}")
        _go(job_id, "clips")
    else:
        store.log(job_id, f"↺ Redo shots {redo} requested by {who or 'reviewer'}")
        _go(job_id, "keyframes", redo_shots=redo or None, shot_notes=notes or {})


def final_decision(job_id: str, approve: bool, rerun_from: str = "", comments: str = "", who: str = ""):
    if approve:
        store.log(job_id, f"✔ Final video approved by {who or 'reviewer'}")
        publish(job_id)
        return
    store.log(job_id, f"↺ Changes requested by {who or 'reviewer'} (from {rerun_from}): {comments}")
    target = {"script": "scripting", "keyframes": "keyframes", "clips": "clips", "voice": "voice",
              "assembly": "assembly"}[rerun_from]
    extra = {"comments": comments} if rerun_from == "script" else {}
    _go(job_id, target, **extra)


def publish(job_id: str):
    """Step 11 (first version): saves final files + caption to the output folder.
    Auto-posting / Google Drive upload plug in here later."""
    j = store.get(job_id)
    sb, jd = j["storyboard"], store.job_dir(job_id)
    dest = config.OUTPUT_DIR / f"{time.strftime('%Y-%m-%d')}_{assemble._slug(sb['title'])}_{job_id[-4:]}"
    dest.mkdir(parents=True, exist_ok=True)
    for rel in sb["outputs"].values():
        shutil.copy(jd / rel, dest)
    shutil.copy(jd / sb["thumbnail"], dest)
    (dest / "caption.txt").write_text(sb["caption"] + "\n\n" + " ".join(sb["hashtags"]), encoding="utf-8")
    (dest / "storyboard.json").write_text(json.dumps(sb, ensure_ascii=False, indent=2), encoding="utf-8")
    state = j["state"] or {}
    state["published_to"] = str(dest)
    store.update(job_id, state=state)
    store.set_status(job_id, "published", str(dest))
