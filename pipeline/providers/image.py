"""Keyframe images.
placeholder  – offline, brand-coloured card (plumbing test only)
pollinations – free; set POLLINATIONS_API_KEY (free at enter.pollinations.ai) for higher limits + no watermark
cloudflare   – free daily quota (Workers AI, FLUX.1-schnell): CF_ACCOUNT_ID + CF_API_TOKEN
huggingface  – HF_TOKEN; routed to whichever provider serves the model (small free monthly credit)
fal          – paid, production (FLUX on fal.ai)
IMAGE_FALLBACK=cloudflare,huggingface → tried in order if the main provider fails."""
import base64
import io
import re
import time
import urllib.parse

import httpx
from PIL import Image, ImageDraw, ImageFont

import config
from config import env


def generate(prompt: str, width: int, height: int, seed: int, label: str = "") -> bytes:
    chain = [config.IMAGE_PROVIDER] + [p.strip() for p in env("IMAGE_FALLBACK").split(",") if p.strip()]
    errors = []
    for p in dict.fromkeys(chain):  # keep order, drop duplicates
        try:
            return _PROVIDERS[p](prompt, width, height, seed, label)
        except KeyError:
            errors.append(f"{p}: unknown provider")
        except Exception as e:
            errors.append(f"{p}: {_short(e)}")
    raise RuntimeError("All image providers failed — " + " | ".join(errors))


def _short(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code} {e.response.text[:160]}"
    return str(e)[:200]


def _to_jpeg(data: bytes, width: int, height: int) -> bytes:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    r = max(width / im.width, height / im.height)  # cover-crop to exact size
    im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
    left, top = (im.width - width) // 2, (im.height - height) // 2
    im = im.crop((left, top, left + width, top + height))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=92)
    return out.getvalue()


def _pollinations(prompt, w, h, seed, label=""):
    key = env("POLLINATIONS_API_KEY")
    params = {"width": w, "height": h, "seed": seed, "model": env("POLLINATIONS_MODEL", "flux")}
    headers = {}
    if key:  # nologo is account-only; sending it anonymously returns 402
        params["nologo"] = "true"
        headers["Authorization"] = f"Bearer {key}"
    url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt[:900])
    for attempt in range(4):
        r = httpx.get(url, params=params, headers=headers, timeout=180, follow_redirects=True)
        # free tier: 1 image per ~45 s per IP → wait the time it asks for, then retry
        if r.status_code in (402, 429) and attempt < 3 and ("FREE_LIMIT" in r.text or r.status_code == 429):
            m = re.search(r"[Rr]etry after (\d+)", r.text)
            time.sleep(int(m.group(1)) + 3 if m else 47)
            continue
        r.raise_for_status()
        return _to_jpeg(r.content, w, h)


def _cloudflare(prompt, w, h, seed, label=""):
    acct, token = env("CF_ACCOUNT_ID"), env("CF_API_TOKEN")
    if not (acct and token):
        raise RuntimeError("CF_ACCOUNT_ID / CF_API_TOKEN not set")
    model = env("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
    r = httpx.post(f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}",
                   headers={"Authorization": f"Bearer {token}"},
                   json={"prompt": prompt[:2000], "steps": 8}, timeout=180)  # this model rejects "seed"
    r.raise_for_status()
    img = r.json()["result"]["image"]
    return _to_jpeg(base64.b64decode(img), w, h)


def _huggingface(prompt, w, h, seed, label=""):
    from huggingface_hub import InferenceClient

    if not config.HF_TOKEN:
        raise RuntimeError("HF_TOKEN not set")
    client = InferenceClient(provider=env("HF_PROVIDER", "auto"), api_key=config.HF_TOKEN)
    im = client.text_to_image(prompt, model=config.HF_IMAGE_MODEL,
                              width=w - w % 16, height=h - h % 16, seed=seed)
    out = io.BytesIO()
    im.save(out, "PNG")
    return _to_jpeg(out.getvalue(), w, h)


def _fal(prompt, w, h, seed, label=""):
    from pipeline.providers.falq import run

    res = run(config.FAL_IMAGE_MODEL, {
        "prompt": prompt, "image_size": {"width": w, "height": h}, "seed": seed, "num_images": 1,
    })
    return _to_jpeg(httpx.get(res["images"][0]["url"], timeout=120).content, w, h)


def _placeholder(prompt, w, h, seed, label=""):
    brand = config.load_brand()
    c1, c2 = brand["colors"]["primary"], brand["colors"]["secondary"]
    im = Image.new("RGB", (w, h), c1)
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        a = tuple(int(int(c1[i:i + 2], 16) * (1 - t) + int(c2[i:i + 2], 16) * t * 0.6) for i in (1, 3, 5))
        d.line([(0, y), (w, y)], fill=a)
    font = ImageFont.truetype(brand["fonts"]["fallback"], max(28, w // 22))
    small = ImageFont.truetype(brand["fonts"]["fallback"], max(20, w // 40))
    d.text((w * 0.08, h * 0.08), label or "SHOT", font=font, fill="white")
    words, line, lines = prompt.split(), "", []
    for wd in words[:60]:
        if len(line) + len(wd) > 34:
            lines.append(line)
            line = ""
        line += wd + " "
    lines.append(line)
    d.multiline_text((w * 0.08, h * 0.2), "\n".join(lines[:12]), font=small, fill=(235, 235, 235), spacing=10)
    d.text((w * 0.08, h * 0.9), "PLACEHOLDER — set IMAGE_PROVIDER", font=small, fill=(255, 255, 255))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()


_PROVIDERS = {"placeholder": _placeholder, "pollinations": _pollinations, "cloudflare": _cloudflare,
              "huggingface": _huggingface, "fal": _fal}


# ---------- character-consistent keyframes ----------
def generate_ref(prompt: str, refs: list, width: int, height: int, seed: int, label: str = "") -> tuple[bytes, bool]:
    """Draw the shot using reference image(s) (character sheet / cast portrait) so the character
    looks the same in every shot. Returns (jpeg, used_reference). Falls back to the normal chain."""
    from pipeline.providers.falq import FalError, run, upload

    model = env("REF_IMAGE_MODEL", "fal-ai/nano-banana/edit")
    if config.FAL_KEY and refs:
        try:
            res = run(model, {
                "prompt": prompt[:3000], "image_urls": [upload(p) for p in refs][:4], "num_images": 1,
                "aspect_ratio": "9:16" if height > width else "16:9", "output_format": "jpeg", "seed": seed,
            }, timeout_s=300)
            return _to_jpeg(httpx.get(res["images"][0]["url"], timeout=120).content, width, height), True
        except FalError as e:
            if e.fatal:
                raise
        except Exception:
            pass
    return generate(prompt, width, height, seed, label), False
