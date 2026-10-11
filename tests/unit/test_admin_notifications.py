from __future__ import annotations

from gildranews.domain.models import ProcessResult
from gildranews.presentation.telegram.notifications import (
    format_admin_notice,
    should_notify_admin,
)


def test_admin_notice_contains_source_status_and_escaped_details() -> None:
    result = ProcessResult(
        status="published",
        channel="source",
        message_id=42,
        title="OpenAI <релиз>",
        reason="A & B",
    )

    notice = format_admin_notice(result)

    assert "✅ <b>Новость опубликована</b>" in notice
    assert '🔗 <b>Источник:</b> <a href="https://t.me/source/42"' in notice
    assert "📌 <b>Заголовок:</b> OpenAI &lt;релиз&gt;" in notice
    assert "📝 <b>Примечание:</b> A &amp; B" in notice


def test_admin_notice_uses_rss_article_url_instead_of_fake_telegram_link() -> None:
    result = ProcessResult(
        status="published",
        channel="wowhead",
        message_id=382863,
        title="Изменения аддонов",
        source_url="https://www.wowhead.com/news=382863/example",
    )

    notice = format_admin_notice(result)

    assert (
        '🔗 <b>Источник:</b> <a href="https://www.wowhead.com/news=382863/example"'
        in notice
    )
    assert "https://t.me/wowhead/382863" not in notice


def test_filtered_notice_explains_that_material_was_skipped() -> None:
    result = ProcessResult(
        status="filtered",
        channel="reddit:wow",
        message_id=123,
        reason="Это обсуждение, а не новая информация",
        source_url="https://reddit.com/r/wow/comments/example",
    )

    notice = format_admin_notice(result)

    assert "ℹ️ <b>Материал пропущен</b>" in notice
    assert "ошибка" not in notice.lower()
    assert "📝 <b>Причина:</b> Это обсуждение, а не новая информация" in notice


def test_ai_error_notice_marks_reason_as_details() -> None:
    result = ProcessResult(
        status="ai_error",
        channel="wowhead",
        message_id=456,
        reason="App Server вернул HTTP 429",
    )

    notice = format_admin_notice(result)

    assert "⚠️ <b>Ошибка обработки ИИ</b>" in notice
    assert "🧾 <b>Подробности:</b> App Server вернул HTTP 429" in notice


def test_confident_typesafe_rejection_does_not_notify_admin() -> None:
    result = ProcessResult(
        status="filtered",
        channel="rss:us.forums.blizzard.com",
        message_id=1,
        reason="TypeSafe: материал не относится к World of Warcraft (релевантность 8%)",
    )

    assert should_notify_admin(result) is False
