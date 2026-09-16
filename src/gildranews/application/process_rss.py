from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from aiogram import Bot

from gildranews.adapters import media as media_downloader
from gildranews.adapters.emoji import catalog as emoji_store
from gildranews.adapters.media import scrape_do_images
from gildranews.adapters.persistence import processing_retries as retry_store
from gildranews.adapters.persistence import publication_guard
from gildranews.adapters.persistence import sqlite as db
from gildranews.adapters.publishing import telegram as tg_writer
from gildranews.adapters.references import wowhead as wowhead_references
from gildranews.adapters.rendering.svg_infographic import render_png
from gildranews.adapters.sources import rss as rss_source
from gildranews.application import warcraft_enrichment
from gildranews.application.ports import ContentAI
from gildranews.config import Config
from gildranews.domain.models import EventFingerprint, FilterResult, ProcessResult

log = logging.getLogger(__name__)
MAX_AI_INPUT_CHARS = 12_000
RSS_MEDIA_HOSTS = {
    "wowhead": {"wow.zamimg.com"},
    "icy-veins": {"static.icy-veins.com"},
}
ResultCallback = Callable[[ProcessResult], Awaitable[None]]
RETRY_LIMIT_PER_RUN = 3
RETRY_TTL_HOURS = {"news": 6, "reddit_topic": 24, "x_topic": 24}


def _retry_payload(item: rss_source.RSSItem) -> str:
    return json.dumps(
        {
            "source": item.source,
            "external_id": item.external_id,
            "title": item.title,
            "content": item.content,
            "published_at": item.published_at.isoformat(),
            "article_url": item.article_url,
            "image_url": item.image_url,
            "video_url": item.video_url,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _retry_item(payload_json: str) -> rss_source.RSSItem:
    value = json.loads(payload_json)
    if not isinstance(value, dict):
        raise TypeError("Retry payload must be a JSON object")
    published_at = datetime.fromisoformat(str(value["published_at"]))
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    return rss_source.RSSItem(
        source=str(value["source"]),
        external_id=int(value["external_id"]),
        title=str(value["title"]),
        content=str(value["content"]),
        published_at=published_at.astimezone(UTC),
        article_url=str(value.get("article_url") or ""),
        image_url=str(value.get("image_url") or ""),
        video_url=str(value.get("video_url") or ""),
    )


async def _defer_processing(
    item: rss_source.RSSItem,
    *,
    content_kind: str,
    quota_source: str | None,
    quota_day: date | None,
    quota_limit: int | None,
    retry_id: int | None,
    error: Exception | str,
) -> None:
    safe_error = f"{type(error).__name__}: {error}" if isinstance(error, Exception) else error
    try:
        if retry_id is None:
            await retry_store.enqueue(
                source=item.source,
                external_id=item.external_id,
                payload_json=_retry_payload(item),
                content_kind=content_kind,
                quota_source=quota_source,
                quota_day=quota_day.isoformat() if quota_day is not None else None,
                quota_limit=quota_limit,
                error=safe_error,
                expires_hours=RETRY_TTL_HOURS.get(content_kind, 6),
            )
        else:
            status = await retry_store.reschedule(
                retry_id,
                error=safe_error,
            )
            if status == "missing":
                raise RuntimeError("Запись очереди повторов не найдена")
            log.warning(
                "processing_retry_rescheduled source=%s external_id=%s "
                "retry_id=%s status=%s",
                item.source,
                item.external_id,
                retry_id,
                status,
            )
    except Exception:
        log.exception(
            "processing_retry_persist_failed source=%s external_id=%s",
            item.source,
            item.external_id,
        )
        try:
            await db.release_claim(item.source, item.external_id)
        except Exception:
            log.exception(
                "processing_retry_claim_release_failed source=%s external_id=%s",
                item.source,
                item.external_id,
            )


def _publication_fingerprint(analysis: FilterResult) -> EventFingerprint:
    if analysis.fingerprint is not None:
        return analysis.fingerprint
    primary = next(
        (reference for reference in analysis.references if reference.role == "primary"),
        analysis.references[0] if analysis.references else None,
    )
    return EventFingerprint(
        game_branch=primary.branch if primary is not None else "retail",
        version="",
        subject=analysis.title,
        action="публикация новости",
        status="",
        effective_date="",
    )


async def process_item(
    *,
    bot: Bot,
    cfg: Config,
    item: rss_source.RSSItem,
    content_ai: ContentAI,
    content_kind: str = "news",
    quota_source: str | None = None,
    quota_day: date | None = None,
    quota_limit: int | None = None,
    retry_id: int | None = None,
) -> ProcessResult:
    if retry_id is None and not await db.claim_message(item.source, item.external_id):
        return ProcessResult(
            "duplicate", item.source, item.external_id, source_url=item.article_url,
        )

    try:
        recent_posts = await db.recent_published_context(
            hours=cfg.dedup_context_hours,
            limit=cfg.dedup_context_limit,
        )
        emoji_map = emoji_store.load()
    except Exception as exc:
        log.exception(
            "RSS processing context failed for %s/%s",
            item.source,
            item.external_id,
        )
        await _defer_processing(
            item,
            content_kind=content_kind,
            quota_source=quota_source,
            quota_day=quota_day,
            quota_limit=quota_limit,
            retry_id=retry_id,
            error=exc,
        )
        return ProcessResult(
            "error", item.source, item.external_id,
            reason=f"{type(exc).__name__}: {exc}",
            source_url=item.article_url,
        )
    try:
        ai_kwargs = {
            "text": item.ai_text[:MAX_AI_INPUT_CHARS],
            "recent_posts": recent_posts,
            "emoji_themes": emoji_store.themes_for_prompt(emoji_map),
        }
        if content_kind != "news":
            ai_kwargs["content_kind"] = content_kind
        analysis = await content_ai.filter_and_rewrite(**ai_kwargs)
    except Exception as exc:
        log.exception("RSS AI analysis failed for %s/%s", item.source, item.external_id)
        await _defer_processing(
            item,
            content_kind=content_kind,
            quota_source=quota_source,
            quota_day=quota_day,
            quota_limit=quota_limit,
            retry_id=retry_id,
            error=exc,
        )
        return ProcessResult(
            "ai_error", item.source, item.external_id,
            reason=f"{type(exc).__name__}: {exc}",
            source_url=item.article_url,
        )

    if analysis is None:
        await _defer_processing(
            item,
            content_kind=content_kind,
            quota_source=quota_source,
            quota_day=quota_day,
            quota_limit=quota_limit,
            retry_id=retry_id,
            error="AI-сервис не вернул валидный ответ",
        )
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

    try:
        emoji_theme = emoji_store.detect_override(
            emoji_map, f"{analysis.title}\n{analysis.body}",
        ) or analysis.emoji_theme
    except Exception as exc:
        log.exception("RSS formatting setup failed for %s/%s", item.source, item.external_id)
        await _defer_processing(
            item,
            content_kind=content_kind,
            quota_source=quota_source,
            quota_day=quota_day,
            quota_limit=quota_limit,
            retry_id=retry_id,
            error=exc,
        )
        return ProcessResult(
            "error", item.source, item.external_id,
            reason=f"{type(exc).__name__}: {exc}", title=analysis.title,
            source_url=item.article_url,
        )
    inline_links: list[tuple[str, str]] = []
    custom_emojis = ()
    if cfg.emoji_autocreate_enabled:
        try:
            async with asyncio.timeout(cfg.emoji_upload_timeout_seconds):
                enrichment = await warcraft_enrichment.enrich(
                    bot,
                    cfg,
                    analysis.references,
                    publication_text=f"{analysis.title}\n{analysis.body}",
                )
            inline_links.extend(enrichment.inline_links)
            custom_emojis = enrichment.emojis
        except Exception:
            log.warning("Warcraft enrichment failed; using ordinary formatting", exc_info=True)
    else:
        resolved_references = await asyncio.gather(
            *(
                wowhead_references.resolve_reference(reference.query, reference.kind)
                for reference in analysis.references
                if reference.kind in {"raid", "creature"}
            ),
            return_exceptions=True,
        )
        compatible_references = [
            reference for reference in analysis.references
            if reference.kind in {"raid", "creature"}
        ]
        for reference, url in zip(
            compatible_references, resolved_references, strict=True,
        ):
            if isinstance(url, Exception):
                log.warning("Wowhead reference lookup failed: %s", type(url).__name__)
            elif url:
                inline_links.append((reference.label, url))
    try:
        post_text = tg_writer.format_post(
            analysis.title,
            analysis.body,
            emoji_theme=emoji_theme,
            emoji_map=emoji_map,
            hashtag_key=analysis.hashtag,
            inline_links=inline_links,
            custom_emojis=custom_emojis,
            subscribe_emoji_id=cfg.subscribe_emoji_id,
        )
    except Exception as exc:
        log.exception("RSS formatting failed for %s/%s", item.source, item.external_id)
        await _defer_processing(
            item,
            content_kind=content_kind,
            quota_source=quota_source,
            quota_day=quota_day,
            quota_limit=quota_limit,
            retry_id=retry_id,
            error=exc,
        )
        return ProcessResult(
            "error", item.source, item.external_id,
            reason=f"{type(exc).__name__}: {exc}", title=analysis.title,
            source_url=item.article_url,
        )

    try:
        reservation = await publication_guard.reserve_publication(
            item.source,
            item.external_id,
            _publication_fingerprint(analysis),
            quota_source=quota_source,
            quota_day=quota_day,
            quota_limit=quota_limit,
        )
    except Exception as exc:
        log.exception("Publication reservation failed for %s/%s", item.source, item.external_id)
        await _defer_processing(
            item,
            content_kind=content_kind,
            quota_source=quota_source,
            quota_day=quota_day,
            quota_limit=quota_limit,
            retry_id=retry_id,
            error=exc,
        )
        return ProcessResult(
            "error", item.source, item.external_id,
            reason=f"{type(exc).__name__}: {exc}", title=analysis.title,
            source_url=item.article_url,
        )
    if not reservation.reserved:
        if reservation.quota_exhausted:
            await db.release_claim(item.source, item.external_id)
            return ProcessResult(
                "daily_limit", item.source, item.external_id,
                reason=reservation.reason, title=analysis.title,
                source_url=item.article_url,
            )
        return ProcessResult(
            "duplicate", item.source, item.external_id,
            reason=reservation.reason, title=analysis.title,
            source_url=item.article_url,
        )

    try:
        with tempfile.TemporaryDirectory(prefix="gildranews_rss_") as tmpdir:
            media: list[tuple[str, str]] = []
            media_dir = Path(tmpdir)
            allowed_media_hosts = RSS_MEDIA_HOSTS.get(item.source)
            if allowed_media_hosts:
                image_url = item.image_url
                video_url = item.video_url
                if item.article_url and (not image_url or not video_url):
                    try:
                        discovered_image, discovered_video = (
                            await rss_source.fetch_article_media(
                                item.article_url,
                                relevance_text=item.title,
                            )
                        )
                        image_url = discovered_image or image_url
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
                            allowed_hosts=allowed_media_hosts,
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
            if not media and cfg.scrape_do_enabled:
                primary_reference = next(
                    (
                        reference for reference in analysis.references
                        if reference.role == "primary"
                    ),
                    analysis.references[0] if analysis.references else None,
                )
                image_query = (
                    primary_reference.query if primary_reference is not None else item.title
                )
                try:
                    searched_image = await scrape_do_images.find_warcraft_image(
                        image_query,
                        entity_kind=(
                            primary_reference.kind if primary_reference is not None else ""
                        ),
                    )
                    if searched_image:
                        source_path = await media_downloader.download(
                            searched_image,
                            media_dir,
                            kind="photo",
                            allowed_hosts=set(scrape_do_images.ALLOWED_IMAGE_HOSTS),
                        )
                        media.append((str(source_path), "photo"))
                except Exception:
                    log.warning(
                        "Scrape.do image discovery failed for %s/%s",
                        item.source,
                        item.external_id,
                        exc_info=True,
                    )
            if not media and analysis.infographic is not None:
                infographic_path = media_dir / "infographic.png"
                rendered = await asyncio.to_thread(
                    render_png, analysis.infographic, infographic_path,
                )
                if rendered:
                    media.append((str(infographic_path), "photo"))
            target_message_id = await tg_writer.publish(
                bot,
                cfg.target_channel,
                post_text,
                media,
                table_rows=(
                    analysis.infographic.table_rows
                    if analysis.infographic is not None
                    else None
                ),
            )
    except Exception as exc:
        log.exception("RSS publish failed for %s/%s", item.source, item.external_id)
        try:
            await publication_guard.fail_publication(
                reservation.reservation_id,
                str(exc),
            )
        except Exception:
            log.exception("Failed to release publication reservation")
        await _defer_processing(
            item,
            content_kind=content_kind,
            quota_source=quota_source,
            quota_day=quota_day,
            quota_limit=quota_limit,
            retry_id=retry_id,
            error=exc,
        )
        return ProcessResult(
            "error", item.source, item.external_id,
            reason=f"{type(exc).__name__}: {exc}", title=analysis.title,
            source_url=item.article_url,
        )

    if target_message_id is None:
        failure = "Telegram API отказал в публикации"
        try:
            await publication_guard.fail_publication(
                reservation.reservation_id,
                failure,
            )
        except Exception:
            log.exception("Failed to release publication reservation")
        await _defer_processing(
            item,
            content_kind=content_kind,
            quota_source=quota_source,
            quota_day=quota_day,
            quota_limit=quota_limit,
            retry_id=retry_id,
            error=failure,
        )
        return ProcessResult(
            "publish_failed", item.source, item.external_id,
            reason=failure, title=analysis.title,
            source_url=item.article_url,
        )
    await publication_guard.complete_publication(
        reservation.reservation_id,
        channel=item.source,
        message_id=item.external_id,
        title=analysis.title,
        body=analysis.body,
        target_message_id=target_message_id,
    )
    return ProcessResult(
        "published", item.source, item.external_id,
        reason=analysis.reason, title=analysis.title,
        source_url=item.article_url,
    )


async def retry_due_items(
    *,
    bot: Bot,
    cfg: Config,
    content_ai: ContentAI,
    on_result: ResultCallback | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    due = await retry_store.due(
        limit=RETRY_LIMIT_PER_RUN,
        now=current_time,
    )
    processed = 0
    published = 0
    failed = 0
    terminal_statuses = {"published", "filtered", "duplicate", "daily_limit"}
    for retry in due:
        retry_id = int(retry["id"])
        try:
            item = _retry_item(str(retry["payload_json"]))
            quota_source = retry["quota_source"]
            result = await process_item(
                bot=bot,
                cfg=cfg,
                item=item,
                content_ai=content_ai,
                content_kind=str(retry["content_kind"]),
                quota_source=str(quota_source) if quota_source else None,
                quota_day=current_time.date() if quota_source else None,
                quota_limit=(
                    int(retry["quota_limit"])
                    if retry["quota_limit"] is not None
                    else None
                ),
                retry_id=retry_id,
            )
        except Exception as exc:
            failed += 1
            log.exception("processing_retry_crashed retry_id=%s", retry_id)
            await retry_store.reschedule(retry_id, error=str(exc))
            continue
        processed += 1
        if result.status in terminal_statuses:
            await retry_store.delete(retry_id)
        else:
            failed += 1
        if result.status == "published":
            published += 1
        if (
            on_result is not None
            and result.status in {"published", "filtered"}
        ):
            try:
                await on_result(result)
            except Exception:
                log.exception("Retry on_result callback failed")
    return {
        "due": len(due),
        "processed": processed,
        "published": published,
        "failed": failed,
    }


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
        elif result.status in {"ai_error", "error", "publish_failed"}:
            detail = result.reason[:240] or result.status
            errors.append(f"{item.source}/{item.external_id}: {detail}")
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
