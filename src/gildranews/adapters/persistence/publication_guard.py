from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import date

import aiosqlite

from gildranews.adapters.persistence import sqlite
from gildranews.domain.dedup import compare_fingerprints
from gildranews.domain.models import EventFingerprint

_lock = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class PublicationReservation:
    reserved: bool
    reservation_id: int | None = None
    reason: str = ""
    quota_exhausted: bool = False


def _fingerprint_json(fingerprint: EventFingerprint) -> str:
    return json.dumps(asdict(fingerprint), ensure_ascii=False, sort_keys=True)


def _fingerprint_from_json(payload: str) -> EventFingerprint:
    value = json.loads(payload)
    value["scope"] = tuple(value.get("scope") or ())
    value["material_facts"] = tuple(value.get("material_facts") or ())
    return EventFingerprint(**value)


async def _recover_stale_reservations(db: aiosqlite.Connection) -> None:
    async with db.execute(
        """SELECT id, quota_source, quota_day FROM publication_candidates
           WHERE status='reserved'
             AND updated_at < datetime('now', '-30 minutes')"""
    ) as cursor:
        stale_rows = await cursor.fetchall()
    for stale_id, quota_source, quota_day in stale_rows:
        await db.execute(
            """UPDATE publication_candidates
               SET status='failed', failure='stale reservation recovered',
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (stale_id,),
        )
        if quota_source and quota_day:
            await db.execute(
                """UPDATE daily_source_usage
                   SET publication_count=MAX(0, publication_count - 1),
                       updated_at=CURRENT_TIMESTAMP
                   WHERE source=? AND day_utc=?""",
                (quota_source, quota_day),
            )


async def reserve_publication(
    source: str,
    external_id: int,
    fingerprint: EventFingerprint,
    *,
    quota_source: str | None = None,
    quota_day: date | None = None,
    quota_limit: int | None = None,
    lookback_days: int = 14,
) -> PublicationReservation:
    """Atomically reserve a story revision and an optional daily source slot."""
    db = await sqlite._get_conn()
    normalized_source = sqlite._normalize(source)
    day_value = quota_day.isoformat() if quota_day is not None else None
    async with _lock:
        try:
            await db.execute("BEGIN IMMEDIATE")
            await _recover_stale_reservations(db)
            async with db.execute(
                """SELECT fingerprint_json FROM publication_candidates
                   WHERE story_key=? AND status IN ('reserved', 'published')
                     AND created_at >= datetime('now', ?)
                   ORDER BY id DESC""",
                (fingerprint.story_key, f"-{max(1, lookback_days)} days"),
            ) as cursor:
                previous_rows = await cursor.fetchall()
            for row in previous_rows:
                previous = _fingerprint_from_json(row[0])
                if compare_fingerprints(fingerprint, previous).is_duplicate:
                    await db.rollback()
                    return PublicationReservation(
                        reserved=False,
                        reason="Тот же сюжет уже зарезервирован или опубликован",
                    )

            if quota_source and day_value and quota_limit is not None:
                async with db.execute(
                    """SELECT publication_count FROM daily_source_usage
                       WHERE source=? AND day_utc=?""",
                    (quota_source, day_value),
                ) as cursor:
                    row = await cursor.fetchone()
                used = int(row[0]) if row else 0
                if used >= max(0, quota_limit):
                    await db.rollback()
                    return PublicationReservation(
                        reserved=False,
                        reason="Суточный лимит источника исчерпан",
                        quota_exhausted=True,
                    )

            cursor = await db.execute(
                """INSERT INTO publication_candidates(
                       source, external_id, story_key, revision_key,
                       fingerprint_json, quota_source, quota_day
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    normalized_source,
                    external_id,
                    fingerprint.story_key,
                    fingerprint.revision_key,
                    _fingerprint_json(fingerprint),
                    quota_source,
                    day_value,
                ),
            )
            if quota_source and day_value and quota_limit is not None:
                await db.execute(
                    """INSERT INTO daily_source_usage(source, day_utc, publication_count)
                       VALUES (?, ?, 1)
                       ON CONFLICT(source, day_utc) DO UPDATE SET
                           publication_count=publication_count + 1,
                           updated_at=CURRENT_TIMESTAMP""",
                    (quota_source, day_value),
                )
            await db.commit()
            return PublicationReservation(True, int(cursor.lastrowid))
        except aiosqlite.IntegrityError:
            await db.rollback()
            return PublicationReservation(
                reserved=False,
                reason="Тот же сюжет уже зарезервирован или опубликован",
            )
        except Exception:
            await db.rollback()
            raise


async def complete_publication(
    reservation_id: int | None,
    *,
    channel: str,
    message_id: int,
    title: str,
    body: str,
    target_message_id: int | None,
) -> None:
    if reservation_id is None:
        await sqlite.record_published(
            channel, message_id, title, body, target_message_id,
        )
        return
    db = await sqlite._get_conn()
    async with _lock:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """SELECT story_key, revision_key, fingerprint_json
               FROM publication_candidates WHERE id=? AND status='reserved'""",
            (reservation_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            await db.rollback()
            raise RuntimeError("Резерв публикации не найден")
        await db.execute(
            """INSERT INTO published_posts(
                   channel, message_id, title, body, target_message_id,
                   story_key, revision_key, fingerprint_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sqlite._normalize(channel), message_id, title, body,
                target_message_id, row[0], row[1], row[2],
            ),
        )
        await db.execute(
            """UPDATE publication_candidates
               SET status='published', target_message_id=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (target_message_id, reservation_id),
        )
        await db.commit()


async def fail_publication(reservation_id: int | None, failure: str) -> None:
    if reservation_id is None:
        return
    db = await sqlite._get_conn()
    async with _lock:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """SELECT quota_source, quota_day FROM publication_candidates
               WHERE id=? AND status='reserved'""",
            (reservation_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is not None:
            await db.execute(
                """UPDATE publication_candidates SET status='failed', failure=?,
                       updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (failure[:500], reservation_id),
            )
            if row[0] and row[1]:
                await db.execute(
                    """UPDATE daily_source_usage
                       SET publication_count=MAX(0, publication_count - 1),
                           updated_at=CURRENT_TIMESTAMP
                       WHERE source=? AND day_utc=?""",
                    (row[0], row[1]),
                )
        await db.commit()


async def daily_publication_count(source: str, day_utc: date) -> int:
    db = await sqlite._get_conn()
    async with db.execute(
        """SELECT publication_count FROM daily_source_usage
           WHERE source=? AND day_utc=?""",
        (source, day_utc.isoformat()),
    ) as cursor:
        row = await cursor.fetchone()
    return int(row[0]) if row else 0
