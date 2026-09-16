from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gildranews.adapters.persistence import sqlite

RETRY_DELAYS_MINUTES = (5, 15, 60, 180)
MAX_ATTEMPTS = 5


def _utc_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


async def enqueue(
    *,
    source: str,
    external_id: int,
    payload_json: str,
    content_kind: str,
    quota_source: str | None,
    quota_day: str | None,
    quota_limit: int | None,
    error: str,
    expires_hours: int,
    now: datetime | None = None,
) -> int:
    """Persist a failed item without postponing an already queued retry."""
    db = await sqlite._get_conn()
    current = _utc_now(now)
    next_retry_at = int(
        (current + timedelta(minutes=RETRY_DELAYS_MINUTES[0])).timestamp()
    )
    expires_at = int((current + timedelta(hours=max(1, expires_hours))).timestamp())
    await db.execute(
        """INSERT INTO processing_retries(
               source, external_id, payload_json, content_kind,
               quota_source, quota_day, quota_limit, next_retry_at,
               expires_at, last_error
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source, external_id) DO UPDATE SET
               payload_json=excluded.payload_json,
               content_kind=excluded.content_kind,
               quota_source=excluded.quota_source,
               quota_day=excluded.quota_day,
               quota_limit=excluded.quota_limit,
               last_error=excluded.last_error,
               updated_at=CURRENT_TIMESTAMP""",
        (
            sqlite._normalize(source), external_id, payload_json, content_kind,
            quota_source, quota_day, quota_limit, next_retry_at,
            expires_at, error[:500],
        ),
    )
    await db.commit()
    async with db.execute(
        "SELECT id FROM processing_retries WHERE source=? AND external_id=?",
        (sqlite._normalize(source), external_id),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("Не удалось сохранить повтор обработки")
    return int(row[0])


async def due(*, limit: int, now: datetime | None = None) -> list[dict]:
    db = await sqlite._get_conn()
    now_ts = int(_utc_now(now).timestamp())
    await db.execute(
        """UPDATE processing_retries
           SET status='failed', last_error='retry expired',
               updated_at=CURRENT_TIMESTAMP
           WHERE status='pending' AND expires_at <= ?""",
        (now_ts,),
    )
    await db.commit()
    async with db.execute(
        """SELECT id, source, external_id, payload_json, content_kind,
                  quota_source, quota_day, quota_limit, attempts
           FROM processing_retries
           WHERE status='pending' AND next_retry_at <= ?
           ORDER BY next_retry_at, id LIMIT ?""",
        (now_ts, max(1, limit)),
    ) as cursor:
        rows = await cursor.fetchall()
    keys = (
        "id", "source", "external_id", "payload_json", "content_kind",
        "quota_source", "quota_day", "quota_limit", "attempts",
    )
    return [dict(zip(keys, row)) for row in rows]


async def reschedule(
    retry_id: int,
    *,
    error: str,
    now: datetime | None = None,
) -> str:
    db = await sqlite._get_conn()
    current = _utc_now(now)
    async with db.execute(
        "SELECT attempts, expires_at FROM processing_retries WHERE id=?",
        (retry_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return "missing"
    attempts = int(row[0]) + 1
    expired = int(row[1]) <= int(current.timestamp())
    status = "failed" if expired or attempts >= MAX_ATTEMPTS else "pending"
    delay_index = min(attempts - 1, len(RETRY_DELAYS_MINUTES) - 1)
    next_retry_at = int(
        (current + timedelta(minutes=RETRY_DELAYS_MINUTES[delay_index])).timestamp()
    )
    await db.execute(
        """UPDATE processing_retries
           SET status=?, attempts=?, next_retry_at=?, last_error=?,
               updated_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        (status, attempts, next_retry_at, error[:500], retry_id),
    )
    await db.commit()
    return status


async def delete(retry_id: int) -> None:
    db = await sqlite._get_conn()
    await db.execute("DELETE FROM processing_retries WHERE id=?", (retry_id,))
    await db.commit()


async def counts() -> dict[str, int]:
    db = await sqlite._get_conn()
    result = {"pending": 0, "failed": 0}
    async with db.execute(
        "SELECT status, COUNT(*) FROM processing_retries GROUP BY status",
    ) as cursor:
        async for status, count in cursor:
            if status in result:
                result[status] = int(count)
    return result
