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
