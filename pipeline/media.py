"""Steps 4, 6, 7, 8: keyframes, video clips, quality check, voiceover."""
import io
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageStat

import config
from pipeline import store
from pipeline.providers import image as image_p
from pipeline.providers import llm
from pipeline.providers import tts
from pipeline.providers import video as video_p

KEY_W, KEY_H = 720, 1280  # 9:16 keyframes; 16:9 is made from them in assembly


def probe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return float(json.loads(out.stdout)["format"]["duration"])


# ---------- Step 4: keyframes ----------
def keyframes(job_id: str, sb: dict, only: list[int] | None = None, notes: dict | None = None):
    d = store.job_dir(job_id) / "keyframes"
    d.mkdir(exist_ok=True)
    notes = notes or {}
    for s in sb["shots"]:
        if only and s["id"] not in only:
            continue
        prompt = s["image_prompt"]
        if notes.get(str(s["id"])):
            prompt += f". Changes requested: {notes[str(s['id'])]}"
        seed = s.get("seed", 1000 + s["id"]) + (7 if only else 0)
        s["seed"] = seed
        store.log(job_id, f"Keyframe shot {s['id']}…")
        img = image_p.generate(prompt, KEY_W, KEY_H, seed, label=f"SHOT {s['id']}")
        path = d / f"shot_{s['id']:02d}_{seed}.jpg"
        path.write_bytes(img)
        s["keyframe"] = str(path.relative_to(store.job_dir(job_id)))
    return sb


# ---------- Step 6: clips ----------
def clips(job_id: str, sb: dict):
    d = store.job_dir(job_id) / "clips"
    d.mkdir(exist_ok=True)
    jd = store.job_dir(job_id)
    for s in sb["shots"]:
        s["takes"] = []
        for t in range(1, config.VIDEO_TAKES + 1):
            store.log(job_id, f"Clip shot {s['id']} take {t}…")
            out = d / f"shot_{s['id']:02d}_t{t}.mp4"
            video_p.generate(jd / s["keyframe"], s["motion_prompt"], max(5.0, s["duration_sec"]),
                             out, seed=s["seed"] + t)
            s["takes"].append(str(out.relative_to(jd)))
    return sb


# ---------- Step 7: quality check ----------
def _frame(path: Path, at: float) -> bytes:
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i", str(path), "-frames:v", "1",
                        "-f", "image2", "-c:v", "mjpeg", "-"], capture_output=True, check=True)
    return r.stdout


def _basic_ok(path: Path) -> tuple[bool, str]:
    try:
        dur = probe_duration(path)
    except Exception:
        return False, "unreadable file"
    if dur < 1.0:
        return False, "too short"
    img = Image.open(io.BytesIO(_frame(path, dur / 2))).convert("L")
    if ImageStat.Stat(img).mean[0] < 8:
        return False, "black frame"
    return True, ""


def _vision_score(path: Path) -> tuple[float, str]:
    dur = probe_duration(path)
    frames = [_frame(path, dur * f) for f in (0.2, 0.8)]
    res = llm.chat_json(
        "You are a strict video QA reviewer for brand ads. Return JSON only.",
        'Rate these 2 frames from one AI-generated clip. Look for warped faces/hands, melted objects, '
        'garbled text, flicker artefacts. JSON: {"score": 0-10, "issues": "short text"}',
        images=frames,
    )
    return float(res.get("score", 0)), str(res.get("issues", ""))


def quality_check(job_id: str, sb: dict) -> tuple[dict, list[int]]:
    """Picks the best take per shot. Returns shots that failed every take."""
    jd = store.job_dir(job_id)
    failed = []
    for s in sb["shots"]:
        best, best_score, why = None, -1.0, ""
        for take in s.get("takes", []):
            ok, reason = _basic_ok(jd / take)
            if not ok:
                why = reason
                continue
            score = 7.0
            if config.QC_VISION and config.LLM_PROVIDER != "mock":
                try:
                    score, why = _vision_score(jd / take)
                except Exception as e:  # QC must never block the pipeline
                    store.log(job_id, f"Vision QC skipped: {e}")
            if score > best_score:
                best, best_score = take, score
        s["clip"] = best if best_score >= 5 else None
        s["qc"] = {"score": best_score, "issues": why}
        if not s["clip"]:
            failed.append(s["id"])
    return sb, failed


# ---------- Step 8: voice ----------
def voice(job_id: str, sb: dict):
    d = store.job_dir(job_id) / "audio"
    d.mkdir(exist_ok=True)
    lang = sb.get("language", "hinglish")
    for s in sb["shots"]:
        out = d / f"shot_{s['id']:02d}.mp3"
        if s["voiceover"].strip():
            tts.speak(s["voiceover"], "en" if lang == "en" else "hi", out)
            s["voice_sec"] = probe_duration(out)
            s["audio"] = str(out.relative_to(store.job_dir(job_id)))
        else:
            s["voice_sec"], s["audio"] = 0.0, None
        # final on-screen length: fits the voice, never shorter than 2.5 s
        s["final_sec"] = round(max(2.5, s["voice_sec"] + 0.5), 2)
    return sb
