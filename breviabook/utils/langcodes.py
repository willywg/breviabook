"""Map human language names (CLI ``--to Spanish``) to BCP-47 primary tags for EPUB/PDF metadata."""

from __future__ import annotations

import re

# Common ebook / CLI language names → ISO 639-1 (BCP-47 primary subtag).
_NAME_TO_CODE: dict[str, str] = {
    "english": "en",
    "spanish": "es",
    "español": "es",
    "espanol": "es",
    "french": "fr",
    "français": "fr",
    "francais": "fr",
    "german": "de",
    "deutsch": "de",
    "portuguese": "pt",
    "português": "pt",
    "portugues": "pt",
    "italian": "it",
    "italiano": "it",
    "dutch": "nl",
    "nederlands": "nl",
    "japanese": "ja",
    "chinese": "zh",
    "korean": "ko",
    "russian": "ru",
    "arabic": "ar",
    "hindi": "hi",
    "polish": "pl",
    "turkish": "tr",
    "swedish": "sv",
    "norwegian": "no",
    "danish": "da",
    "finnish": "fi",
    "greek": "el",
    "hebrew": "he",
    "czech": "cs",
    "romanian": "ro",
    "hungarian": "hu",
    "thai": "th",
    "vietnamese": "vi",
    "indonesian": "id",
    "catalan": "ca",
    "ukrainian": "uk",
}

_BCP47_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


def to_bcp47(lang: str) -> str:
    """Return a BCP-47 language tag for ``lang`` (name or code).

    ``"Spanish"`` → ``"es"``; ``"es-MX"`` → ``"es-MX"`` (primary lowercased). Unknown names
    that are not code-shaped return ``"und"``.
    """
    raw = lang.strip()
    if not raw:
        return "und"
    keyed = raw.lower().replace("_", "-")
    if keyed in _NAME_TO_CODE:
        return _NAME_TO_CODE[keyed]
    # First token only (e.g. "Spanish (Latin America)").
    first = keyed.split()[0].strip("()")
    if first in _NAME_TO_CODE:
        return _NAME_TO_CODE[first]
    if _BCP47_RE.fullmatch(raw.replace("_", "-")):
        parts = raw.replace("_", "-").split("-")
        return "-".join([parts[0].lower(), *parts[1:]])
    return "und"
