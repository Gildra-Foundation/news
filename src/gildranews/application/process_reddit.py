"""Daily Reddit topic discovery and publication orchestration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from aiogram import Bot

from gildranews.adapters.sources import reddit as reddit_source
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
    if not cfg.reddit_enabled:
        return {"fetched": 0, "reviewed": 0, "published": 0, "error": None}
    current_time = (now or datetime.now(UTC)).astimezone(UTC)

    candidates: dict[tuple[str, str], reddit_source.RedditTopic] = {}
    errors: list[str] = []
    for subreddit in cfg.reddit_subreddits:
        try:
            topics = await reddit_source.fetch_top_posts(
                cfg.reddit_api_key,
                subreddit,
                limit=cfg.reddit_candidates_per_subreddit,
            )
        except Exception as exc:
            log.exception("RedditAPIs fetch failed for r/%s", subreddit)
            errors.append(f"r/{subreddit}: {type(exc).__name__}")
            continue
        for topic in topics:
            candidates[(topic.subreddit.lower(), topic.reddit_id)] = topic

    ranked = sorted(
        candidates.values(),
        key=lambda topic: (topic.engagement, topic.upvotes, topic.comments),
        reverse=True,
    )
    reviewed = 0
    published = 0
    for topic in ranked[:MAX_AI_REVIEWS]:
        if published >= cfg.reddit_max_posts_per_run:
            break
        result = await process_rss.process_item(
            bot=bot,
            cfg=cfg,
            item=topic.as_feed_item(),
            content_ai=content_ai,
            content_kind="reddit_topic",
            quota_source="reddit",
            quota_day=current_time.date(),
            quota_limit=cfg.reddit_max_posts_per_day,
        )
        if result.status != "duplicate":
            reviewed += 1
            if on_result is not None:
                try:
                    await on_result(result)
                except Exception:
                    log.exception("Reddit on_result callback failed")
        if result.status == "published":
            published += 1
        elif result.status == "ai_error" or result.status == "daily_limit":
            break

    return {
        "fetched": len(ranked),
        "reviewed": reviewed,
        "published": published,
        "error": "; ".join(errors) or None,
    }
