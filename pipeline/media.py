"""Steps 4, 6, 7, 8: keyframes, video clips, quality check, voiceover."""
import difflib
import hashlib
import io
import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageStat

import config
from pipeline import budget, store, styles, whiteboard
from pipeline.providers import image as image_p
from pipeline.providers import llm
from pipeline.providers import tts
from pipeline.providers import video as video_p

KEY_W, KEY_H = 720, 1280  # 9:16 keyframes; 16:9 is made from them in assembly


def probe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return float(json.loads(out.stdout)["format"]["duration"])


# ---------- cast: one reference image per character ----------
def _char(sb: dict, cid: str) -> dict | None:
    return next((c for c in sb.get("characters") or [] if c.get("id") == cid), None)


def cast(job_id: str, sb: dict) -> dict:
    """Brand mascots use their sheet; other characters get a reference portrait made once,
    so every shot shows the same face/outfit."""
    jd = store.job_dir(job_id)
    d = jd / "cast"
    d.mkdir(exist_ok=True)
    st = styles.get(sb.get("style"))
    sheets = [config.ROOT / c["sheet"] for c in sb.get("characters", []) if c.get("sheet")]
    for c in sb.get("characters") or []:
        if c.get("sheet") and (config.ROOT / c["sheet"]).is_file():
            c["ref"] = str(config.ROOT / c["sheet"])
            continue
        if c.get("ref") and Path(c["ref"]).is_file():
            continue
        look = st["image"] or config.load_brand()["visual_style"]
        prompt = (f"Character reference sheet of ONE person: {c.get('name')}, {c.get('look')}. Full body front view and "
                  f"a face close-up side by side, plain light background. {look}. No text.")
        if sheets:  # new character drawn in the SAME art style as the brand mascot
            prompt = "Create a NEW, different character in exactly the same art style as the reference image. " + prompt
        _pay(job_id, config.env("REF_IMAGE_MODEL", "fal-ai/nano-banana/edit") if config.FAL_KEY else "", 1)
        img, used = image_p.generate_ref(prompt, sheets[:1], KEY_W, KEY_H, 4242, label=c.get("name", ""))
        if used:
            budget.record(job_id, "cast portrait", budget.estimate(config.env("REF_IMAGE_MODEL", "fal-ai/nano-banana/edit")))
        path = d / f"{c.get('id', 'char')}.jpg"
        path.write_bytes(img)
        c["ref"] = str(path)
        store.log(job_id, f"Cast: reference made for {c.get('name')}")
    return sb


def _pay(job_id: str, model: str, n: int = 1):
    """Budget check before a paid image call."""
    if model:
        budget.check(budget.estimate(model) * n, model)


# ---------- Step 4: keyframes ----------
def keyframes(job_id: str, sb: dict, only: list[int] | None = None, notes: dict | None = None):
    d = store.job_dir(job_id) / "keyframes"
    d.mkdir(exist_ok=True)
    notes = notes or {}
    st = styles.get(sb.get("style"))
    talking = st["renderer"] == "talking"
    if talking:
        cast(job_id, sb)
    ref_model = config.env("REF_IMAGE_MODEL", "fal-ai/nano-banana/edit")
    for s in sb["shots"]:
        if only and s["id"] not in only:
            continue
        prompt = s["image_prompt"]
        if st["image"] and st["image"][:40] not in prompt:   # enforce the chosen look
            prompt = f"{prompt}, {st['image']}"
        if notes.get(str(s["id"])):
            prompt += f". Changes requested: {notes[str(s['id'])]}"
        seed = s.get("seed", 1000 + s["id"]) + (7 if only else 0)
        s["seed"] = seed
        store.log(job_id, f"Keyframe shot {s['id']}…")
        c = _char(sb, s.get("speaker", "")) if talking else None
        if c and c.get("ref"):
            prompt = (f"Use the EXACT character from the reference image ({c.get('name')}: {c.get('look')}) — same face, "
                      f"hair, glasses, outfit and art style. Scene: {prompt}. The character faces the camera, mouth visible. "
                      f"Vertical 9:16 frame, no text, no speech bubbles with words.")
            if config.FAL_KEY:
                _pay(job_id, ref_model)
            img, used = image_p.generate_ref(prompt, [c["ref"]], KEY_W, KEY_H, seed, label=f"SHOT {s['id']}")
            if used:
                budget.record(job_id, f"keyframe {s['id']}", budget.estimate(ref_model))
            else:
                store.log(job_id, f"⚠ Shot {s['id']}: reference model unavailable — normal image used, character may differ")
        else:
            if config.IMAGE_PROVIDER == "fal":
                _pay(job_id, config.FAL_IMAGE_MODEL)
            img = image_p.generate(prompt, KEY_W, KEY_H, seed, label=f"SHOT {s['id']}")
            if config.IMAGE_PROVIDER == "fal":
                budget.record(job_id, f"keyframe {s['id']}", budget.estimate(config.FAL_IMAGE_MODEL))
        path = d / f"shot_{s['id']:02d}_{seed}.jpg"
        path.write_bytes(img)
        s["keyframe"] = str(path.relative_to(store.job_dir(job_id)))
    return sb


# ---------- Step 6: clips ----------
def _sig(s: dict) -> str:
    """Fingerprint of a shot's inputs: if unchanged, an existing clip is reused (resume without paying twice)."""
    raw = f"{s.get('keyframe')}|{s.get('audio')}|{s.get('a_start')}|{s.get('a_end')}|{s.get('final_sec')}|{s.get('seed')}|{s.get('motion_prompt')}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def shot_audio(job_id: str, s: dict, first: bool) -> Path:
    """The exact audio this shot will have in the final video (same lead-in + padding as assembly),
    so the lip-sync lines up perfectly."""
    jd = store.job_dir(job_id)
    out = jd / "audio" / f"shot_{s['id']:02d}.wav"
    dur = s.get("final_sec") or s.get("duration_sec") or 3
    if s.get("audio") and s.get("a_end"):
        delay = "adelay=200|200," if first else ""
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{s['a_start']:.3f}", "-to", f"{s['a_end']:.3f}",
               "-i", str(jd / s["audio"]), "-af",
               f"aresample=44100,aformat=channel_layouts=stereo,{delay}apad,atrim=duration={dur}", str(out)]
    else:
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-t", f"{dur}", "-i",
               "anullsrc=r=44100:cl=stereo", str(out)]
    subprocess.run(cmd, check=True)
    return out


def _talk_prompt(sb: dict, s: dict, st: dict) -> str:
    c = _char(sb, s.get("speaker", ""))
    if c:
        who = f"{c.get('name')} ({c.get('look')}) speaks the line to the camera with clear, accurate lip-sync"
    else:
        who = "the voice is an off-screen narrator; nobody on screen speaks, mouths stay closed"
    tone = f" Emotion: {s['delivery']}." if s.get("delivery") else ""
    return f"{who}. Acting: {s.get('motion_prompt', '')}.{tone} {st['motion']}. Keep the exact character design, no text."


def clips(job_id: str, sb: dict, force: bool = False):
    d = store.job_dir(job_id) / "clips"
    d.mkdir(exist_ok=True)
    jd = store.job_dir(job_id)
    st = styles.get(sb.get("style"))
    brand = config.load_brand()
    quota_out = False
    if st["renderer"] == "talking":
        return _talking_clips(job_id, sb, force)
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


def _talking_clips(job_id: str, sb: dict, force: bool):
    jd = store.job_dir(job_id)
    st = styles.get(sb.get("style"))
    first_id = sb["shots"][0]["id"] if sb["shots"] else None
    for s in sb["shots"]:
        sig = _sig(s)
        old = s.get("takes") or []
        if not force and s.get("clip_sig") == sig and old and (jd / old[-1]).is_file():
            store.log(job_id, f"Shot {s['id']}: clip already made — reused")
            continue
        audio = shot_audio(job_id, s, first=s["id"] == first_id)
        out = jd / "clips" / f"shot_{s['id']:02d}_s{s['seed']}.mp4"
        store.log(job_id, f"Animating shot {s['id']} (talking, {s.get('final_sec')}s)…")
        how = video_p.talking(jd / s["keyframe"], audio, _talk_prompt(sb, s, st), out, s["seed"],
                              float(s.get("final_sec") or 3),
                              budget_check=budget.check,
                              on_cost=lambda m, usd, sid=s["id"]: budget.record(job_id, f"clip {sid} {m}", usd),
                              log=lambda m: store.log(job_id, m))
        s["made_with"] = how
        s["lipsync"] = how not in ("motion", "zoompan")
        s["motion_fallback"] = how == "zoompan"
        if not s["lipsync"]:
            store.log(job_id, f"⚠ Shot {s['id']}: lip-sync models failed — used {how} so the video still completes")
        s["takes"] = [str(out.relative_to(jd))]
        s["clip_sig"] = sig
        store.update(job_id, storyboard=sb)   # checkpoint after every shot
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


def _ref_jpeg(path: str) -> bytes:
    im = Image.open(path).convert("RGB")
    im.thumbnail((1024, 1024))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85)
    return out.getvalue()


def _vision_score(path: Path, ref: str | None = None) -> tuple[float, str]:
    dur = probe_duration(path)
    frames = [_frame(path, dur * f) for f in (0.2, 0.8)]
    ask = ('Rate these 2 frames from one AI-generated clip. Look for warped faces/hands, melted objects, '
           'garbled text, flicker artefacts.')
    if ref:
        frames = [_ref_jpeg(ref)] + frames
        ask = ('Image 1 is the CHARACTER REFERENCE. Images 2-3 are frames from one AI-generated clip. Check the '
               'character in the frames matches the reference (face, hair, glasses, outfit, art style) and look for '
               'warped faces/hands, extra limbs, melted objects, garbled text. Score 0 if it is a different character.')
    res = llm.chat_json("You are a strict video QA reviewer for brand ads. Return JSON only.",
                        ask + ' JSON: {"score": 0-10, "issues": "short text"}', images=frames)
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
                    c = _char(sb, s.get("speaker", ""))
                    score, why = _vision_score(jd / take, c.get("ref") if c else None)
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


def _voice_id(sb: dict, speaker: str) -> str:
    c = _char(sb, speaker) or {}
    if c.get("voice_id"):
        return c["voice_id"]
    g = str(c.get("voice", "")).lower()
    if "female" in g or "woman" in g:
        return config.env("ELEVENLABS_VOICE_FEMALE")
    if "male" in g or "man" in g:
        return config.env("ELEVENLABS_VOICE_MALE")
    return ""


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", tts.strip_tags(t).lower()).split())


def speech_check(job_id: str, sb: dict, path: Path, lines: list[str]) -> tuple[float, str]:
    """Speech QC: transcribe the recorded voice and check every scripted word was actually said.
    Returns (score 0-10, issues). Skipped (10) when no FAL_KEY / QC_SPEECH=false."""
    if config.env("QC_SPEECH", "true").lower() != "true" or not config.FAL_KEY:
        return 10.0, "not checked"
    from pipeline.providers.falq import run, upload

    try:
        res = run("fal-ai/whisper", {"audio_url": upload(path), "task": "transcribe",
                                     "language": "en" if sb.get("language") == "en" else "hi",
                                     "chunk_level": "none"}, timeout_s=300)
        budget.record(job_id, "speech check", budget.estimate("fal-ai/whisper"))
        heard = res.get("text", "")
    except Exception as e:
        store.log(job_id, f"Speech QC skipped: {str(e)[:120]}")
        return 10.0, "not checked"
    script_txt = " ".join(tts.strip_tags(l) for l in lines)
    if config.LLM_PROVIDER != "mock":
        try:
            r = llm.chat_json("You check voice-over recordings against the script. Return JSON only.",
                              f"SCRIPT:\n{script_txt}\n\nTRANSCRIPT OF THE RECORDING:\n{heard}\n\n"
                              "Ignore Devanagari vs Roman spelling, punctuation and small filler words. Are any "
                              "scripted words missing, changed, or extra words added, or is it gibberish? "
                              'JSON: {"score": 0-10, "issues": "short text"}')
            return float(r.get("score", 0)), str(r.get("issues", ""))
        except Exception as e:
            store.log(job_id, f"Speech QC (AI) skipped: {str(e)[:120]}")
    ratio = difflib.SequenceMatcher(None, _norm(script_txt), _norm(heard)).ratio()
    return round(10 * ratio, 1), f"text match {ratio:.0%}"


def _record(job_id, sb, voiced, lang, d) -> tuple[Path, list[tuple[float, float]], str]:
    """Record all lines. One speaker → one natural take split at pauses.
    Several speakers → each line in its own character's voice, joined with short gaps."""
    speakers = {s.get("speaker", "narrator") for s in voiced}
    if len(speakers) <= 1:
        vid = _voice_id(sb, next(iter(speakers))) if speakers else ""
        full = d / ("voice_full.mp3" if tts.provider() in ("elevenlabs", "edge") else "voice_full.wav")
        text = " … ".join(_line_for_tts(s) for s in voiced)
        used = tts.speak(text, lang, full, vid)
        return full, _split(full, voiced), used
    parts, bounds, t, used = [], [], 0.0, ""
    for s in voiced:
        p = d / f"line_{s['id']:02d}.wav"
        raw = d / f"line_{s['id']:02d}_raw{'.mp3' if tts.provider() in ('elevenlabs', 'edge') else '.wav'}"
        used = tts.speak(_line_for_tts(s), lang, raw, _voice_id(sb, s.get("speaker", "")))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af",
                        "aresample=44100,aformat=channel_layouts=mono,silenceremove=start_periods=1:start_threshold=-45dB,"
                        "areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse,apad=pad_dur=0.28",
                        str(p)], check=True)
        dur = probe_duration(p)
        raw_dur = probe_duration(raw)
        if dur < 0.4 * raw_dur:   # trimming ate the speech (very quiet voice): keep the untrimmed line
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af",
                            "aresample=44100,aformat=channel_layouts=mono,apad=pad_dur=0.28", str(p)], check=True)
            dur = probe_duration(p)
        bounds.append((t, t + dur))
        t += dur
        parts.append(p)
    full = d / "voice_full.wav"
    lst = d / "lines.txt"
    lst.write_text("".join(f"file '{x.resolve()}'\n" for x in parts))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c:a", "pcm_s16le", str(full)], check=True)
    return full, bounds, used


def _split(full: Path, voiced: list) -> list[tuple[float, float]]:
    total = probe_duration(full)
    # where each line should roughly end, by share of characters
    lens = [max(1, len(tts.strip_tags(s["voiceover"]))) for s in voiced]
    expected, acc = [], 0
    for n in lens[:-1]:
        acc += n
        expected.append(total * acc / sum(lens))
    gaps = [((a + b) / 2, b - a) for a, b in _silences(full) if 0.3 < (a + b) / 2 < total - 0.3]
    cuts, last = [], 0.0
    for e in expected:
        options = [g for g in gaps if g[0] > last + 0.6 and abs(g[0] - e) < max(1.5, 0.25 * total / len(voiced))]
        # prefer the longest pause near the expected point
        best = max(options, key=lambda g: g[1] - abs(g[0] - e) * 0.15, default=None)
        c = best[0] if best else max(e, last + 0.8)
        cuts.append(c)
        last = c
    b = [0.0] + cuts + [total]
    return [(b[i], b[i + 1]) for i in range(len(voiced))]


def voice(job_id: str, sb: dict):
    """Record the voice (with speech QC + automatic re-takes), then give each shot its slice."""
    jd = store.job_dir(job_id)
    d = jd / "audio"
    d.mkdir(exist_ok=True)
    lang = "en" if sb.get("language") == "en" else "hi"
    voiced = [s for s in sb["shots"] if s["voiceover"].strip()]
    for s in sb["shots"]:
        s["audio"], s["a_start"], s["a_end"], s["voice_sec"] = None, 0.0, 0.0, 0.0
    if voiced:
        best = None
        tries = 1 + int(config.env("VOICE_RETAKES", "2"))
        for attempt in range(tries):
            store.log(job_id, f"Voice ({tts.provider()}): take {attempt + 1}, {len(voiced)} lines")
            full, bounds, used = _record(job_id, sb, voiced, lang, d)
            score, issues = speech_check(job_id, sb, full, [s["voiceover"] for s in voiced])
            store.log(job_id, f"Speech QC take {attempt + 1}: {score}/10 {issues}")
            keep = d / f"take_{attempt + 1}{full.suffix}"
            full.replace(keep)
            if not best or score > best[0]:
                best = (score, keep, bounds, used, issues)
            if score >= 7:
                break
        score, keep, bounds, used, issues = best
        sb["voice_qc"] = {"score": score, "issues": issues, "provider": used}
        if used != tts.provider():
            store.log(job_id, f"⚠ Main voice failed — used fallback voice '{used}'. Re-run Voice when it is back.")
        if score < 7:
            store.log(job_id, f"⚠ Voice QC {score}/10 after {tries} takes: {issues} — listen before approving")
        for s, (a, b) in zip(voiced, bounds):
            s["audio"] = str(keep.relative_to(jd))
            s["a_start"], s["a_end"] = round(a, 3), round(b, 3)
            s["voice_sec"] = round(b - a, 3)
        sb["voice_file"] = str(keep.relative_to(jd))
    for i, s in enumerate(sb["shots"]):
        if s["audio"]:
            extra = 0.25 if i == 0 else 0.05          # small lead-in on the first shot
            if i == len(sb["shots"]) - 1:
                extra += 0.4                          # breathing room before the end card
            s["final_sec"] = round(max(2.0, s["voice_sec"] + extra), 2)
        else:
            s["final_sec"] = 2.5
    return sb
