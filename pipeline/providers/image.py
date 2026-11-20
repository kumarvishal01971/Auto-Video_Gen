"""Keyframe images.
placeholder  – offline, brand-coloured card (plumbing test only)
pollinations – free, no key (testing only; check their terms before commercial use)
huggingface  – HF Inference API, free HF token has small monthly credits (FLUX.1-schnell)
fal          – paid, production (FLUX on fal.ai)"""
import io
import time
import urllib.parse

import httpx
from PIL import Image, ImageDraw, ImageFont

import config


def generate(prompt: str, width: int, height: int, seed: int, label: str = "") -> bytes:
    p = config.IMAGE_PROVIDER
    if p == "pollinations":
        return _pollinations(prompt, width, height, seed)
    if p == "huggingface":
        return _huggingface(prompt, width, height, seed)
    if p == "fal":
        return _fal(prompt, width, height, seed)
    return _placeholder(prompt, width, height, seed, label)


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


def _pollinations(prompt, w, h, seed):
    q = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{q}?width={w}&height={h}&seed={seed}&nologo=true&model=flux"
    r = httpx.get(url, timeout=180, follow_redirects=True)
    r.raise_for_status()
    return _to_jpeg(r.content, w, h)


def _huggingface(prompt, w, h, seed):
    url = f"https://router.huggingface.co/hf-inference/models/{config.HF_IMAGE_MODEL}"
    body = {"inputs": prompt, "parameters": {"width": w - w % 16, "height": h - h % 16, "seed": seed}}
    for _ in range(5):
        r = httpx.post(url, json=body, headers={"Authorization": f"Bearer {config.HF_TOKEN}"}, timeout=180)
        if r.status_code == 503:  # model loading
            time.sleep(15)
            continue
        r.raise_for_status()
        return _to_jpeg(r.content, w, h)
    raise RuntimeError("Hugging Face model did not load")


def _fal(prompt, w, h, seed):
    from pipeline.providers.falq import run

    res = run(config.FAL_IMAGE_MODEL, {
        "prompt": prompt, "image_size": {"width": w, "height": h}, "seed": seed, "num_images": 1,
    })
    url = res["images"][0]["url"]
    return _to_jpeg(httpx.get(url, timeout=120).content, w, h)


def _placeholder(prompt, w, h, seed, label):
    brand = config.load_brand()
    c1, c2 = brand["colors"]["primary"], brand["colors"]["secondary"]
    im = Image.new("RGB", (w, h), c1)
    d = ImageDraw.Draw(im)
    for y in range(h):  # simple vertical gradient
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
