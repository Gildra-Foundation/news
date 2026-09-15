from __future__ import annotations

import pytest

from gildranews.adapters.persistence import sqlite
from gildranews.domain.models import ResolvedWarcraftEntity


def _entity(branch: str = "retail") -> ResolvedWarcraftEntity:
    return ResolvedWarcraftEntity(
        branch=branch,
        kind="spell",
        external_id=133,
        canonical_name="Fireball",
        localized_name="Огненный шар",
        page_url=f"https://www.wowhead.com/{'classic/' if branch == 'classic' else ''}spell=133",
        icon_url="https://wow.zamimg.com/images/wow/icons/large/fireball.jpg",
    )


@pytest.mark.asyncio
async def test_emoji_registry_reuses_ready_asset_by_content_hash(monkeypatch, tmp_path) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    try:
        entity = _entity()
        await sqlite.upsert_warcraft_entity(entity, icon_sha256="a" * 64)
        await sqlite.upsert_emoji_asset(
            sha256="a" * 64,
            source_url=entity.icon_url,
            local_path="/tmp/icon.webp",
            fallback="🔥",
        )
        await sqlite.mark_emoji_asset_ready(
            "a" * 64,
            custom_emoji_id="emoji-133",
            file_id="file-133",
            sticker_set_name="gildra_warcraft_retail_01_by_bot",
        )

        result = await sqlite.ready_emoji_for_entity(entity)

        assert result is not None
        assert result.custom_emoji_id == "emoji-133"
        assert result.fallback == "🔥"
        assert await sqlite.emoji_uploads_today() == 1
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_emoji_registry_keeps_retail_and_classic_entities_separate(
    monkeypatch, tmp_path,
) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    try:
        await sqlite.upsert_warcraft_entity(_entity("retail"), icon_sha256="a" * 64)
        await sqlite.upsert_warcraft_entity(_entity("classic"), icon_sha256="b" * 64)

        rows = await sqlite.list_warcraft_entities()

        assert {(row["branch"], row["icon_sha256"]) for row in rows} == {
            ("retail", "a" * 64),
            ("classic", "b" * 64),
        }
    finally:
        await sqlite.close()
