"""Step 9: assembly & branding with FFmpeg.
Fits each clip to the voiceover, adds on-screen text, subtitles, logo, CTA end card and music,
and exports every format in config.FORMATS (9:16 and 16:9)."""
import random
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config
from pipeline import store
from pipeline.media import probe_duration
from pipeline.providers.video import kenburns

ENDCARD_SEC = 3.0
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def run(cmd: list[str]):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {r.stderr[-800:]}")


def _font_path(brand: dict, text: str, kind: str = "heading") -> str:
    p = config.ROOT / brand["fonts"].get(kind, "")
    if p.is_file():
        return str(p)
    if DEVANAGARI.search(text):
        r = subprocess.run(["fc-match", "-f", "%{file}", ":lang=hi:weight=bold"], capture_output=True, text=True)
        if r.stdout.strip():
            return r.stdout.strip()
    return brand["fonts"]["fallback"]


def _ass_color(hex_color: str, alpha: str = "00") -> str:
    h = hex_color.lstrip("#")
    return f"&H{alpha}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _ts(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(t: str) -> str:
    return t.replace("\n", " ").replace("{", "(").replace("}", ")")


def write_ass(sb: dict, w: int, h: int, brand: dict, path: Path):
    font = brand.get("subtitle_font", "Noto Sans Devanagari")
    base = min(w, h)
    sub_size, title_size = int(base * 0.05), int(base * 0.065)
    prim = _ass_color(brand["colors"]["primary"], "20")
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sub,{font},{sub_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,1,2,60,60,{int(h * 0.12)},1",
        f"Style: Title,{font},{title_size},&H00FFFFFF,&H00FFFFFF,{prim},{prim},1,0,0,0,100,100,0,0,3,18,0,8,60,60,{int(h * 0.1)},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    t = 0.0
    for s in sb["shots"]:
        dur = s["final_sec"]
        if s.get("on_screen_text"):
            lines.append(f"Dialogue: 1,{_ts(t + 0.15)},{_ts(t + dur - 0.1)},Title,,0,0,0,,{{\\fad(250,200)}}{_esc(s['on_screen_text'])}")
        words = s["voiceover"].split()
        if words and s.get("voice_sec"):
            chunks = [words[i:i + 5] for i in range(0, len(words), 5)]
            per = s["voice_sec"] / len(chunks)
            for i, c in enumerate(chunks):
                a = t + 0.2 + i * per
                lines.append(f"Dialogue: 0,{_ts(a)},{_ts(a + per)},Sub,,0,0,0,,{_esc(' '.join(c))}")
        t += dur
    path.write_text("\n".join(lines), encoding="utf-8")


def endcard_image(sb: dict, w: int, h: int, brand: dict, path: Path):
    im = Image.new("RGB", (w, h), brand["colors"]["background"])
    d = ImageDraw.Draw(im)
    d.rectangle([0, int(h * 0.78), w, h], fill=brand["colors"]["primary"])
    logo = config.ROOT / brand["logo"]
    y = int(h * 0.22)
    if logo.is_file():
        lg = Image.open(logo).convert("RGBA")
        lw = int(min(w, h) * 0.45)
        lg = lg.resize((lw, int(lg.height * lw / lg.width)))
        im.paste(lg, ((w - lw) // 2, y), lg)
        y += lg.height + int(h * 0.05)
    else:
        f = ImageFont.truetype(_font_path(brand, brand["brand_name"]), int(min(w, h) * 0.08))
        d.text((w / 2, y), brand["brand_name"], font=f, fill=brand["colors"]["secondary"], anchor="mt")
        y += int(h * 0.12)
    cta, sub = sb["cta"].get("text", ""), sb["cta"].get("subtext", brand["website"])
    f1 = ImageFont.truetype(_font_path(brand, cta), int(min(w, h) * 0.075))
    f2 = ImageFont.truetype(_font_path(brand, sub, "body"), int(min(w, h) * 0.04))
    d.text((w / 2, y), cta, font=f1, fill=brand["colors"]["text"], anchor="mt")
    d.text((w / 2, int(h * 0.89)), sub, font=f2, fill="white", anchor="mm")
    im.save(path, quality=95)


def fit_clip(src: Path, w: int, h: int, dur: float, out: Path):
    sw, sh = Image.open(src).size if src.suffix == ".jpg" else _dims(src)
    same = abs((sw / sh) - (w / h)) < 0.1
    tail = (f"setsar=1,fps={config.FPS},tpad=stop_mode=clone:stop_duration={dur},"
            f"trim=duration={dur},setpts=PTS-STARTPTS,format=yuv420p")
    if same:
        vf = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},{tail}[v]"
    else:  # e.g. vertical clip in a 16:9 frame: blurred background fill
        vf = (f"[0:v]split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
              f"boxblur=40:5,eq=brightness=-0.12[bg];[b]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{tail}[v]")
    run(["ffmpeg", "-y", "-i", str(src), "-filter_complex", vf, "-map", "[v]", "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-r", str(config.FPS), str(out)])


def _dims(p: Path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    a, b = r.stdout.strip().split(",")[:2]
    return int(a), int(b)


def build_audio(job_id: str, sb: dict, brand: dict, total: float, out: Path):
    jd = store.job_dir(job_id)
    inputs, parts = [], []
    for i, s in enumerate(sb["shots"]):
        if s.get("audio"):
            inputs += ["-i", str(jd / s["audio"])]
        else:
            inputs += ["-f", "lavfi", "-t", "1", "-i", "anullsrc=r=44100:cl=stereo"]
        parts.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo,"
                     f"adelay=200|200,apad,atrim=duration={s['final_sec']}[a{i}]")
    n = len(sb["shots"])
    fc = ";".join(parts) + ";" + "".join(f"[a{i}]" for i in range(n)) + \
        f"concat=n={n}:v=0:a=1,apad,atrim=duration={total}[voice]"
    music_dir = config.ROOT / brand.get("music_dir", "brand/music")
    tracks = sorted(music_dir.glob("*.mp3")) + sorted(music_dir.glob("*.wav")) if music_dir.is_dir() else []
    if tracks:
        inputs += ["-stream_loop", "-1", "-i", str(random.choice(tracks))]
        vol = brand.get("music_volume", 0.12)
        fc += (f";[{n}:a]aresample=44100,aformat=channel_layouts=stereo,volume={vol},atrim=duration={total},"
               f"afade=t=out:st={max(0, total - 2)}:d=2[m];[voice][m]amix=inputs=2:normalize=0[out]")
    else:
        fc += ";[voice]anull[out]"
    run(["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[out]", "-c:a", "aac", "-b:a", "192k", str(out)])


def assemble(job_id: str, sb: dict) -> dict:
    brand = config.load_brand()
    jd = store.job_dir(job_id)
    work = jd / "work"
    final = jd / "final"
    work.mkdir(exist_ok=True)
    final.mkdir(exist_ok=True)
    total = sum(s["final_sec"] for s in sb["shots"]) + ENDCARD_SEC
    audio = work / "audio.m4a"
    build_audio(job_id, sb, brand, total, audio)
    outputs = {}
    for name, (w, h) in config.FORMATS.items():
        store.log(job_id, f"Assembling {name}…")
        parts = []
        for s in sb["shots"]:
            src = jd / (s.get("clip") or s["keyframe"])
            p = work / f"{name}_shot_{s['id']:02d}.mp4"
            if src.suffix == ".jpg":  # no usable clip: animate the keyframe instead
                tmp = work / f"kb_{s['id']:02d}.mp4"
                kenburns(src, s["final_sec"], tmp, s["id"])
                src = tmp
            fit_clip(src, w, h, s["final_sec"], p)
            parts.append(p)
        card_img = work / f"{name}_endcard.jpg"
        endcard_image(sb, w, h, brand, card_img)
        card_vid = work / f"{name}_endcard_kb.mp4"
        kenburns(card_img, ENDCARD_SEC, card_vid, 0)
        card_fit = work / f"{name}_endcard.mp4"
        fit_clip(card_vid, w, h, ENDCARD_SEC, card_fit)
        parts.append(card_fit)

        lst = work / f"{name}_list.txt"
        lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
        silent = work / f"{name}_silent.mp4"
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(silent)])

        ass = work / f"{name}.ass"
        write_ass(sb, w, h, brand, ass)
        fontsdir = config.ROOT / "brand" / "fonts"
        sub = f"subtitles='{ass.resolve()}'" + (f":fontsdir='{fontsdir.resolve()}'" if fontsdir.is_dir() else "")
        logo = config.ROOT / brand["logo"]
        out = final / f"{_slug(sb['title'])}_{name}.mp4"
        if logo.is_file():
            lw = int(min(w, h) * 0.16)
            fc = (f"[0:v]{sub}[v1];[2:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa=0.9[lg];"
                  f"[v1][lg]overlay=W-w-{int(w * 0.04)}:{int(h * 0.03)}:enable='lt(t,{total - ENDCARD_SEC})'[v]")
            ins = ["-i", str(silent), "-i", str(audio), "-i", str(logo)]
        else:
            fc, ins = f"[0:v]{sub}[v]", ["-i", str(silent), "-i", str(audio)]
        run(["ffmpeg", "-y", *ins, "-filter_complex", fc, "-map", "[v]", "-map", "1:a",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)])
        outputs[name] = str(out.relative_to(jd))
    thumb = final / "thumbnail.jpg"
    run(["ffmpeg", "-y", "-ss", "1.5", "-i", str(jd / outputs["9x16"]), "-frames:v", "1", str(thumb)])
    sb["outputs"] = outputs
    sb["thumbnail"] = str(thumb.relative_to(jd))
    sb["duration_total"] = round(probe_duration(jd / outputs["9x16"]), 1)
    return sb


def _slug(t: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")[:40]
    return s or "promo"
