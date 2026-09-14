from __future__ import annotations

import pytest

from gildranews.adapters.persistence import sqlite


@pytest.mark.asyncio
async def test_recent_published_context_returns_full_posts_from_last_two_days(
    monkeypatch, tmp_path,
) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    try:
        await sqlite.record_published("wowhead", 1, "Старая", "Старый текст")
        await sqlite.record_published("wowhead", 2, "Новая", "Новый полный текст")
        db = await sqlite._get_conn()
        await db.execute(
            "UPDATE published_posts SET posted_at = datetime('now', '-49 hours') "
            "WHERE message_id = 1"
        )
        await db.commit()

        posts = await sqlite.recent_published_context(hours=48, limit=50)

        assert posts == [
            {
                "title": "Новая",
                "body": "Новый полный текст",
                "posted_at": posts[0]["posted_at"],
            },
        ]
    finally:
        await sqlite.close()
