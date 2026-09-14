from __future__ import annotations

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
