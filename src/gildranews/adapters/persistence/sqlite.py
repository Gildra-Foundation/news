from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable, Sequence

import aiosqlite

from gildranews.domain.models import (
    PublishedPostContext,
    ResolvedWarcraftEntity,
    TelegramEmojiAsset,
)

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
    posted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    story_key TEXT,
    revision_key TEXT,
    fingerprint_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_published_posted_at ON published_posts(posted_at);
CREATE TABLE IF NOT EXISTS publication_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id INTEGER NOT NULL,
    story_key TEXT NOT NULL,
    revision_key TEXT NOT NULL,
    fingerprint_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'reserved',
    quota_source TEXT,
    quota_day TEXT,
    failure TEXT,
    target_message_id INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_publication_candidates_story
    ON publication_candidates(story_key, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_publication_candidates_active_revision
    ON publication_candidates(story_key, revision_key)
    WHERE status IN ('reserved', 'published');
CREATE TABLE IF NOT EXISTS daily_source_usage (
    source TEXT NOT NULL,
    day_utc TEXT NOT NULL,
    publication_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, day_utc)
);
CREATE TABLE IF NOT EXISTS warcraft_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch TEXT NOT NULL,
    kind TEXT NOT NULL,
    external_id INTEGER NOT NULL,
    canonical_name TEXT NOT NULL,
    localized_name TEXT NOT NULL,
    page_url TEXT NOT NULL,
    icon_url TEXT NOT NULL,
    icon_sha256 TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(branch, kind, external_id)
);
CREATE INDEX IF NOT EXISTS idx_warcraft_entities_icon
    ON warcraft_entities(icon_sha256);
CREATE TABLE IF NOT EXISTS telegram_emoji_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    source_url TEXT NOT NULL,
    local_path TEXT NOT NULL,
    fallback TEXT NOT NULL,
    custom_emoji_id TEXT,
    file_id TEXT,
    sticker_set_name TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    next_retry_at TEXT,
    ready_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_telegram_emoji_status
    ON telegram_emoji_assets(status, next_retry_at);
CREATE TABLE IF NOT EXISTS service_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    image_url TEXT,
    original_text TEXT,
    inline_links_json TEXT NOT NULL DEFAULT '[]',
    custom_emojis_json TEXT NOT NULL DEFAULT '[]',
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
    if "inline_links_json" not in cols:
        await db.execute(
            "ALTER TABLE drafts ADD COLUMN inline_links_json TEXT NOT NULL DEFAULT '[]'"
        )
    if "custom_emojis_json" not in cols:
        await db.execute(
            "ALTER TABLE drafts ADD COLUMN custom_emojis_json TEXT NOT NULL DEFAULT '[]'"
        )
    # Миграция published_posts: target_message_id (id поста в нашем канале — для ссылок в дайджесте)
    async with db.execute("PRAGMA table_info(published_posts)") as cur:
        pcols = {row[1] async for row in cur}
    if "target_message_id" not in pcols:
        await db.execute("ALTER TABLE published_posts ADD COLUMN target_message_id INTEGER")
    if "story_key" not in pcols:
        await db.execute("ALTER TABLE published_posts ADD COLUMN story_key TEXT")
    if "revision_key" not in pcols:
        await db.execute("ALTER TABLE published_posts ADD COLUMN revision_key TEXT")
    if "fingerprint_json" not in pcols:
        await db.execute("ALTER TABLE published_posts ADD COLUMN fingerprint_json TEXT")
    await db.execute(
        """INSERT OR IGNORE INTO daily_source_usage(source, day_utc, publication_count)
           SELECT 'reddit', date(posted_at), COUNT(*) FROM published_posts
           WHERE channel LIKE 'reddit:%' GROUP BY date(posted_at)"""
    )
    await db.execute(
        """INSERT OR IGNORE INTO daily_source_usage(source, day_utc, publication_count)
           SELECT 'x', date(posted_at), COUNT(*) FROM published_posts
           WHERE channel = 'x' GROUP BY date(posted_at)"""
    )
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


async def release_claim(channel: str, message_id: int) -> None:
    """Allow a transiently failed item to be retried by the next polling run."""
    db = await _get_conn()
    await db.execute(
        "DELETE FROM seen_messages WHERE channel = ? AND message_id = ?",
        (_normalize(channel), message_id),
    )
    await db.commit()


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


async def recent_published_context(
    hours: int = 48,
    limit: int = 50,
) -> list[PublishedPostContext]:
    """Return bounded recent post bodies so AI can detect semantic duplicates."""
    bounded_hours = max(1, min(hours, 168))
    bounded_limit = max(1, min(limit, 100))
    db = await _get_conn()
    async with db.execute(
        """SELECT title, body, posted_at FROM published_posts
           WHERE posted_at >= datetime('now', ?)
           ORDER BY id DESC LIMIT ?""",
        (f"-{bounded_hours} hours", bounded_limit),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "title": row[0],
            "body": row[1][:1_000],
            "posted_at": row[2],
        }
        for row in rows
    ]


# ---------- Warcraft entities / Telegram custom emoji ----------
async def upsert_warcraft_entity(
    entity: ResolvedWarcraftEntity,
    *,
    icon_sha256: str | None,
) -> None:
    db = await _get_conn()
    await db.execute(
        """INSERT INTO warcraft_entities(
               branch, kind, external_id, canonical_name, localized_name,
               page_url, icon_url, icon_sha256
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(branch, kind, external_id) DO UPDATE SET
               canonical_name=excluded.canonical_name,
               localized_name=excluded.localized_name,
               page_url=excluded.page_url,
               icon_url=excluded.icon_url,
               icon_sha256=COALESCE(excluded.icon_sha256, warcraft_entities.icon_sha256),
               updated_at=CURRENT_TIMESTAMP""",
        (
            entity.branch,
            entity.kind,
            entity.external_id,
            entity.canonical_name,
            entity.localized_name,
            entity.page_url,
            entity.icon_url,
            icon_sha256,
        ),
    )
    await db.commit()


async def upsert_emoji_asset(
    *,
    sha256: str,
    source_url: str,
    local_path: str,
    fallback: str,
) -> None:
    db = await _get_conn()
    await db.execute(
        """INSERT INTO telegram_emoji_assets(
               sha256, source_url, local_path, fallback
           ) VALUES (?, ?, ?, ?)
           ON CONFLICT(sha256) DO UPDATE SET
               source_url=excluded.source_url,
               local_path=excluded.local_path,
               fallback=excluded.fallback,
               updated_at=CURRENT_TIMESTAMP""",
        (sha256, source_url, local_path, fallback),
    )
    await db.commit()


async def emoji_asset_by_hash(sha256: str) -> dict | None:
    db = await _get_conn()
    async with db.execute(
        """SELECT id, sha256, source_url, local_path, fallback,
                  custom_emoji_id, file_id, sticker_set_name, status,
                  attempts, last_error, next_retry_at
           FROM telegram_emoji_assets WHERE sha256 = ?""",
        (sha256,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    keys = [
        "id", "sha256", "source_url", "local_path", "fallback",
        "custom_emoji_id", "file_id", "sticker_set_name", "status",
        "attempts", "last_error", "next_retry_at",
    ]
    return dict(zip(keys, row, strict=True))


async def claim_emoji_asset(sha256: str) -> bool:
    db = await _get_conn()
    cur = await db.execute(
        """UPDATE telegram_emoji_assets
           SET status='uploading', attempts=attempts + 1,
               last_error=NULL, updated_at=CURRENT_TIMESTAMP
           WHERE sha256 = ? AND attempts < 5 AND (
               status = 'queued'
               OR (status = 'failed' AND (
                   next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP
               ))
               OR (status = 'uploading' AND updated_at < datetime('now', '-15 minutes'))
           )""",
        (sha256,),
    )
    await db.commit()
    return cur.rowcount == 1


async def mark_emoji_asset_ready(
    sha256: str,
    *,
    custom_emoji_id: str,
    file_id: str,
    sticker_set_name: str,
) -> None:
    db = await _get_conn()
    await db.execute(
        """UPDATE telegram_emoji_assets
           SET status='ready', custom_emoji_id=?, file_id=?, sticker_set_name=?,
               ready_at=CURRENT_TIMESTAMP, next_retry_at=NULL,
               last_error=NULL, updated_at=CURRENT_TIMESTAMP
           WHERE sha256=?""",
        (custom_emoji_id, file_id, sticker_set_name, sha256),
    )
    await db.commit()


async def mark_emoji_asset_failed(sha256: str, error: str) -> None:
    asset = await emoji_asset_by_hash(sha256)
    attempts = int(asset["attempts"]) if asset else 1
    delay = 5 if attempts <= 1 else 30 if attempts == 2 else 180
    status = "disabled" if attempts >= 5 else "failed"
    db = await _get_conn()
    await db.execute(
        """UPDATE telegram_emoji_assets
           SET status=?, last_error=?, next_retry_at=datetime('now', ?),
               updated_at=CURRENT_TIMESTAMP
           WHERE sha256=?""",
        (status, error[:500], f"+{delay} minutes", sha256),
    )
    await db.commit()


async def ready_emoji_for_entity(
    entity: ResolvedWarcraftEntity,
) -> TelegramEmojiAsset | None:
    db = await _get_conn()
    async with db.execute(
        """SELECT a.custom_emoji_id, a.file_id, a.sticker_set_name, a.fallback
           FROM warcraft_entities e
           JOIN telegram_emoji_assets a ON a.sha256 = e.icon_sha256
           WHERE e.branch=? AND e.kind=? AND e.external_id=?
             AND a.status='ready'
           LIMIT 1""",
        (entity.branch, entity.kind, entity.external_id),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return TelegramEmojiAsset(
        custom_emoji_id=row[0],
        file_id=row[1],
        sticker_set_name=row[2],
        fallback=row[3],
    )


async def emoji_uploads_today() -> int:
    db = await _get_conn()
    async with db.execute(
        """SELECT COUNT(*) FROM telegram_emoji_assets
           WHERE ready_at >= date('now')"""
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def ready_emoji_count_in_set(sticker_set_name: str) -> int:
    db = await _get_conn()
    async with db.execute(
        """SELECT COUNT(*) FROM telegram_emoji_assets
           WHERE status='ready' AND sticker_set_name=?""",
        (sticker_set_name,),
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def list_warcraft_entities() -> list[dict]:
    db = await _get_conn()
    async with db.execute(
        """SELECT branch, kind, external_id, canonical_name, localized_name,
                  page_url, icon_url, icon_sha256
           FROM warcraft_entities ORDER BY id"""
    ) as cur:
        rows = await cur.fetchall()
    keys = [
        "branch", "kind", "external_id", "canonical_name", "localized_name",
        "page_url", "icon_url", "icon_sha256",
    ]
    return [dict(zip(keys, row, strict=True)) for row in rows]


async def list_emoji_assets(limit: int = 20) -> list[dict]:
    db = await _get_conn()
    async with db.execute(
        """SELECT id, sha256, fallback, status, attempts, sticker_set_name,
                  custom_emoji_id, last_error
           FROM telegram_emoji_assets ORDER BY id DESC LIMIT ?""",
        (max(1, min(limit, 100)),),
    ) as cur:
        rows = await cur.fetchall()
    keys = [
        "id", "sha256", "fallback", "status", "attempts", "sticker_set_name",
        "custom_emoji_id", "last_error",
    ]
    return [dict(zip(keys, row, strict=True)) for row in rows]


async def due_emoji_uploads(limit: int = 2) -> list[dict]:
    db = await _get_conn()
    async with db.execute(
        """SELECT a.sha256, a.source_url, a.local_path, a.fallback,
                  e.branch, e.kind, e.external_id, e.canonical_name,
                  e.localized_name, e.page_url, e.icon_url
           FROM telegram_emoji_assets a
           JOIN warcraft_entities e ON e.icon_sha256 = a.sha256
           WHERE a.attempts < 5 AND (
               a.status='queued'
               OR (a.status='failed' AND (
                   a.next_retry_at IS NULL OR a.next_retry_at <= CURRENT_TIMESTAMP
               ))
           )
           GROUP BY a.sha256
           ORDER BY a.id
           LIMIT ?""",
        (max(1, min(limit, 10)),),
    ) as cur:
        rows = await cur.fetchall()
    keys = [
        "sha256", "source_url", "local_path", "fallback", "branch", "kind",
        "external_id", "canonical_name", "localized_name", "page_url", "icon_url",
    ]
    return [dict(zip(keys, row, strict=True)) for row in rows]


async def set_service_state(key: str, value: str, detail: str = "") -> None:
    db = await _get_conn()
    await db.execute(
        """INSERT INTO service_state(key, value, detail)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value,
               detail=excluded.detail, updated_at=CURRENT_TIMESTAMP""",
        (key[:100], value[:100], detail[:500]),
    )
    await db.commit()


async def get_service_state(key: str) -> dict | None:
    db = await _get_conn()
    async with db.execute(
        "SELECT value, detail, updated_at FROM service_state WHERE key=?",
        (key[:100],),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return {"value": row[0], "detail": row[1], "updated_at": row[2]}


async def retry_emoji_asset(asset_id: int) -> bool:
    db = await _get_conn()
    cur = await db.execute(
        """UPDATE telegram_emoji_assets
           SET status='queued', attempts=0, next_retry_at=NULL,
               last_error=NULL, updated_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        (asset_id,),
    )
    await db.commit()
    return cur.rowcount == 1


async def disable_emoji_asset(asset_id: int) -> bool:
    db = await _get_conn()
    cur = await db.execute(
        """UPDATE telegram_emoji_assets
           SET status='disabled', updated_at=CURRENT_TIMESTAMP WHERE id=?""",
        (asset_id,),
    )
    await db.commit()
    return cur.rowcount == 1


# ---------- Drafts ----------
async def create_draft(
    source_url: str,
    title: str,
    body: str,
    image_url: str | None,
    original_text: str,
    media_type: str = "photo",
    hashtag: str = "",
    inline_links: Sequence[tuple[str, str]] = (),
    custom_emojis: Sequence[TelegramEmojiAsset] = (),
) -> int:
    db = await _get_conn()
    links_json = json.dumps(list(inline_links), ensure_ascii=False)
    emojis_json = json.dumps(
        [
            {
                "custom_emoji_id": asset.custom_emoji_id,
                "file_id": asset.file_id,
                "sticker_set_name": asset.sticker_set_name,
                "fallback": asset.fallback,
                "placement_label": asset.placement_label,
            }
            for asset in custom_emojis[:2]
        ],
        ensure_ascii=False,
    )
    cur = await db.execute(
        "INSERT INTO drafts(source_url, title, body, image_url, original_text, "
        "media_type, hashtag, inline_links_json, custom_emojis_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source_url, title, body, image_url, original_text, media_type, hashtag,
            links_json, emojis_json,
        ),
    )
    await db.commit()
    return cur.lastrowid


async def get_draft(draft_id: int) -> dict | None:
    db = await _get_conn()
    async with db.execute(
        "SELECT id, source_url, title, body, image_url, original_text, "
        "COALESCE(include_original, 0), COALESCE(media_type, 'photo'), "
        "COALESCE(hashtag, ''), COALESCE(inline_links_json, '[]'), "
        "COALESCE(custom_emojis_json, '[]') "
        "FROM drafts WHERE id = ?",
        (draft_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    try:
        inline_links = [tuple(item) for item in json.loads(row[9])]
    except (json.JSONDecodeError, TypeError, ValueError):
        inline_links = []
    try:
        custom_emojis = [TelegramEmojiAsset(**item) for item in json.loads(row[10])]
    except (json.JSONDecodeError, TypeError, ValueError):
        custom_emojis = []
    return {
        "id": row[0], "source_url": row[1], "title": row[2],
        "body": row[3], "image_url": row[4], "original_text": row[5],
        "include_original": bool(row[6]),
        "media_type": row[7],
        "hashtag": row[8],
        "inline_links": inline_links,
        "custom_emojis": custom_emojis,
    }


async def set_draft_enrichment(
    draft_id: int,
    inline_links: Sequence[tuple[str, str]],
    custom_emojis: Sequence[TelegramEmojiAsset],
) -> None:
    links_json = json.dumps(list(inline_links), ensure_ascii=False)
    emojis_json = json.dumps(
        [
            {
                "custom_emoji_id": asset.custom_emoji_id,
                "file_id": asset.file_id,
                "sticker_set_name": asset.sticker_set_name,
                "fallback": asset.fallback,
                "placement_label": asset.placement_label,
            }
            for asset in custom_emojis[:2]
        ],
        ensure_ascii=False,
    )
    db = await _get_conn()
    await db.execute(
        "UPDATE drafts SET inline_links_json=?, custom_emojis_json=? WHERE id=?",
        (links_json, emojis_json, draft_id),
    )
    await db.commit()


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
    # Освободившиеся страницы переиспользует SQLite. VACUUM здесь небезопасен:
    # другая корутина может открыть транзакцию на общем соединении после commit.
    return {
        "drafts": drafts_removed,
        "edits": edits_removed,
        "seen": seen_removed,
        "runs": runs_removed,
        "published": published_removed,
    }
