from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path

import httpx
from aiogram import Bot

from gildranews.adapters.references import wowhead
from gildranews.adapters.warcraft.emoji_registry import (
    TelegramEmojiRegistry,
    pick_fallback,
)
from gildranews.adapters.warcraft.icons import IconError, fetch_and_normalize_icon
from gildranews.config import Config
from gildranews.domain.models import TelegramEmojiAsset, WarcraftEntityRef

log = logging.getLogger(__name__)

EXPANSION_EMOJI_IDS = {
    "the last titan": "5283041899882523483",
    "wow: forever": "5280580484189953856",
}
_WOW_FOREVER_RE = re.compile(r"\bWoW\s*:?\s*Forever\b", re.IGNORECASE)

_KIND_PRIORITY = {
    "expansion": -1,
    "spell": 0,
    "talent": 1,
    "item": 2,
    "boss": 3,
    "class": 4,
    "specialization": 5,
    "raid": 6,
    "dungeon": 7,
}


@dataclass(frozen=True, slots=True)
class WarcraftEnrichment:
    inline_links: tuple[tuple[str, str], ...] = ()
    emojis: tuple[TelegramEmojiAsset, ...] = ()


def _premium_or_brand_fallback(
    emojis: list[TelegramEmojiAsset],
    cfg: Config,
) -> tuple[TelegramEmojiAsset, ...]:
    if any(asset.custom_emoji_id.isdigit() for asset in emojis):
        return tuple(emojis)
    if cfg.subscribe_emoji_id.isdigit():
        return (
            TelegramEmojiAsset(
                custom_emoji_id=cfg.subscribe_emoji_id,
                file_id="",
                sticker_set_name="gildra_brand",
                fallback="🛡️",
            ),
        )
    return tuple(emojis)


async def enrich(
    bot: Bot,
    cfg: Config,
    references: tuple[WarcraftEntityRef, ...],
    *,
    publication_text: str = "",
) -> WarcraftEnrichment:
    emojis: list[TelegramEmojiAsset] = []
    if match := _WOW_FOREVER_RE.search(publication_text):
        emoji_id = EXPANSION_EMOJI_IDS.get("wow: forever", "")
        if emoji_id.isdigit():
            emojis.append(
                TelegramEmojiAsset(
                    custom_emoji_id=emoji_id,
                    file_id="",
                    sticker_set_name="gildra_warcraft_expansions",
                    fallback="🎮",
                    placement_label=match.group(0),
                )
            )
    if not references:
        return WarcraftEnrichment(emojis=_premium_or_brand_fallback(emojis, cfg))
    ordered = sorted(
        references[:3],
        key=lambda ref: (
            0 if ref.role == "primary" else 1,
            _KIND_PRIORITY.get(ref.kind, 20),
        ),
    )
    registry = TelegramEmojiRegistry(
        bot,
        owner_user_id=cfg.admin_user_id,
        enabled=cfg.emoji_autocreate_enabled,
        set_prefix=cfg.emoji_set_prefix,
        daily_upload_limit=cfg.emoji_max_new_per_day,
        upload_timeout_seconds=cfg.emoji_upload_timeout_seconds,
    )
    icon_dir = Path(cfg.emoji_icon_dir)
    links: list[tuple[str, str]] = []
    async with httpx.AsyncClient(
        timeout=20,
        headers={"User-Agent": "GildraNews/0.1 Warcraft enrichment"},
    ) as client:
        for reference in ordered:
            if reference.kind == "expansion":
                emoji_id = EXPANSION_EMOJI_IDS.get(reference.query.casefold().strip(), "")
                if (
                    emoji_id.isdigit()
                    and len(emojis) < 2
                    and all(asset.custom_emoji_id != emoji_id for asset in emojis)
                ):
                    emojis.append(
                        TelegramEmojiAsset(
                            custom_emoji_id=emoji_id,
                            file_id="",
                            sticker_set_name="gildra_warcraft_expansions",
                            fallback="🎮",
                            placement_label=reference.label.strip(),
                        )
                    )
                continue
            try:
                entity = await wowhead.resolve_entity(reference, http_client=client)
            except Exception:
                log.warning("Warcraft entity lookup failed for %s", reference.query, exc_info=True)
                continue
            if entity is None:
                continue
            if reference.kind not in {"class", "specialization"}:
                links.append((reference.label, entity.page_url))
            if len(emojis) >= 2 or not entity.icon_url:
                continue
            try:
                icon = await fetch_and_normalize_icon(
                    entity.icon_url,
                    icon_dir,
                    http_client=client,
                )
                asset = await registry.get_or_create(
                    entity,
                    icon,
                    fallback=pick_fallback(entity.kind),
                )
            except (IconError, OSError, httpx.HTTPError):
                log.warning("Warcraft icon enrichment failed for %s", entity.key, exc_info=True)
                continue
            if asset is None:
                asset = TelegramEmojiAsset(
                    custom_emoji_id="",
                    file_id="",
                    sticker_set_name="",
                    fallback=pick_fallback(entity.kind),
                )
            emojis.append(replace(asset, placement_label=reference.label.strip()))
    return WarcraftEnrichment(tuple(links), _premium_or_brand_fallback(emojis, cfg))
