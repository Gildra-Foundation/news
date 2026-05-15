"""Еженедельный дайджест канала: запрос постов за 7 дней, Gemini-выжимка,
публикация с фирменной обложкой и хэштегом #дайджест@runeuronews."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile

import ai
import db
import tg_writer
from config import Config

log = logging.getLogger(__name__)

COVER_PATH = "assets/digest_cover.jpg"
TARGET_CHANNEL_USERNAME = "runeuronews"  # для построения t.me/runeuronews/<id> ссылок


def _post_link(target_msg_id: int) -> str:
    return f"https://t.me/{TARGET_CHANNEL_USERNAME}/{target_msg_id}"


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _build_digest_html(intro: str, sections, items_by_id: dict[int, dict]) -> str:
    """Собирает финальный HTML-текст дайджеста с встроенными ссылками."""
    today = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    blocks: list[str] = []
    blocks.append(f"<b>🗓 Дайджест недели · {today}</b>")
    if intro:
        blocks.append(_html_escape(intro.strip()))

    for section in sections:
        name = _html_escape(section.name.strip())
        section_lines = [f"<b>{name}</b>"]
        for item in section.items:
            row = items_by_id.get(item.id)
            if not row:
                continue
            link = _post_link(row["target_message_id"])
            summary = _html_escape(item.summary.strip().rstrip("."))
            section_lines.append(f'• <a href="{link}">{summary}</a>')
        if len(section_lines) > 1:
            blocks.append("\n".join(section_lines))

    # Хэштег в конце через tg_writer.HASHTAGS
    blocks.append(tg_writer.HASHTAGS["дайджест"])
    return "\n\n".join(blocks)


async def build_and_publish(bot: Bot, cfg: Config) -> dict:
    """Собирает посты за 7 дней, отправляет в Gemini для дайджеста, публикует
    в канал с обложкой. Возвращает {published: bool, target_msg_id, posts_count, reason}."""
    posts = await db.recent_published_for_digest(days=7)
    if not posts:
        return {"published": False, "reason": "за неделю нет постов с target_message_id"}

    # Готовим вход для Gemini — id, title, краткий excerpt body
    ai_input: list[dict] = []
    items_by_id: dict[int, dict] = {}
    for p in posts:
        items_by_id[p["id"]] = p
        ai_input.append({
            "id": p["id"],
            "title": p["title"],
            "body_excerpt": (p["body"] or "")[:400],
        })

    digest = await ai.make_weekly_digest(
        api_key=cfg.gemini_api_key,
        model=cfg.gemini_model,
        posts=ai_input,
    )
    if digest is None or not digest.sections:
        return {"published": False, "reason": "Gemini не вернул дайджест"}

    text = _build_digest_html(digest.intro, digest.sections, items_by_id)
    if len(text) > 1024:
        # caption у фото ограничен 1024 видимых символов. Если перебор —
        # шлём картинку без подписи + текстом следом одним сообщением (4096 limit).
        log.info("digest text %d chars — отправляем фото + отдельным текстом", len(text))
        try:
            cover = FSInputFile(COVER_PATH) if os.path.exists(COVER_PATH) else None
            if cover:
                sent_photo = await bot.send_photo(
                    chat_id=cfg.target_channel, photo=cover,
                )
                target_msg_id = sent_photo.message_id
            else:
                target_msg_id = None
            sent_text = await bot.send_message(
                chat_id=cfg.target_channel, text=text, disable_web_page_preview=True,
            )
            return {
                "published": True,
                "target_msg_id": target_msg_id or sent_text.message_id,
                "posts_count": sum(len(s.items) for s in digest.sections),
                "reason": "ok (photo+text split)",
            }
        except TelegramAPIError as e:
            log.exception("digest publish (split) failed")
            return {"published": False, "reason": f"{type(e).__name__}: {e}"}

    # Иначе — одной публикацией: фото + caption
    try:
        if os.path.exists(COVER_PATH):
            sent = await bot.send_photo(
                chat_id=cfg.target_channel,
                photo=FSInputFile(COVER_PATH),
                caption=text,
            )
        else:
            sent = await bot.send_message(
                chat_id=cfg.target_channel, text=text, disable_web_page_preview=True,
            )
    except TelegramAPIError as e:
        log.exception("digest publish failed")
        return {"published": False, "reason": f"{type(e).__name__}: {e}"}

    return {
        "published": True,
        "target_msg_id": sent.message_id,
        "posts_count": sum(len(s.items) for s in digest.sections),
        "reason": "ok",
    }
