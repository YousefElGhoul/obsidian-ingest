"""Provider names and shared content categories; URL routing lives in providers/."""

from enum import StrEnum


class Provider(StrEnum):
    """Recognized URL providers; recognition alone does not imply acquisition support."""

    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"


class ContentCategory(StrEnum):
    """Coarse format/duration labels for navigation, not semantic classifications."""

    CLIP = "clip"
    SHORT_FORM = "short_form"
    LONG_FORM = "long_form"
    EXTENDED = "extended"
