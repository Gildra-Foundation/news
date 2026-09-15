from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import FSInputFile, InputSticker

from gildranews.adapters.persistence import sqlite
from gildranews.adapters.warcraft.icons import NormalizedIcon
from gildranews.domain.models import ResolvedWarcraftEntity, TelegramEmojiAsset

log = logging.getLogger(__name__)

SET_SOFT_LIMIT = 190
TELEGRAM_SET_LIMIT = 200
_USERNAME_RE = re.compile(r"[^a-z0-9_]+")
_upload_lock = asyncio.Lock()


class EmojiUploadError(RuntimeError):
    """Telegram did not return a usable custom emoji after an upload."""


class TelegramEmojiRegistry:
    def __init__(
        self,
        bot: Bot,
        *,
        owner_user_id: int,
        enabled: bool,
        set_prefix: str = "gildra_warcraft",
        daily_upload_limit: int = 10,
        upload_timeout_seconds: int = 15,
    ) -> None:
        self.bot = bot
        self.owner_user_id = owner_user_id
        self.enabled = enabled
        self.set_prefix = _USERNAME_RE.sub("_", set_prefix.lower()).strip("_")
        self.daily_upload_limit = max(0, min(daily_upload_limit, 100))
        self.upload_timeout_seconds = max(1, min(upload_timeout_seconds, 60))

    async def get_or_create(
        self,
        entity: ResolvedWarcraftEntity,
        icon: NormalizedIcon,
        *,
        fallback: str,
    ) -> TelegramEmojiAsset | None:
        await sqlite.upsert_warcraft_entity(entity, icon_sha256=icon.sha256)
        await sqlite.upsert_emoji_asset(
            sha256=icon.sha256,
            source_url=icon.source_url,
            local_path=str(icon.path),
            fallback=fallback,
        )
        ready = await sqlite.ready_emoji_for_entity(entity)
        if ready is not None:
            return ready
        if not self.enabled or self.owner_user_id <= 0 or self.daily_upload_limit <= 0:
            return None

        async with _upload_lock:
            ready = await sqlite.ready_emoji_for_entity(entity)
            if ready is not None:
                return ready
            if await sqlite.emoji_uploads_today() >= self.daily_upload_limit:
                return None
            if not await sqlite.claim_emoji_asset(icon.sha256):
                return await sqlite.ready_emoji_for_entity(entity)
            try:
                async with asyncio.timeout(self.upload_timeout_seconds):
                    asset = await self._upload(entity, icon, fallback=fallback)
            except (TimeoutError, OSError, TelegramAPIError, EmojiUploadError) as exc:
                log.warning("Custom emoji upload failed for %s: %s", entity.key, exc)
                await sqlite.mark_emoji_asset_failed(icon.sha256, str(exc))
                return None
            await sqlite.mark_emoji_asset_ready(
                icon.sha256,
                custom_emoji_id=asset.custom_emoji_id,
                file_id=asset.file_id,
                sticker_set_name=asset.sticker_set_name,
            )
            return asset

    async def _upload(
        self,
        entity: ResolvedWarcraftEntity,
        icon: NormalizedIcon,
        *,
        fallback: str,
    ) -> TelegramEmojiAsset:
        set_name, existing = await self._select_set(entity.branch)
        old_ids = _custom_ids(existing)
        sticker = InputSticker(
            sticker=FSInputFile(icon.path),
            format="static",
            emoji_list=[fallback],
            keywords=[entity.kind, entity.canonical_name[:32]],
        )
        if existing is None:
            await self.bot.create_new_sticker_set(
                user_id=self.owner_user_id,
                name=set_name,
                title=_set_title(entity.branch, set_name),
                stickers=[sticker],
                sticker_type="custom_emoji",
            )
        else:
            await self.bot.add_sticker_to_set(
                user_id=self.owner_user_id,
                name=set_name,
                sticker=sticker,
            )
        updated = await self._get_set(set_name)
        if updated is None:
            raise EmojiUploadError("Telegram не вернул созданный набор")
        candidates = [
            item for item in updated.stickers
            if getattr(item, "custom_emoji_id", None) not in old_ids
        ]
        if not candidates:
            raise EmojiUploadError("Telegram не вернул ID нового эмодзи")
        created = candidates[-1]
        custom_emoji_id = getattr(created, "custom_emoji_id", None)
        file_id = getattr(created, "file_id", None)
        if not custom_emoji_id or not file_id:
            raise EmojiUploadError("Новый стикер не является Custom Emoji")
        return TelegramEmojiAsset(
            custom_emoji_id=custom_emoji_id,
            file_id=file_id,
            sticker_set_name=set_name,
            fallback=fallback,
        )

    async def _select_set(self, branch: str) -> tuple[str, Any | None]:
        me = await self.bot.get_me()
        username = _USERNAME_RE.sub("_", (me.username or "bot").lower()).strip("_")
        for index in range(1, 100):
            name = f"{self.set_prefix}_{branch}_{index:02d}_by_{username}"[:64]
            sticker_set = await self._get_set(name)
            local_count = await sqlite.ready_emoji_count_in_set(name)
            remote_count = len(sticker_set.stickers) if sticker_set is not None else 0
            if max(local_count, remote_count) < SET_SOFT_LIMIT:
                return name, sticker_set
        raise EmojiUploadError("достигнут предел наборов Custom Emoji")

    async def _get_set(self, name: str) -> Any | None:
        try:
            return await self.bot.get_sticker_set(name)
        except TelegramBadRequest as exc:
            message = str(exc).lower()
            if "stickerset_invalid" in message or "not found" in message:
                return None
            raise


def _custom_ids(sticker_set: Any | None) -> set[str]:
    if sticker_set is None:
        return set()
    return {
        value
        for item in sticker_set.stickers
        if (value := getattr(item, "custom_emoji_id", None))
    }


def _set_title(branch: str, set_name: str) -> str:
    number = set_name.rsplit("_by_", 1)[0].rsplit("_", 1)[-1]
    branch_title = {"retail": "Retail", "classic": "Classic", "forever": "Forever"}.get(
        branch, branch.title(),
    )
    return f"Gildra Warcraft {branch_title} {number}"[:64]


def pick_fallback(kind: str) -> str:
    by_kind: dict[str, str] = {
        "class": "⚔️",
        "specialization": "⚔️",
        "spell": "✨",
        "talent": "✨",
        "item": "🎒",
        "cosmetic": "👑",
        "transmog_set": "👑",
        "mount": "🐉",
        "pet": "🐾",
        "achievement": "🏆",
        "raid": "🏰",
        "dungeon": "🗝️",
        "boss": "💀",
        "creature": "👹",
        "faction": "🛡️",
        "profession": "🔨",
        "event": "📅",
    }
    return by_kind.get(kind, "⚔️")


def limit_assets(assets: Sequence[TelegramEmojiAsset]) -> tuple[TelegramEmojiAsset, ...]:
    return tuple(assets[:2])
