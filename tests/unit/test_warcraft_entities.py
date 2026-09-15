from __future__ import annotations

from gildranews.domain.models import (
    ResolvedWarcraftEntity,
    TelegramEmojiAsset,
    WarcraftEntityRef,
)


def test_warcraft_reference_supports_branch_kind_and_priority_role() -> None:
    reference = WarcraftEntityRef(
        label="Огненный шар",
        query="Fireball",
        kind="spell",
        branch="classic",
        role="primary",
    )

    assert reference.branch == "classic"
    assert reference.kind == "spell"
    assert reference.role == "primary"


def test_warcraft_reference_supports_expansion_kind() -> None:
    reference = WarcraftEntityRef(
        label="The Last Titan",
        query="The Last Titan",
        kind="expansion",
        role="primary",
    )

    assert reference.kind == "expansion"


def test_resolved_entity_and_telegram_asset_keep_stable_identity() -> None:
    entity = ResolvedWarcraftEntity(
        branch="retail",
        kind="item",
        external_id=19019,
        canonical_name="Thunderfury, Blessed Blade of the Windseeker",
        localized_name="Громовая Ярость",
        page_url="https://www.wowhead.com/item=19019",
        icon_url="https://wow.zamimg.com/icon.jpg",
    )
    asset = TelegramEmojiAsset(
        custom_emoji_id="123",
        file_id="file-123",
        sticker_set_name="gildra_warcraft_retail_01_by_bot",
        fallback="⚔️",
    )

    assert entity.key == "retail:item:19019"
    assert asset.html == '<tg-emoji emoji-id="123">⚔️</tg-emoji>'
