from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

import aiosqlite

log = logging.getLogger(__name__)

DB_PATH = "data/newsbot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    username TEXT PRIMARY KEY,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS seen_messages (
    channel TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (channel, message_id)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    fetched INTEGER,
    selected INTEGER,
    published INTEGER,
    error TEXT
);
CREATE TABLE IF NOT EXISTS published_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    posted_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_published_posted_at ON published_posts(posted_at);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    image_url TEXT,
    original_text TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pending_edits (
    user_id INTEGER PRIMARY KEY,
    draft_id INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


# ---------- Persistent connection ----------
_conn: aiosqlite.Connection | None = None
_conn_lock = asyncio.Lock()


async def _get_conn() -> aiosqlite.Connection:
    """Возвращает единое соединение на процесс. aiosqlite внутри сериализует
    операции через очередь — безопасно для конкурентных корутин."""
    global _conn
    if _conn is not None:
        return _conn
    async with _conn_lock:
        if _conn is None:
            c = await aiosqlite.connect(DB_PATH)
            # PRAGMA-настройки применяются на это соединение
            await c.execute("PRAGMA journal_mode=WAL")
            await c.execute("PRAGMA synchronous=NORMAL")
            await c.execute("PRAGMA temp_store=MEMORY")
            await c.execute("PRAGMA mmap_size=67108864")
            await c.commit()
            _conn = c
    return _conn


async def close() -> None:
    """Закрыть соединение на shutdown. Идемпотентно."""
    global _conn
    if _conn is not None:
        try:
            await _conn.close()
        except Exception:
            log.warning("DB close raised", exc_info=True)
        _conn = None


def _normalize(username: str) -> str:
    u = username.strip().lstrip("@").lower()
    u = u.removeprefix("https://t.me/")
    u = u.removeprefix("t.me/")
    return u.split("/")[0]


async def init() -> None:
    db = await _get_conn()
    await db.executescript(SCHEMA)
    # Миграция: добавляем колонки в drafts если их нет
    async with db.execute("PRAGMA table_info(drafts)") as cur:
        cols = {row[1] async for row in cur}
    if "include_original" not in cols:
        await db.execute("ALTER TABLE drafts ADD COLUMN include_original INTEGER DEFAULT 0")
    if "media_type" not in cols:
        await db.execute("ALTER TABLE drafts ADD COLUMN media_type TEXT DEFAULT 'photo'")
    if "hashtag" not in cols:
        await db.execute("ALTER TABLE drafts ADD COLUMN hashtag TEXT DEFAULT ''")
    # Миграция published_posts: target_message_id (id поста в нашем канале — для ссылок в дайджесте)
    async with db.execute("PRAGMA table_info(published_posts)") as cur:
        pcols = {row[1] async for row in cur}
    if "target_message_id" not in pcols:
        await db.execute("ALTER TABLE published_posts ADD COLUMN target_message_id INTEGER")
    await db.commit()


# ---------- Sources ----------
async def list_sources() -> list[str]:
    db = await _get_conn()
    async with db.execute("SELECT username FROM sources ORDER BY username") as cur:
        return [row[0] async for row in cur]


async def add_source(username: str) -> bool:
    u = _normalize(username)
    if not u:
        return False
    db = await _get_conn()
    try:
        await db.execute("INSERT INTO sources(username) VALUES (?)", (u,))
        await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def remove_source(username: str) -> bool:
    u = _normalize(username)
    db = await _get_conn()
    cur = await db.execute("DELETE FROM sources WHERE username = ?", (u,))
    await db.commit()
    return cur.rowcount > 0


async def seed_sources(usernames: Iterable[str]) -> None:
    db = await _get_conn()
    for u in usernames:
        await db.execute(
            "INSERT OR IGNORE INTO sources(username) VALUES (?)",
            (_normalize(u),),
        )
    await db.commit()


# ---------- Seen messages / claim ----------
async def is_seen(channel: str, message_id: int) -> bool:
    db = await _get_conn()
    async with db.execute(
        "SELECT 1 FROM seen_messages WHERE channel = ? AND message_id = ?",
        (_normalize(channel), message_id),
    ) as cur:
        return await cur.fetchone() is not None


async def mark_seen(channel: str, message_id: int) -> None:
    db = await _get_conn()
    await db.execute(
        "INSERT OR IGNORE INTO seen_messages(channel, message_id) VALUES (?, ?)",
        (_normalize(channel), message_id),
    )
    await db.commit()


async def claim_message(channel: str, message_id: int) -> bool:
    """Атомарно «застолбить» сообщение. True если первый раз, False если уже было."""
    db = await _get_conn()
    cur = await db.execute(
        "INSERT OR IGNORE INTO seen_messages(channel, message_id) VALUES (?, ?)",
        (_normalize(channel), message_id),
    )
    await db.commit()
    return cur.rowcount > 0


# ---------- Runs ----------
async def record_run(fetched: int, selected: int, published: int, error: str | None) -> None:
    db = await _get_conn()
    await db.execute(
        """INSERT INTO runs(finished_at, fetched, selected, published, error)
           VALUES (CURRENT_TIMESTAMP, ?, ?, ?, ?)""",
        (fetched, selected, published, error),
    )
    await db.commit()


async def last_run() -> dict | None:
    db = await _get_conn()
    async with db.execute(
        "SELECT started_at, finished_at, fetched, selected, published, error "
        "FROM runs ORDER BY id DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    keys = ["started_at", "finished_at", "fetched", "selected", "published", "error"]
    return dict(zip(keys, row))


# ---------- Published / dedup ----------
async def was_published(channel: str, message_id: int) -> str | None:
    db = await _get_conn()
    async with db.execute(
        "SELECT title FROM published_posts WHERE channel = ? AND message_id = ? LIMIT 1",
        (_normalize(channel), message_id),
    ) as cur:
        row = await cur.fetchone()
        return row[0] if row else None


async def record_published(
    channel: str,
    message_id: int,
    title: str,
    body: str,
    target_message_id: int | None = None,
) -> None:
    db = await _get_conn()
    await db.execute(
        "INSERT INTO published_posts(channel, message_id, title, body, target_message_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (_normalize(channel), message_id, title, body, target_message_id),
    )
    await db.commit()


async def recent_published_for_digest(days: int = 7) -> list[dict]:
    """Посты опубликованные за последние N дней, с ID в нашем канале для построения ссылок.
    Только записи где есть target_message_id."""
    db = await _get_conn()
    async with db.execute(
        """SELECT id, title, body, target_message_id, posted_at
           FROM published_posts
           WHERE posted_at >= datetime('now', ?) AND target_message_id IS NOT NULL
           ORDER BY id ASC""",
        (f"-{days} days",),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {"id": r[0], "title": r[1], "body": r[2], "target_message_id": r[3], "posted_at": r[4]}
        for r in rows
    ]


async def recent_published_titles(hours: int = 24, limit: int = 50) -> list[str]:
    db = await _get_conn()
    async with db.execute(
        """SELECT title FROM published_posts
           WHERE posted_at >= datetime('now', ?)
           ORDER BY id DESC LIMIT ?""",
        (f"-{hours} hours", limit),
    ) as cur:
        return [row[0] async for row in cur]


# ---------- Drafts ----------
async def create_draft(
    source_url: str,
    title: str,
    body: str,
    image_url: str | None,
    original_text: str,
    media_type: str = "photo",
    hashtag: str = "",
) -> int:
    db = await _get_conn()
    cur = await db.execute(
        "INSERT INTO drafts(source_url, title, body, image_url, original_text, "
        "media_type, hashtag) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (source_url, title, body, image_url, original_text, media_type, hashtag),
    )
    await db.commit()
    return cur.lastrowid


async def get_draft(draft_id: int) -> dict | None:
    db = await _get_conn()
    async with db.execute(
        "SELECT id, source_url, title, body, image_url, original_text, "
        "COALESCE(include_original, 0), COALESCE(media_type, 'photo'), "
        "COALESCE(hashtag, '') "
        "FROM drafts WHERE id = ?",
        (draft_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "source_url": row[1], "title": row[2],
        "body": row[3], "image_url": row[4], "original_text": row[5],
        "include_original": bool(row[6]),
        "media_type": row[7],
        "hashtag": row[8],
    }


async def set_draft_include_original(draft_id: int, value: bool) -> None:
    db = await _get_conn()
    await db.execute(
        "UPDATE drafts SET include_original = ? WHERE id = ?",
        (1 if value else 0, draft_id),
    )
    await db.commit()


async def update_draft(
    draft_id: int, title: str, body: str, hashtag: str | None = None,
) -> None:
    db = await _get_conn()
    if hashtag is None:
        await db.execute(
            "UPDATE drafts SET title = ?, body = ? WHERE id = ?",
            (title, body, draft_id),
        )
    else:
        await db.execute(
            "UPDATE drafts SET title = ?, body = ?, hashtag = ? WHERE id = ?",
            (title, body, hashtag, draft_id),
        )
    await db.commit()


async def set_draft_image(draft_id: int, image_path: str) -> None:
    db = await _get_conn()
    await db.execute(
        "UPDATE drafts SET image_url = ? WHERE id = ?",
        (image_path, draft_id),
    )
    await db.commit()


async def delete_draft(draft_id: int) -> None:
    db = await _get_conn()
    await db.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
    await db.commit()


async def list_draft_ids() -> list[int]:
    db = await _get_conn()
    async with db.execute("SELECT id FROM drafts") as cur:
        return [row[0] async for row in cur]


# ---------- Pending edits (replaces in-memory dict) ----------
async def set_pending_edit(user_id: int, draft_id: int) -> None:
    db = await _get_conn()
    await db.execute(
        "INSERT INTO pending_edits(user_id, draft_id) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET draft_id=excluded.draft_id, "
        "created_at=CURRENT_TIMESTAMP",
        (user_id, draft_id),
    )
    await db.commit()


async def get_pending_edit(user_id: int) -> int | None:
    db = await _get_conn()
    async with db.execute(
        "SELECT draft_id FROM pending_edits WHERE user_id = ?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else None


async def clear_pending_edit(user_id: int) -> None:
    db = await _get_conn()
    await db.execute("DELETE FROM pending_edits WHERE user_id = ?", (user_id,))
    await db.commit()


# ---------- Maintenance ----------
async def cleanup_old_data() -> dict:
    """Чистит старые данные. Возвращает счётчики удалённого."""
    db = await _get_conn()
    # Драфты старше 24h
    cur = await db.execute(
        "DELETE FROM drafts WHERE created_at < datetime('now', '-24 hours')"
    )
    drafts_removed = cur.rowcount
    # Pending-edit-state старше 24h — тоже шлак
    cur = await db.execute(
        "DELETE FROM pending_edits WHERE created_at < datetime('now', '-24 hours')"
    )
    edits_removed = cur.rowcount
    # seen_messages старше 30 дней
    cur = await db.execute(
        "DELETE FROM seen_messages WHERE seen_at < datetime('now', '-30 days')"
    )
    seen_removed = cur.rowcount
    # runs старше 7 дней
    cur = await db.execute(
        "DELETE FROM runs WHERE started_at < datetime('now', '-7 days')"
    )
    runs_removed = cur.rowcount
    # published_posts старше 30 дней
    cur = await db.execute(
        "DELETE FROM published_posts WHERE posted_at < datetime('now', '-30 days')"
    )
    published_removed = cur.rowcount
    await db.commit()
    # VACUUM нельзя внутри транзакции — отдельным execute
    await db.execute("VACUUM")
    return {
        "drafts": drafts_removed,
        "edits": edits_removed,
        "seen": seen_removed,
        "runs": runs_removed,
        "published": published_removed,
    }
