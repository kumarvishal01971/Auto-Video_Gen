"""Central settings. Every secret comes from environment variables (.env, git-ignored)."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


DATA_DIR = Path(env("DATA_DIR", str(ROOT / "data")))
JOBS_DIR = DATA_DIR / "jobs"
OUTPUT_DIR = DATA_DIR / "output"
DB_PATH = DATA_DIR / "pipeline.db"
BRAND_DIR = ROOT / "brand"

for d in (JOBS_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---- Provider selection (change in .env, no code changes) ----
LLM_PROVIDER = env("LLM_PROVIDER", "mock")          # mock | openai_compat
LLM_BASE_URL = env("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
LLM_API_KEY = env("LLM_API_KEY")
LLM_MODEL = env("LLM_MODEL", "gemini-2.5-flash")

IMAGE_PROVIDER = env("IMAGE_PROVIDER", "placeholder")  # placeholder | pollinations | huggingface | fal
HF_TOKEN = env("HF_TOKEN")
HF_IMAGE_MODEL = env("HF_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell")
FAL_KEY = env("FAL_KEY")
FAL_IMAGE_MODEL = env("FAL_IMAGE_MODEL", "fal-ai/flux/schnell")

VIDEO_PROVIDER = env("VIDEO_PROVIDER", "kenburns")    # kenburns | hf_space (free AI) | fal_wan (paid AI)
FAL_VIDEO_MODEL = env("FAL_VIDEO_MODEL", "fal-ai/wan/v2.2-a14b/image-to-video/turbo")
VIDEO_TAKES = int(env("VIDEO_TAKES", "1"))            # 2 in production

TTS_PROVIDER = env("TTS_PROVIDER", "edge")            # sarvam | elevenlabs | edge | silent
EDGE_VOICE_HI = env("EDGE_VOICE_HI", "hi-IN-SwaraNeural")
EDGE_VOICE_EN = env("EDGE_VOICE_EN", "en-IN-NeerjaNeural")
ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = env("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_MODEL = env("ELEVENLABS_MODEL", "eleven_v3")  # v3 = emotion tags

QC_VISION = env("QC_VISION", "false").lower() == "true"  # uses the LLM (must support images)

DASHBOARD_PASSWORD = env("DASHBOARD_PASSWORD")        # optional basic protection

# Output formats
_ALL_FORMATS = {"9x16": (1080, 1920), "16x9": (1920, 1080)}
# which shapes to export: 9x16 = Reels/Shorts (vertical), 16x9 = YouTube/Facebook (horizontal)
FORMATS = {k: v for k, v in _ALL_FORMATS.items()
           if k in [f.strip() for f in env("FORMATS", "9x16,16x9").split(",")]} or {"9x16": _ALL_FORMATS["9x16"]}
FORMAT_LABELS = {"9x16": "Reels 9:16", "16x9": "YouTube 16:9"}
FPS = 24


def load_brand() -> dict:
    """Section A inputs: brand kit, tone, strategy. Edit brand/brand.json."""
    with open(BRAND_DIR / "brand.json", encoding="utf-8") as f:
        return json.load(f)
