from gildranews.application.translation_qa import (
    normalize_wow_class_terms,
    untranslated_terms,
)


def test_untranslated_terms_detects_raid_and_ability_names() -> None:
    text = "В Venomous Abyss способность Caustic Claws создаёт Blightscale Spawn."

    assert untranslated_terms(text) == (
        "Venomous",
        "Abyss",
        "Caustic",
        "Claws",
        "Blightscale",
        "Spawn",
    )


def test_untranslated_terms_allows_wow_and_blizzard_names() -> None:
    assert untranslated_terms("Blizzard изменила World of Warcraft и WoW: Forever.") == ()


def test_untranslated_terms_allows_common_russian_gaming_terms() -> None:
    text = "Новый контент патча усилил билд спека после нерфа."

    assert untranslated_terms(text) == ()


def test_normalize_wow_class_terms_distinguishes_mage_from_warlock() -> None:
    assert normalize_wow_class_terms(
        "The Undead Mage and Paladin share an intro scene.",
        "У нежити-паладина и колдуна общая сцена.",
    ) == "У нежити-паладина и мага общая сцена."
    assert normalize_wow_class_terms(
        "The Undead Warlock and Paladin share an intro scene.",
        "У нежити-паладина и колдуна общая сцена.",
    ) == "У нежити-паладина и чернокнижника общая сцена."


def test_normalize_wow_class_terms_preserves_ambiguous_lore_wording() -> None:
    source = "A mage meets a warlock and an unnamed sorcerer."
    translated = "Маг встречает чернокнижника и безымянного колдуна."

    assert normalize_wow_class_terms(source, translated) == translated
