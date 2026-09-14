from gildranews.application.translation_qa import untranslated_terms


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


def test_untranslated_terms_detects_avoidable_russian_loanwords() -> None:
    text = "Новый контент патча усилил билд спека после нерфа."

    assert untranslated_terms(text) == (
        "контент",
        "патча",
        "билд",
        "спека",
        "нерфа",
    )
