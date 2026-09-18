from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from gildranews.domain.models import EventFingerprint


@dataclass(frozen=True, slots=True)
class DedupDecision:
    is_duplicate: bool
    is_material_update: bool
    reason: str


_WORD_RE = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?%?")
_STOP_WORDS = {
    "без", "более", "бы", "был", "была", "были", "было", "в", "во", "для",
    "до", "же", "за", "и", "из", "или", "к", "как", "на", "не", "но", "о",
    "от", "по", "под", "при", "с", "со", "то", "у", "чем", "это",
}
_RU_SUFFIXES = (
    "ющими", "ющего", "ющему", "ющемся", "ениями", "аниями", "иями", "ями",
    "ами", "ение", "ания", "ого", "ему", "ому", "ыми", "ими", "иях", "ах",
    "ях", "ов", "ев", "ей", "ам", "ям", "ом", "ем", "ый", "ий", "ой", "ая",
    "яя", "ое", "ее", "ые", "ие", "ую", "юю", "ы", "и", "а", "я", "у", "ю",
    "е", "о", "ь",
)


def _stem_word(word: str) -> str:
    if len(word) <= 4 or not any("а" <= char <= "я" for char in word):
        return word
    for suffix in _RU_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return {
        _stem_word(word)
        for word in _WORD_RE.findall(normalized)
        if word not in _STOP_WORDS and len(word) > 2
    }


def _same_story(candidate: EventFingerprint, previous: EventFingerprint) -> bool:
    if candidate.story_key == previous.story_key:
        return True
    candidate_data = candidate.canonical()
    previous_data = previous.canonical()
    if candidate_data["game_branch"] != previous_data["game_branch"]:
        return False
    candidate_version = str(candidate_data["version"])
    previous_version = str(previous_data["version"])
    if candidate_version and previous_version and candidate_version != previous_version:
        return False
    candidate_subject = _tokens(str(candidate_data["subject"]))
    previous_subject = _tokens(str(previous_data["subject"]))
    if not candidate_subject or not previous_subject:
        return False
    shared = candidate_subject & previous_subject
    overlap = len(shared) / min(len(candidate_subject), len(previous_subject))
    return len(shared) >= 3 and overlap >= 0.6


def _fact_is_covered(candidate_fact: str, previous_facts: list[str]) -> bool:
    candidate_tokens = _tokens(candidate_fact)
    candidate_numbers = set(_NUMBER_RE.findall(candidate_fact))
    for previous_fact in previous_facts:
        previous_numbers = set(_NUMBER_RE.findall(previous_fact))
        if candidate_numbers - previous_numbers:
            continue
        previous_tokens = _tokens(previous_fact)
        if not candidate_tokens or not previous_tokens:
            continue
        shared = candidate_tokens & previous_tokens
        overlap = len(shared) / min(len(candidate_tokens), len(previous_tokens))
        if len(shared) >= 2 and overlap >= 0.3:
            return True
    return False


def compare_fingerprints(
    candidate: EventFingerprint,
    previous: EventFingerprint,
) -> DedupDecision:
    """Apply the deterministic part of the one-story publication policy."""
    if not _same_story(candidate, previous):
        return DedupDecision(False, False, "Другой сюжет")

    candidate_data = candidate.canonical()
    previous_data = previous.canonical()
    status_changed = bool(candidate_data["status"] and previous_data["status"]) and (
        candidate_data["status"] != previous_data["status"]
    )
    date_changed = bool(
        candidate_data["effective_date"] and previous_data["effective_date"]
    ) and (
        candidate_data["effective_date"] != previous_data["effective_date"]
    )
    previous_facts = [str(value) for value in previous_data["material_facts"]]
    new_facts = {
        str(fact)
        for fact in candidate_data["material_facts"]
        if str(fact) not in previous_facts
        and not _fact_is_covered(str(fact), previous_facts)
    }
    if status_changed or date_changed or new_facts:
        return DedupDecision(False, True, "Есть существенное развитие сюжета")
    return DedupDecision(True, False, "Тот же сюжет без новых существенных фактов")
