from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from gildranews.adapters.persistence import processing_retries, sqlite


@pytest.mark.asyncio
async def test_processing_retry_survives_reopen_and_becomes_due(
    monkeypatch,
    tmp_path,
) -> None:
    database = tmp_path / "newsbot.db"
    monkeypatch.setattr(sqlite, "DB_PATH", str(database))
    await sqlite.init()
    now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    payload = json.dumps({"title": "Important news"})

    retry_id = await processing_retries.enqueue(
        source="wowhead",
        external_id=382999,
        payload_json=payload,
        content_kind="news",
        quota_source=None,
        quota_day=None,
        quota_limit=None,
        error="App Server unavailable",
        expires_hours=6,
        now=now,
    )
    assert await processing_retries.due(now=now, limit=10) == []
    assert await processing_retries.counts() == {"pending": 1, "failed": 0}

    await sqlite.close()
    await sqlite.init()
    due = await processing_retries.due(
        now=now + timedelta(minutes=6),
        limit=10,
    )

    assert len(due) == 1
    assert due[0]["id"] == retry_id
    assert due[0]["payload_json"] == payload
    assert due[0]["attempts"] == 1
    await sqlite.close()


@pytest.mark.asyncio
async def test_processing_retry_stops_after_five_failures(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    retry_id = await processing_retries.enqueue(
        source="wowhead",
        external_id=382999,
        payload_json="{}",
        content_kind="news",
        quota_source=None,
        quota_day=None,
        quota_limit=None,
        error="first",
        expires_hours=24,
        now=now,
    )

    statuses = []
    for attempt in range(2, 6):
        statuses.append(
            await processing_retries.reschedule(
                retry_id,
                error=f"failure {attempt}",
                now=now,
            ),
        )

    assert statuses == ["pending", "pending", "pending", "failed"]
    assert await processing_retries.due(
        now=now + timedelta(days=1),
        limit=10,
    ) == []
    assert await processing_retries.counts() == {"pending": 0, "failed": 1}
    await sqlite.close()
