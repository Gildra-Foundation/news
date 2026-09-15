from gildranews.application.translation_qa import (
    artificial_style_markers,
    normalize_wow_class_terms,
    presentation_issues,
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


def test_artificial_style_markers_detects_ai_editorial_cliches() -> None:
    text = (
        "Важно отметить, что данный материал открывает новые возможности. "
        "Таким образом, игроки получат больше вариантов."
    )

    assert artificial_style_markers(text) == (
        "важно отметить",
        "данный материал",
        "открывает новые возможности",
        "таким образом",
    )


def test_presentation_issues_detects_verbatim_title_and_dense_body() -> None:
    title = "Blizzard ослабила урон босса"
    body = title + ". " + ("Очень длинное предложение без полезной паузы " * 20)

    assert presentation_issues(title, body) == (
        "body_too_long",
        "title_repeated_at_start",
        "sentence_too_long",
        "paragraph_too_dense",
    )
