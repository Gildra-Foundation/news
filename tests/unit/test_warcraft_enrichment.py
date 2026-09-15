from __future__ import annotations

from pathlib import Path

import pytest

from gildranews.application import warcraft_enrichment
from gildranews.config import Config
from gildranews.domain.models import (
    ResolvedWarcraftEntity,
    TelegramEmojiAsset,
    WarcraftEntityRef,
)


def _cfg(tmp_path: Path) -> Config:
    return Config(
        tg_api_id=0,
        tg_api_hash="",
        bot_token="token",
        target_channel="@gildrawow",
        admin_user_id=42,
        gemini_api_key="",
        gemini_model="model",
        lookback_minutes=60,
        interval_minutes=30,
        max_posts_per_run=3,
        emoji_autocreate_enabled=True,
        emoji_icon_dir=str(tmp_path),
    )


@pytest.mark.asyncio
async def test_enricher_prioritizes_primary_entity_and_limits_post_to_two(
    monkeypatch, tmp_path,
) -> None:
    resolved_order: list[str] = []

    async def resolve_entity(reference, http_client=None):
        resolved_order.append(reference.query)
        return ResolvedWarcraftEntity(
            branch=reference.branch,
            kind=reference.kind,
            external_id=len(resolved_order),
            canonical_name=reference.query,
            localized_name=reference.label,
            page_url=f"https://www.wowhead.com/spell={len(resolved_order)}",
            icon_url=f"https://wow.zamimg.com/{len(resolved_order)}.jpg",
        )

    async def normalize(url, destination_dir, http_client=None):
        return type("Icon", (), {
            "path": tmp_path / "icon.webp",
            "sha256": url,
            "source_url": url,
        })()

    class Registry:
        def __init__(self, *args, **kwargs):
            pass

        async def get_or_create(self, entity, icon, fallback):
            return TelegramEmojiAsset(
                custom_emoji_id=str(entity.external_id),
                file_id=f"file-{entity.external_id}",
                sticker_set_name="set",
                fallback=fallback,
            )

    monkeypatch.setattr(warcraft_enrichment.wowhead, "resolve_entity", resolve_entity)
    monkeypatch.setattr(warcraft_enrichment, "fetch_and_normalize_icon", normalize)
    monkeypatch.setattr(warcraft_enrichment, "TelegramEmojiRegistry", Registry)

    result = await warcraft_enrichment.enrich(
        object(),
        _cfg(tmp_path),
        (
            WarcraftEntityRef("Маг", "Mage", "class", role="secondary"),
            WarcraftEntityRef("Огненный шар", "Fireball", "spell", role="primary"),
            WarcraftEntityRef("Посох", "Staff", "item", role="secondary"),
        ),
    )

    assert resolved_order == ["Fireball", "Staff", "Mage"]
    assert [asset.custom_emoji_id for asset in result.emojis] == ["1", "2"]
    assert result.inline_links == (
        ("Огненный шар", "https://www.wowhead.com/spell=1"),
        ("Посох", "https://www.wowhead.com/spell=2"),
    )
    assert [asset.placement_label for asset in result.emojis] == [
        "Огненный шар",
        "Посох",
    ]


@pytest.mark.asyncio
async def test_enricher_keeps_class_emoji_but_does_not_link_class(
    monkeypatch, tmp_path,
) -> None:
    async def resolve_entity(reference, http_client=None):
        return ResolvedWarcraftEntity(
            branch="retail",
            kind="class",
            external_id=8,
            canonical_name="Mage",
            localized_name=reference.label,
            page_url="https://www.wowhead.com/class=8/mage",
            icon_url="https://wow.zamimg.com/classicon_mage.jpg",
        )

    async def normalize(url, destination_dir, http_client=None):
        return type("Icon", (), {
            "path": tmp_path / "icon.webp",
            "sha256": "mage",
            "source_url": url,
        })()

    class Registry:
        def __init__(self, *args, **kwargs):
            pass

        async def get_or_create(self, entity, icon, fallback):
            return TelegramEmojiAsset("8", "file-8", "set", fallback)

    monkeypatch.setattr(warcraft_enrichment.wowhead, "resolve_entity", resolve_entity)
    monkeypatch.setattr(warcraft_enrichment, "fetch_and_normalize_icon", normalize)
    monkeypatch.setattr(warcraft_enrichment, "TelegramEmojiRegistry", Registry)

    result = await warcraft_enrichment.enrich(
        object(),
        _cfg(tmp_path),
        (WarcraftEntityRef("Маги", "Mage", "class", role="secondary"),),
    )

    assert result.inline_links == ()
    assert result.emojis[0].placement_label == "Маги"
