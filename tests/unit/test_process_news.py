from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gildranews.adapters.sources.telegram import FetchedPost
from gildranews.application import process_news
from gildranews.config import Config
from gildranews.domain.models import FilterResult


class RejectingNewsFilter:
    async def filter_and_rewrite(self, text, recent_posts, emoji_themes):
        assert text == "Исходный текст"
        assert recent_posts == [
            {
                "title": "Старая новость",
                "body": "Старый текст",
                "posted_at": "2026-09-14 10:00:00",
            },
        ]
        assert emoji_themes == [{"key": "release", "desc": "Релиз"}]
        return FilterResult(is_news=False, reason="не подходит аудитории")


@pytest.mark.asyncio
async def test_process_post_uses_injected_news_filter(monkeypatch) -> None:
    async def claim_message(channel: str, message_id: int) -> bool:
        return True

    async def recent_context(hours: int, limit: int) -> list[dict[str, str]]:
        assert hours == 48
        assert limit == 50
        return [
            {
                "title": "Старая новость",
                "body": "Старый текст",
                "posted_at": "2026-09-14 10:00:00",
            },
        ]

    monkeypatch.setattr(process_news.db, "claim_message", claim_message)
    monkeypatch.setattr(process_news.db, "recent_published_context", recent_context)
    monkeypatch.setattr(
        process_news.emoji_store,
        "load",
        lambda: {"release": {"id": "1", "fallback": "🚀", "desc": "Релиз"}},
    )

    cfg = Config(
        tg_api_id=1,
        tg_api_hash="hash",
        bot_token="token",
        target_channel="@channel",
        admin_user_id=1,
        gemini_api_key="legacy-key",
        gemini_model="legacy-model",
        lookback_minutes=45,
        interval_minutes=30,
        max_posts_per_run=3,
    )
    post = FetchedPost(
        channel="source",
        message_id=42,
        text="Исходный текст",
        date=datetime.now(UTC),
    )

    result = await process_news.process_post(
        client=object(),
        bot=object(),
        cfg=cfg,
        post=post,
        news_filter=RejectingNewsFilter(),
    )

    assert result == process_news.ProcessResult(
        status="filtered",
        channel="source",
        message_id=42,
        reason="не подходит аудитории",
    )
