"""Create a cloned voice on ElevenLabs from your own recording(s).

ONLY clone a voice with the speaker's clear permission (ElevenLabs requires this too).
Good samples: 1–3 minutes total, one speaker, no music/noise, natural expressive speech
(ideally the same tone you want in the ads).

Usage (from the project folder):
    python tools/clone_voice.py "UdhaarBox Voice" sample1.mp3 [sample2.mp3 ...]
Then put the printed voice id in .env:
    TTS_PROVIDER=elevenlabs
    ELEVENLABS_VOICE_ID=<printed id>
Needs ELEVENLABS_API_KEY in .env and a plan that includes instant voice cloning (Starter or above).
For Sarvam, cloning is done in their dashboard (dashboard.sarvam.ai) with a consent step;
then set SARVAM_SPEAKER to the custom voice id they give you."""
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    if not config.ELEVENLABS_API_KEY:
        sys.exit("ELEVENLABS_API_KEY is not set in .env")
    name, paths = sys.argv[1], [Path(p) for p in sys.argv[2:]]
    files = [("files", (p.name, p.read_bytes(), "audio/mpeg")) for p in paths]
    r = httpx.post("https://api.elevenlabs.io/v1/voices/add",
                   headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                   data={"name": name, "description": "Video Pilot brand voice"}, files=files, timeout=300)
    if r.status_code >= 400:
        sys.exit(f"ElevenLabs error {r.status_code}: {r.text[:400]}")
    print("Voice created. Add this to .env:\nELEVENLABS_VOICE_ID=" + r.json()["voice_id"])


if __name__ == "__main__":
    main()
