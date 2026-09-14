from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from aiogram import Bot

from gildranews.adapters.emoji import catalog as emoji_store
from gildranews.adapters.persistence import sqlite as db
from gildranews.adapters.publishing import telegram as tg_writer
from gildranews.adapters.rendering.svg_infographic import render_png
from gildranews.adapters.sources.rss import RSSItem
from gildranews.application.ports import ContentAI
from gildranews.config import Config
from gildranews.domain.models import ProcessResult

log = logging.getLogger(__name__)
MAX_AI_INPUT_CHARS = 12_000


async def process_item(
    *,
    bot: Bot,
    cfg: Config,
    item: RSSItem,
    content_ai: ContentAI,
) -> ProcessResult:
    if not await db.claim_message(item.source, item.external_id):
        return ProcessResult("duplicate", item.source, item.external_id)

    recent_titles = await db.recent_published_titles(hours=48, limit=100)
    emoji_map = emoji_store.load()
    try:
        analysis = await content_ai.filter_and_rewrite(
            text=item.ai_text[:MAX_AI_INPUT_CHARS],
            recent_titles=recent_titles,
            emoji_themes=emoji_store.themes_for_prompt(emoji_map),
        )
    except Exception as exc:
        log.exception("RSS AI analysis failed for %s/%s", item.source, item.external_id)
        await db.release_claim(item.source, item.external_id)
        return ProcessResult(
            "ai_error", item.source, item.external_id,
            reason=f"{type(exc).__name__}: {exc}",
        )

    if analysis is None:
        await db.release_claim(item.source, item.external_id)
        return ProcessResult(
            "ai_error", item.source, item.external_id,
            reason="AI-сервис не вернул валидный ответ",
        )
    if not analysis.is_news:
        return ProcessResult(
            "filtered", item.source, item.external_id, reason=analysis.reason,
        )

    emoji_theme = emoji_store.detect_override(
        emoji_map, f"{analysis.title}\n{analysis.body}",
    ) or analysis.emoji_theme
    post_text = tg_writer.format_post(
        analysis.title,
        analysis.body,
        emoji_theme=emoji_theme,
        emoji_map=emoji_map,
        hashtag_key=analysis.hashtag,
    )

    try:
        with tempfile.TemporaryDirectory(prefix="gildranews_rss_") as tmpdir:
            media: list[tuple[str, str]] = []
            if analysis.infographic is not None:
                infographic_path = Path(tmpdir) / "infographic.png"
                rendered = await asyncio.to_thread(
                    render_png, analysis.infographic, infographic_path,
                )
                if rendered:
                    media.append((str(infographic_path), "photo"))
            target_message_id = await tg_writer.publish(
                bot, cfg.target_channel, post_text, media,
            )
    except Exception as exc:
        log.exception("RSS publish failed for %s/%s", item.source, item.external_id)
        await db.release_claim(item.source, item.external_id)
        return ProcessResult(
            "error", item.source, item.external_id,
            reason=f"{type(exc).__name__}: {exc}", title=analysis.title,
        )

    if target_message_id is None:
        await db.release_claim(item.source, item.external_id)
        return ProcessResult(
            "publish_failed", item.source, item.external_id,
            reason="Telegram API отказал в публикации", title=analysis.title,
        )
    await db.record_published(
        item.source,
        item.external_id,
        analysis.title,
        analysis.body,
        target_message_id=target_message_id,
    )
    return ProcessResult(
        "published", item.source, item.external_id,
        reason=analysis.reason, title=analysis.title,
    )
