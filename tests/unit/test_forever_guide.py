from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from gildranews.application import forever_guide
from gildranews.config import Config


def _config() -> Config:
    return Config(
        tg_api_id=1,
        tg_api_hash="hash",
        bot_token="token",
        target_channel="@gildrawow",
        admin_user_id=1,
        gemini_api_key="",
        gemini_model="model",
        lookback_minutes=45,
        interval_minutes=30,
        max_posts_per_run=3,
        forever_guide_message_id=44,
        forever_release_date=date(2026, 11, 4),
    )


def test_build_guide_adds_new_internal_links_and_escapes_titles() -> None:
    text = forever_guide.build_guide_text(
        [
            {"title": "Новый рейд <опасно>", "target_message_id": 48},
            {"title": "Новая зона", "target_message_id": 51},
        ],
        forever_emoji_id="5280580484189953856",
        subscribe_emoji_id="5280756831252167912",
    )

    assert 'href="https://t.me/gildrawow/48"' in text
    assert "Новый рейд &lt;опасно&gt;" in text
    assert 'href="https://t.me/gildrawow/51"' in text
    assert '<tg-emoji emoji-id="5280580484189953856">' in text
    assert len(text) <= forever_guide.MAX_CAPTION_CHARS


@pytest.mark.asyncio
async def test_refresh_edits_pinned_guide_and_remembers_rendered_version(
    monkeypatch,
) -> None:
    edits: list[tuple[int, str]] = []
    states: list[tuple[str, str, str]] = []

    async def posts(*, after_message_id: int, search: str, limit: int):
        assert (after_message_id, search, limit) == (44, "WoW: Forever", 30)
        return [{"title": "Свежая новость", "target_message_id": 50}]

    async def emoji_id(name: str):
        assert name == "WoW: Forever"
        return "5280580484189953856"

    async def state(_key: str):
        return None

    async def save_state(key: str, value: str, detail: str = ""):
        states.append((key, value, detail))

    async def edit(client, channel: str, message_id: int, text: str):
        assert client is CLIENT
        assert channel == "@gildrawow"
        edits.append((message_id, text))
        return True

    CLIENT = object()
    monkeypatch.setattr(forever_guide.db, "published_posts_after", posts)
    monkeypatch.setattr(forever_guide.db, "ready_custom_emoji_id", emoji_id)
    monkeypatch.setattr(forever_guide.db, "get_service_state", state)
    monkeypatch.setattr(forever_guide.db, "set_service_state", save_state)
    monkeypatch.setattr(forever_guide.mtproto, "edit", edit)

    result = await forever_guide.refresh(
        CLIENT,
        _config(),
        now=datetime(2026, 9, 16, 8, 0, tzinfo=UTC),
    )

    assert result == {"updated": True, "links": 1, "reason": ""}
    assert edits[0][0] == 44
    assert "Свежая новость" in edits[0][1]
    assert states[0][0] == forever_guide.STATE_KEY


@pytest.mark.asyncio
async def test_refresh_stops_on_release_day(monkeypatch) -> None:
    async def unexpected(*args, **kwargs):
        raise AssertionError("Telegram and SQLite must not be touched after release")

    monkeypatch.setattr(forever_guide.db, "published_posts_after", unexpected)
    monkeypatch.setattr(forever_guide.mtproto, "edit", unexpected)

    result = await forever_guide.refresh(
        object(),
        _config(),
        now=datetime(2026, 11, 4, 0, 0, tzinfo=UTC),
    )

    assert result == {"updated": False, "links": 0, "reason": "release_reached"}
