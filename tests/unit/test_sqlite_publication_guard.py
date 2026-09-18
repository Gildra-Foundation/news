from __future__ import annotations

import asyncio
from datetime import date

import aiosqlite
import pytest

from gildranews.adapters.persistence import publication_guard, sqlite
from gildranews.domain.models import EventFingerprint


def _fingerprint(*, status: str = "announced", fact: str = "урон снижен на 20%") -> EventFingerprint:
    return EventFingerprint(
        game_branch="retail",
        version="12.2.5",
        subject="ослабление босса Сзорак",
        action="снизить урон",
        status=status,
        effective_date="",
        material_facts=(fact,),
    )


@pytest.mark.asyncio
async def test_same_story_from_two_sources_gets_one_atomic_reservation(
    monkeypatch, tmp_path,
) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    try:
        results = await asyncio.gather(
            publication_guard.reserve_publication("wowhead", 101, _fingerprint()),
            publication_guard.reserve_publication("icy-veins", 202, _fingerprint()),
        )

        assert sum(result.reserved for result in results) == 1
        duplicate = next(result for result in results if not result.reserved)
        assert duplicate.reason == "Тот же сюжет уже зарезервирован или опубликован"
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_paraphrased_story_with_different_key_is_blocked(
    monkeypatch, tmp_path,
) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    previous = EventFingerprint(
        game_branch="retail",
        version="",
        subject="призрачные фиксации на Извивающемся алтаре",
        action="включить танков в третью волну",
        status="live",
        effective_date="",
        material_facts=("Танки всегда получают фиксацию в третьей волне",),
    )
    paraphrase = EventFingerprint(
        game_branch="retail",
        version="12.1",
        subject="Неприятная фиксация на Извивающемся алтаре",
        action="исправить выбор танков",
        status="live",
        effective_date="2026-09-17",
        material_facts=(
            "Неприятная фиксация выбирала танков чаще, чем предусмотрено",
        ),
    )
    try:
        first = await publication_guard.reserve_publication("wowhead", 101, previous)
        assert first.reserved
        await publication_guard.complete_publication(
            first.reservation_id,
            channel="wowhead",
            message_id=101,
            title="Первый пост",
            body="Текст",
            target_message_id=64,
        )

        duplicate = await publication_guard.reserve_publication(
            "icy-veins", 202, paraphrase,
        )

        assert duplicate.reserved is False
        assert duplicate.reason == "Тот же сюжет уже зарезервирован или опубликован"
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_material_story_update_is_allowed(monkeypatch, tmp_path) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    try:
        first = await publication_guard.reserve_publication("wowhead", 101, _fingerprint())
        assert first.reserved
        await publication_guard.complete_publication(
            first.reservation_id,
            channel="wowhead",
            message_id=101,
            title="Первый пост",
            body="Текст",
            target_message_id=10,
        )

        update = await publication_guard.reserve_publication(
            "icy-veins",
            202,
            _fingerprint(status="live", fact="изменение уже действует"),
        )

        assert update.reserved
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_daily_source_quota_survives_database_reopen(monkeypatch, tmp_path) -> None:
    database = tmp_path / "newsbot.db"
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(database))
    await sqlite.init()
    day = date(2026, 9, 15)
    try:
        for index in range(2):
            reservation = await publication_guard.reserve_publication(
                "reddit:wow",
                100 + index,
                EventFingerprint(
                    game_branch="retail",
                    version="",
                    subject=f"сюжет {index}",
                    action="обсуждение",
                    status="",
                    effective_date="",
                ),
                quota_source="reddit",
                quota_day=day,
                quota_limit=2,
            )
            assert reservation.reserved
            await publication_guard.complete_publication(
                reservation.reservation_id,
                channel="reddit:wow",
                message_id=100 + index,
                title=f"Пост {index}",
                body="Текст",
                target_message_id=20 + index,
            )

        await sqlite.close()
        await sqlite.init()
        blocked = await publication_guard.reserve_publication(
            "reddit:competitivewow",
            999,
            EventFingerprint(
                game_branch="retail",
                version="",
                subject="третий сюжет",
                action="обсуждение",
                status="",
                effective_date="",
            ),
            quota_source="reddit",
            quota_day=day,
            quota_limit=2,
        )

        assert not blocked.reserved
        assert blocked.quota_exhausted
        assert await publication_guard.daily_publication_count("reddit", day) == 2
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_stale_reservation_releases_story_and_daily_slot(monkeypatch, tmp_path) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    day = date(2026, 9, 15)
    try:
        stale = await publication_guard.reserve_publication(
            "x",
            1,
            _fingerprint(),
            quota_source="x",
            quota_day=day,
            quota_limit=1,
        )
        assert stale.reserved
        db = await sqlite._get_conn()
        await db.execute(
            "UPDATE publication_candidates SET updated_at=datetime('now', '-31 minutes')"
        )
        await db.commit()

        replacement = await publication_guard.reserve_publication(
            "x",
            2,
            EventFingerprint(
                game_branch="retail",
                version="12.2.5",
                subject="другой сюжет",
                action="добавить событие",
                status="announced",
                effective_date="",
            ),
            quota_source="x",
            quota_day=day,
            quota_limit=1,
        )

        assert replacement.reserved
        assert await publication_guard.daily_publication_count("x", day) == 1
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_failed_publication_completion_rolls_back_transaction(
    monkeypatch,
    tmp_path,
) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    try:
        reservation = await publication_guard.reserve_publication(
            "wowhead",
            303,
            _fingerprint(),
        )
        assert reservation.reserved
        db = await sqlite._get_conn()
        await db.execute(
            """CREATE TRIGGER reject_published_post
               BEFORE INSERT ON published_posts
               BEGIN SELECT RAISE(ABORT, 'forced test failure'); END""",
        )
        await db.commit()

        with pytest.raises(aiosqlite.DatabaseError, match="forced test failure"):
            await publication_guard.complete_publication(
                reservation.reservation_id,
                channel="wowhead",
                message_id=303,
                title="Пост",
                body="Текст",
                target_message_id=55,
            )

        assert not db.in_transaction
    finally:
        await sqlite.close()
