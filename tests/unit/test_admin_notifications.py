from __future__ import annotations

from gildranews.domain.models import ProcessResult
from gildranews.presentation.telegram.notifications import format_admin_notice


def test_admin_notice_contains_source_status_and_escaped_details() -> None:
    result = ProcessResult(
        status="published",
        channel="source",
        message_id=42,
        title="OpenAI <релиз>",
        reason="A & B",
    )

    notice = format_admin_notice(result)

    assert "✅ <b>Опубликовано</b>" in notice
    assert 'href="https://t.me/source/42"' in notice
    assert "OpenAI &lt;релиз&gt;" in notice
    assert "A &amp; B" in notice


def test_admin_notice_uses_rss_article_url_instead_of_fake_telegram_link() -> None:
    result = ProcessResult(
        status="published",
        channel="wowhead",
        message_id=382863,
        title="Изменения аддонов",
        source_url="https://www.wowhead.com/news=382863/example",
    )

    notice = format_admin_notice(result)

    assert 'href="https://www.wowhead.com/news=382863/example"' in notice
    assert "https://t.me/wowhead/382863" not in notice
