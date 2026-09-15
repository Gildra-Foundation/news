from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from gildranews.adapters.sources.rss import RSSItem
from gildranews.application import process_rss
from gildranews.application.warcraft_enrichment import WarcraftEnrichment
from gildranews.config import Config
from gildranews.domain.models import (
    EntityReference,
    FilterResult,
    InfographicFact,
    InfographicSpec,
    TelegramEmojiAsset,
)


class AcceptingNewsAI:
    async def filter_and_rewrite(self, text, recent_posts, emoji_themes):
        assert "Minimap Addon Tech" in text
        assert "wowhead.com" not in text.lower()
        assert recent_posts == [
            {
                "title": "Старая новость",
                "body": "Blizzard уже меняла эту механику.",
                "posted_at": "2026-09-14 10:00:00",
            },
        ]
        return FilterResult(
            is_news=True,
            reason="Изменится работа аддонов",
            title="Blizzard ограничит подсказки на миникарте",
            body="Аддоны больше не смогут менять текстуру компаса внутри подземелий.",
            hashtag="новости",
        )


class FailingNewsAI:
    async def filter_and_rewrite(self, text, recent_posts, emoji_themes):
        raise RuntimeError("temporary outage")


class RaidNewsAI:
    async def filter_and_rewrite(self, text, recent_posts, emoji_themes):
        return FilterResult(
            is_news=True,
            reason="Ослабление рейда",
            title="В «Ядовитой Бездне» дополнительно ослабят боссов",
            body="Урон нескольких механик снизят.",
            hashtag="новости",
            infographic=InfographicSpec(
                title="Ослабления боссов",
                facts=(
                    InfographicFact(value="25%", label="снижение"),
                    InfographicFact(value="8", label="существ за волну"),
                ),
            ),
            references=(
                EntityReference(
                    label="Ядовитой Бездне",
                    query="Venomous Abyss",
                    kind="raid",
                ),
            ),
        )


@pytest.mark.asyncio
async def test_process_rss_item_publishes_without_source_attribution(monkeypatch) -> None:
    published: dict = {}
    recorded: dict = {}

    async def claim_message(channel: str, message_id: int) -> bool:
        return True

    async def recent_context(hours: int, limit: int) -> list[dict[str, str]]:
        assert hours == 48
        assert limit == 50
        return [
            {
                "title": "Старая новость",
                "body": "Blizzard уже меняла эту механику.",
                "posted_at": "2026-09-14 10:00:00",
            },
        ]

    async def publish(
        bot,
        target_channel: str,
        text: str,
        media_files=None,
        *,
        table_rows=None,
    ) -> int:
        published.update(text=text, media_files=media_files, target_channel=target_channel)
        assert table_rows is None
        return 77

    async def download(url, destination_dir, *, kind, allowed_hosts):
        assert allowed_hosts == {"wow.zamimg.com"}
        return destination_dir / ("source.jpg" if kind == "photo" else "source.mp4")

    async def fetch_article_media(url, *, relevance_text=""):
        assert url == "https://www.wowhead.com/news=382863/example"
        assert relevance_text == "Minimap Addon Tech Will Be Disabled"
        return "https://wow.zamimg.com/image.jpg", "https://wow.zamimg.com/clip.mp4"

    async def record_published(channel, message_id, title, body, target_message_id) -> None:
        recorded.update(
            channel=channel,
            message_id=message_id,
            title=title,
            body=body,
            target_message_id=target_message_id,
        )

    monkeypatch.setattr(process_rss.db, "claim_message", claim_message)
    monkeypatch.setattr(process_rss.db, "recent_published_context", recent_context)
    monkeypatch.setattr(process_rss.db, "record_published", record_published)
    monkeypatch.setattr(process_rss.emoji_store, "load", dict)
    monkeypatch.setattr(process_rss.emoji_store, "themes_for_prompt", lambda _emap: [])
    monkeypatch.setattr(process_rss.tg_writer, "publish", publish)
    monkeypatch.setattr(process_rss.media_downloader, "download", download)
    monkeypatch.setattr(process_rss.rss_source, "fetch_article_media", fetch_article_media)

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
        image_url="https://wow.zamimg.com/image.jpg",
    )

    result = await process_rss.process_item(
        bot=object(), cfg=cfg, item=item, content_ai=AcceptingNewsAI(),
    )

    assert result.status == "published"
    assert published["target_channel"] == "@gildrawow"
    assert "wowhead" not in published["text"].lower()
    assert item.article_url not in published["text"]
    assert "Оригинальный пост" not in published["text"]
    assert 'href="https://t.me/gildrawow"' in published["text"]
    assert [kind for _path, kind in published["media_files"]] == ["photo", "video"]
    assert recorded == {
        "channel": "wowhead",
        "message_id": 382863,
        "title": "Blizzard ограничит подсказки на миникарте",
        "body": "Аддоны больше не смогут менять текстуру компаса внутри подземелий.",
        "target_message_id": 77,
    }


@pytest.mark.asyncio
async def test_icy_veins_uses_article_raid_cover_instead_of_infographic(
    monkeypatch,
) -> None:
    published: dict = {}

    async def claim_message(channel: str, message_id: int) -> bool:
        return True

    async def recent_context(hours: int, limit: int) -> list[dict[str, str]]:
        return []

    async def fetch_article_media(url, *, relevance_text=""):
        assert url == "https://www.icy-veins.com/wow/news/raid-tuning/"
        assert relevance_text == "Massive Venomous Abyss Raid Tuning"
        return (
            "https://static.icy-veins.com/wp/venomousabyss-ulatek.webp",
            "",
        )

    async def download(url, destination_dir, *, kind, allowed_hosts):
        assert url.endswith("venomousabyss-ulatek.webp")
        assert kind == "photo"
        assert allowed_hosts == {"static.icy-veins.com"}
        return destination_dir / "raid.webp"

    async def publish(
        bot,
        target_channel: str,
        text: str,
        media_files=None,
        *,
        table_rows=None,
    ) -> int:
        published["media_files"] = media_files
        published["text"] = text
        published["table_rows"] = table_rows
        return 88

    async def enrich(bot, cfg, references, *, publication_text=""):
        assert references[0].query == "Venomous Abyss"
        assert "Ядовитой Бездне" in publication_text
        return WarcraftEnrichment(
            inline_links=(("Ядовитой Бездне", "https://www.wowhead.com/zone=16915"),),
            emojis=(TelegramEmojiAsset("123", "file", "set", "🏰"),),
        )

    async def record_published(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(process_rss.db, "claim_message", claim_message)
    monkeypatch.setattr(process_rss.db, "recent_published_context", recent_context)
    monkeypatch.setattr(process_rss.db, "record_published", record_published)
    monkeypatch.setattr(process_rss.emoji_store, "load", dict)
    monkeypatch.setattr(process_rss.emoji_store, "themes_for_prompt", lambda _emap: [])
    monkeypatch.setattr(process_rss.rss_source, "fetch_article_media", fetch_article_media)
    monkeypatch.setattr(process_rss.media_downloader, "download", download)
    monkeypatch.setattr(process_rss.tg_writer, "publish", publish)
    monkeypatch.setattr(process_rss.warcraft_enrichment, "enrich", enrich)

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
        emoji_autocreate_enabled=True,
    )
    item = RSSItem(
        source="icy-veins",
        external_id=4328696118622736771,
        title="Massive Venomous Abyss Raid Tuning",
        content="Fragments reduced by 25%; eight spawns remain.",
        published_at=datetime.now(UTC),
        article_url="https://www.icy-veins.com/wow/news/raid-tuning/",
        image_url="https://static.icy-veins.com/wp/generic-cover.webp",
    )

    result = await process_rss.process_item(
        bot=object(), cfg=cfg, item=item, content_ai=RaidNewsAI(),
    )

    assert result.status == "published"
    assert len(published["media_files"]) == 1
    media_path, media_kind = published["media_files"][0]
    assert media_path.endswith("/raid.webp")
    assert media_kind == "photo"
    assert (
        '<a href="https://www.wowhead.com/zone=16915">Ядовитой Бездне</a>'
        in published["text"]
    )
    assert published["text"].startswith('<tg-emoji emoji-id="123">🏰</tg-emoji>')
    assert published["table_rows"] == (
        ("снижение", "25%"),
        ("существ за волну", "8"),
    )


@pytest.mark.asyncio
async def test_scrape_do_finds_entity_image_before_infographic_fallback(
    monkeypatch,
) -> None:
    published: dict = {}

    async def claim_message(_channel: str, _message_id: int) -> bool:
        return True

    async def recent_context(hours: int, limit: int) -> list[dict[str, str]]:
        assert (hours, limit) == (48, 50)
        return []

    async def find_image(query: str, *, entity_kind: str = "") -> str:
        assert query == "Venomous Abyss"
        assert entity_kind == "raid"
        return "https://static.icy-veins.com/wp/venomous-abyss-raid.webp"

    async def download(url, destination_dir, *, kind, allowed_hosts):
        assert url.endswith("venomous-abyss-raid.webp")
        assert kind == "photo"
        assert allowed_hosts == {"static.icy-veins.com", "wow.zamimg.com"}
        return destination_dir / "searched-raid.webp"

    async def enrich(*_args, **_kwargs):
        return WarcraftEnrichment()

    async def publish(
        _bot,
        _target_channel,
        text,
        media_files=None,
        *,
        table_rows=None,
    ) -> int:
        published.update(text=text, media_files=media_files, table_rows=table_rows)
        return 89

    async def record_published(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(process_rss.db, "claim_message", claim_message)
    monkeypatch.setattr(process_rss.db, "recent_published_context", recent_context)
    monkeypatch.setattr(process_rss.db, "record_published", record_published)
    monkeypatch.setattr(process_rss.emoji_store, "load", dict)
    monkeypatch.setattr(process_rss.emoji_store, "themes_for_prompt", lambda _emap: [])
    monkeypatch.setattr(process_rss.warcraft_enrichment, "enrich", enrich)
    monkeypatch.setattr(process_rss.scrape_do_images, "find_warcraft_image", find_image)
    monkeypatch.setattr(process_rss.media_downloader, "download", download)
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
        emoji_autocreate_enabled=True,
        scrape_do_enabled=True,
    )
    item = RSSItem(
        source="reddit:wow",
        external_id=12345,
        title="Massive Venomous Abyss Raid Tuning",
        content="Fragments reduced by 25%; eight spawns remain.",
        published_at=datetime.now(UTC),
        article_url="https://www.reddit.com/r/wow/comments/9ix/",
    )

    result = await process_rss.process_item(
        bot=object(), cfg=cfg, item=item, content_ai=RaidNewsAI(),
    )

    assert result.status == "published"
    assert len(published["media_files"]) == 1
    assert published["media_files"][0][0].endswith("/searched-raid.webp")
    assert published["table_rows"] == (
        ("снижение", "25%"),
        ("существ за волну", "8"),
    )


@pytest.mark.asyncio
async def test_process_rss_item_releases_claim_when_ai_is_temporarily_unavailable(
    monkeypatch,
) -> None:
    released: list[tuple[str, int]] = []

    async def claim_message(channel: str, message_id: int) -> bool:
        return True

    async def recent_context(hours: int, limit: int) -> list[dict[str, str]]:
        return []

    async def release_claim(channel: str, message_id: int) -> None:
        released.append((channel, message_id))

    monkeypatch.setattr(process_rss.db, "claim_message", claim_message)
    monkeypatch.setattr(process_rss.db, "recent_published_context", recent_context)
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


@pytest.mark.asyncio
async def test_run_once_processes_only_items_inside_lookback(monkeypatch) -> None:
    now = datetime(2026, 9, 14, 16, 0, tzinfo=UTC)
    recent = RSSItem(
        source="wowhead",
        external_id=2,
        title="Recent",
        content="Recent article body",
        published_at=now - timedelta(minutes=10),
    )
    old = RSSItem(
        source="wowhead",
        external_id=1,
        title="Old",
        content="Old article body",
        published_at=now - timedelta(hours=4),
    )
    processed: list[int] = []
    runs: list[tuple[int, int, int, str | None]] = []

    async def fetch_feed(url: str, *, source: str):
        assert url == "https://www.wowhead.com/news/rss/all"
        assert source == "wowhead"
        return [recent, old]

    async def process_item(**kwargs):
        processed.append(kwargs["item"].external_id)
        return process_rss.ProcessResult(
            "published", "wowhead", kwargs["item"].external_id,
        )

    async def record_run(fetched, selected, published, error) -> None:
        runs.append((fetched, selected, published, error))

    monkeypatch.setattr(process_rss.rss_source, "fetch_feed", fetch_feed)
    monkeypatch.setattr(process_rss, "process_item", process_item)
    monkeypatch.setattr(process_rss.db, "record_run", record_run)

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
        rss_feed_urls=("https://www.wowhead.com/news/rss/all",),
    )

    result = await process_rss.run_once(
        bot=object(), cfg=cfg, content_ai=object(), now=now,
    )

    assert processed == [2]
    assert result == {"fetched": 1, "selected": 1, "published": 1, "error": None}
    assert runs == [(1, 1, 1, None)]
