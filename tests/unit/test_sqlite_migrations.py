from __future__ import annotations

import sqlite3 as stdlib_sqlite

import pytest

from gildranews.adapters.ai.news_selector import SelectionAuditRecord
from gildranews.adapters.persistence import sqlite


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
            "typesafe-selection-v1",
        )
        assert '"blocked": true' in row[4]
        assert "source text" not in row[4]
    finally:
        await sqlite.close()
