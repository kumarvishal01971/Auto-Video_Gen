"""Steps 4, 6, 7, 8: keyframes, video clips, quality check, voiceover."""
import io
import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageStat

import config
from pipeline import store, styles, whiteboard
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
        st = styles.get(sb.get("style"))
        if st["image"] and st["image"][:40] not in prompt:   # enforce the chosen look
            prompt = f"{prompt}, {st['image']}"
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
    st = styles.get(sb.get("style"))
    brand = config.load_brand()
    quota_out = False
    for s in sb["shots"]:
        s["takes"] = []
        if st["renderer"] == "whiteboard":   # drawn on by a marker, no video AI needed
            store.log(job_id, f"Whiteboard drawing shot {s['id']}…")
            out = d / f"shot_{s['id']:02d}_wb.mp4"
            whiteboard.render(jd / s["keyframe"], max(5.0, s["duration_sec"]), out, brand)
            s["takes"].append(str(out.relative_to(jd)))
            continue
        motion = s["motion_prompt"] + (f", {st['motion']}" if st["motion"] else "")
        s.pop("motion_fallback", None)
        for t in range(1, config.VIDEO_TAKES + 1):
            store.log(job_id, f"Clip shot {s['id']} take {t}…")
            out = d / f"shot_{s['id']:02d}_t{t}.mp4"
            if quota_out:
                video_p.kenburns(jd / s["keyframe"], max(5.0, s["duration_sec"]), out, s["seed"] + t)
                s["motion_fallback"] = True
            else:
                try:
                    video_p.generate(jd / s["keyframe"], motion, max(5.0, s["duration_sec"]),
                                     out, seed=s["seed"] + t)
                except video_p.QuotaExhausted as e:
                    # free allowance gone: finish the video with zoom/pan instead of failing
                    quota_out = True
                    store.log(job_id, f"⚠ {e}. Remaining shots use zoom/pan; redo clips tomorrow for full motion.")
                    video_p.kenburns(jd / s["keyframe"], max(5.0, s["duration_sec"]), out, s["seed"] + t)
                    s["motion_fallback"] = True
            s["takes"].append(str(out.relative_to(jd)))
            if quota_out:
                break
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
def _line_for_tts(s: dict) -> str:
    """Spoken line with its emotion tag (tags are stripped later for voices that can't perform them)."""
    line = s["voiceover"].strip()
    if not line:
        return ""
    if line[-1] not in ".!?…।":
        line += "."
    tag = s.get("delivery", "").strip()
    return f"[{tag}] {line}" if tag and not line.startswith("[") else line


def _silences(path: Path) -> list[tuple[float, float]]:
    r = subprocess.run(["ffmpeg", "-i", str(path), "-af", "silencedetect=noise=-34dB:d=0.16", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return list(zip(starts, ends))


def voice(job_id: str, sb: dict):
    """Speak the whole script in ONE take so intonation flows like a real voice-over,
    then cut it at the natural pauses between lines so each shot gets its own line."""
    jd = store.job_dir(job_id)
    d = jd / "audio"
    d.mkdir(exist_ok=True)
    lang = "en" if sb.get("language") == "en" else "hi"
    voiced = [s for s in sb["shots"] if s["voiceover"].strip()]
    for s in sb["shots"]:
        s["audio"], s["a_start"], s["a_end"], s["voice_sec"] = None, 0.0, 0.0, 0.0
    if voiced:
        full = d / ("voice_full.mp3" if tts.provider() in ("elevenlabs", "edge") else "voice_full.wav")
        text = " … ".join(_line_for_tts(s) for s in voiced)
        store.log(job_id, f"Voice ({tts.provider()}): one take, {len(voiced)} lines")
        tts.speak(text, lang, full)
        total = probe_duration(full)
        # where each line should roughly end, by share of characters
        lens = [max(1, len(tts.strip_tags(s["voiceover"]))) for s in voiced]
        expected, acc = [], 0
        for n in lens[:-1]:
            acc += n
            expected.append(total * acc / sum(lens))
        gaps = [((a + b) / 2, b - a) for a, b in _silences(full) if 0.3 < (a + b) / 2 < total - 0.3]
        cuts, last = [], 0.0
        for k, e in enumerate(expected):
            options = [g for g in gaps if g[0] > last + 0.6 and abs(g[0] - e) < max(1.5, 0.25 * total / len(voiced))]
            # prefer the longest pause near the expected point
            best = max(options, key=lambda g: g[1] - abs(g[0] - e) * 0.15, default=None)
            c = best[0] if best else max(e, last + 0.8)
            cuts.append(c)
            last = c
        bounds = [0.0] + cuts + [total]
        for i, s in enumerate(voiced):
            s["audio"] = str(full.relative_to(jd))
            s["a_start"], s["a_end"] = round(bounds[i], 3), round(bounds[i + 1], 3)
            s["voice_sec"] = round(bounds[i + 1] - bounds[i], 3)
    for i, s in enumerate(sb["shots"]):
        if s["audio"]:
            extra = 0.25 if i == 0 else 0.05          # small lead-in on the first shot
            if i == len(sb["shots"]) - 1:
                extra += 0.4                          # breathing room before the end card
            s["final_sec"] = round(max(2.0, s["voice_sec"] + extra), 2)
        else:
            s["final_sec"] = 2.5
    return sb
