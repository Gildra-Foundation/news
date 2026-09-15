from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?%?")
_LINK_RE = re.compile(r"https?://[^\s)>]+")
_CODE_RE = re.compile(r"`[^`]+`")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
_ALLOWED_LATIN_WORDS = frozenset(
    {"blizzard", "forever", "world", "of", "warcraft", "wow"}
)
_ARTIFICIAL_STYLE_PHRASES = (
    "важно отметить",
    "стоит отметить",
    "следует отметить",
    "таким образом",
    "в заключение",
    "данный материал",
    "материал отмечает",
    "материал сообщает",
    "открывает новые возможности",
    "это подчёркивает",
    "это подчеркивает",
)
_ARTIFICIAL_STYLE_RE = re.compile(
    "|".join(re.escape(phrase) for phrase in _ARTIFICIAL_STYLE_PHRASES),
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"[^.!?]+(?:[.!?]+|$)")
_MAGE_RE = re.compile(r"(?<![A-Za-z])mage(?![A-Za-z])", re.IGNORECASE)
_WARLOCK_RE = re.compile(r"(?<![A-Za-z])warlock(?![A-Za-z])", re.IGNORECASE)
_SORCERER_FORMS = {
    "колдун": ("маг", "чернокнижник"),
    "колдуна": ("мага", "чернокнижника"),
    "колдуну": ("магу", "чернокнижнику"),
    "колдуном": ("магом", "чернокнижником"),
    "колдуне": ("маге", "чернокнижнике"),
    "колдуны": ("маги", "чернокнижники"),
    "колдунов": ("магов", "чернокнижников"),
    "колдунам": ("магам", "чернокнижникам"),
    "колдунами": ("магами", "чернокнижниками"),
    "колдунах": ("магах", "чернокнижниках"),
}
_SORCERER_RE = re.compile(
    r"\b(" + "|".join(sorted(_SORCERER_FORMS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _preserve_case(source: str, replacement: str) -> str:
    if source.isupper():
        return replacement.upper()
    if source[:1].isupper():
        return replacement.capitalize()
    return replacement


def normalize_wow_class_terms(source: str, translated: str) -> str:
    """Correct the common Mage/Warlock ambiguity using the English source."""
    has_mage = bool(_MAGE_RE.search(source))
    has_warlock = bool(_WARLOCK_RE.search(source))
    if has_mage == has_warlock:
        return translated
    replacement_index = 0 if has_mage else 1

    def replace(match: re.Match[str]) -> str:
        original = match.group(0)
        replacement = _SORCERER_FORMS[original.casefold()][replacement_index]
        return _preserve_case(original, replacement)

    return _SORCERER_RE.sub(replace, translated)


def untranslated_terms(text: str) -> tuple[str, ...]:
    """Return Latin terms that should have been translated or transliterated."""
    seen: set[str] = set()
    result: list[str] = []
    for match in _LATIN_WORD_RE.finditer(_LINK_RE.sub("", _CODE_RE.sub("", text))):
        term = match.group(0)
        normalized = term.casefold()
        if normalized in _ALLOWED_LATIN_WORDS or normalized in seen:
            continue
        seen.add(normalized)
        result.append(term)
    return tuple(result)


def artificial_style_markers(text: str) -> tuple[str, ...]:
    """Return high-confidence editorial clichés that make a post sound generated."""
    return tuple(match.group(0).casefold() for match in _ARTIFICIAL_STYLE_RE.finditer(text))


def presentation_issues(title: str, body: str) -> tuple[str, ...]:
    """Return high-confidence layout problems that warrant one repair pass."""
    issues: list[str] = []
    clean_title = re.sub(r"\W+", " ", title, flags=re.UNICODE).casefold().strip()
    first_sentence = _SENTENCE_RE.match(body.strip())
    clean_first = re.sub(
        r"\W+", " ", first_sentence.group(0) if first_sentence else "",
        flags=re.UNICODE,
    ).casefold().strip()
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    sentences = [match.group(0).strip() for match in _SENTENCE_RE.finditer(body)]

    if len(body) > 750:
        issues.append("body_too_long")
    if clean_title and clean_first.startswith(clean_title):
        issues.append("title_repeated_at_start")
    if any(len(sentence) > 260 for sentence in sentences):
        issues.append("sentence_too_long")
    if any(len(paragraph) > 500 for paragraph in paragraphs):
        issues.append("paragraph_too_dense")
    if len(paragraphs) > 3:
        issues.append("too_many_paragraphs")
    return tuple(issues)


@dataclass(frozen=True, slots=True)
class TranslationQA:
    missing_numbers: tuple[str, ...]
    missing_links: tuple[str, ...]
    code_spans_match: bool
    untranslated_terms: tuple[str, ...] = ()

    @property
    def ready_for_editor(self) -> bool:
        return (
            not self.missing_numbers
            and not self.missing_links
            and self.code_spans_match
            and not self.untranslated_terms
        )


def check_translation(source: str, translated: str) -> TranslationQA:
    """Mechanical fidelity checks adapted from Manacost TranslateTeam."""
    source_numbers = _NUMBER_RE.findall(source)
    target_numbers = _NUMBER_RE.findall(translated)
    source_links = _LINK_RE.findall(source)
    target_links = _LINK_RE.findall(translated)
    def missing(source_values: list[str], target_values: list[str]) -> tuple[str, ...]:
        remaining = Counter(target_values)
        result: list[str] = []
        for value in source_values:
            if remaining[value]:
                remaining[value] -= 1
            else:
                result.append(value)
        return tuple(result)

    return TranslationQA(
        missing_numbers=missing(source_numbers, target_numbers),
        missing_links=missing(source_links, target_links),
        code_spans_match=len(_CODE_RE.findall(source)) == len(_CODE_RE.findall(translated)),
        untranslated_terms=untranslated_terms(translated),
    )
