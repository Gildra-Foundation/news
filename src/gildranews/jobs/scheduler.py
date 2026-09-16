from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from gildranews.adapters.persistence import sqlite as db
from gildranews.adapters.rendering import cards
from gildranews.adapters.warcraft.emoji_registry import TelegramEmojiRegistry
from gildranews.application import (
    digest,
    forever_guide,
    process_reddit,
    process_rss,
    process_x,
)
from gildranews.application.ports import ContentAI
from gildranews.config import Config
from gildranews.domain.models import ProcessResult

if TYPE_CHECKING:
    from telethon import TelegramClient

log = logging.getLogger(__name__)

ResultCallback = Callable[[ProcessResult], Awaitable[None]]
SCREENSHOT_PATTERN = re.compile(r"draft_(\d+)\.png$")


def find_orphan_screenshots(directory: Path, active_draft_ids: set[int]) -> list[Path]:
    orphans: list[Path] = []
    for path in directory.iterdir():
        match = SCREENSHOT_PATTERN.fullmatch(path.name)
        if match and int(match.group(1)) not in active_draft_ids:
            orphans.append(path)
    return sorted(orphans)


class ScheduledJobs:
    def __init__(
        self,
        client: TelegramClient | None,
        bot: Bot,
        cfg: Config,
        on_result: ResultCallback,
        content_ai: ContentAI,
        publisher_client: TelegramClient | None = None,
    ) -> None:
        self._client = client
        self._bot = bot
        self._cfg = cfg
        self._on_result = on_result
        self._content_ai = content_ai
        self._publisher_client = publisher_client
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._startup_tasks: set[asyncio.Task] = set()
        self._pipeline_lock = asyncio.Lock()

    async def run_pipeline(self) -> None:
        if self._client is None:
            return
        from gildranews.application import process_news

        async with self._pipeline_lock:
            await process_news.run_once(
                self._client,
                self._bot,
                self._cfg,
                on_result=self._on_result,
                news_filter=self._content_ai,
            )

    async def run_rss_pipeline(self) -> None:
        async with self._pipeline_lock:
            await process_rss.run_once(
                bot=self._bot,
                cfg=self._cfg,
                content_ai=self._content_ai,
                on_result=self._on_result,
            )

    async def retry_processing(self) -> None:
        async with self._pipeline_lock:
            result = await process_rss.retry_due_items(
                bot=self._bot,
                cfg=self._cfg,
                content_ai=self._content_ai,
                on_result=self._on_result,
            )
        if result["due"]:
            log.info("Processing retry queue: %s", result)

    async def run_reddit_pipeline(self) -> None:
        async with self._pipeline_lock:
            result = await process_reddit.run_once(
                bot=self._bot,
                cfg=self._cfg,
                content_ai=self._content_ai,
                on_result=self._on_result,
            )
        log.info("Daily Reddit topics: %s", result)

    async def run_x_pipeline(self) -> None:
        async with self._pipeline_lock:
            result = await process_x.run_once(
                bot=self._bot,
                cfg=self._cfg,
                content_ai=self._content_ai,
                on_result=self._on_result,
            )
        log.info("Scheduled X topics: %s", result)

    async def run_social_discovery(self) -> None:
        if self._cfg.reddit_enabled:
            await self.run_reddit_pipeline()
        if self._cfg.x_enabled:
            await self.run_x_pipeline()

    async def retry_custom_emojis(self) -> None:
        if not self._cfg.emoji_autocreate_enabled:
            return
        registry = TelegramEmojiRegistry(
            self._bot,
            owner_user_id=self._cfg.admin_user_id,
            enabled=True,
            set_prefix=self._cfg.emoji_set_prefix,
            daily_upload_limit=self._cfg.emoji_max_new_per_day,
            upload_timeout_seconds=self._cfg.emoji_upload_timeout_seconds,
        )
        completed = await registry.retry_due(limit=2)
        if completed:
            log.info("Custom Emoji queue: uploaded=%d", completed)

    async def refresh_forever_guide(self) -> None:
        if self._publisher_client is None or self._cfg.forever_guide_message_id <= 0:
            return
        try:
            result = await forever_guide.refresh(self._publisher_client, self._cfg)
            if result.get("updated"):
                log.info("WoW: Forever guide refreshed: %s", result)
        except Exception:
            log.exception("WoW: Forever guide refresh failed")

    async def cleanup(self) -> None:
        try:
            stats = await db.cleanup_old_data()
            if any(stats.values()):
                log.info(
                    "DB cleanup: drafts=%d seen=%d runs=%d published=%d retries=%d",
                    stats["drafts"],
                    stats["seen"],
                    stats["runs"],
                    stats["published"],
                    stats["retries"],
                )

            active_draft_ids = set(await db.list_draft_ids())
            for path in find_orphan_screenshots(cards.screenshot_dir(), active_draft_ids):
                cards.cleanup_screenshot(str(path))
        except Exception:
            log.exception("cleanup failed")

    async def publish_digest(self) -> None:
        try:
            result = await digest.build_and_publish(
                self._bot, self._cfg, content_ai=self._content_ai,
            )
            log.info("Weekly digest: %s", result)
            if not self._cfg.admin_user_id:
                return
            message = (
                f"📅 Дайджест опубликован. Пунктов: {result.get('posts_count', 0)}"
                if result.get("published")
                else f"⚠️ Дайджест не вышел: {result.get('reason')}"
            )
            try:
                await self._bot.send_message(self._cfg.admin_user_id, message)
            except TelegramAPIError:
                log.warning("digest admin notify failed", exc_info=True)
        except Exception:
            log.exception("scheduled digest failed")

    def start(self) -> None:
        if self._cfg.interval_minutes > 0 and self._cfg.rss_enabled:
            self._scheduler.add_job(
                self.run_rss_pipeline,
                trigger="interval",
                minutes=self._cfg.interval_minutes,
                id="rss_pipeline",
                max_instances=1,
                coalesce=True,
            )
        self._scheduler.add_job(
            self.retry_processing,
            trigger="interval",
            minutes=5,
            id="processing_retry",
            max_instances=1,
            coalesce=True,
        )
        if self._cfg.interval_minutes > 0 and self._client is not None:
            self._scheduler.add_job(
                self.run_pipeline,
                trigger="interval",
                minutes=self._cfg.interval_minutes,
                id="news_pipeline",
                max_instances=1,
                coalesce=True,
            )
        self._scheduler.add_job(
            self.cleanup,
            trigger="interval",
            hours=24,
            id="db_cleanup",
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.add_job(
            self.publish_digest,
            trigger="cron",
            day_of_week="sun",
            hour=18,
            minute=0,
            id="weekly_digest",
            max_instances=1,
            coalesce=True,
        )
        if self._cfg.reddit_enabled or self._cfg.x_enabled:
            self._scheduler.add_job(
                self.run_social_discovery,
                trigger="cron",
                hour=",".join(map(str, self._cfg.social_discovery_hours_utc)),
                minute=0,
                id="social_discovery",
                max_instances=1,
                coalesce=True,
            )
        if self._cfg.emoji_autocreate_enabled:
            self._scheduler.add_job(
                self.retry_custom_emojis,
                trigger="interval",
                minutes=5,
                id="custom_emoji_retry",
                max_instances=1,
                coalesce=True,
            )
        if (
            self._publisher_client is not None
            and self._cfg.forever_guide_message_id > 0
        ):
            self._scheduler.add_job(
                self.refresh_forever_guide,
                trigger="interval",
                minutes=self._cfg.forever_guide_refresh_minutes,
                id="forever_guide_refresh",
                max_instances=1,
                coalesce=True,
            )
        self._scheduler.start()
        log.info(
            "Processing retries every 5 minutes; cleanup daily; "
            "digest Sunday 18:00 UTC",
        )

        self._start_task(self.retry_processing())
        if self._cfg.rss_enabled:
            self._start_task(self.run_rss_pipeline())
        if self._client is not None:
            self._start_task(self.run_pipeline())
        self._start_task(self.cleanup())
        if self._cfg.emoji_autocreate_enabled:
            self._start_task(self.retry_custom_emojis())
        if self._publisher_client is not None and self._cfg.forever_guide_message_id > 0:
            self._start_task(self.refresh_forever_guide())

    def _start_task(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._startup_tasks.add(task)
        task.add_done_callback(self._startup_task_done)

    def _startup_task_done(self, task: asyncio.Task) -> None:
        self._startup_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error(
                "Startup job failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    async def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        for task in self._startup_tasks:
            task.cancel()
        if self._startup_tasks:
            await asyncio.gather(*self._startup_tasks, return_exceptions=True)
