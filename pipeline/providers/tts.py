"""Voiceover.
edge       – free, no key (Microsoft Edge voices incl. Hindi). Testing; confirm licence before commercial use.
elevenlabs – ELEVENLABS_API_KEY (free tier is non-commercial; Starter+ for commercial)
silent     – offline placeholder: silence sized to the line (plumbing test only)"""
import asyncio
import subprocess
from pathlib import Path

import httpx

import config


def speak(text: str, language: str, out_path: Path) -> Path:
    p = config.TTS_PROVIDER
    if p == "elevenlabs":
        _eleven(text, out_path)
    elif p == "edge":
        _edge(text, language, out_path)
    else:
        _silent(text, out_path)
    if not out_path.is_file() or out_path.stat().st_size < 500:
        raise RuntimeError(f"Voice provider '{p}' returned no audio (check internet / API key)")
    return out_path


def _edge(text, language, out_path):
    import edge_tts

    voice = config.EDGE_VOICE_EN if language == "en" else config.EDGE_VOICE_HI
    asyncio.run(edge_tts.Communicate(text, voice, rate="+5%").save(str(out_path)))
    return out_path


def _eleven(text, out_path):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}"
    r = httpx.post(url, headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                   json={"text": text, "model_id": config.ELEVENLABS_MODEL}, timeout=120)
    r.raise_for_status()
    out_path.write_bytes(r.content)
    return out_path


def _silent(text, out_path):
    secs = max(1.5, len(text.split()) / 2.4)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=stereo", "-t", f"{secs:.2f}", "-q:a", "4",
                    str(out_path)], check=True)
    return out_path
