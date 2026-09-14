from __future__ import annotations

from gildranews.domain.models import FilterResult, ProcessResult, Rewrite


def test_text_processing_models_are_value_objects() -> None:
    rewrite = Rewrite(title="Заголовок", body="Текст", hashtag="новости")
    decision = FilterResult(
        is_news=True,
        reason="важная новость",
        title=rewrite.title,
        body=rewrite.body,
        hashtag=rewrite.hashtag,
    )

    assert rewrite == Rewrite(title="Заголовок", body="Текст", hashtag="новости")
    assert decision.title == rewrite.title


def test_process_result_keeps_pipeline_outcome() -> None:
    result = ProcessResult(
        status="published",
        channel="source",
        message_id=42,
        title="Заголовок",
    )

    assert result.status == "published"
    assert result.message_id == 42
