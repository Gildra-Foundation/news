from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass

from aiogram import Bot
from telethon import TelegramClient

import ai
import db
import emoji_store
import tg_reader
import tg_writer
from config import Config


@dataclass
class ProcessResult:
    status: str  # 'duplicate' | 'filtered' | 'published' | 'publish_failed' | 'ai_error' | 'error'
    channel: str
    message_id: int
    reason: str = ""
    title: str = ""

log = logging.getLogger(__name__)


async def run_once(
    client: TelegramClient,
    bot: Bot,
    cfg: Config,
    on_result=None,
) -> dict:
    """Backup-поллинг. Вызывает on_result(ProcessResult) для каждого обработанного поста.
    Используется и как safety-net каждые INTERVAL_MINUTES, и для catch-up на старте.
    """
    fetched = 0
    selected_count = 0
    published = 0
    error: str | None = None

    try:
        sources = await db.list_sources()
        if not sources:
            log.info("Источников нет — пропуск прогона")
            await db.record_run(0, 0, 0, None)
            return {"fetched": 0, "selected": 0, "published": 0}

        posts = await tg_reader.fetch_recent(
            client=client,
            channels=sources,
            lookback_minutes=cfg.lookback_minutes,
            seen_check=db.is_seen,
        )
        fetched = len(posts)
        if fetched > 0:
            log.info("Поллинг: %d новых постов из %d источников", fetched, len(sources))

        for post in posts:
            result = await process_post(client, bot, cfg, post)
            if result.status == "published":
                published += 1
                selected_count += 1
            elif result.status == "filtered":
                selected_count += 0  # для совместимости журнала
            if on_result and result.status != "duplicate":
                try:
                    await on_result(result)
                except Exception:
                    log.exception("on_result callback failed")
            if result.status == "published":
                await asyncio.sleep(2)  # rate limit на публикацию

    except Exception as e:
        log.exception("Ошибка в прогоне: %s", e)
        error = f"{type(e).__name__}: {e}"

    await db.record_run(fetched, selected_count, published, error)
    return {"fetched": fetched, "selected": selected_count, "published": published, "error": error}


async def process_post(
    client: TelegramClient,
    bot: Bot,
    cfg: Config,
    post: tg_reader.FetchedPost,
    *,
    force: bool = False,
) -> ProcessResult:
    """Real-time обработка одного поста. Возвращает ProcessResult с статусом и причиной.
    force=True — для ручной отправки админом по ссылке: пропускаем claim,
    но проверяем, не публиковали ли этот msg ранее."""
    if force:
        existing = await db.was_published(post.channel, post.message_id)
        if existing:
            return ProcessResult(
                "already_published", post.channel, post.message_id,
                reason="Этот пост уже публиковался ранее", title=existing,
            )
    else:
        if not await db.claim_message(post.channel, post.message_id):
            return ProcessResult("duplicate", post.channel, post.message_id)

    recent_titles = await db.recent_published_titles(hours=24, limit=50)
    emoji_map = emoji_store.load()
    emoji_themes = emoji_store.themes_for_prompt(emoji_map)

    try:
        filt = await ai.filter_and_rewrite(
            api_key=cfg.gemini_api_key,
            model=cfg.gemini_model,
            text=post.text,
            recent_titles=recent_titles,
            emoji_themes=emoji_themes,
        )
    except Exception as e:
        log.exception("Ошибка фильтра Gemini для @%s/%s", post.channel, post.message_id)
        return ProcessResult("ai_error", post.channel, post.message_id, reason=f"{type(e).__name__}: {e}")

    if filt is None:
        return ProcessResult(
            "ai_error", post.channel, post.message_id,
            reason="Gemini не вернул валидный ответ",
        )

    if not filt.is_news:
        log.info("Отфильтровано: @%s/%s — %s", post.channel, post.message_id, filt.reason)
        return ProcessResult("filtered", post.channel, post.message_id, reason=filt.reason)

    try:
        with tempfile.TemporaryDirectory(prefix="newsbot_rt_") as tmpdir:
            media = await tg_reader.download_post_media(client, post, tmpdir)
            # Если в title/body упомянута конкретная компания/инструмент/язык —
            # подменяем категорию Gemini на специализированный лого
            emoji_theme = filt.emoji_theme
            override = emoji_store.detect_override(
                emoji_map, f"{filt.title}\n{filt.body}",
            )
            if override:
                emoji_theme = override
            text = tg_writer.format_post(
                filt.title, filt.body,
                emoji_theme=emoji_theme, emoji_map=emoji_map,
                hashtag_key=filt.hashtag,
            )
            target_msg_id = await tg_writer.publish(bot, cfg.target_channel, text, media)
        if target_msg_id:
            log.info("Опубликовано: @%s/%s media=%d → %d", post.channel, post.message_id, len(media), target_msg_id)
            await db.record_published(
                post.channel, post.message_id, filt.title, filt.body,
                target_message_id=target_msg_id,
            )
            if force:
                # При force claim не делался — пометим seen вручную, чтобы real-time
                # не словил этот msg повторно через push-апдейт
                await db.mark_seen(post.channel, post.message_id)
            return ProcessResult(
                "published", post.channel, post.message_id,
                reason=filt.reason, title=filt.title,
            )
        return ProcessResult(
            "publish_failed", post.channel, post.message_id,
            reason="Telegram API отказал в публикации", title=filt.title,
        )
    except Exception as e:
        log.exception("Ошибка публикации @%s/%s", post.channel, post.message_id)
        return ProcessResult(
            "error", post.channel, post.message_id,
            reason=f"{type(e).__name__}: {e}", title=filt.title,
        )
