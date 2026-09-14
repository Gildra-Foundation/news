from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?%?")
_LINK_RE = re.compile(r"https?://[^\s)>]+")
_CODE_RE = re.compile(r"`[^`]+`")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
_CYRILLIC_WORD_RE = re.compile(r"[А-Яа-яЁё]+")
_ALLOWED_LATIN_WORDS = frozenset(
    {"blizzard", "forever", "world", "of", "warcraft", "wow"}
)
_BANNED_RUSSIAN_STEMS = (
    "контент",
    "патч",
    "трансмог",
    "левел",
    "бафф",
    "нерф",
    "билд",
    "ивент",
)
_BANNED_SPEC_FORMS = frozenset(
    {"спек", "спека", "спеку", "спеком", "спеке", "спеки", "спеков", "спекам", "спеками", "спеках"}
)


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
    for match in _CYRILLIC_WORD_RE.finditer(text):
        term = match.group(0)
        normalized = term.casefold()
        if normalized in seen:
            continue
        if normalized in _BANNED_SPEC_FORMS or normalized.startswith(_BANNED_RUSSIAN_STEMS):
            seen.add(normalized)
            result.append(term)
    return tuple(result)


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
