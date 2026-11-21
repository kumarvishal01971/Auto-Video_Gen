"""Video Pilot — dashboard + API.  Run:  uvicorn app:app --host 0.0.0.0 --port 8000
Pages: dashboard, new video (step 1), approvals (steps 3, 5, 10), production, library, all videos,
campaigns, activity history, usage, brand kit, settings."""
import json
import secrets
import time
from collections import defaultdict
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
from pipeline import orchestrator, store, styles

APP_NAME = "Video Pilot"
app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory=str(config.ROOT / "static")), name="static")
app.mount("/brand-assets", StaticFiles(directory=str(config.BRAND_DIR)), name="brand")
templates = Jinja2Templates(directory=str(config.ROOT / "templates"))
security = HTTPBasic(auto_error=False)

GATES = {"await_script": "Script & storyboard", "await_keyframes": "Keyframes", "await_final": "Final video"}
DONE = {"published", "failed"}
UNCAT = "Uncategorised"


def auth(creds: HTTPBasicCredentials | None = Depends(security)):
    """Optional: set DASHBOARD_PASSWORD in .env to require a password (any username)."""
    if not config.DASHBOARD_PASSWORD:
        return "team"
    if not creds or not secrets.compare_digest(creds.password, config.DASHBOARD_PASSWORD):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    return creds.username or "team"


@app.on_event("startup")
def _start():
    orchestrator.start_worker()


# ---------- helpers ----------
def _providers():
    return {"Script AI": f"{config.LLM_MODEL}" if config.LLM_PROVIDER != "mock" else "mock",
            "Images": config.IMAGE_PROVIDER, "Video": config.VIDEO_PROVIDER, "Voice": config.TTS_PROVIDER}


def _campaign(j) -> str:
    return (j["brief"].get("campaign") or "").strip() or UNCAT


def _created(j) -> str:
    i = j["id"]  # YYYYMMDD-HHMMSS-xxxx
    return f"{i[6:8]}/{i[4:6]}/{i[0:4]} {i[9:11]}:{i[11:13]}"


def page(request: Request, name: str, active: str, **ctx):
    jobs = ctx.get("_jobs") or store.all_jobs()
    ctx.pop("_jobs", None)
    nav = {
        "trash": len(store.all_jobs(trash=True)),
        "approvals": sum(j["status"] in GATES for j in jobs),
        "production": sum(j["status"] not in GATES and j["status"] not in DONE for j in jobs),
        "failed": sum(j["status"] == "failed" for j in jobs),
    }
    return templates.TemplateResponse(request, name, {
        "app_name": APP_NAME, "active": active, "nav": nav, "providers": _providers(),
        "created": _created, "campaign_of": _campaign, "gates": GATES, "fmt_label": config.FORMAT_LABELS,
        "style_name": lambda j: styles.get(j["brief"].get("style"))["short"], "styles": styles.STYLES, **ctx})


# ---------- pages ----------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user=Depends(auth)):
    jobs = store.all_jobs()
    month = time.strftime("%Y%m")
    published = [j for j in jobs if j["status"] == "published"]
    camps = defaultdict(list)
    for j in jobs:
        camps[_campaign(j)].append(j)
    stats = {
        "waiting": [j for j in jobs if j["status"] in GATES],
        "production": [j for j in jobs if j["status"] not in GATES and j["status"] not in DONE],
        "published_month": [j for j in published if j["id"].startswith(month)],
        "total": len(jobs),
    }
    recent = [j for j in published][:6]
    return page(request, "index.html", "dashboard", _jobs=jobs, jobs=jobs, stats=stats, recent=recent,
                campaigns=sorted(camps.items(), key=lambda kv: kv[1][0]["created"], reverse=True)[:5],
                activity=_activity(jobs)[:8])


@app.get("/new", response_class=HTMLResponse)
def new_form(request: Request, src: str = "", campaign: str = "", user=Depends(auth)):
    prefill = {}
    if src and (j := store.get(src)):
        prefill = dict(j["brief"])
        prefill["topic"] = prefill.get("topic", "") + " (new version)"
    if campaign:
        prefill["campaign"] = campaign
    names = sorted({_campaign(j) for j in store.all_jobs()} - {UNCAT})
    return page(request, "new.html", "new", brand=config.load_brand(), p=prefill, campaign_names=names)


BRIEF_FIELDS = ["topic", "style", "campaign", "objective", "audience", "platforms", "key_message", "cta", "facts",
                "language", "duration", "deadline", "requested_by"]


@app.post("/new")
async def new_submit(request: Request, user=Depends(auth)):
    form = await request.form()
    brief = {k: str(form.get(k, "")).strip() for k in BRIEF_FIELDS}
    brief["duration"] = int(brief["duration"] or 40)
    if not brief["topic"]:
        raise HTTPException(400, "Topic is required")
    job_id = store.create(brief)
    orchestrator.enqueue(job_id)
    return RedirectResponse(f"/job/{job_id}", 303)


@app.get("/approvals", response_class=HTMLResponse)
def approvals(request: Request, user=Depends(auth)):
    jobs = store.all_jobs()
    groups = {g: [j for j in jobs if j["status"] == g] for g in GATES}
    return page(request, "approvals.html", "approvals", _jobs=jobs, groups=groups)


@app.get("/production", response_class=HTMLResponse)
def production(request: Request, user=Depends(auth)):
    jobs = store.all_jobs()
    rows = [j for j in jobs if j["status"] not in GATES and j["status"] != "published"]
    return page(request, "videos.html", "production", _jobs=jobs, rows=rows, title="In production",
                subtitle="Videos the pipeline is working on right now, plus any that failed and need a retry.",
                refresh=True)


@app.get("/videos", response_class=HTMLResponse)
def all_videos(request: Request, q: str = "", status: str = "", campaign: str = "", user=Depends(auth)):
    jobs = store.all_jobs()
    rows = _filter(jobs, q, status, campaign)
    return page(request, "videos.html", "videos", _jobs=jobs, rows=rows, title="All videos", q=q,
                status=status, campaign=campaign, statuses=store.STATUSES, filters=True,
                campaign_names=sorted({_campaign(j) for j in jobs}),
                subtitle="Every brief ever submitted, at any stage. Search by topic, requester or campaign.")


@app.get("/library", response_class=HTMLResponse)
def library(request: Request, q: str = "", campaign: str = "", user=Depends(auth)):
    jobs = store.all_jobs()
    rows = _filter([j for j in jobs if j["status"] == "published"], q, "", campaign)
    return page(request, "library.html", "library", _jobs=jobs, rows=rows, q=q, campaign=campaign,
                campaign_names=sorted({_campaign(j) for j in jobs if j["status"] == "published"}))


@app.get("/campaigns", response_class=HTMLResponse)
def campaigns(request: Request, user=Depends(auth)):
    jobs = store.all_jobs()
    camps = defaultdict(list)
    for j in jobs:
        camps[_campaign(j)].append(j)
    cards = []
    for name, js in camps.items():
        cards.append({"name": name, "total": len(js),
                      "published": sum(j["status"] == "published" for j in js),
                      "waiting": sum(j["status"] in GATES for j in js),
                      "working": sum(j["status"] not in GATES and j["status"] not in DONE for j in js),
                      "last": max(j["updated"] for j in js),
                      "thumb": next((j for j in js if (j["storyboard"] or {}).get("thumbnail")), None)})
    cards.sort(key=lambda c: c["last"], reverse=True)
    return page(request, "campaigns.html", "campaigns", _jobs=jobs, cards=cards)


@app.get("/campaigns/{name}", response_class=HTMLResponse)
def campaign_detail(request: Request, name: str, user=Depends(auth)):
    jobs = store.all_jobs()
    rows = [j for j in jobs if _campaign(j) == name]
    return page(request, "videos.html", "campaigns", _jobs=jobs, rows=rows, title=name,
                subtitle=f"{len(rows)} video(s) in this campaign.", campaign_page=name)


@app.get("/history", response_class=HTMLResponse)
def history(request: Request, q: str = "", user=Depends(auth)):
    jobs = store.all_jobs()
    items = _activity(jobs)
    if q:
        items = [i for i in items if q.lower() in (i["msg"] + i["topic"]).lower()]
    return page(request, "history.html", "history", _jobs=jobs, items=items[:400], q=q)


@app.get("/usage", response_class=HTMLResponse)
def usage(request: Request, user=Depends(auth)):
    jobs = store.all_jobs()
    months = defaultdict(lambda: {"briefs": 0, "published": 0, "images": 0, "clips": 0, "voice_words": 0,
                                  "rework": 0})
    for j in jobs:
        m = months[j["id"][:6]]
        m["briefs"] += 1
        m["published"] += j["status"] == "published"
        jd = store.job_dir(j["id"])
        m["images"] += len(list((jd / "keyframes").glob("*.jpg")))
        m["clips"] += len(list((jd / "clips").glob("*.mp4")))
        m["voice_words"] += sum(len(s.get("voiceover", "").split()) for s in (j["storyboard"] or {}).get("shots", []))
        m["rework"] += sum(any(k in h["msg"] for k in ("rejected", "Redo", "Changes requested"))
                           for h in j["history"] or [])
    clip_rate = float(config.env("COST_PER_CLIP_USD", "0.10" if config.VIDEO_PROVIDER == "fal_wan" else "0"))
    img_rate = float(config.env("COST_PER_IMAGE_USD", "0.003" if config.IMAGE_PROVIDER == "fal" else "0"))
    inr = float(config.env("USD_INR", "88"))
    rows = []
    for k in sorted(months, reverse=True):
        m = months[k]
        m["month"] = time.strftime("%b %Y", time.strptime(k, "%Y%m"))
        m["est_inr"] = round((m["clips"] * clip_rate + m["images"] * img_rate) * inr)
        m["per_video"] = round(m["est_inr"] / m["published"]) if m["published"] else None
        rows.append(m)
    return page(request, "usage.html", "usage", _jobs=jobs, rows=rows, clip_rate=clip_rate, img_rate=img_rate,
                inr=inr)


@app.get("/brand", response_class=HTMLResponse)
def brand_kit(request: Request, user=Depends(auth)):
    b = config.load_brand()
    music = sorted(p.name for p in (config.ROOT / b.get("music_dir", "brand/music")).glob("*") if p.suffix in (".mp3", ".wav"))
    fonts = sorted(p.name for p in (config.BRAND_DIR / "fonts").glob("*.ttf"))
    return page(request, "brand.html", "brand", b=b, music=music, fonts=fonts)


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request, user=Depends(auth)):
    keys = {"LLM_API_KEY": bool(config.LLM_API_KEY), "HF_TOKEN": bool(config.HF_TOKEN),
            "FAL_KEY": bool(config.FAL_KEY), "ELEVENLABS_API_KEY": bool(config.ELEVENLABS_API_KEY),
            "CF_API_TOKEN": bool(config.env("CF_API_TOKEN")), "POLLINATIONS_API_KEY": bool(config.env("POLLINATIONS_API_KEY")),
            "SARVAM_API_KEY": bool(config.env("SARVAM_API_KEY")),
            "DASHBOARD_PASSWORD": bool(config.DASHBOARD_PASSWORD)}
    rows = [("Script AI", config.LLM_PROVIDER, config.LLM_MODEL if config.LLM_PROVIDER != "mock" else "—"),
            ("Images", config.IMAGE_PROVIDER, "fallback: " + (config.env("IMAGE_FALLBACK") or "none")),
            ("Video clips", config.VIDEO_PROVIDER, f"{config.VIDEO_TAKES} take(s) per shot"),
            ("Quality check", "vision AI" if config.QC_VISION else "basic checks", ""),
            ("Voice", config.TTS_PROVIDER, {"sarvam": "speaker: " + config.env("SARVAM_SPEAKER", "ritu"),
                                            "elevenlabs": f"{config.ELEVENLABS_MODEL} · voice {config.ELEVENLABS_VOICE_ID[:8]}…",
                                            "edge": config.EDGE_VOICE_HI}.get(config.TTS_PROVIDER, "")),
            ("Export shapes", ", ".join(config.FORMAT_LABELS.get(k, k) for k in config.FORMATS), "")]
    return page(request, "settings.html", "settings", rows=rows, keys=keys,
                paths={"Jobs": config.JOBS_DIR, "Published videos": config.OUTPUT_DIR, "Brand kit": config.BRAND_DIR},
                formats=config.FORMATS)


@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str, user=Depends(auth)):
    j = store.get(job_id) or _404()
    active = "trash" if (j["state"] or {}).get("deleted") else "approvals" if j["status"] in GATES else "videos"
    return page(request, "job.html", active,
                j=j, sb=j["storyboard"], sb_json=json.dumps(j["storyboard"], ensure_ascii=False, indent=2),
                running=j["status"] not in store.WAITING | DONE)


# ---------- approval actions ----------
@app.post("/job/{job_id}/script")
def script_gate(job_id: str, action: str = Form(...), comments: str = Form(""),
                storyboard_json: str = Form(""), user=Depends(auth)):
    _expect(job_id, "await_script")
    edited = None
    if storyboard_json.strip():
        try:
            edited = json.loads(storyboard_json)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"Storyboard JSON is invalid: {e}")
        if edited == store.get(job_id)["storyboard"]:
            edited = None
    orchestrator.script_decision(job_id, action == "approve", comments, edited, user)
    return RedirectResponse(f"/job/{job_id}", 303)


@app.post("/job/{job_id}/keyframes")
async def keyframe_gate(request: Request, job_id: str, user=Depends(auth)):
    _expect(job_id, "await_keyframes")
    form = await request.form()
    redo = [int(x) for x in form.getlist("redo")]
    notes = {k[5:]: str(v) for k, v in form.items() if k.startswith("note_") and str(v).strip()}
    redo = sorted(set(redo) | {int(k) for k in notes})
    approve = form.get("action") == "approve" and not redo
    orchestrator.keyframe_decision(job_id, approve, redo, notes, user)
    return RedirectResponse(f"/job/{job_id}", 303)


@app.post("/job/{job_id}/final")
def final_gate(job_id: str, action: str = Form(...), rerun_from: str = Form("assembly"),
               comments: str = Form(""), user=Depends(auth)):
    _expect(job_id, "await_final")
    orchestrator.final_decision(job_id, action == "approve", rerun_from, comments, user)
    return RedirectResponse(f"/job/{job_id}", 303)


@app.post("/job/{job_id}/retry")
def retry(job_id: str, step: str = Form("scripting"), user=Depends(auth)):
    store.set_status(job_id, step)
    orchestrator.enqueue(job_id)
    return RedirectResponse(f"/job/{job_id}", 303)


# ---------- delete / trash ----------
@app.post("/job/{job_id}/delete")
def delete_video(job_id: str, back: str = Form("/videos"), user=Depends(auth)):
    store.get(job_id) or _404()
    store.trash(job_id, user)
    store.log(job_id, f"🗑 Moved to Trash by {user}")
    return RedirectResponse(_safe(back), 303)


@app.post("/job/{job_id}/restore")
def restore_video(job_id: str, user=Depends(auth)):
    j = store.get(job_id) or _404()
    store.restore(job_id)
    store.log(job_id, f"↩ Restored from Trash by {user}")
    if store.is_busy(j):  # was mid-pipeline when trashed: carry on
        orchestrator.enqueue(job_id)
    return RedirectResponse("/trash", 303)


@app.post("/job/{job_id}/purge")
def purge_video(job_id: str, user=Depends(auth)):
    j = store.get(job_id) or _404()
    if store.is_busy(j) and not (j["state"] or {}).get("deleted"):
        raise HTTPException(409, "This video is being generated right now. Wait for it to finish, then delete it.")
    store.purge(job_id)
    return RedirectResponse("/trash", 303)


@app.post("/campaigns/{name}/delete")
def delete_campaign(name: str, user=Depends(auth)):
    for j in store.all_jobs():
        if _campaign(j) == name:
            store.trash(j["id"], user)
            store.log(j["id"], f"🗑 Moved to Trash by {user} (campaign '{name}' deleted)")
    return RedirectResponse("/campaigns", 303)


@app.get("/trash", response_class=HTMLResponse)
def trash_page(request: Request, user=Depends(auth)):
    return page(request, "trash.html", "trash", rows=store.all_jobs(trash=True))


@app.post("/trash/empty")
def empty_trash(user=Depends(auth)):
    for j in store.all_jobs(trash=True):
        store.purge(j["id"])
    return RedirectResponse("/trash", 303)


def _safe(url: str) -> str:
    return url if url.startswith("/") and not url.startswith("//") else "/videos"


@app.get("/files/{job_id}/{path:path}")
def files(job_id: str, path: str, user=Depends(auth)):
    base = store.job_dir(job_id).resolve()
    f = (base / path).resolve()
    if not str(f).startswith(str(base)) or not f.is_file():
        _404()
    return FileResponse(f)


# ---------- JSON API (for n8n, Google Forms, WhatsApp bots etc.) ----------
@app.post("/api/briefs")
async def api_brief(request: Request, user=Depends(auth)):
    data = await request.json()
    brief = {k: data.get(k, "") for k in BRIEF_FIELDS}
    brief["duration"] = int(brief["duration"] or 40)
    job_id = store.create(brief)
    orchestrator.enqueue(job_id)
    return {"job_id": job_id, "url": f"/job/{job_id}"}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str, user=Depends(auth)):
    j = store.get(job_id) or _404()
    return {k: j[k] for k in ("id", "status", "status_label", "error", "storyboard", "history")}


# ---------- internals ----------
def _filter(jobs, q="", status="", campaign=""):
    q = q.lower().strip()
    out = []
    for j in jobs:
        b = j["brief"]
        hay = " ".join(str(b.get(k, "")) for k in ("topic", "requested_by", "campaign", "key_message")).lower()
        if q and q not in hay:
            continue
        if status and j["status"] != status:
            continue
        if campaign and _campaign(j) != campaign:
            continue
        out.append(j)
    return out


def _activity(jobs):
    """All jobs' logs merged, newest first. Log times have no year, so sort by job order + position."""
    items = []
    for j in jobs:
        for n, h in enumerate(j["history"] or []):
            items.append({"t": h["t"], "msg": h["msg"].split("\n")[0][:220], "job": j["id"],
                          "topic": j["brief"].get("topic", ""), "key": (j["id"][:15], h["t"], n)})
    items.sort(key=lambda i: (time.strptime(i["t"][:15], "%d %b %H:%M:%S")[1:6], i["key"]), reverse=True)
    return items


def _expect(job_id, status):
    j = store.get(job_id) or _404()
    if (j["state"] or {}).get("deleted"):
        raise HTTPException(409, "This video is in Trash. Restore it first.")
    if j["status"] != status:
        raise HTTPException(409, f"Video is at '{j['status_label']}', not waiting for this approval")


def _404():
    raise HTTPException(404, "Not found")
