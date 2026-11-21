"""Whiteboard-style clip: the drawing's outlines are 'drawn' by a marker, colours fill in, then it holds.
Optional: put a transparent PNG of a hand holding a marker in brand/hand.png and set "hand_tip": [x, y]
(pixel position of the pen tip inside that PNG) in brand.json. Without it a marker pen is drawn."""
import math
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import config

TILE = 20


def _marker(width: int) -> tuple[Image.Image, tuple[int, int]]:
    """A marker pen pointing down-left. Returns (RGBA image, tip position)."""
    L = int(width * 0.34)
    W = max(14, int(L * 0.15))
    size = int(L * 1.15)
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    tip = (int(size * 0.08), int(size * 0.92))
    u = (math.cos(math.radians(-50)), math.sin(math.radians(-50)))  # towards the back of the pen (up-right)
    v = (-u[1], u[0])

    def pt(a, b):
        return (tip[0] + u[0] * a + v[0] * b, tip[1] + u[1] * a + v[1] * b)

    cone = L * 0.13
    shadow = [(x + 6, y + 8) for x, y in [pt(0, 0), pt(cone, W / 2), pt(L, W / 2), pt(L, -W / 2), pt(cone, -W / 2)]]
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon(shadow, fill=(0, 0, 0, 70))
    im.alpha_composite(sh.filter(ImageFilter.GaussianBlur(5)))
    d.polygon([pt(0, 0), pt(cone * 0.45, W * 0.16), pt(cone * 0.45, -W * 0.16)], fill=(10, 10, 10, 255))  # nib
    d.polygon([pt(cone * 0.45, W * 0.16), pt(cone, W / 2), pt(cone, -W / 2), pt(cone * 0.45, -W * 0.16)],
              fill=(235, 235, 235, 255), outline=(60, 60, 60, 255))
    d.polygon([pt(cone, W / 2), pt(L * 0.86, W / 2), pt(L * 0.86, -W / 2), pt(cone, -W / 2)], fill=(28, 28, 28, 255))
    d.polygon([pt(L * 0.5, W / 2), pt(L * 0.58, W / 2), pt(L * 0.58, -W / 2), pt(L * 0.5, -W / 2)],
              fill=(201, 162, 74, 255))  # brand gold band
    d.polygon([pt(L * 0.86, W * 0.55), pt(L, W * 0.55), pt(L, -W * 0.55), pt(L * 0.86, -W * 0.55)], fill=(12, 12, 12, 255))
    return im, tip


def _hand(brand: dict, width: int):
    p = config.ROOT / brand.get("whiteboard_hand", "brand/hand.png")
    if p.is_file():
        im = Image.open(p).convert("RGBA")
        scale = width * 0.55 / im.width
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
        tx, ty = brand.get("hand_tip", [0, 0])
        return im, (int(tx * scale), int(ty * scale))
    return _marker(width)


def _order(tiles_on: np.ndarray) -> list[tuple[int, int]]:
    """Visit inked tiles like a hand drawing: bands of 3 rows, sweeping left/right alternately."""
    rows, cols = tiles_on.shape
    order = []
    for band, r0 in enumerate(range(0, rows, 3)):
        cs = range(cols) if band % 2 == 0 else range(cols - 1, -1, -1)
        for c in cs:
            for r in range(r0, min(r0 + 3, rows)):
                if tiles_on[r, c]:
                    order.append((r, c))
    return order


def render(image_path: Path, seconds: float, out_path: Path, brand: dict) -> Path:
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    w, h = w - w % 2, h - h % 2
    img = img.crop((0, 0, w, h))
    a = np.asarray(img).astype(np.int16)
    gray = a.mean(axis=2)
    line = gray < 105                                         # dark outlines
    white = np.full_like(a, 255)
    line_art = np.where(line[..., None], a, white).astype(np.uint8)
    full = np.asarray(img)

    th, tw = math.ceil(h / TILE), math.ceil(w / TILE)
    pad = np.zeros((th * TILE, tw * TILE), bool)
    pad[:h, :w] = line
    tiles_on = pad.reshape(th, TILE, tw, TILE).sum(axis=(1, 3)) > 6
    order = _order(tiles_on)

    fps = config.FPS
    seconds = max(seconds, 3.0)
    draw_s = min(3.2, max(1.6, seconds * 0.55))
    fill_s = 0.5
    n_frames = int(seconds * fps)
    n_draw, n_fill = int(draw_s * fps), int(fill_s * fps)
    pen, tip = _hand(brand, w)
    rng = np.random.default_rng(7)

    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
         "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", str(out_path)], stdin=subprocess.PIPE)
    revealed = np.zeros((th, tw), bool)
    shown = 0
    last_rc = order[0] if order else (th // 2, tw // 2)
    try:
        for f in range(n_frames):
            if f < n_draw and order:
                x = (f + 1) / n_draw
                target = int(len(order) * (1 - (1 - x) ** 1.4))
                for r, c in order[shown:target]:
                    revealed[r, c] = True
                    last_rc = (r, c)
                shown = max(shown, target)
                m = np.kron(revealed, np.ones((TILE, TILE), bool))[:h, :w]
                frame = np.where((m & line)[..., None], line_art, 255).astype(np.uint8)
                fr = Image.fromarray(frame)
                px = int(last_rc[1] * TILE + TILE / 2 + rng.normal(0, 2)) - tip[0]
                py = int(last_rc[0] * TILE + TILE / 2 + rng.normal(0, 2)) - tip[1]
                fr.paste(pen, (px, py), pen)
            elif f < n_draw + n_fill:
                t = (f - n_draw + 1) / max(1, n_fill)
                base = Image.blend(Image.fromarray(line_art), Image.fromarray(full), t)
                fr = base
                # pen slides away to the bottom-right
                px = int(last_rc[1] * TILE - tip[0] + t * w * 0.9)
                py = int(last_rc[0] * TILE - tip[1] + t * h * 0.6)
                fr.paste(pen, (px, py), pen)
            else:
                t = (f - n_draw - n_fill) / max(1, n_frames - n_draw - n_fill)
                z = 1 + 0.035 * t                               # gentle push-in while holding
                cw, ch = int(w / z), int(h / z)
                fr = img.crop(((w - cw) // 2, (h - ch) // 2, (w - cw) // 2 + cw, (h - ch) // 2 + ch)).resize((w, h))
            proc.stdin.write(fr.convert("RGB").tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("FFmpeg failed while rendering the whiteboard clip")
    return out_path
