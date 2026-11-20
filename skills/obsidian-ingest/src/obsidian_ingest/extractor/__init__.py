"""Provider names and shared content categories; URL routing lives in providers/."""

from enum import StrEnum


class Provider(StrEnum):
    """Recognized URL providers; recognition alone does not imply acquisition support."""

    YOUTUBE = "youtube"
    ARTICLE = "article"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"


class SourceKind(StrEnum):
    """How a source's atoms are located: by time (video) or by position in a text."""

    TIMED = "timed"
    TEXT = "text"


class ContentCategory(StrEnum):
    """Coarse format/duration labels for navigation, not semantic classifications."""

    CLIP = "clip"
    SHORT_FORM = "short_form"
    LONG_FORM = "long_form"
    EXTENDED = "extended"
