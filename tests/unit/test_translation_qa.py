from __future__ import annotations

from gildranews.application.translation_qa import check_translation


def test_translation_qa_accepts_preserved_facts() -> None:
    result = check_translation(
        "Model scored 67.8% in `bench`. https://example.com/report",
        "Модель набрала 67.8% в `bench`. https://example.com/report",
    )

    assert result.ready_for_editor is True
    assert result.missing_numbers == ()
    assert result.missing_links == ()


def test_translation_qa_rejects_lost_link_number_and_code_span() -> None:
    result = check_translation(
        "Version 5.6 costs $20. Run `tool test`. https://example.com",
        "Новая версия стала дешевле.",
    )

    assert result.ready_for_editor is False
    assert result.missing_numbers == ("5.6", "20")
    assert result.missing_links == ("https://example.com",)
    assert result.code_spans_match is False


def test_translation_qa_detects_lost_duplicate_number() -> None:
    result = check_translation("5.6 быстрее 5.6", "5.6 быстрее")

    assert result.ready_for_editor is False
    assert result.missing_numbers == ("5.6",)
