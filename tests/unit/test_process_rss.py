from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gildranews.adapters.sources.rss import RSSItem
from gildranews.application import process_rss
from gildranews.config import Config
from gildranews.domain.models import FilterResult


class AcceptingNewsAI:
    async def filter_and_rewrite(self, text, recent_titles, emoji_themes):
        assert "Minimap Addon Tech" in text
        assert "wowhead.com" not in text.lower()
        assert recent_titles == ["Старая новость"]
        return FilterResult(
            is_news=True,
            reason="Изменится работа аддонов",
            title="Blizzard ограничит подсказки на миникарте",
            body="Аддоны больше не смогут менять текстуру компаса внутри подземелий.",
            hashtag="новости",
        )


class FailingNewsAI:
    async def filter_and_rewrite(self, text, recent_titles, emoji_themes):
        raise RuntimeError("temporary outage")


@pytest.mark.asyncio
async def test_process_rss_item_publishes_without_source_attribution(monkeypatch) -> None:
    published: dict = {}
    recorded: dict = {}

    async def claim_message(channel: str, message_id: int) -> bool:
        return True

    async def recent_titles(hours: int, limit: int) -> list[str]:
        return ["Старая новость"]

    async def publish(bot, target_channel: str, text: str, media_files=None) -> int:
        published.update(text=text, media_files=media_files, target_channel=target_channel)
        return 77

    async def record_published(channel, message_id, title, body, target_message_id) -> None:
        recorded.update(
            channel=channel,
            message_id=message_id,
            title=title,
            body=body,
            target_message_id=target_message_id,
        )

    monkeypatch.setattr(process_rss.db, "claim_message", claim_message)
    monkeypatch.setattr(process_rss.db, "recent_published_titles", recent_titles)
    monkeypatch.setattr(process_rss.db, "record_published", record_published)
    monkeypatch.setattr(process_rss.emoji_store, "load", dict)
    monkeypatch.setattr(process_rss.emoji_store, "themes_for_prompt", lambda _emap: [])
    monkeypatch.setattr(process_rss.tg_writer, "publish", publish)

    cfg = Config(
        tg_api_id=0,
        tg_api_hash="",
        bot_token="token",
        target_channel="@gildrawow",
        admin_user_id=1,
        gemini_api_key="",
        gemini_model="model",
        lookback_minutes=120,
        interval_minutes=30,
        max_posts_per_run=3,
    )
    item = RSSItem(
        source="wowhead",
        external_id=382863,
        title="Minimap Addon Tech Will Be Disabled",
        content="Blizzard will disable custom minimap assets.",
        published_at=datetime.now(UTC),
        article_url="https://www.wowhead.com/news=382863/example",
    )

    result = await process_rss.process_item(
        bot=object(), cfg=cfg, item=item, content_ai=AcceptingNewsAI(),
    )

    assert result.status == "published"
    assert published["target_channel"] == "@gildrawow"
    assert "wowhead" not in published["text"].lower()
    assert "https://" not in published["text"]
    assert recorded == {
        "channel": "wowhead",
        "message_id": 382863,
        "title": "Blizzard ограничит подсказки на миникарте",
        "body": "Аддоны больше не смогут менять текстуру компаса внутри подземелий.",
        "target_message_id": 77,
    }


@pytest.mark.asyncio
async def test_process_rss_item_releases_claim_when_ai_is_temporarily_unavailable(
    monkeypatch,
) -> None:
    released: list[tuple[str, int]] = []

    async def claim_message(channel: str, message_id: int) -> bool:
        return True

    async def recent_titles(hours: int, limit: int) -> list[str]:
        return []

    async def release_claim(channel: str, message_id: int) -> None:
        released.append((channel, message_id))

    monkeypatch.setattr(process_rss.db, "claim_message", claim_message)
    monkeypatch.setattr(process_rss.db, "recent_published_titles", recent_titles)
    monkeypatch.setattr(process_rss.db, "release_claim", release_claim)
    monkeypatch.setattr(process_rss.emoji_store, "load", dict)
    monkeypatch.setattr(process_rss.emoji_store, "themes_for_prompt", lambda _emap: [])

    cfg = Config(
        tg_api_id=0,
        tg_api_hash="",
        bot_token="token",
        target_channel="@gildrawow",
        admin_user_id=1,
        gemini_api_key="",
        gemini_model="model",
        lookback_minutes=120,
        interval_minutes=30,
        max_posts_per_run=3,
    )
    item = RSSItem(
        source="wowhead",
        external_id=382863,
        title="Minimap Addon Tech Will Be Disabled",
        content="Blizzard will disable custom minimap assets.",
        published_at=datetime.now(UTC),
    )

    result = await process_rss.process_item(
        bot=object(), cfg=cfg, item=item, content_ai=FailingNewsAI(),
    )

    assert result.status == "ai_error"
    assert released == [("wowhead", 382863)]
