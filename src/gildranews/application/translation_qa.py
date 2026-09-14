from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?%?")
_LINK_RE = re.compile(r"https?://[^\s)>]+")
_CODE_RE = re.compile(r"`[^`]+`")


@dataclass(frozen=True, slots=True)
class TranslationQA:
    missing_numbers: tuple[str, ...]
    missing_links: tuple[str, ...]
    code_spans_match: bool

    @property
    def ready_for_editor(self) -> bool:
        return not self.missing_numbers and not self.missing_links and self.code_spans_match


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
    )
