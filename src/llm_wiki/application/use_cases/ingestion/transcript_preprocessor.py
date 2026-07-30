"""Transcript preprocessing: filler word removal for VI/EN transcripts.

Conservative word list — excludes grammatical words (thi, la, a) that carry
semantic meaning in Vietnamese finance contexts. Only strips pure interjections.
"""

import re

VI_FILLERS = r"\b(à|ừm|nhỉ|nhé|nha|ờ|hả|nè|nhá|đúng không|phải không)\b"
EN_FILLERS = (
    r"\b(um|uh|like|you know|I mean|sort of|kind of|basically|actually|literally|right|okay|so)\b"
)
_COMBINED = (
    r"\b(à|ừm|nhỉ|nhé|nha|um|uh|like|you know|I mean"
    r"|sort of|kind of|basically|actually|literally)\b"
)
COMBINED_FILLERS = _COMBINED


def preprocess(transcript_text: str, lang: str | None = None) -> str:
    """Remove filler words, normalize whitespace, dedup adjacent sentences.

    Args:
        transcript_text: Raw transcript text.
        lang: 'vi', 'en', or None (defaults to bilingual combined pattern).

    Returns:
        Cleaned transcript text.
    """
    if lang == "vi":
        pattern = VI_FILLERS
    elif lang == "en":
        pattern = EN_FILLERS
    else:
        pattern = COMBINED_FILLERS

    text = re.sub(pattern, "", transcript_text, flags=re.IGNORECASE)

    # Normalize whitespace (collapse multiple spaces, trim)
    text = re.sub(r"\s+", " ", text).strip()

    # Remove exact duplicate adjacent sentences (>80% character overlap)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        return text

    deduped = [sentences[0]]
    for i in range(1, len(sentences)):
        prev = sentences[i - 1]
        curr = sentences[i]
        similarity = _char_overlap(prev, curr)
        if similarity < 0.8:
            deduped.append(curr)

    return " ".join(deduped)


def _char_overlap(a: str, b: str) -> float:
    """Simple character-level overlap ratio between two strings."""
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    return len(intersection) / min(len(set_a), len(set_b))
