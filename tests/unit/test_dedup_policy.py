from __future__ import annotations

from gildranews.domain.dedup import compare_fingerprints
from gildranews.domain.models import EventFingerprint


def _armor_event(**overrides) -> EventFingerprint:
    values = {
        "game_branch": "retail",
        "version": "12.2.5",
        "subject": "ограничения типов брони для обликов",
        "action": "снять ограничения",
        "status": "announced",
        "effective_date": "",
        "scope": ("ткань", "кожа", "кольчуга", "латы"),
        "material_facts": (
            "ограничения оружия сохранятся",
        ),
    }
    values.update(overrides)
    return EventFingerprint(**values)


def test_same_armor_change_with_more_examples_is_duplicate() -> None:
    previous = _armor_event()
    candidate = _armor_event(
        scope=(
            "ткань",
            "кожа",
            "кольчуга",
            "латы",
            "маг с латными наплечниками",
            "воин с тканевым снаряжением",
        ),
    )

    decision = compare_fingerprints(candidate, previous)

    assert decision.is_duplicate is True
    assert decision.is_material_update is False


def test_same_story_with_new_release_date_is_material_update() -> None:
    previous = _armor_event(status="announced", effective_date="")
    candidate = _armor_event(status="scheduled", effective_date="2026-10-01")

    decision = compare_fingerprints(candidate, previous)

    assert decision.is_duplicate is False
    assert decision.is_material_update is True


def test_different_change_in_same_version_is_not_duplicate() -> None:
    previous = _armor_event()
    candidate = EventFingerprint(
        game_branch="retail",
        version="12.2.5",
        subject="урон огненной глыбы",
        action="снизить урон",
        status="live",
        effective_date="",
        scope=(),
        material_facts=("урон снижен на 15%",),
    )

    decision = compare_fingerprints(candidate, previous)

    assert decision.is_duplicate is False
    assert decision.is_material_update is False


def test_story_and_revision_keys_are_stable_across_case_and_spacing() -> None:
    first = _armor_event(subject="  Ограничения   типов БРОНИ для обликов ")
    second = _armor_event(subject="ограничения типов брони для обликов")

    assert first.story_key == second.story_key
    assert first.revision_key == second.revision_key


def test_real_alatar_fixation_paraphrase_is_duplicate() -> None:
    previous = EventFingerprint(
        game_branch="retail",
        version="",
        subject="призрачные фиксации на Извивающемся алтаре",
        action="включить танков в третью волну",
        status="live",
        effective_date="",
        scope=("эпохальная сложность", "рейд"),
        material_facts=(
            "Танки всегда получают фиксацию в третьей волне",
            "В первых двух волнах выбирают по 8 не-танков без повторений",
        ),
    )
    candidate = EventFingerprint(
        game_branch="retail",
        version="12.1",
        subject="Неприятная фиксация на Извивающемся алтаре",
        action="исправить выбор танков",
        status="live",
        effective_date="2026-09-17",
        scope=("рейд «Извивающийся алтарь»", "танки"),
        material_facts=(
            "Неприятная фиксация выбирала танков чаще, чем предусмотрено",
        ),
    )

    decision = compare_fingerprints(candidate, previous)

    assert decision.is_duplicate is True
    assert decision.is_material_update is False


def test_different_alatar_mechanic_is_not_merged() -> None:
    previous = EventFingerprint(
        game_branch="retail",
        version="12.1",
        subject="призрачные фиксации на Извивающемся алтаре",
        action="исправить выбор танков",
        status="live",
        effective_date="",
    )
    candidate = EventFingerprint(
        game_branch="retail",
        version="12.1",
        subject="урон ядовитой волны на Извивающемся алтаре",
        action="снизить урон",
        status="live",
        effective_date="",
    )

    assert compare_fingerprints(candidate, previous).is_duplicate is False


def test_new_numeric_value_in_paraphrased_story_is_material_update() -> None:
    previous = EventFingerprint(
        game_branch="retail",
        version="12.1",
        subject="призрачные фиксации на Извивающемся алтаре",
        action="исправить выбор танков",
        status="live",
        effective_date="",
        material_facts=("Танки получают фиксацию в третьей волне",),
    )
    candidate = EventFingerprint(
        game_branch="retail",
        version="12.1",
        subject="Неприятная фиксация на Извивающемся алтаре",
        action="изменить выбор танков",
        status="live",
        effective_date="",
        material_facts=("Фиксация теперь выбирает 3 танков в третьей волне",),
    )

    decision = compare_fingerprints(candidate, previous)

    assert decision.is_duplicate is False
    assert decision.is_material_update is True
