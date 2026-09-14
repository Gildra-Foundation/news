from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gildranews.adapters.sources.x_search import XTopic
from gildranews.application import process_x
from gildranews.config import Config


@pytest.mark.asyncio
async def test_x_run_reviews_top_topics_and_publishes_one(monkeypatch) -> None:
    topics = [
        XTopic(101, "First useful topic", "author1", 200, 20, 10, 2_000),
        XTopic(102, "Most useful topic", "author2", 500, 80, 20, 9_000),
    ]
    processed: list[tuple[str, str]] = []

    async def fetch_top_posts(api_key, query):
        assert api_key == "secret"
        assert '"World of Warcraft"' in query
        assert "since:2026-09-13" in query
        return topics

    async def process_item(**kwargs):
        processed.append((kwargs["item"].content, kwargs["content_kind"]))
        return process_x.ProcessResult(
            "published",
            kwargs["item"].source,
            kwargs["item"].external_id,
        )

    monkeypatch.setattr(process_x.x_source, "fetch_top_posts", fetch_top_posts)
    monkeypatch.setattr(process_x.process_rss, "process_item", process_item)

    cfg = Config(
        tg_api_id=0,
        tg_api_hash="",
        bot_token="token",
        target_channel="@gildrawow",
        admin_user_id=1,
        gemini_api_key="",
        gemini_model="model",
        lookback_minutes=45,
        interval_minutes=30,
        max_posts_per_run=3,
        x_enabled=True,
        x_api_key="secret",
    )

    result = await process_x.run_once(
        bot=object(),
        cfg=cfg,
        content_ai=object(),
        now=datetime(2026, 9, 14, 8, 0, tzinfo=UTC),
    )

    assert processed == [(topics[1].as_feed_item().content, "x_topic")]
    assert result == {"fetched": 2, "reviewed": 1, "published": 1, "error": None}
