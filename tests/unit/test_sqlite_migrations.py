from __future__ import annotations

import sqlite3 as stdlib_sqlite

import pytest

from gildranews.adapters.ai.news_selector import SelectionAuditRecord
from gildranews.adapters.persistence import sqlite


@pytest.mark.asyncio
async def test_run_metrics_include_pipeline_duration_and_error_rate(
    monkeypatch,
    tmp_path,
) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    try:
        await sqlite.record_run(
            7,
            2,
            1,
            None,
            pipeline="rss",
            duration_ms=1250,
        )
        await sqlite.record_run(
            3,
            0,
            0,
            "AI timeout",
            pipeline="rss",
            duration_ms=240_000,
        )

        latest = await sqlite.last_run()
        summary = await sqlite.run_summary(hours=24)

        assert latest is not None
        assert latest["pipeline"] == "rss"
        assert latest["duration_ms"] == 240_000
        assert summary == {
            "runs": 2,
            "runs_with_errors": 1,
            "measured_runs": 2,
            "avg_duration_ms": 120_625,
            "max_duration_ms": 240_000,
        }
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_fresh_database_records_every_schema_migration(monkeypatch, tmp_path) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))

    await sqlite.init()
    try:
        db = await sqlite._get_conn()
        async with db.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ) as cursor:
            applied = await cursor.fetchall()

        assert applied == list(sqlite.MIGRATION_IDENTITIES)
        assert applied[-1][0] == sqlite.CURRENT_SCHEMA_VERSION
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_legacy_database_is_upgraded_once(monkeypatch, tmp_path) -> None:
    database = tmp_path / "legacy.db"
    with stdlib_sqlite.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL
            );
            CREATE TABLE published_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                posted_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(database))

    await sqlite.init()
    await sqlite.init()
    try:
        db = await sqlite._get_conn()
        async with db.execute("PRAGMA table_info(drafts)") as cursor:
            draft_columns = {row[1] async for row in cursor}
        async with db.execute("PRAGMA table_info(published_posts)") as cursor:
            published_columns = {row[1] async for row in cursor}
        async with db.execute(
            "SELECT version, COUNT(*) FROM schema_migrations GROUP BY version"
        ) as cursor:
            migration_counts = await cursor.fetchall()

        assert {"include_original", "media_type", "hashtag"} <= draft_columns
        assert {"story_key", "revision_key", "fingerprint_json"} <= published_columns
        assert migration_counts == [
            (version, 1) for version, _ in sqlite.MIGRATION_IDENTITIES
        ]
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_selector_decision_is_persisted_without_source_text(monkeypatch, tmp_path) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    try:
        await sqlite.record_news_selector_decision(
            SelectionAuditRecord(
                content_sha256="a" * 64,
                content_kind="news",
                choice="reject",
                confidence=0.98,
                probabilities={"accept": 0.02, "reject": 0.98},
                wow_relevance=0.05,
                has_substance=0.1,
                branch="unknown",
                information_status="opinion",
                model="typesafe/jev-1.13",
                cost=0.00001,
                threshold=0.9,
                shadow_mode=False,
                blocked=True,
                block_reason="not_wow",
                rejection_reason="not_wow",
            )
        )
        db = await sqlite._get_conn()
        async with db.execute(
            """SELECT content_sha256, decision, model, prompt_version, detail_json
               FROM ai_decisions"""
        ) as cursor:
            row = await cursor.fetchone()

        assert row[:4] == (
            "a" * 64,
            "reject",
            "typesafe/jev-1.13",
            "typesafe-selection-v2",
        )
        assert '"blocked": true' in row[4]
        assert '"rejection_reason": "not_wow"' in row[4]
        assert "source text" not in row[4]

        await sqlite.record_news_selector_outcome(
            "a" * 64,
            "news",
            "luna_accepted",
        )
        async with db.execute(
            "SELECT detail_json FROM ai_decisions WHERE content_sha256 = ?",
            ("a" * 64,),
        ) as cursor:
            outcome_row = await cursor.fetchone()
        assert '"outcome": "luna_accepted"' in outcome_row[0]

        summary = await sqlite.news_selector_summary(hours=24)
        assert summary == {
            "total": 1,
            "accepted": 0,
            "rejected": 1,
            "errors": 0,
            "blocked": 1,
            "blocked_not_wow": 1,
            "blocked_low_substance": 0,
            "blocked_promotional": 0,
            "blocked_unclassified": 0,
            "luna_accepted": 1,
            "luna_filtered": 0,
            "luna_errors": 0,
            "luna_would_accept": 0,
            "typesafe_blocked": 0,
            "review_sampled": 0,
            "cost": pytest.approx(0.00001),
            "last_decision_at": summary["last_decision_at"],
        }
        assert summary["last_decision_at"]
    finally:
        await sqlite.close()
