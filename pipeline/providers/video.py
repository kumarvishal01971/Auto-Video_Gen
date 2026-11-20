"""Image-to-video clips.
kenburns – free, offline: smooth zoom/pan on the keyframe with FFmpeg (also useful for real-photo shots)
fal_wan  – Wan 2.2 image-to-video on fal.ai (needs FAL_KEY). Check fal's model page for params on first run."""
import subprocess
from pathlib import Path

import httpx

import config


def generate(image_path: Path, motion_prompt: str, seconds: float, out_path: Path, seed: int) -> Path:
    if config.VIDEO_PROVIDER == "fal_wan":
        return _fal_wan(image_path, motion_prompt, out_path, seed)
    return kenburns(image_path, seconds, out_path, seed)


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
