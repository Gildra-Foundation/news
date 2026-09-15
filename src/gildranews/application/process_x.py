"""Scheduled X topic discovery and publication orchestration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from aiogram import Bot

from gildranews.adapters.sources import x_search as x_source
from gildranews.application import process_rss
from gildranews.application.ports import ContentAI
from gildranews.config import Config
from gildranews.domain.models import ProcessResult

log = logging.getLogger(__name__)
MAX_AI_REVIEWS = 8
ResultCallback = Callable[[ProcessResult], Awaitable[None]]


async def run_once(
    *,
    bot: Bot,
    cfg: Config,
    content_ai: ContentAI,
    on_result: ResultCallback | None = None,
    now: datetime | None = None,
) -> dict[str, int | str | None]:
    if not cfg.x_enabled:
        return {"fetched": 0, "reviewed": 0, "published": 0, "error": None}

    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    since = (current_time - timedelta(days=1)).date().isoformat()
    query = f"{cfg.x_search_query} since:{since}"
    try:
        fetched = await x_source.fetch_top_posts(cfg.x_api_key, query)
    except Exception as exc:
        log.exception("GetXAPI search failed")
        return {
            "fetched": 0,
            "reviewed": 0,
            "published": 0,
            "error": type(exc).__name__,
        }

    candidates = {topic.tweet_id: topic for topic in fetched}
    ranked = sorted(
        candidates.values(),
        key=lambda topic: (topic.engagement, topic.likes, topic.reposts),
        reverse=True,
    )
    reviewed = 0
    published = 0
    for topic in ranked[:MAX_AI_REVIEWS]:
        if published >= cfg.x_max_posts_per_run:
            break
        result = await process_rss.process_item(
            bot=bot,
            cfg=cfg,
            item=topic.as_feed_item(),
            content_ai=content_ai,
            content_kind="x_topic",
            quota_source="x",
            quota_day=current_time.date(),
            quota_limit=cfg.x_max_posts_per_day,
        )
        if result.status != "duplicate":
            reviewed += 1
            if on_result is not None:
                try:
                    await on_result(result)
                except Exception:
                    log.exception("X on_result callback failed")
        if result.status == "published":
            published += 1
        elif result.status == "ai_error" or result.status == "daily_limit":
            break

    return {"fetched": len(ranked), "reviewed": reviewed, "published": published, "error": None}
