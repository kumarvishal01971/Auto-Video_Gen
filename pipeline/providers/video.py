"""Image-to-video clips.
hf_space – FREE real AI motion: Wan 2.2 running on a public Hugging Face ZeroGPU Space.
           Uses your free daily GPU allowance (HF_TOKEN). When it runs out, shots fall back to kenburns.
fal_wan  – paid, reliable Wan 2.2 image-to-video on fal.ai (needs FAL_KEY)
kenburns – free, offline: smooth zoom/pan on the still (not real motion)
talking() – TALKING CHARACTERS: LTX audio-to-video. The character's face, lips and body are animated to the
           real voice clip (lip-sync + emotion). Tries TALK_MODELS in order, then plain motion, then zoom/pan,
           so a shot is ALWAYS produced."""
import shutil
import subprocess
from pathlib import Path

import httpx

import config
from config import env


class QuotaExhausted(RuntimeError):
    """Free AI-video allowance used up for today."""


def generate(image_path: Path, motion_prompt: str, seconds: float, out_path: Path, seed: int) -> Path:
    if config.VIDEO_PROVIDER == "fal_wan":
        return _fal_wan(image_path, motion_prompt, out_path, seed)
    if config.VIDEO_PROVIDER == "hf_space":
        return _hf_space(image_path, motion_prompt, seconds, out_path, seed)
    return kenburns(image_path, seconds, out_path, seed)


def is_ai() -> bool:
    return config.VIDEO_PROVIDER in ("hf_space", "fal_wan")


# ---------- free: Hugging Face ZeroGPU Space ----------
_clients: dict = {}


def _space_call(sid: str, image_path: Path, prompt: str, seconds: float, seed: int):
    from gradio_client import Client, handle_file

    if sid not in _clients:
        _clients[sid] = Client(sid, token=config.HF_TOKEN or None, verbose=False)
    client = _clients[sid]
    eps = client.view_api(return_format="dict", print_info=False)["named_endpoints"]
    # the endpoint that takes an image + a prompt
    name = "/generate_video" if "/generate_video" in eps else next(
        k for k, v in eps.items() if any(p["component"] == "Image" for p in v["parameters"]))
    kwargs, first_image = {}, True
    for p in eps[name]["parameters"]:
        n, comp = p["parameter_name"], p["component"]
        if comp == "Image":
            kwargs[n] = handle_file(str(image_path)) if first_image else None
            first_image = False
        elif n == "prompt":
            kwargs[n] = prompt
        elif n == "duration_seconds":
            kwargs[n] = round(min(5.0, max(2.0, seconds)), 1)
        elif n == "seed":
            kwargs[n] = int(seed) % 2147483647
        elif n == "randomize_seed":
            kwargs[n] = False
        elif not p.get("parameter_has_default"):
            kwargs[n] = None
    res = client.predict(api_name=name, **kwargs)
    first = res[0] if isinstance(res, (list, tuple)) else res
    path = first.get("video") if isinstance(first, dict) else first
    if not path or not Path(path).is_file():
        raise RuntimeError(f"Space {sid} returned no video")
    return path


def _hf_space(image_path, motion_prompt, seconds, out_path, seed):
    spaces = [s.strip() for s in env("HF_VIDEO_SPACES", "zerogpu-aoti/wan2-2-fp8da-aoti-faster,"
                                     "r3gm/wan2-2-fp8da-aoti-preview").split(",") if s.strip()]
    prompt = f"{motion_prompt}. Smooth natural motion, the scene comes alive, cinematic, no text"
    errors = []
    for sid in spaces:
        try:
            shutil.copy(_space_call(sid, image_path, prompt, seconds, seed), out_path)
            return out_path
        except Exception as e:  # try the next Space
            msg = str(e)
            if any(k in msg.lower() for k in ("quota", "exceeded", "gpu task aborted", "you have exceeded")):
                raise QuotaExhausted(f"Free Hugging Face GPU allowance used up ({sid})") from e
            errors.append(f"{sid}: {msg[:160]}")
    raise RuntimeError("Free AI video Spaces failed — " + " | ".join(errors))


def kenburns(image_path: Path, seconds: float, out_path: Path, seed: int = 0) -> Path:
    from PIL import Image

    w, h = Image.open(image_path).size
    w, h = w - w % 2, h - h % 2
    frames = max(1, int(seconds * config.FPS))
    style = seed % 4
    z_in = f"min(1+0.15*on/{frames},1.15)"
    z_out = f"max(1.15-0.15*on/{frames},1.0)"
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if style == 0:
        zp = f"z='{z_in}':x='{cx}':y='{cy}'"
    elif style == 1:
        zp = f"z='{z_out}':x='{cx}':y='{cy}'"
    elif style == 2:  # slow pan right
        zp = f"z='1.12':x='(iw-iw/zoom)*on/{frames}':y='{cy}'"
    else:  # slow pan up
        zp = f"z='1.12':x='{cx}':y='(ih-ih/zoom)*(1-on/{frames})'"
    vf = f"scale={w * 2}:{h * 2},zoompan={zp}:d={frames}:s={w}x{h}:fps={config.FPS},format=yuv420p"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(image_path),
           "-vf", vf, "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "20", str(out_path)]
    subprocess.run(cmd, check=True)
    return out_path


def _fal_wan(image_path, motion_prompt, out_path, seed):
    from pipeline.providers.falq import data_uri, run

    res = run(config.FAL_VIDEO_MODEL, {
        "prompt": motion_prompt,
        "image_url": data_uri(Path(image_path).read_bytes()),
        "resolution": "720p",
        "seed": seed,
    })
    url = (res.get("video") or {}).get("url")
    if not url:
        raise RuntimeError(f"No video in fal response: {str(res)[:300]}")
    out_path.write_bytes(httpx.get(url, timeout=300).content)
    return out_path


# ---------- talking characters (audio-to-video, lip-sync) ----------
TALK_DEFAULT = "fal-ai/ltx-2-19b/distilled/audio-to-video,fal-ai/ltx-2-19b/audio-to-video"


def _talk_payload(model: str, img_url: str, audio_url: str, prompt: str, seed: int) -> dict:
    if model.startswith("lightricks/ltx-2.5"):
        return {"audio_url": audio_url, "image_url": img_url, "prompt": prompt, "aspect_ratio": "9:16"}
    return {"prompt": prompt, "audio_url": audio_url, "image_url": img_url, "match_audio_length": True,
            "video_size": "auto", "fps": 25, "seed": int(seed) % 2147483647, "audio_strength": 1.0,
            "image_strength": 1.0, "enable_prompt_expansion": False, "video_quality": "high"}


def talking(image_path: Path, audio_path: Path, prompt: str, out_path: Path, seed: int, seconds: float,
            budget_check=None, on_cost=None, log=print) -> str:
    """Returns how the shot was made: the model id (lip-synced), 'motion' or 'zoompan' (fallbacks)."""
    from pipeline.providers.falq import FalError, download, run, upload

    models = [m.strip() for m in env("TALK_MODELS", TALK_DEFAULT).split(",") if m.strip()]
    if config.FAL_KEY and models:
        try:
            img_url, audio_url = upload(image_path), upload(audio_path)
        except FalError as e:
            log(f"⚠ upload failed: {e}")
            models = []
        for m in models:
            if m.startswith("lightricks/ltx-2.5") and not 2.0 <= seconds <= 20.0:
                continue
            from pipeline import budget
            est = budget.estimate(m, seconds)
            if budget_check:
                budget_check(est, m)          # raises BudgetExceeded → job pauses, nothing wasted
            try:
                res = run(m, _talk_payload(m, img_url, audio_url, prompt, seed), timeout_s=900)
                url = (res.get("video") or {}).get("url")
                if not url:
                    raise FalError(f"no video in response: {str(res)[:200]}")
                download(url, out_path)
                if on_cost:
                    on_cost(m, est)
                return m
            except FalError as e:
                log(f"⚠ {m.split('/')[-2] if '/' in m else m} failed: {str(e)[:160]}")
                if e.fatal:
                    break
            except Exception as e:  # never let one model stop the video
                log(f"⚠ {m} failed: {str(e)[:160]}")
    # fallback 1: real motion without lip-sync; fallback 2: zoom/pan — the shot is never missing
    if is_ai():
        try:
            generate(image_path, prompt, max(5.0, seconds), out_path, seed)
            return "motion"
        except Exception as e:
            log(f"⚠ motion fallback failed: {str(e)[:160]}")
    kenburns(image_path, max(2.0, seconds), out_path, seed)
    return "zoompan"
