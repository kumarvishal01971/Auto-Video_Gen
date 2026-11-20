"""Dashboard + API. Run:  uvicorn app:app --host 0.0.0.0 --port 8000
Pages: brief form (step 1) and the three approval gates (steps 3, 5, 10)."""
import json
import secrets

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

import config
from pipeline import orchestrator, store

app = FastAPI(title="Promo Video Pipeline")
templates = Jinja2Templates(directory=str(config.ROOT / "templates"))
security = HTTPBasic(auto_error=False)


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


def _providers():
    return {"Script AI": f"{config.LLM_PROVIDER} ({config.LLM_MODEL})" if config.LLM_PROVIDER != "mock" else "mock",
            "Images": config.IMAGE_PROVIDER, "Video": config.VIDEO_PROVIDER, "Voice": config.TTS_PROVIDER}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, user=Depends(auth)):
    return templates.TemplateResponse(request, "index.html", {"jobs": store.all_jobs(), "providers": _providers()})


@app.get("/new", response_class=HTMLResponse)
def new_form(request: Request, user=Depends(auth)):
    return templates.TemplateResponse(request, "new.html", {"brand": config.load_brand()})


BRIEF_FIELDS = ["topic", "objective", "audience", "platforms", "key_message", "cta", "facts",
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


@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str, user=Depends(auth)):
    j = store.get(job_id) or _404()
    return templates.TemplateResponse(request, "job.html", {
        "j": j, "sb": j["storyboard"], "sb_json": json.dumps(j["storyboard"], ensure_ascii=False, indent=2),
        "running": j["status"] not in store.WAITING | {"published", "failed"}})


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


@app.get("/files/{job_id}/{path:path}")
def files(job_id: str, path: str, user=Depends(auth)):
    base = store.job_dir(job_id).resolve()
    f = (base / path).resolve()
    if not str(f).startswith(str(base)) or not f.is_file():
        _404()
    return FileResponse(f)


# ---- JSON API (for n8n, Google Forms, WhatsApp bots etc.) ----
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


def _expect(job_id, status):
    j = store.get(job_id) or _404()
    if j["status"] != status:
        raise HTTPException(409, f"Job is at '{j['status_label']}', not waiting for this approval")


def _404():
    raise HTTPException(404, "Not found")
