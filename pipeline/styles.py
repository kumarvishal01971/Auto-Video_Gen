"""Video styles (the Style dropdown on the brief form)."""

STYLES = {
    "whiteboard": {
        "label": "Whiteboard — hand-drawn explainer",
        "short": "Whiteboard",
        "renderer": "whiteboard",
        "script": (
            "WHITEBOARD EXPLAINER. Each shot is ONE simple doodle that explains the line visually "
            "(a metaphor/icon scene: coin jar, calendar, phone with a tick, smiling family, gift box, rupee coins, "
            "arrow up, clock). 1-3 objects per shot, centred, lots of white space. Think 'teacher drawing on a board'."
        ),
        "image": (
            "hand-drawn whiteboard doodle illustration, clean bold black marker outlines, simple flat colour fills "
            "in warm gold and soft accent colours, pure white background, centred composition with white margins, "
            "cartoon icon style, no shading, no gradients, no text, no letters"
        ),
        "motion": "",
    },
    "stickman3d": {
        "label": "3D stickman — animated story characters",
        "short": "3D stickman",
        "renderer": "video",
        "script": (
            "3D STICKMAN STORY. Characters are faceless smooth white 3D stickman/mannequin figures acting out a "
            "short relatable story with clear body language (worried, thinking, celebrating, jumping). "
            "Keep the SAME characters and setting across shots."
        ),
        "image": (
            "3D render, minimalist smooth matte white stickman mannequin characters with round heads and no facial "
            "features, expressive body pose, clean colourful 3D environment, soft studio lighting, gold and black "
            "accents, Pixar-style render, vertical composition, no text"
        ),
        "motion": "smooth character animation, expressive body movement, gentle camera move",
    },
    "realistic": {
        "label": "Realistic — AI photo-video",
        "short": "Realistic",
        "renderer": "video",
        "script": "REALISTIC LIFESTYLE. Photorealistic cinematic shots of real-looking Indian people and places.",
        "image": "",  # uses brand visual_style
        "motion": "",
    },
}
DEFAULT = "realistic"


def get(name: str | None) -> dict:
    return STYLES.get(name or DEFAULT, STYLES[DEFAULT]) | {"key": name if name in STYLES else DEFAULT}
