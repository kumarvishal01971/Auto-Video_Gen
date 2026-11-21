"""Voiceover. The whole script is spoken in ONE take (natural flow), then split per shot.
sarvam     – Sarvam Bulbul: Indian expressive voices, great Hindi/Hinglish. SARVAM_API_KEY (₹ free credits on signup)
elevenlabs – ElevenLabs. Default model eleven_v3 understands emotion tags like [excited] [warmly] [laughs].
             Use ELEVENLABS_VOICE_ID of a library voice or of your own cloned voice (see tools/clone_voice.py).
edge       – free Microsoft voices; flat delivery, testing only
silent     – offline placeholder: silence sized to the text (plumbing test only)"""
import asyncio
import base64
import re
import subprocess
from pathlib import Path

import httpx

import config
from config import env

TAG = re.compile(r"\[[^\]]{1,30}\]")


def strip_tags(text: str) -> str:
    return re.sub(r"\s{2,}", " ", TAG.sub("", text)).strip()


def provider() -> str:
    return config.TTS_PROVIDER


def keeps_tags() -> bool:
    """Only ElevenLabs v3/v4 performs [emotion] tags; everyone else must not read them aloud."""
    return provider() == "elevenlabs" and config.ELEVENLABS_MODEL.startswith(("eleven_v3", "eleven_v4"))


def speak(text: str, language: str, out_path: Path) -> Path:
    p = provider()
    if not keeps_tags():
        text = strip_tags(text)
    if p == "sarvam":
        _sarvam(text, language, out_path)
    elif p == "elevenlabs":
        _eleven(text, language, out_path)
    elif p == "edge":
        _edge(text, language, out_path)
    else:
        _silent(text, out_path)
    if not out_path.is_file() or out_path.stat().st_size < 500:
        raise RuntimeError(f"Voice provider '{p}' returned no audio (check internet / API key)")
    return out_path


def _sarvam(text, language, out_path):
    key = env("SARVAM_API_KEY")
    if not key:
        raise RuntimeError("SARVAM_API_KEY is not set in .env")
    body = {
        "text": text[:2500],
        "language_code": "en-IN" if language == "en" else "hi-IN",
        "model": env("SARVAM_MODEL", "bulbul:v3"),
        "speaker": env("SARVAM_SPEAKER", "ritu"),
        "pace": float(env("SARVAM_PACE", "1.05")),
        "temperature": float(env("SARVAM_TEMPERATURE", "0.75")),
        "speech_sample_rate": 44100,
        "output_audio_codec": "wav",
        "enable_preprocessing": True,
    }
    r = httpx.post("https://api.sarvam.ai/text-to-speech", json=body,
                   headers={"api-subscription-key": key}, timeout=180)
    if r.status_code >= 400:
        raise RuntimeError(f"Sarvam HTTP {r.status_code}: {r.text[:300]}")
    out_path.write_bytes(base64.b64decode(r.json()["audios"][0]))


def _eleven(text, language, out_path):
    if not config.ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is not set in .env")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}"
    body = {"text": text, "model_id": config.ELEVENLABS_MODEL,
            "voice_settings": {"stability": float(env("ELEVENLABS_STABILITY", "0.5")),
                               "similarity_boost": float(env("ELEVENLABS_SIMILARITY", "0.8"))}}
    r = httpx.post(url, headers={"xi-api-key": config.ELEVENLABS_API_KEY}, json=body,
                   params={"output_format": "mp3_44100_128"}, timeout=180)
    if r.status_code >= 400:
        raise RuntimeError(f"ElevenLabs HTTP {r.status_code}: {r.text[:300]}")
    out_path.write_bytes(r.content)


def _edge(text, language, out_path):
    import edge_tts

    voice = config.EDGE_VOICE_EN if language == "en" else config.EDGE_VOICE_HI
    asyncio.run(edge_tts.Communicate(text, voice, rate="+5%").save(str(out_path)))


def _silent(text, out_path):
    # silence with short gaps at each "…" so the shot-splitting logic can be tested offline
    parts = [p for p in re.split(r"\s*…\s*|\s*\.\.\.\s*", text) if p.strip()]
    # add a faint tone during "speech" so pauses are detectable
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    filt = []
    for i, p in enumerate(parts):
        d = max(1.2, len(p.split()) / 2.4)
        cmd += ["-f", "lavfi", "-t", f"{d:.2f}", "-i", "sine=frequency=220:sample_rate=44100"]
        cmd += ["-f", "lavfi", "-t", "0.45", "-i", "anullsrc=r=44100:cl=mono"]
        filt.append(f"[{2 * i}:a]volume=0.05[v{i}];[v{i}][{2 * i + 1}:a]concat=n=2:v=0:a=1[c{i}]")
    filt.append("".join(f"[c{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[o]")
    cmd += ["-filter_complex", ";".join(filt), "-map", "[o]", str(out_path)]
    subprocess.run(cmd, check=True)
