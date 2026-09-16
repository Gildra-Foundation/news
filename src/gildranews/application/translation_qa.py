from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?%?")
_LINK_RE = re.compile(r"https?://[^\s)>]+")
_CODE_RE = re.compile(r"`[^`]+`")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
_ALLOWED_LATIN_WORDS = frozenset(
    {"blizzard", "classic", "forever", "retail", "world", "of", "warcraft", "wow"}
)
WOW_EXPANSION_NAMES = (
    "The Last Titan",
    "The War Within",
    "Battle for Azeroth",
    "Warlords of Draenor",
    "Mists of Pandaria",
    "Wrath of the Lich King",
    "The Burning Crusade",
    "Dragonflight",
    "Shadowlands",
    "Midnight",
    "Legion",
    "Cataclysm",
)
_EXPANSION_TRANSLATIONS = {
    "The Last Titan": (
        "Последний Титан",
        "Последнего Титана",
        "Последнем Титане",
    ),
    "The War Within": (
        "Война Внутри",
        "Война внутри",
        "Войны внутри",
        "Войне внутри",
    ),
    "Battle for Azeroth": ("Битва за Азерот", "Битве за Азерот"),
    "Warlords of Draenor": (
        "Военачальники Дренора",
        "Военачальниках Дренора",
    ),
    "Mists of Pandaria": ("Туманы Пандарии", "Туманах Пандарии"),
    "Wrath of the Lich King": (
        "Гнев Короля-лича",
        "Гнев Короля Лича",
        "Гневе Короля-лича",
        "Гневе Короля Лича",
    ),
    "The Burning Crusade": (
        "Пылающий крестовый поход",
        "Пылающем крестовом походе",
    ),
    "Dragonflight": ("Драконий полёт", "Драконий полет"),
    "Shadowlands": (
        "Тёмные Земли",
        "Темные Земли",
        "Тёмных Землях",
        "Темных Землях",
    ),
    "Midnight": ("Полночь", "Полуночи"),
    "Legion": ("Легион", "Легионе"),
    "Cataclysm": ("Катаклизм", "Катаклизме"),
}
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
_TITLE_TOKEN_RE = re.compile(
    r"\d+(?:[.,]\d+)?%?|[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*"
)
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
_AUGMENTATION_RE = re.compile(r"\bAugmentation\b", re.IGNORECASE)
_RETRIBUTION_RE = re.compile(r"\bRetribution\b", re.IGNORECASE)
_DEVASTATION_RE = re.compile(r"\bDevastation\b", re.IGNORECASE)
_LEGACY_BRANCH_RE = re.compile(
    r"\b(?:Timewalking|Cataclysm|Wrath of the Lich King|Mists of Pandaria|Classic)\b",
    re.IGNORECASE,
)
_RETAIL_CONTEXT_RE = re.compile(
    r"(?:основн|актуальн)\w*\s+верси\w*(?:\s+(?:игры|WoW|World of Warcraft))?|\bRetail\b",
    re.IGNORECASE,
)
_RESTORATION_DRUID_SOURCE_RE = re.compile(
    r"\b(?:druid\b.{0,160}\brestoration|restoration\b.{0,160}\bdruid)\b",
    re.IGNORECASE | re.DOTALL,
)
_RESTORATION_DRUID_TRANSLATION_RE = re.compile(
    r"(?P<prefix>\bдруид[а-яё]*\b(?:\s+специализаци[а-яё]*)?\s+[«„“\"]?)"
    r"(?P<term>восстановление|восстановления|восстановлению|восстановлением|восстановлении)"
    r"(?P<suffix>[»“”\"]?)",
    re.IGNORECASE,
)
_RESTORATION_DRUID_FORMS = {
    "восстановление": "исцеление",
    "восстановления": "исцеления",
    "восстановлению": "исцелению",
    "восстановлением": "исцелением",
    "восстановлении": "исцелении",
}


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


def normalize_wow_specialization_terms(source: str, translated: str) -> str:
    """Keep class specializations distinct from similarly named abilities."""
    if not _RESTORATION_DRUID_SOURCE_RE.search(source):
        return translated

    def replace(match: re.Match[str]) -> str:
        original = match.group("term")
        replacement = _RESTORATION_DRUID_FORMS[original.casefold()]
        return (
            match.group("prefix")
            + _preserve_case(original, replacement)
            + match.group("suffix")
        )

    return _RESTORATION_DRUID_TRANSLATION_RE.sub(replace, translated)


def normalize_wow_expansion_names(source: str, translated: str) -> str:
    """Keep official English expansion names when they are present in the source."""
    result = translated
    for canonical, translated_forms in _EXPANSION_TRANSLATIONS.items():
        if not re.search(re.escape(canonical), source, flags=re.IGNORECASE):
            continue
        for translated_form in translated_forms:
            result = re.sub(
                re.escape(translated_form),
                canonical,
                result,
                flags=re.IGNORECASE,
            )
    return result


def specialization_issues(source: str, translated: str) -> tuple[str, ...]:
    """Detect ambiguous literal translations of frequently confused WoW specs."""
    text = translated.casefold()
    issues: list[str] = []
    if _AUGMENTATION_RE.search(source) and (
        "насыщател" not in text
        or "пробудител" not in text
        or re.search(r"\bусилени\w*\b", text)
    ):
        issues.append("augmentation_mistranslated")
    if _RETRIBUTION_RE.search(source) and (
        "воздаяни" not in text or "паладин" not in text
    ):
        issues.append("retribution_without_paladin")
    if _DEVASTATION_RE.search(source) and (
        "опустошител" not in text or "пробудител" not in text
    ):
        issues.append("devastation_without_evoker")
    return tuple(issues)


def branch_context_issues(
    source: str,
    published_text: str,
    branch: str,
) -> tuple[str, ...]:
    """Flag posts whose game branch would be ambiguous to an ordinary reader."""
    if branch == "retail":
        if _LEGACY_BRANCH_RE.search(source) and not _RETAIL_CONTEXT_RE.search(
            published_text,
        ):
            return ("retail_branch_not_explained",)
    elif branch == "classic" and "classic" not in published_text.casefold():
        return ("classic_branch_not_explained",)
    elif branch == "forever" and "wow: forever" not in published_text.casefold():
        return ("forever_branch_not_explained",)
    return ()


def untranslated_terms(text: str) -> tuple[str, ...]:
    """Return Latin terms that should have been translated or transliterated."""
    checked_text = _LINK_RE.sub("", _CODE_RE.sub("", text))
    for expansion_name in WOW_EXPANSION_NAMES:
        checked_text = re.sub(
            re.escape(expansion_name), "", checked_text, flags=re.IGNORECASE,
        )
    seen: set[str] = set()
    result: list[str] = []
    for match in _LATIN_WORD_RE.finditer(checked_text):
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


def split_dense_paragraphs(body: str, *, limit: int = 300) -> str:
    """Split dense paragraphs at sentence boundaries without changing their text."""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    result: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= limit:
            result.append(paragraph)
            continue
        sentences = [match.group(0).strip() for match in _SENTENCE_RE.finditer(paragraph)]
        if not sentences or any(len(sentence) > limit for sentence in sentences):
            result.append(paragraph)
            continue
        chunk = ""
        for sentence in sentences:
            candidate = f"{chunk} {sentence}".strip()
            if chunk and len(candidate) > limit:
                result.append(chunk)
                chunk = sentence
            else:
                chunk = candidate
        if chunk:
            result.append(chunk)
    return "\n\n".join(result)


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
    title_words = _TITLE_TOKEN_RE.findall(title)

    if len(title_words) > 9:
        issues.append("title_too_long")
    if len(body) > 650:
        issues.append("body_too_long")
    if clean_title and clean_first.startswith(clean_title):
        issues.append("title_repeated_at_start")
    if any(len(sentence) > 180 for sentence in sentences):
        issues.append("sentence_too_long")
    if any(";" in sentence for sentence in sentences):
        issues.append("sentence_too_complex")
    if any(len(paragraph) > 300 for paragraph in paragraphs):
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
