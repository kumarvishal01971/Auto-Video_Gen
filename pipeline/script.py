"""Step 2: brief -> script + storyboard (JSON)."""
import config
from pipeline import styles
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
      "image_prompt": "detailed English prompt for the still image in the chosen STYLE; NO text, letters, logos or signage in the image",
      "motion_prompt": "English: camera + subject motion for image-to-video, e.g. 'slow dolly in, curtains moving gently'",
      "on_screen_text": "max 5 punchy words shown big on screen, Roman script, or empty",
      "delivery": "ONE emotion/direction word for the voice actor: excited | warm | curious | worried | relieved | playful | confident | reassuring | whispers | laughs",
      "voiceover": "the line as SPOKEN by a human voice actor (see VOICEOVER rules)",
      "caption": "the same line for on-screen subtitles, Roman script (Hinglish in English letters), max 10 words"
    }
  ],
  "cta": {"text": "max 5 words", "subtext": "website / phone / handle"},
  "caption": "social media caption, 1-3 sentences, in the video language",
  "hashtags": ["#tag1", "#tag2"]
}"""


def build_prompt(brief: dict, brand: dict, comments: str = "", previous: dict | None = None) -> str:
    tone = brand["tone_guide"]
    style = styles.get(brief.get("style"))
    # AI video clips are max ~5 s, so keep each line short enough to stay in motion
    shot_range = "3-5" if config.VIDEO_PROVIDER in ("hf_space", "fal_wan") and style["renderer"] == "video" else "3-7"
    lang = brief.get("language") or tone["language_default"]
    dur = int(brief.get("duration") or 40)
    if dur < 20:   # short spots: ~3 s per shot
        n_shots = f"{max(2, dur // 4)} to {max(3, dur // 3)}"
    else:
        n_shots = "5 to 7" if style["renderer"] == "talking" else "6 to 8"
    p = f"""Create a {brief.get('duration', 40)}-second promotional video storyboard.

BRIEF
- Topic: {brief.get('topic')}
- Objective: {brief.get('objective')}
- Target audience: {brief.get('audience') or brand['strategy']['audience_default']}
- Platforms: {brief.get('platforms')}
- Key message: {brief.get('key_message')}
- Call to action: {brief.get('cta') or brand['default_cta']}
- Facts that MUST be used exactly (do not invent others): {brief.get('facts') or 'none'}
- Language: {lang} (hinglish = everyday Hindi mixed with common English words)

STYLE
- {style['script']}

BRAND
- Brand: {brand['brand_name']} ({brand['website']})
- Visual style for every image_prompt: {style['image'] or brand['visual_style']}
- Never show: {brand.get('visual_avoid', 'alcohol, smoking, gambling, cash being thrown')}
- Voice: {tone['voice']}
- Never use: {', '.join(tone['words_to_avoid'])}
- Compliance: {tone['compliance']}

RULES
- {n_shots} shots; durations add up to about {brief.get('duration', 40)} seconds ({shot_range} s each).
- Shot 1 must hook in the first 2 seconds. Last shot leads into the CTA.
- Voiceover pace: about 2.3 words per second of shot duration.

VOICEOVER (this is what makes it sound human, not robotic)
- Write how a real person TALKS, not how ads read: short phrases, natural fillers where they fit
  ("arre", "yaar", "bas", "sach mein?", "toh"), questions, a small laugh or sigh if it fits the moment.
- Use punctuation to direct pacing: commas for small pauses, "…" for a beat, "!" for energy, "?" for curiosity.
- For hindi/hinglish write Hindi words in Devanagari and English words in English letters
  (e.g. "नया साल आ गया… और plans ready हैं, पर wallet? 😅" without emojis). For english, plain English.
- The lines must flow as ONE continuous story when read back-to-back; vary emotion across shots
  (e.g. curious → worried → relieved → excited → confident).
- Keep characters/locations consistent across image_prompts (repeat key descriptors).
- No text inside images; on-screen text is added later by our templates.
- RELEVANCE: each shot's image_prompt must SHOW what that shot's voiceover is saying, literally and
  concretely (e.g. line about an empty wallet -> "an open empty brown wallet with a moth flying out";
  line about opening the app -> "a hand holding a smartphone showing a simple loan app screen with a big
  tick"). Never a generic or decorative picture. Every shot must show a DIFFERENT scene/object.
- "visual" must describe that same picture in one plain sentence so the reviewer can check it.

Return JSON in exactly this schema:
{SCHEMA}"""
    if style["renderer"] == "talking":
        p += talking_rules(style, brand, brief)
    if previous and comments:
        p += f"\n\nPREVIOUS VERSION (rejected):\n{previous}\n\nREVIEWER COMMENTS — fix all of these:\n{comments}"
    return p


def brand_cast(style_key: str, brand: dict) -> list[dict]:
    return [c for c in brand.get("characters", []) if style_key in c.get("styles", [])]


def talking_rules(style: dict, brand: dict, brief: dict) -> str:
    cast = brand_cast(style["key"], brand)
    if cast:
        who = "\n".join(f"- id \"{c['id']}\": {c['name']} — LOOK: {c['look']}. PERSONALITY: {c.get('personality', '')}. "
                        f"VOICE: {c.get('voice', '')}" for c in cast)
        cast_rule = (f"Use these brand characters (copy their id, name and LOOK exactly):\n{who}\n"
                     "You may add ONE supporting character only if the story really needs it, in the same art style.")
    else:
        cast_rule = ("Invent 1-2 characters. Give each a precise, repeatable LOOK (age, face, hair, clothes, "
                     "colours) and a VOICE (male/female, age, tone).")
    return f"""

TALKING CHARACTERS (the characters speak on camera with lip-sync — this is what makes it feel real)
{cast_rule}
- Add to the JSON: "characters": [{{"id": "short_id", "name": "...", "look": "...", "voice": "male|female, age, tone"}}]
- Every shot has "speaker": the id of the character who speaks the line ON CAMERA (or "narrator" only for the last CTA shot if nobody is on screen).
- image_prompt: the speaker in a medium or close shot facing the camera, mouth clearly visible, ONE clear
  facial expression and gesture that matches the line, repeat the speaker's full LOOK every time.
- motion_prompt = ACTING: facial expression changes, gesture, body movement and camera for that line
  (e.g. "slumps, long sigh, eyes roll up, then a small helpless laugh; slow push in").
- Each line is 2-7 seconds when spoken (at least 4 words).
- The line must sound like THIS character talking (their personality), not like an announcer.
- You may put up to 2 voice-acting tags inside a voiceover line where a real person would do it:
  [sighs] [laughs] [chuckles] [excited] [whispers] [sarcastic] [relieved] [gasps]. Never in "caption"."""


def generate(brief: dict, comments: str = "", previous: dict | None = None) -> dict:
    brand = config.load_brand()
    if config.LLM_PROVIDER == "mock":
        sb = _mock(brief, brand)
    else:
        sb = llm.chat_json(SYSTEM, build_prompt(brief, brand, comments, previous))
    return normalise(sb, brief, brand)


def normalise(sb: dict, brief: dict, brand: dict) -> dict:
    sb["style"] = styles.get(brief.get("style"))["key"]
    sb.setdefault("title", brief.get("topic", "Promo"))
    sb.setdefault("language", brief.get("language") or brand["tone_guide"]["language_default"])
    shots = sb.get("shots") or []
    for i, s in enumerate(shots, 1):
        s["id"] = i
        s["duration_sec"] = float(s.get("duration_sec") or 5)
        for k in ("visual", "image_prompt", "motion_prompt", "on_screen_text", "voiceover", "caption", "delivery"):
            s.setdefault(k, "")
        s["delivery"] = str(s["delivery"]).strip(" []").lower()
        s.setdefault("speaker", "narrator")
    sb["shots"] = shots
    st = styles.get(brief.get("style"))
    if st["renderer"] == "talking":
        cast = {c.get("id"): c for c in sb.get("characters") or [] if c.get("id")}
        for bc in brand_cast(st["key"], brand):   # brand mascots: sheet, look and voice always from brand.json
            cast[bc["id"]] = {**cast.get(bc["id"], {}), "id": bc["id"], "name": bc["name"], "look": bc["look"],
                              "voice": bc.get("voice", ""), "sheet": bc.get("sheet", ""),
                              "voice_id": bc.get("voice_id", ""), "brand": True}
        known = set(cast)
        for s in shots:
            if s["speaker"] not in known and s["speaker"] != "narrator":
                # match by name, else the main character
                hit = next((k for k, c in cast.items() if str(c.get("name", "")).lower() == str(s["speaker"]).lower()), None)
                s["speaker"] = hit or (next(iter(cast)) if cast else "narrator")
        sb["characters"] = list(cast.values())
    sb.setdefault("cta", {"text": brief.get("cta") or "Learn more", "subtext": brand["website"]})
    sb.setdefault("caption", "")
    sb.setdefault("hashtags", [])
    return sb


def _mock(brief, brand):
    """Template storyboard for testing without an AI key."""
    topic = brief.get("topic", "Our new launch")
    st = styles.get(brief.get("style"))
    if st["renderer"] == "talking":
        return _mock_talking(brief, brand, st)
    look = st["image"] or brand["visual_style"]
    beats = [  # visual, motion, on-screen, delivery, voiceover (TTS), caption (subtitle)
        ("Friends planning a New Year party", "slow push in", "Plans ready?", "excited",
         "नया साल आ गया… और plans? एकदम ready!", "Naya saal aa gaya… aur plans? Ekdum ready!"),
        ("A young man checks his phone, worried", "slow zoom on face", "Wallet khaali?", "worried",
         "पर ये wallet… हम्म, थोड़ा खाली है यार।", "Par ye wallet… hmm, thoda khaali hai yaar."),
        ("He opens the UdhaarBox app on his phone", "gentle dolly in", "UdhaarBox kholo", "curious",
         "तो क्या करें? सीधा UdhaarBox खोलो।", "Toh kya karein? Seedha UdhaarBox kholo."),
        ("Simple steps on the phone, he smiles", "slow pan", "Bas kuch taps", "relieved",
         "बस कुछ taps… और काम हो गया।", "Bas kuch taps… aur kaam ho gaya."),
        ("Friends celebrating under fireworks", "crane up", "Tension free!", "laughs",
         "अब party भी, खुशियाँ भी… tension zero!", "Ab party bhi, khushiyan bhi… tension zero!"),
        ("Phone with app icon, gold confetti", "slow zoom out", "Abhi download karo", "confident",
         "नए साल की शुरुआत, UdhaarBox के साथ।", "Naye saal ki shuruaat, UdhaarBox ke saath."),
    ]
    return {
        "title": topic,
        "language": "hinglish",
        "shots": [
            {"duration_sec": 5, "visual": v, "image_prompt": f"{v}, {look}",
             "motion_prompt": m, "on_screen_text": t, "delivery": d, "voiceover": vo, "caption": cap}
            for v, m, t, d, vo, cap in beats
        ],
        "cta": {"text": brief.get("cta") or brand.get("default_cta", "Download today"), "subtext": brand["website"]},
        "caption": f"{topic} — {brief.get('key_message', '')}",
        "hashtags": ["#" + brand["brand_name"].replace(" ", ""), "#NewYear"],
    }


def _mock_talking(brief, brand, st):
    cast = brand_cast(st["key"], brand) or [{"id": "rohan", "name": "Rohan",
        "look": "Indian man, 28, short black hair, light stubble, navy shirt with rolled sleeves",
        "voice": "male, 28, warm Hinglish"}]
    c = cast[0]
    topic = brief.get("topic", "Our new launch")
    look = st["image"] or brand["visual_style"]
    beats = [  # visual, acting, on-screen, voiceover, caption
        ("night office desk, files piled up, calendar shows 31 DEC; {n} slumps, cheek on hand",
         "slumps, long sigh, eyes roll up, small helpless laugh; slow push in", "Month-end?",
         "[sighs] New Year आ गया… और salary? अभी दूर है यार।", "New Year aa gaya… aur salary? Abhi door hai yaar."),
        ("{n} counts on fingers, thought bubbles of party, rent and a gift box", "counts on fingers, panics, rubs forehead",
         "", "Party, rent, माँ का gift… सब एक साथ? कैसे होगा?", "Party, rent, Maa ka gift… sab ek saath?"),
        ("{n} pops up smiling holding a phone toward camera with the app open", "eyes go wide, springs up, grins, swings phone to camera",
         "UdhaarBox kholo", "[excited] अरे रुको! Tension क्यों? UdhaarBox खोलो!", "Arre ruko! Tension kyun? UdhaarBox kholo!"),
        ("close shot of {n} tapping the phone, ticks popping, relaxed smile", "taps phone, nods, leans back relaxed",
         "Simple steps", "Details भरो, eligibility check करो… बस, हो गया।", "Details bharo, eligibility check karo… bas!"),
        ("{n} on a rooftop at night with fireworks, arms wide, joyful", "throws arms wide, laughs, winks and gives a thumbs-up",
         "Tension free!", "[laughs] तो इस New Year… tension को बोलो bye bye!", "Toh is New Year… tension ko bolo bye bye!"),
    ]
    return {
        "title": topic, "language": "hinglish",
        "characters": [{k: c.get(k, "") for k in ("id", "name", "look", "voice")}],
        "shots": [{"duration_sec": 5, "speaker": c["id"], "visual": v.format(n=c["name"]),
                   "image_prompt": f"{v.format(n=c['name'])}. {c['name']}: {c['look']}. {look}",
                   "motion_prompt": m, "on_screen_text": t, "delivery": "", "voiceover": vo, "caption": cap}
                  for v, m, t, vo, cap in beats],
        "cta": {"text": brief.get("cta") or brand.get("default_cta", "Download today"), "subtext": brand["website"]},
        "caption": f"{topic} — {brief.get('key_message', '')}",
        "hashtags": ["#" + brand["brand_name"].replace(" ", ""), "#NewYear"],
    }
