"""Step 2: brief -> script + storyboard (JSON)."""
import config
from pipeline.providers import llm

SYSTEM = """You are a senior creative director at an Indian advertising agency.
You write 30-45 second promotional videos for social media (Reels/Shorts first).
Return ONLY valid JSON matching the schema given. No markdown."""

SCHEMA = """{
  "title": "short internal title",
  "language": "hi | en | hinglish",
  "shots": [
    {
      "id": 1,
      "duration_sec": 5,
      "visual": "what the viewer sees, 1 sentence",
      "image_prompt": "detailed English prompt for a photorealistic still image; NO text, letters, logos or signage in the image",
      "motion_prompt": "English: camera + subject motion for image-to-video, e.g. 'slow dolly in, curtains moving gently'",
      "on_screen_text": "max 6 words shown on screen (in the video language), or empty",
      "voiceover": "the line spoken over this shot, in the video language"
    }
  ],
  "cta": {"text": "max 5 words", "subtext": "website / phone / handle"},
  "caption": "social media caption, 1-3 sentences, in the video language",
  "hashtags": ["#tag1", "#tag2"]
}"""


def build_prompt(brief: dict, brand: dict, comments: str = "", previous: dict | None = None) -> str:
    tone = brand["tone_guide"]
    lang = brief.get("language") or tone["language_default"]
    p = f"""Create a {brief.get('duration', 40)}-second promotional video storyboard.

BRIEF
- Topic: {brief.get('topic')}
- Objective: {brief.get('objective')}
- Target audience: {brief.get('audience') or brand['strategy']['audience_default']}
- Platforms: {brief.get('platforms')}
- Key message: {brief.get('key_message')}
- Call to action: {brief.get('cta') or brand['default_cta']}
- Facts that MUST be used exactly (do not invent others): {brief.get('facts') or 'none'}
- Language: {lang} (hinglish = Hindi in Devanagari mixed with common English words)

BRAND
- Brand: {brand['brand_name']} ({brand['website']})
- Visual style for every image_prompt: {brand['visual_style']}
- Voice: {tone['voice']}
- Never use: {', '.join(tone['words_to_avoid'])}
- Compliance: {tone['compliance']}

RULES
- 6 to 8 shots; durations add up to about {brief.get('duration', 40)} seconds (3-7 s each).
- Shot 1 must hook in the first 2 seconds. Last shot leads into the CTA.
- Voiceover pace: about 2.3 words per second of shot duration.
- Keep characters/locations consistent across image_prompts (repeat key descriptors).
- No text inside images; on-screen text is added later by our templates.

Return JSON in exactly this schema:
{SCHEMA}"""
    if previous and comments:
        p += f"\n\nPREVIOUS VERSION (rejected):\n{previous}\n\nREVIEWER COMMENTS — fix all of these:\n{comments}"
    return p


def generate(brief: dict, comments: str = "", previous: dict | None = None) -> dict:
    brand = config.load_brand()
    if config.LLM_PROVIDER == "mock":
        sb = _mock(brief, brand)
    else:
        sb = llm.chat_json(SYSTEM, build_prompt(brief, brand, comments, previous))
    return normalise(sb, brief, brand)


def normalise(sb: dict, brief: dict, brand: dict) -> dict:
    sb.setdefault("title", brief.get("topic", "Promo"))
    sb.setdefault("language", brief.get("language") or brand["tone_guide"]["language_default"])
    shots = sb.get("shots") or []
    for i, s in enumerate(shots, 1):
        s["id"] = i
        s["duration_sec"] = float(s.get("duration_sec") or 5)
        for k in ("visual", "image_prompt", "motion_prompt", "on_screen_text", "voiceover"):
            s.setdefault(k, "")
    sb["shots"] = shots
    sb.setdefault("cta", {"text": brief.get("cta") or "Learn more", "subtext": brand["website"]})
    sb.setdefault("caption", "")
    sb.setdefault("hashtags", [])
    return sb


def _mock(brief, brand):
    topic = brief.get("topic", "Our new launch")
    style = brand["visual_style"]
    beats = [
        ("Aerial view at sunrise", "slow aerial push in", "Ek nayi shuruaat", f"{topic} — ek nayi shuruaat."),
        ("Happy family arriving", "camera tracks with the family", "Aapke sapnon ka ghar", "Jahan har sapna sach hota hai."),
        ("Premium interiors detail", "slow pan across the room", "Premium quality", "Har kone mein premium quality."),
        ("Green landscaped garden", "gentle crane up", "Prakriti ke kareeb", "Hariyali aur sukoon, dono saath."),
        ("Team of experts at work", "slow dolly left", "Bharosa jo dikhta hai", f"{brand['brand_name']} ka bharosa."),
        ("Night facade lit up", "slow zoom out", "Aaj hi judiye", "Aaj hi apna kal surakshit kijiye."),
    ]
    return {
        "title": topic,
        "language": "hinglish",
        "shots": [
            {"duration_sec": 6, "visual": v, "image_prompt": f"{v}, {topic}, {style}",
             "motion_prompt": m, "on_screen_text": t, "voiceover": vo}
            for v, m, t, vo in beats
        ],
        "cta": {"text": brief.get("cta") or "Abhi sampark karein", "subtext": brand["website"]},
        "caption": f"{topic} — {brief.get('key_message', '')}",
        "hashtags": ["#Prabhatam", "#NewLaunch"],
    }
