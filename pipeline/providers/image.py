"""Keyframe images.
placeholder  – offline, brand-coloured card (plumbing test only)
pollinations – free; API key removes watermark and increases limits
cloudflare   – free daily quota (requires account ID and API token)
huggingface  – routed to available providers (small free monthly credit)
fal          – paid, production (FLUX on fal.ai)"""
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
    chain = [config.IMAGE_PROVIDER] + [p.strip() for p in config.IMAGE_FALLBACK.split(",") if p.strip()]
    errors = []
    for provider in dict.fromkeys(chain):
        handler = _PROVIDERS.get(provider)
        if handler is None:
            errors.append(f"{provider}: unknown provider")
            continue
        try:
            return handler(prompt, width, height, seed, label)
        except Exception as e:
            errors.append(f"{provider}: {_short(e)}")
    raise RuntimeError("All image providers failed — " + " | ".join(errors))


def _short(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code} {error.response.text[:160]}"
    return str(error)[:200]


def _to_jpeg(data: bytes, width: int, height: int) -> bytes:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    # cover-crop to exact size
    r = max(width / im.width, height / im.height)
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
    if key:
        params["nologo"] = "true"
        headers["Authorization"] = f"Bearer {key}"
    url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt[:900])
    for attempt in range(4):
        r = httpx.get(url, params=params, headers=headers, timeout=180, follow_redirects=True)
        free_limit = r.status_code == 402 and "FREE_LIMIT" in r.text
        retryable = r.status_code in (429, 503) or free_limit
        if retryable and attempt < 3:
            time.sleep(_pollinations_retry_delay(r, 47 if r.status_code in (402, 429) else 10))
            continue
        r.raise_for_status()
        return _to_jpeg(r.content, w, h)
    raise RuntimeError("Pollinations rate limit persisted after retries")


def _pollinations_retry_delay(response: httpx.Response, default: int) -> int:
    retry_after = response.headers.get("Retry-After", "")
    if retry_after.isdigit():
        return min(max(int(retry_after), 1), 300)
    match = re.search(r"[Rr]etry after (\d+)", response.text)
    if match:
        return min(int(match.group(1)) + 3, 300)
    return default


def _cloudflare(prompt, w, h, seed, label=""):
    account_id, token = env("CF_ACCOUNT_ID"), env("CF_API_TOKEN")
    if not (account_id and token):
        raise RuntimeError("CF_ACCOUNT_ID / CF_API_TOKEN not set")
    model = env("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
    response = httpx.post(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}",
        headers={"Authorization": f"Bearer {token}"},
        # This Workers AI model rejects a seed field.
        json={"prompt": prompt[:2000], "steps": 8},
        timeout=180,
    )
    response.raise_for_status()
    image = response.json()["result"]["image"]
    return _to_jpeg(base64.b64decode(image), w, h)


def _huggingface(prompt, w, h, seed, label=""):
    from huggingface_hub import InferenceClient

    if not config.HF_TOKEN:
        raise RuntimeError("HF_TOKEN not set")
    client = InferenceClient(provider=env("HF_PROVIDER", "auto"), api_key=config.HF_TOKEN)
    image = client.text_to_image(
        prompt,
        model=config.HF_IMAGE_MODEL,
        width=w - w % 16,
        height=h - h % 16,
        seed=seed,
    )
    out = io.BytesIO()
    image.save(out, "PNG")
    return _to_jpeg(out.getvalue(), w, h)


def _fal(prompt, w, h, seed, label=""):
    from pipeline.providers.falq import run

    res = run(config.FAL_IMAGE_MODEL, {
        "prompt": prompt, "image_size": {"width": w, "height": h}, "seed": seed, "num_images": 1,
    })
    return _to_jpeg(httpx.get(res["images"][0]["url"], timeout=120).content, w, h)


def _placeholder(prompt, w, h, seed, label):
    brand = config.load_brand()
    c1, c2 = brand["colors"]["primary"], brand["colors"]["secondary"]
    im = Image.new("RGB", (w, h), c1)
    d = ImageDraw.Draw(im)
    for y in range(h):  # simple vertical gradient
        t = y / h
        a = tuple(int(int(c1[i:i + 2], 16) * (1 - t) + int(c2[i:i + 2], 16) * t * 0.6) for i in (1, 3, 5))
        d.line([(0, y), (w, y)], fill=a)
    fallback = str(config.resolve_font(brand["fonts"]["fallback"]))
    font = ImageFont.truetype(fallback, max(28, w // 22))
    small = ImageFont.truetype(fallback, max(20, w // 40))
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


_PROVIDERS = {
    "placeholder": _placeholder,
    "pollinations": _pollinations,
    "cloudflare": _cloudflare,
    "huggingface": _huggingface,
    "fal": _fal,
}
