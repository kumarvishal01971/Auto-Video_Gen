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
    "cartoon": {
        "label": "Animated character — mascot talks (lip-sync)",
        "short": "Animated",
        "renderer": "talking",
        "script": (
            "ANIMATED MASCOT AD. The brand mascot(s) listed under CHARACTERS act and SPEAK on camera like a "
            "funny, relatable friend. Each shot: ONE speaking character in a medium or close shot facing the "
            "camera, a clear facial expression and gesture that matches the line, a simple set behind them."
        ),
        "image": (
            "2D hand-drawn cartoon animation still, bold black outlines, flat colours with light cel shading, "
            "warm cream and soft yellow backgrounds, big expressive cartoon face, character drawn exactly like "
            "the reference sheet (same hair, glasses, outfit, proportions), vertical composition, no text"
        ),
        "motion": (
            "bouncy, snappy 2D cartoon animation with squash and stretch and quick anticipation, doodle effects "
            "like sweat drops and sparkle lines, very expressive face and hands, accurate lip-sync to the voice, "
            "same character design throughout"
        ),
    },
    "realistic": {
        "label": "Realistic — real-looking people talk (lip-sync)",
        "short": "Realistic",
        "renderer": "talking",
        "script": (
            "REALISTIC LIFESTYLE AD. Photorealistic, real-looking Indian people act and SPEAK on camera like a "
            "real conversation or a person talking to the viewer. Each shot: ONE speaking person in a medium or "
            "close shot, natural expression and gesture that matches the line."
        ),
        "image": "",  # uses brand visual_style
        "motion": (
            "natural human acting, subtle head and shoulder movement, natural blinking, believable facial "
            "expressions that match the emotion, accurate lip-sync, gentle handheld cinematic camera"
        ),
    },
}
DEFAULT = "realistic"


def talking(name: str | None) -> bool:
    return get(name)["renderer"] == "talking"


def get(name: str | None) -> dict:
    return STYLES.get(name or DEFAULT, STYLES[DEFAULT]) | {"key": name if name in STYLES else DEFAULT}
