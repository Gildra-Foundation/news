from __future__ import annotations

import logging
import os.path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from gildranews.adapters.emoji import catalog as emoji_store
from gildranews.adapters.persistence import sqlite as db
from gildranews.adapters.publishing import telegram as telegram_publisher

log = logging.getLogger(__name__)


def draft_keyboard(draft_id: int, include_original: bool = False) -> InlineKeyboardMarkup:
    original_label = "✓ Оригинал в посте" if include_original else "🔗 Оригинал в посте"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit:{draft_id}"),
                InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub:{draft_id}"),
            ],
            [
                InlineKeyboardButton(text=original_label, callback_data=f"orig:{draft_id}"),
                InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel:{draft_id}"),
            ],
        ]
    )


def photo_argument(image_reference: str | None) -> str | FSInputFile | None:
    if not image_reference:
        return None
    if image_reference.startswith(("http://", "https://")):
        return image_reference
    if os.path.isfile(image_reference):
        return FSInputFile(image_reference)
    return None


def draft_tail_url(draft: dict) -> str | None:
    source_url = draft.get("source_url") or ""
    if source_url.startswith(("https://github.com", "http://github.com")):
        return source_url
    return None


def format_draft_text(draft: dict, subscribe_emoji_id: str = "") -> str:
    original_url = draft["source_url"] if draft.get("include_original") else None
    emoji_map = emoji_store.load()
    override = emoji_store.detect_override(
        emoji_map,
        f"{draft.get('title', '')}\n{draft.get('body', '')}",
    )
    return telegram_publisher.format_post(
        draft["title"],
        draft["body"],
        emoji_theme=override or "",
        emoji_map=emoji_map,
        original_url=original_url,
        tail_url=draft_tail_url(draft),
        hashtag_key=draft.get("hashtag") or "",
        inline_links=draft.get("inline_links") or (),
        custom_emojis=draft.get("custom_emojis") or (),
        subscribe_emoji_id=subscribe_emoji_id,
    )


async def send_preview(
    bot: Bot,
    chat_id: int,
    draft_id: int,
    subscribe_emoji_id: str = "",
) -> None:
    draft = await db.get_draft(draft_id)
    if not draft:
        await bot.send_message(chat_id, "Черновик не найден.")
        return

    text = format_draft_text(draft, subscribe_emoji_id)
    keyboard = draft_keyboard(draft_id, include_original=draft.get("include_original", False))
    media = photo_argument(draft["image_url"])
    media_type = draft.get("media_type") or "photo"
    sent = False
    if media:
        try:
            if media_type == "video":
                await bot.send_video(
                    chat_id=chat_id,
                    video=media,
                    caption=text,
                    reply_markup=keyboard,
                )
            else:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=media,
                    caption=text,
                    reply_markup=keyboard,
                )
            sent = True
        except TelegramAPIError as error:
            log.warning("Не удалось отправить превью с медиа: %s — fallback text", error)
            text = telegram_publisher.without_custom_emojis(text)

    if not sent:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
