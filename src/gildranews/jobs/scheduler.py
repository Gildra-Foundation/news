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
from gildranews.application import digest, process_reddit, process_rss
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
    ) -> None:
        self._client = client
        self._bot = bot
        self._cfg = cfg
        self._on_result = on_result
        self._content_ai = content_ai
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._startup_tasks: set[asyncio.Task] = set()

    async def run_pipeline(self) -> None:
        if self._client is None:
            return
        from gildranews.application import process_news

        await process_news.run_once(
            self._client,
            self._bot,
            self._cfg,
            on_result=self._on_result,
            news_filter=self._content_ai,
        )

    async def run_rss_pipeline(self) -> None:
        await process_rss.run_once(
            bot=self._bot,
            cfg=self._cfg,
            content_ai=self._content_ai,
            on_result=self._on_result,
        )

    async def run_reddit_pipeline(self) -> None:
        result = await process_reddit.run_once(
            bot=self._bot,
            cfg=self._cfg,
            content_ai=self._content_ai,
            on_result=self._on_result,
        )
        log.info("Daily Reddit topics: %s", result)

    async def cleanup(self) -> None:
        try:
            stats = await db.cleanup_old_data()
            if any(stats.values()):
                log.info(
                    "DB cleanup: drafts=%d seen=%d runs=%d published=%d",
                    stats["drafts"],
                    stats["seen"],
                    stats["runs"],
                    stats["published"],
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
        if self._cfg.interval_minutes > 0:
            if self._cfg.rss_enabled:
                self._scheduler.add_job(
                    self.run_rss_pipeline,
                    trigger="interval",
                    minutes=self._cfg.interval_minutes,
                    id="rss_pipeline",
                    max_instances=1,
                    coalesce=True,
                )
            if self._client is not None:
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
            if self._cfg.reddit_enabled:
                self._scheduler.add_job(
                    self.run_reddit_pipeline,
                    trigger="cron",
                    hour=self._cfg.reddit_daily_hour_utc,
                    minute=0,
                    id="reddit_daily",
                    max_instances=1,
                    coalesce=True,
                )
            self._scheduler.start()
            if self._client is not None:
                log.info(
                    "Safety-net поллинг каждые %d мин, cleanup раз в сутки, "
                    "дайджест по воскресеньям 18:00 UTC",
                    self._cfg.interval_minutes,
                )
            elif not self._cfg.rss_enabled:
                log.info(
                    "Telegram-reader отключён; cleanup раз в сутки, "
                    "дайджест по воскресеньям 18:00 UTC",
                )

        if self._cfg.rss_enabled:
            self._start_task(self.run_rss_pipeline())
        if self._client is not None:
            self._start_task(self.run_pipeline())
        self._start_task(self.cleanup())

    def _start_task(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._startup_tasks.add(task)
        task.add_done_callback(self._startup_tasks.discard)

    async def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        for task in self._startup_tasks:
            task.cancel()
        if self._startup_tasks:
            await asyncio.gather(*self._startup_tasks, return_exceptions=True)
