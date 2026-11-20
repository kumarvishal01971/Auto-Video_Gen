# Promo Video Pipeline — v0.1 (test build)

Brief → AI script & storyboard → **approve** → keyframe images → **approve** → video clips → quality check
→ voiceover + subtitles + music → branded 9:16 and 16:9 videos → **approve** → published folder.

Matches the team workflow chart (steps 1–11). Step 12 (performance tracking) comes later.

## 1. Install (once)
1. Install **Python 3.11+** and **FFmpeg** (Windows: `winget install Gyan.FFmpeg`, Mac: `brew install ffmpeg`).
2. Unzip this folder, then run `start.bat` (Windows) or `./start.sh` (Mac/Linux).
   First run creates `.env` — add your keys (see below) and run it again.
3. Open **http://localhost:8000** → **+ New brief**.

## 2. Free test keys
| What | Setting in `.env` | Get it |
|---|---|---|
| Script AI | `LLM_API_KEY` (Gemini) | aistudio.google.com → Get API key (free tier). Groq / DeepSeek also work — see `.env.example` |
| Images | `IMAGE_PROVIDER=pollinations` | no key needed. Or `huggingface` + free `HF_TOKEN` |
| Video | `VIDEO_PROVIDER=kenburns` | free — animates the approved stills |
| Voice | `TTS_PROVIDER=edge` | free, no key (Hindi + English voices) |

### Automatic free-provider fallback
- Script/storyboard: Gemini remains primary. If it fails, the app tries Groq when `GROQ_API_KEY` is set; `GROQ_BASE_URL` and `GROQ_MODEL` default to Groq's API and Qwen 3.8 27B.
- Keyframe images: the app tries `IMAGE_PROVIDER` first, then each provider in `IMAGE_FALLBACK` in order. Pollinations works without a key but may include a watermark; `POLLINATIONS_API_KEY` enables no-watermark requests. Cloudflare needs `CF_ACCOUNT_ID` and `CF_API_TOKEN`. Hugging Face needs `HF_TOKEN`.
- Without the corresponding fallback key/token, the primary provider's failure is surfaced instead of silently substituting placeholder output.

Real AI video: set `VIDEO_PROVIDER=fal_wan` + `FAL_KEY` (paid, ≈ $0.10 per 5 s clip on Wan 2.2 Turbo).
Load $5–10 for the trial. Switching providers never needs code changes — only `.env`.

> Free services (pollinations, edge voices, ElevenLabs free tier) are for **testing**.
> Before publishing commercially, move to paid/licensed providers.

## 3. Set up the brand (Section A of the chart)
- `brand/brand.json` — colours, tone guide, words to avoid, compliance, strategy, default CTA, visual style.
- `brand/logo.png` — end-card logo; `brand/watermark.png` — corner watermark.
- `brand/fonts/` — `heading.ttf`, `body.ttf`. For Hindi subtitles install **Noto Sans Devanagari**
  (or put the .ttf here) — name is set in `subtitle_font`.
- `brand/music/` — licensed .mp3 tracks; one is picked per video, mixed under the voice.

## 4. Where things are
- Every video: `data/jobs/<id>/` (keyframes, clips, audio, final).
- Approved videos: `data/output/<date>_<title>/` — both formats, thumbnail, `caption.txt`, `storyboard.json`.
- Deleted videos and campaigns move to **Trash** and can be restored. **Delete forever** and **Empty Trash** permanently remove their job history and generated files.

## 5. Connect other tools (optional)
- `POST /api/briefs` (JSON, same fields as the form) creates a video — use from n8n, Google Forms, WhatsApp bots.
- `GET /api/jobs/<id>` returns status — n8n can poll it and send "approval needed" emails/WhatsApp.

## 6. Code map
| File | Step |
|---|---|
| `app.py`, `templates/`, `static/` | dashboard, approvals, production, video library, campaigns, activity, usage, brand, settings, API |
| `pipeline/script.py` | 2 · script & storyboard prompt |
| `pipeline/media.py` | 4 keyframes · 6 clips · 7 QC · 8 voice |
| `pipeline/assemble.py` | 9 · FFmpeg assembly, subtitles, logo, end card, music, both formats |
| `pipeline/orchestrator.py` | runs steps, stops at gates, handles reject/redo routes, 11 publish |
| `pipeline/providers/` | swappable AI services (LLM, image, video, voice) |
| `config.py`, `.env` | settings and keys (keys only in `.env`) |

## Known limits of v0.1
- One video is processed at a time (fine for 30/month; avoids rate limits).
- No login beyond optional `DASHBOARD_PASSWORD`; put behind the group dashboard's Google login before team-wide use.
- Publishing saves files only; Drive upload / auto-posting is the next step.
- Upscaling is FFmpeg Lanczos (720p → 1080p); Real-ESRGAN can be added later.
