from __future__ import annotations

import pytest

from gildranews.config import Config
from gildranews.jobs.scheduler import ScheduledJobs, find_orphan_screenshots


def test_find_orphan_screenshots_ignores_active_and_unrelated_files(tmp_path) -> None:
    active = tmp_path / "draft_1.png"
    orphan = tmp_path / "draft_2.png"
    unrelated = tmp_path / "cover.png"
    active.touch()
    orphan.touch()
    unrelated.touch()

    assert find_orphan_screenshots(tmp_path, {1}) == [orphan]


def test_start_skips_telegram_pipeline_without_reader() -> None:
    cfg = Config(
        tg_api_id=0,
        tg_api_hash="",
        bot_token="token",
        target_channel="@channel",
        admin_user_id=1,
        gemini_api_key="",
        gemini_model="model",
        lookback_minutes=45,
        interval_minutes=30,
        max_posts_per_run=3,
    )
    jobs = ScheduledJobs(None, object(), cfg, object(), object())
    scheduled: list[str] = []
    startup: list[str] = []

    class FakeScheduler:
        def add_job(self, _func, **kwargs) -> None:
            scheduled.append(kwargs["id"])

        def start(self) -> None:
            return None

    def capture_startup(coroutine) -> None:
        startup.append(coroutine.cr_code.co_name)
        coroutine.close()

    jobs._scheduler = FakeScheduler()
    jobs._start_task = capture_startup

    jobs.start()

    assert "news_pipeline" not in scheduled
    assert scheduled == ["rss_pipeline", "db_cleanup", "weekly_digest"]
    assert startup == ["run_rss_pipeline", "cleanup"]


def test_start_schedules_reddit_and_x_topics_morning_and_evening() -> None:
    cfg = Config(
        tg_api_id=0,
        tg_api_hash="",
        bot_token="token",
        target_channel="@channel",
        admin_user_id=1,
        gemini_api_key="",
        gemini_model="model",
        lookback_minutes=45,
        interval_minutes=30,
        max_posts_per_run=3,
        reddit_enabled=True,
        reddit_api_key="secret",
        x_enabled=True,
        x_api_key="x-secret",
        social_discovery_hours_utc=(8, 18),
    )
    jobs = ScheduledJobs(None, object(), cfg, object(), object())
    scheduled: list[dict] = []

    class FakeScheduler:
        def add_job(self, _func, **kwargs) -> None:
            scheduled.append(kwargs)

        def start(self) -> None:
            return None

    jobs._scheduler = FakeScheduler()
    jobs._start_task = lambda coroutine: coroutine.close()

    jobs.start()

    discovery_job = next(job for job in scheduled if job["id"] == "social_discovery")
    assert discovery_job["trigger"] == "cron"
    assert discovery_job["hour"] == "8,18"
    assert discovery_job["minute"] == 0


def test_start_schedules_forever_guide_refresh_with_mtproto_publisher() -> None:
    cfg = Config(
        tg_api_id=1,
        tg_api_hash="hash",
        bot_token="token",
        target_channel="@channel",
        admin_user_id=1,
        gemini_api_key="",
        gemini_model="model",
        lookback_minutes=45,
        interval_minutes=30,
        max_posts_per_run=3,
        forever_guide_message_id=44,
    )
    jobs = ScheduledJobs(
        None, object(), cfg, object(), object(), publisher_client=object(),
    )
    scheduled: list[dict] = []

    class FakeScheduler:
        def add_job(self, _func, **kwargs) -> None:
            scheduled.append(kwargs)

        def start(self) -> None:
            return None

    jobs._scheduler = FakeScheduler()
    jobs._start_task = lambda coroutine: coroutine.close()

    jobs.start()

    guide_job = next(job for job in scheduled if job["id"] == "forever_guide_refresh")
    assert guide_job["trigger"] == "interval"
    assert guide_job["minutes"] == 30


@pytest.mark.asyncio
async def test_social_discovery_runs_reddit_then_x() -> None:
    cfg = Config(
        tg_api_id=0,
        tg_api_hash="",
        bot_token="token",
        target_channel="@channel",
        admin_user_id=1,
        gemini_api_key="",
        gemini_model="model",
        lookback_minutes=45,
        interval_minutes=30,
        max_posts_per_run=3,
        reddit_enabled=True,
        reddit_api_key="secret",
        x_enabled=True,
        x_api_key="x-secret",
    )
    jobs = ScheduledJobs(None, object(), cfg, object(), object())
    calls: list[str] = []

    async def reddit() -> None:
        calls.append("reddit")

    async def x() -> None:
        calls.append("x")

    jobs.run_reddit_pipeline = reddit
    jobs.run_x_pipeline = x

    await jobs.run_social_discovery()

    assert calls == ["reddit", "x"]
