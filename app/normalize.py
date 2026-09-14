"""نرمال‌سازی متن فارسی — بدون وابستگی سنگین.

Whisper emits Arabic codepoints mixed into Persian text (ي/ك instead of ی/ک),
inconsistent ZWNJ, and Arabic-Indic digits. Left alone this breaks search,
diffing and export. hazm does this too but drags in scipy + gensim +
scikit-learn (~77 MB of wheels) for what is, here, a character map.
"""
import re
import unicodedata

ZWNJ = "‌"

# Arabic -> Persian letterforms, plus invisible junk Whisper likes to emit.
_CHAR_MAP = {
    "ي": "ی",  # ARABIC YEH        -> FARSI YEH
    "ى": "ی",  # ALEF MAKSURA      -> FARSI YEH
    "ے": "ی",  # YEH BARREE        -> FARSI YEH
    "ك": "ک",  # ARABIC KAF        -> KEHEH
    "ڪ": "ک",  # SWASH KAF         -> KEHEH
    "ة": "ه",  # TEH MARBUTA       -> HEH
    "ؤ": "و",  # WAW WITH HAMZA    -> WAW
    "إ": "ا",  # ALEF WITH HAMZA BELOW -> ALEF
    "أ": "ا",  # ALEF WITH HAMZA ABOVE -> ALEF
    "آ": "آ",  # ALEF WITH MADDA (keep)
    "​": "",        # ZERO WIDTH SPACE
    "‍": "",        # ZWJ
    "﻿": "",        # BOM
    " ": " ",       # NBSP
    "،": "،",  # ARABIC COMMA (keep)
    "ھ": "ه",
    "ۀ": "ه" + ZWNJ + "ی",
}

# Harakat / tatweel: strip, they never help a meeting transcript.
_DIACRITICS = re.compile(r"[ً-ْٰـ]")

_ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_LATIN_DIGITS = "0123456789"

_TO_PERSIAN_DIGITS = str.maketrans(_LATIN_DIGITS + _ARABIC_INDIC, _PERSIAN_DIGITS * 2)
_TO_LATIN_DIGITS = str.maketrans(_PERSIAN_DIGITS + _ARABIC_INDIC, _LATIN_DIGITS * 2)

# Affixes that take a ZWNJ rather than a space in correct Persian orthography.
_PREFIXES = ("می", "نمی", "بی")
_SUFFIXES = (
    "ها", "های", "هایی", "هایم", "هایت", "هایش", "هایمان", "هایتان", "هایشان",
    "تر", "تری", "ترین", "ام", "ای", "اید", "ایم", "اند",
)

_RE_PREFIX = re.compile(r"(?<![؀-ۿ])(" + "|".join(_PREFIXES) + r")\s+(?=[؀-ۿ])")
_RE_SUFFIX = re.compile(r"(?<=[؀-ۿ])\s+(" + "|".join(_SUFFIXES) + r")(?![؀-ۿ])")

_RE_SPACE_BEFORE_PUNCT = re.compile(r"\s+([،؛:؟!.,;?)\]}»])")
_RE_SPACE_AFTER_OPEN = re.compile(r"([(\[{«])\s+")
_RE_MISSING_SPACE_AFTER_PUNCT = re.compile(r"([،؛:؟!])(?=[^\s\d])")
_RE_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_RE_MULTI_ZWNJ = re.compile(ZWNJ + "{2,}")
_RE_STRAY_ZWNJ = re.compile(r"(?<![؀-ۿ])" + ZWNJ + r"|" + ZWNJ + r"(?![؀-ۿ])")
_RE_MULTI_NEWLINE = re.compile(r"\n{3,}")

# Whisper's signature failure on silence: the same phrase repeated forever.
_RE_REPEATED_WORD = re.compile(r"\b(\S+)(\s+\1\b){3,}")


def _fix_punctuation_forms(text: str) -> str:
    """Prefer Persian punctuation over the Latin forms Whisper sometimes picks."""
    return text.replace("?", "؟").replace(";", "؛")


def normalize(text: str, *, digits: str = "persian", fix_punctuation: bool = True) -> str:
    """Normalize one chunk of Whisper output.

    digits: "persian" (۱۲۳), "latin" (123) or "keep".
    """
    if not text:
        return ""

    text = unicodedata.normalize("NFC", text)
    for src, dst in _CHAR_MAP.items():
        if src != dst:
            text = text.replace(src, dst)
    text = _DIACRITICS.sub("", text)

    if fix_punctuation:
        text = _fix_punctuation_forms(text)

    text = _RE_PREFIX.sub(r"\1" + ZWNJ, text)
    text = _RE_SUFFIX.sub(ZWNJ + r"\1", text)

    text = _RE_SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _RE_SPACE_AFTER_OPEN.sub(r"\1", text)
    text = _RE_MISSING_SPACE_AFTER_PUNCT.sub(r"\1 ", text)

    text = _RE_MULTI_ZWNJ.sub(ZWNJ, text)
    text = _RE_STRAY_ZWNJ.sub("", text)
    text = _RE_MULTI_SPACE.sub(" ", text)
    text = _RE_MULTI_NEWLINE.sub("\n\n", text)

    if digits == "persian":
        text = text.translate(_TO_PERSIAN_DIGITS)
    elif digits == "latin":
        text = text.translate(_TO_LATIN_DIGITS)

    return text.strip()


def collapse_repetitions(text: str) -> str:
    """Squash `condition_on_previous_text` hallucination loops down to one copy."""
    return _RE_REPEATED_WORD.sub(r"\1", text)


def is_hallucinated(text: str, *, min_len: int = 12) -> bool:
    """Heuristic: a segment that is one token repeated is almost always noise."""
    stripped = text.strip()
    if len(stripped) < min_len:
        return False
    words = stripped.split()
    return len(words) >= 4 and len(set(words)) == 1
