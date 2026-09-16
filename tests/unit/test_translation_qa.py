from gildranews.application.translation_qa import (
    artificial_style_markers,
    normalize_wow_class_terms,
    normalize_wow_expansion_names,
    normalize_wow_ptr_terms,
    normalize_wow_specialization_terms,
    presentation_issues,
    specialization_issues,
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
    assert untranslated_terms(
        "Blizzard изменила World of Warcraft, Retail, Classic и WoW: Forever.",
    ) == ()


def test_untranslated_terms_allows_common_russian_gaming_terms() -> None:
    text = "Новый контент патча усилил билд спека после нерфа."

    assert untranslated_terms(text) == ()


def test_expansion_names_stay_official_and_are_not_reported_as_anglicisms() -> None:
    source = "The Last Titan will conclude the Worldsoul Saga."
    translated = "Последний Титан завершит Сагу души мира."

    normalized = normalize_wow_expansion_names(source, translated)

    assert normalized == "The Last Titan завершит Сагу души мира."
    assert untranslated_terms(normalized) == ()


def test_other_translated_expansion_names_are_restored_from_source() -> None:
    source = "Midnight follows The War Within, after Dragonflight and Shadowlands."
    translated = (
        "Полночь выйдет после Войны внутри, а до них были Драконий полёт "
        "и Тёмные Земли."
    )

    assert normalize_wow_expansion_names(source, translated) == (
        "Midnight выйдет после The War Within, а до них были Dragonflight "
        "и Shadowlands."
    )


def test_normalize_wow_ptr_terms_keeps_the_official_abbreviation() -> None:
    source = "Class changes are now available on the 12.1.5 PTR."

    assert normalize_wow_ptr_terms(
        source,
        "Изменения появились в тестовом игровом мире.",
    ) == "Изменения появились на PTR."
    assert untranslated_terms("Изменения появились на PTR.") == ()


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


def test_normalize_wow_specialization_terms_separates_druid_from_spell() -> None:
    source = (
        "DRUID\nRestoration\nNature's Bounty causes Regrowth to heal allies."
    )
    translated = (
        "У друида «Восстановления» талант меняет способность "
        "«Восстановление»."
    )

    assert normalize_wow_specialization_terms(source, translated) == (
        "У друида «Исцеления» талант меняет способность "
        "«Восстановление»."
    )


def test_specialization_issues_detects_ambiguous_augmentation_translation() -> None:
    source = (
        "Augmentation rises seven spots. Retribution gains 50k logs and "
        "Devastation rises four spots."
    )
    translated = (
        "Усиление поднялось на семь мест. Воздаяние стало популярнее, "
        "а Опустошение поднялось на четыре позиции."
    )

    assert specialization_issues(source, translated) == (
        "augmentation_mistranslated",
        "retribution_without_paladin",
        "devastation_without_evoker",
    )


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


def test_presentation_issues_rejects_hard_to_read_sentence() -> None:
    body = "Изменение " + "затронет игроков во всех режимах " * 6 + "."

    assert "sentence_too_long" in presentation_issues("Новое изменение", body)


def test_presentation_issues_rejects_semicolon_clause_chain() -> None:
    body = (
        "На старте дадут 16 очков; позже Blizzard добавит новые деревья."
    )

    assert presentation_issues("Система наследия", body) == (
        "sentence_too_complex",
    )


def test_presentation_issues_rejects_single_overloaded_paragraph() -> None:
    body = " ".join("Каждая фраза сообщает новый факт." for _ in range(10))

    assert presentation_issues("Изменения босса", body) == (
        "paragraph_too_dense",
    )


def test_presentation_issues_rejects_overloaded_title() -> None:
    title = "Blizzard объявила новые изменения для всех классов в следующем обновлении"

    assert presentation_issues(title, "Изменения выйдут на следующей неделе.") == (
        "title_too_long",
    )


def test_presentation_issues_counts_decimal_percentage_as_one_title_word() -> None:
    title = "90,46% эпохальных ключей закрыли вовремя на третьей неделе сезона"

    assert presentation_issues(title, "Доля успешных прохождений выросла.") == ()
