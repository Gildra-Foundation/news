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
