from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gildranews.adapters.sources.reddit import RedditTopic
from gildranews.application import process_reddit
from gildranews.config import Config


@pytest.mark.asyncio
async def test_daily_reddit_run_asks_ai_for_best_topics_and_publishes_one(
    monkeypatch,
) -> None:
    topics = {
        "worldofwarcraft": [
            RedditTopic("aaa111", "Useful guide", "Details", "worldofwarcraft", 300, 20),
        ],
        "competitivewow": [
            RedditTopic("bbb222", "Important discovery", "More details", "competitivewow", 500, 80),
        ],
    }
    processed: list[tuple[str, str]] = []

    async def fetch_top_posts(api_key, subreddit, *, limit):
        assert api_key == "secret"
        assert limit == 15
        return topics[subreddit]

    async def process_item(**kwargs):
        assert kwargs["quota_source"] == "reddit"
        assert kwargs["quota_day"].isoformat() == "2026-09-15"
        assert kwargs["quota_limit"] == 2
        processed.append((kwargs["item"].title, kwargs["content_kind"]))
        return process_reddit.ProcessResult(
            "published",
            kwargs["item"].source,
            kwargs["item"].external_id,
        )

    monkeypatch.setattr(process_reddit.reddit_source, "fetch_top_posts", fetch_top_posts)
    monkeypatch.setattr(process_reddit.process_rss, "process_item", process_item)

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
        reddit_enabled=True,
        reddit_api_key="secret",
        reddit_subreddits=("worldofwarcraft", "competitivewow"),
    )

    result = await process_reddit.run_once(
        bot=object(),
        cfg=cfg,
        content_ai=object(),
        now=datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
    )

    assert processed == [("Important discovery", "reddit_topic")]
    assert result == {"fetched": 2, "reviewed": 1, "published": 1, "error": None}
