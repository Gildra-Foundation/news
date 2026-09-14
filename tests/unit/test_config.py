from __future__ import annotations

import pytest

from gildranews import config


def test_load_allows_bot_api_mode_without_telethon_credentials(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TARGET_CHANNEL", "channel")
    monkeypatch.setenv("AI_PROVIDER", "app_server")
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)

    cfg = config.load()

    assert cfg.tg_api_id == 0
    assert cfg.tg_api_hash == ""
    assert cfg.telegram_reader_enabled is False
    assert cfg.rss_enabled is True
    assert cfg.rss_feed_urls == ("https://www.wowhead.com/news/rss/all",)


def test_load_enables_daily_reddit_topics_with_existing_api_key(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TARGET_CHANNEL", "channel")
    monkeypatch.setenv("AI_PROVIDER", "app_server")
    monkeypatch.setenv("REDDITAPIS_ENABLED", "true")
    monkeypatch.setenv("REDDITAPIS_KEY", "reddit-secret")
    monkeypatch.setenv("REDDIT_SUBREDDITS", "r/worldofwarcraft, competitivewow")
    monkeypatch.setenv("SOCIAL_DISCOVERY_HOURS_UTC", "8,18")
    monkeypatch.setenv("GETXAPI_ENABLED", "true")
    monkeypatch.setenv("GETXAPI_KEY", "x-secret")

    cfg = config.load()

    assert cfg.reddit_enabled is True
    assert cfg.reddit_api_key == "reddit-secret"
    assert cfg.reddit_subreddits == ("worldofwarcraft", "competitivewow")
    assert cfg.social_discovery_hours_utc == (8, 18)
    assert cfg.reddit_max_posts_per_run == 1
    assert cfg.x_enabled is True
    assert cfg.x_api_key == "x-secret"
    assert cfg.x_max_posts_per_run == 1


def test_enabled_reddit_topics_require_api_key(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TARGET_CHANNEL", "channel")
    monkeypatch.setenv("AI_PROVIDER", "app_server")
    monkeypatch.setenv("REDDITAPIS_ENABLED", "true")
    monkeypatch.delenv("REDDITAPIS_KEY", raising=False)

    with pytest.raises(RuntimeError, match="REDDITAPIS_KEY"):
        config.load()


def test_enabled_x_topics_require_api_key(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TARGET_CHANNEL", "channel")
    monkeypatch.setenv("AI_PROVIDER", "app_server")
    monkeypatch.setenv("GETXAPI_ENABLED", "true")
    monkeypatch.delenv("GETXAPI_KEY", raising=False)

    with pytest.raises(RuntimeError, match="GETXAPI_KEY"):
        config.load()


def test_telegram_reader_requires_explicit_enable(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TARGET_CHANNEL", "@channel")
    monkeypatch.setenv("AI_PROVIDER", "app_server")
    monkeypatch.setenv("TG_API_ID", "123")
    monkeypatch.setenv("TG_API_HASH", "api-hash")
    monkeypatch.delenv("TELEGRAM_READER_ENABLED", raising=False)

    assert config.load().telegram_reader_enabled is False

    monkeypatch.setenv("TELEGRAM_READER_ENABLED", "true")

    assert config.load().telegram_reader_enabled is True


def test_enabled_telegram_reader_requires_credentials(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TARGET_CHANNEL", "@channel")
    monkeypatch.setenv("AI_PROVIDER", "app_server")
    monkeypatch.setenv("TELEGRAM_READER_ENABLED", "true")
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)

    with pytest.raises(RuntimeError, match="TG_API_ID и TG_API_HASH"):
        config.load()
