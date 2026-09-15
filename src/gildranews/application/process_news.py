from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from aiogram import Bot
from telethon import TelegramClient

from gildranews.adapters.ai.provider import build_content_ai
from gildranews.adapters.emoji import catalog as emoji_store
from gildranews.adapters.persistence import sqlite as db
from gildranews.adapters.publishing import telegram as tg_writer
from gildranews.adapters.rendering.svg_infographic import render_png
from gildranews.adapters.sources import telegram as tg_reader
from gildranews.application import warcraft_enrichment
from gildranews.application.ports import NewsFilter
from gildranews.config import Config
from gildranews.domain.models import ProcessResult

log = logging.getLogger(__name__)


async def run_once(
    client: TelegramClient,
    bot: Bot,
    cfg: Config,
    on_result=None,
    news_filter: NewsFilter | None = None,
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
            result = await process_post(
                client,
                bot,
                cfg,
                post,
                news_filter=news_filter,
            )
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
        log.exception("Ошибка в прогоне")
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
    news_filter: NewsFilter | None = None,
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

    recent_posts = await db.recent_published_context(
        hours=cfg.dedup_context_hours,
        limit=cfg.dedup_context_limit,
    )
    emoji_map = emoji_store.load()
    emoji_themes = emoji_store.themes_for_prompt(emoji_map)

    try:
        processor = news_filter or build_content_ai(cfg)
        filt = await processor.filter_and_rewrite(
            text=post.text,
            recent_posts=recent_posts,
            emoji_themes=emoji_themes,
        )
    except Exception as e:
        log.exception("Ошибка AI-фильтра для @%s/%s", post.channel, post.message_id)
        return ProcessResult("ai_error", post.channel, post.message_id, reason=f"{type(e).__name__}: {e}")

    if filt is None:
        return ProcessResult(
            "ai_error", post.channel, post.message_id,
            reason="AI-сервис не вернул валидный ответ",
        )

    if not filt.is_news:
        log.info("Отфильтровано: @%s/%s — %s", post.channel, post.message_id, filt.reason)
        return ProcessResult("filtered", post.channel, post.message_id, reason=filt.reason)

    try:
        with tempfile.TemporaryDirectory(prefix="newsbot_rt_") as tmpdir:
            media = await tg_reader.download_post_media(client, post, tmpdir)
            if not media and filt.infographic is not None:
                infographic_path = Path(tmpdir) / "infographic.png"
                if await asyncio.to_thread(render_png, filt.infographic, infographic_path):
                    media = [str(infographic_path)]
            # Если в title/body упомянута конкретная компания/инструмент/язык —
            # подменяем категорию Gemini на специализированный лого
            emoji_theme = filt.emoji_theme
            override = emoji_store.detect_override(
                emoji_map, f"{filt.title}\n{filt.body}",
            )
            if override:
                emoji_theme = override
            inline_links = ()
            custom_emojis = ()
            if cfg.emoji_autocreate_enabled and filt.references:
                try:
                    async with asyncio.timeout(cfg.emoji_upload_timeout_seconds):
                        enrichment = await warcraft_enrichment.enrich(
                            bot, cfg, filt.references,
                        )
                    inline_links = enrichment.inline_links
                    custom_emojis = enrichment.emojis
                except Exception:
                    log.warning(
                        "Warcraft enrichment failed; using ordinary formatting",
                        exc_info=True,
                    )
            text = tg_writer.format_post(
                filt.title, filt.body,
                emoji_theme=emoji_theme, emoji_map=emoji_map,
                hashtag_key=filt.hashtag,
                inline_links=inline_links,
                custom_emojis=custom_emojis,
                subscribe_emoji_id=cfg.subscribe_emoji_id,
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
