from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiogram import Bot

from gildranews.adapters import media as media_downloader
from gildranews.adapters.emoji import catalog as emoji_store
from gildranews.adapters.persistence import sqlite as db
from gildranews.adapters.publishing import telegram as tg_writer
from gildranews.adapters.rendering.svg_infographic import render_png
from gildranews.adapters.sources import rss as rss_source
from gildranews.application.ports import ContentAI
from gildranews.config import Config
from gildranews.domain.models import ProcessResult

log = logging.getLogger(__name__)
MAX_AI_INPUT_CHARS = 12_000
WOWHEAD_MEDIA_HOSTS = {"wow.zamimg.com"}
ResultCallback = Callable[[ProcessResult], Awaitable[None]]


async def process_item(
    *,
    bot: Bot,
    cfg: Config,
    item: rss_source.RSSItem,
    content_ai: ContentAI,
) -> ProcessResult:
    if not await db.claim_message(item.source, item.external_id):
        return ProcessResult(
            "duplicate", item.source, item.external_id, source_url=item.article_url,
        )

    recent_posts = await db.recent_published_context(
        hours=cfg.dedup_context_hours,
        limit=cfg.dedup_context_limit,
    )
    emoji_map = emoji_store.load()
    try:
        analysis = await content_ai.filter_and_rewrite(
            text=item.ai_text[:MAX_AI_INPUT_CHARS],
            recent_posts=recent_posts,
            emoji_themes=emoji_store.themes_for_prompt(emoji_map),
        )
    except Exception as exc:
        log.exception("RSS AI analysis failed for %s/%s", item.source, item.external_id)
        await db.release_claim(item.source, item.external_id)
        return ProcessResult(
            "ai_error", item.source, item.external_id,
            reason=f"{type(exc).__name__}: {exc}",
            source_url=item.article_url,
        )

    if analysis is None:
        await db.release_claim(item.source, item.external_id)
        return ProcessResult(
            "ai_error", item.source, item.external_id,
            reason="AI-сервис не вернул валидный ответ",
            source_url=item.article_url,
        )
    if not analysis.is_news:
        return ProcessResult(
            "filtered", item.source, item.external_id,
            reason=analysis.reason, source_url=item.article_url,
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
            media_dir = Path(tmpdir)
            if item.source == "wowhead":
                image_url = item.image_url
                video_url = item.video_url
                if item.article_url and (not image_url or not video_url):
                    try:
                        discovered_image, discovered_video = (
                            await rss_source.fetch_article_media(item.article_url)
                        )
                        image_url = image_url or discovered_image
                        video_url = video_url or discovered_video
                    except Exception:
                        log.warning(
                            "RSS article media discovery failed for %s/%s",
                            item.source,
                            item.external_id,
                            exc_info=True,
                        )
                for media_url, kind in (
                    (image_url, "photo"),
                    (video_url, "video"),
                ):
                    if not media_url:
                        continue
                    try:
                        source_path = await media_downloader.download(
                            media_url,
                            media_dir,
                            kind=kind,
                            allowed_hosts=WOWHEAD_MEDIA_HOSTS,
                        )
                        media.append((str(source_path), kind))
                    except Exception:
                        log.warning(
                            "RSS media download failed for %s/%s (%s)",
                            item.source,
                            item.external_id,
                            kind,
                            exc_info=True,
                        )
            if analysis.infographic is not None:
                infographic_path = media_dir / "infographic.png"
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
            source_url=item.article_url,
        )

    if target_message_id is None:
        await db.release_claim(item.source, item.external_id)
        return ProcessResult(
            "publish_failed", item.source, item.external_id,
            reason="Telegram API отказал в публикации", title=analysis.title,
            source_url=item.article_url,
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
        source_url=item.article_url,
    )


async def run_once(
    *,
    bot: Bot,
    cfg: Config,
    content_ai: ContentAI,
    on_result: ResultCallback | None = None,
    now: datetime | None = None,
) -> dict[str, int | str | None]:
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff = current_time - timedelta(minutes=cfg.lookback_minutes)
    candidates: list[rss_source.RSSItem] = []
    errors: list[str] = []

    for feed_url in cfg.rss_feed_urls:
        source = rss_source.source_key(feed_url)
        try:
            feed_items = await rss_source.fetch_feed(feed_url, source=source)
        except Exception as exc:
            log.exception("RSS fetch failed for %s", source)
            errors.append(f"{source}: {type(exc).__name__}")
            continue
        candidates.extend(
            item for item in feed_items
            if cutoff <= item.published_at <= current_time
        )

    candidates.sort(key=lambda item: item.published_at)
    selected = 0
    published = 0
    for item in candidates:
        if published >= cfg.max_posts_per_run:
            break
        result = await process_item(
            bot=bot, cfg=cfg, item=item, content_ai=content_ai,
        )
        if result.status == "published":
            selected += 1
            published += 1
        if on_result is not None and result.status != "duplicate":
            try:
                await on_result(result)
            except Exception:
                log.exception("RSS on_result callback failed")

    error = "; ".join(errors) or None
    await db.record_run(len(candidates), selected, published, error)
    return {
        "fetched": len(candidates),
        "selected": selected,
        "published": published,
        "error": error,
    }
