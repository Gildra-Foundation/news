from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from gildranews.adapters.persistence import sqlite
from gildranews.adapters.warcraft.emoji_registry import TelegramEmojiRegistry
from gildranews.adapters.warcraft.icons import NormalizedIcon
from gildranews.domain.models import ResolvedWarcraftEntity


class FakeBot:
    def __init__(self) -> None:
        self.sets: dict[str, list[SimpleNamespace]] = {}
        self.uploads = 0

    async def get_me(self):
        return SimpleNamespace(username="gildranews_bot")

    async def get_sticker_set(self, name: str):
        stickers = self.sets.get(name)
        return SimpleNamespace(stickers=list(stickers)) if stickers is not None else None

    async def create_new_sticker_set(self, **kwargs):
        self.uploads += 1
        await asyncio.sleep(0)
        self.sets[kwargs["name"]] = [
            SimpleNamespace(custom_emoji_id="emoji-133", file_id="file-133")
        ]
        return True

    async def add_sticker_to_set(self, **kwargs):
        self.uploads += 1
        self.sets[kwargs["name"]].append(
            SimpleNamespace(custom_emoji_id="emoji-next", file_id="file-next")
        )
        return True


def _entity() -> ResolvedWarcraftEntity:
    return ResolvedWarcraftEntity(
        branch="retail",
        kind="spell",
        external_id=133,
        canonical_name="Fireball",
        localized_name="Огненный шар",
        page_url="https://www.wowhead.com/spell=133",
        icon_url="https://wow.zamimg.com/images/wow/icons/large/fireball.jpg",
    )


def _class_entity() -> ResolvedWarcraftEntity:
    return ResolvedWarcraftEntity(
        branch="retail",
        kind="class",
        external_id=8,
        canonical_name="Mage",
        localized_name="Маг",
        page_url="https://www.wowhead.com/class=8/mage",
        icon_url="https://wow.zamimg.com/images/wow/icons/large/classicon_mage.jpg",
    )


def _icon(tmp_path: Path) -> NormalizedIcon:
    path = tmp_path / "icon.webp"
    path.write_bytes(b"webp")
    return NormalizedIcon(path=path, sha256="a" * 64, source_url=_entity().icon_url)


@pytest.mark.asyncio
async def test_registry_creates_set_and_reuses_uploaded_asset(monkeypatch, tmp_path) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    bot = FakeBot()
    registry = TelegramEmojiRegistry(bot, owner_user_id=42, enabled=True)
    try:
        first = await registry.get_or_create(_entity(), _icon(tmp_path), fallback="🔥")
        second = await registry.get_or_create(_entity(), _icon(tmp_path), fallback="🔥")

        assert first is not None
        assert first.custom_emoji_id == "emoji-133"
        assert second == first
        assert bot.uploads == 1
        assert first.sticker_set_name == "gildra_warcraft_retail_01_by_gildranews_bot"
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_registry_concurrent_calls_upload_only_once(monkeypatch, tmp_path) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    bot = FakeBot()
    registry = TelegramEmojiRegistry(bot, owner_user_id=42, enabled=True)
    try:
        first, second = await asyncio.gather(
            registry.get_or_create(_entity(), _icon(tmp_path), fallback="🔥"),
            registry.get_or_create(_entity(), _icon(tmp_path), fallback="🔥"),
        )

        assert first is not None
        assert second == first
        assert bot.uploads == 1
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_registry_does_not_upload_when_disabled(monkeypatch, tmp_path) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    bot = FakeBot()
    try:
        result = await TelegramEmojiRegistry(
            bot, owner_user_id=42, enabled=False,
        ).get_or_create(_entity(), _icon(tmp_path), fallback="🔥")

        assert result is None
        assert bot.uploads == 0
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_registry_places_classes_in_core_set(monkeypatch, tmp_path) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    bot = FakeBot()
    try:
        result = await TelegramEmojiRegistry(
            bot, owner_user_id=42, enabled=True,
        ).get_or_create(_class_entity(), _icon(tmp_path), fallback="⚔️")

        assert result is not None
        assert result.sticker_set_name == "gildra_warcraft_core_01_by_gildranews_bot"
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_registry_rolls_over_before_telegram_set_limit(monkeypatch, tmp_path) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    bot = FakeBot()

    async def count(name: str) -> int:
        return 190 if "_retail_01_" in name else 0

    monkeypatch.setattr(sqlite, "ready_emoji_count_in_set", count)
    try:
        result = await TelegramEmojiRegistry(
            bot, owner_user_id=42, enabled=True,
        ).get_or_create(_entity(), _icon(tmp_path), fallback="🔥")

        assert result is not None
        assert result.sticker_set_name == "gildra_warcraft_retail_02_by_gildranews_bot"
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_registry_records_unexpected_upload_failure(monkeypatch, tmp_path) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    bot = FakeBot()

    async def fail(**kwargs):
        raise RuntimeError("unexpected adapter failure")

    bot.create_new_sticker_set = fail
    try:
        result = await TelegramEmojiRegistry(
            bot, owner_user_id=42, enabled=True,
        ).get_or_create(_entity(), _icon(tmp_path), fallback="🔥")
        row = await sqlite.emoji_asset_by_hash("a" * 64)

        assert result is None
        assert row is not None
        assert row["status"] == "failed"
        assert row["attempts"] == 1
    finally:
        await sqlite.close()
