from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from urllib.parse import urlsplit

import aiosqlite

from gildranews.adapters.persistence import sqlite
from gildranews.application.site_articles import WebsiteArticle
from gildranews.domain.models import EventFingerprint

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS website_jobs (
        published_post_id INTEGER PRIMARY KEY REFERENCES published_posts(id),
        source_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        lease_until TEXT,
        lease_token TEXT,
        last_error TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS website_articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        published_post_id INTEGER NOT NULL UNIQUE REFERENCES published_posts(id),
        article_json TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_website_jobs_due ON website_jobs(status, available_at)",
)


def source_payload(
    text: str, url: str, published_at: str = "", fingerprint: EventFingerprint | None = None,
) -> dict:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
        raise ValueError("Website source must have a public HTTP URL")
    if not text.strip():
        raise ValueError("Website source is empty")
    return {
        "text": text[:24000], "url": url, "source_published_at": published_at,
        "fingerprint": asdict(fingerprint) if fingerprint else {},
        "story_key": fingerprint.story_key if fingerprint else "",
        "revision_key": fingerprint.revision_key if fingerprint else "",
    }


async def enqueue(db: aiosqlite.Connection, post_id: int, source: dict) -> None:
    """Called inside the publication transaction; no independent commit."""
    await db.execute(
        "INSERT OR IGNORE INTO website_jobs(published_post_id, source_json) VALUES (?, ?)",
        (post_id, json.dumps(source, ensure_ascii=False)),
    )


async def claim_job(lease_seconds: int = 900) -> dict | None:
    db = await sqlite._get_conn()
    await db.execute(
        """UPDATE website_jobs SET status='failed', last_error='worker_lease_expired'
           WHERE status='generating' AND lease_until < CURRENT_TIMESTAMP AND attempts >= 3""",
    )
    token = uuid.uuid4().hex
    async with db.execute(
        """UPDATE website_jobs
           SET status='generating', attempts=attempts+1, lease_token=?,
               lease_until=datetime('now', ?)
           WHERE published_post_id=(
               SELECT published_post_id FROM website_jobs WHERE attempts < 3 AND (
                   (status='pending' AND available_at <= CURRENT_TIMESTAMP)
                   OR (status='generating' AND lease_until < CURRENT_TIMESTAMP)
               ) ORDER BY published_post_id LIMIT 1
           ) RETURNING published_post_id, source_json, attempts, lease_token""",
        (token, f"+{lease_seconds} seconds"),
    ) as cursor:
        row = await cursor.fetchone()
    await db.commit()
    if row is None:
        return None
    return {"post_id": row[0], "source": json.loads(row[1]), "attempts": row[2], "token": row[3]}


async def complete_job(job: dict, article: WebsiteArticle) -> None:
    db = await sqlite._get_conn()
    source = job["source"]
    fingerprint = source["fingerprint"]
    payload = article.model_dump(exclude={"evidence"})
    payload.update({
        "sources": [{"url": source["url"], "published_at": source["source_published_at"]}],
        "game_branch": fingerprint.get("game_branch", "unknown"),
        "version": fingerprint.get("version", ""),
        "information_status": fingerprint.get("status", "unknown"),
        "effective_date": fingerprint.get("effective_date", ""),
        "story_key": source.get("story_key", ""),
        "revision_key": source.get("revision_key", ""),
    })
    # One statement verifies the lease and creates the release. IDs are assigned only
    # here so a delayed job never disappears behind a reader's pagination cursor.
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """INSERT OR IGNORE INTO website_articles(published_post_id, article_json, evidence_json)
               SELECT published_post_id, ?, ? FROM website_jobs
               WHERE published_post_id=? AND status='generating' AND lease_token=?""",
            (json.dumps(payload, ensure_ascii=False),
             json.dumps([entry.model_dump() for entry in article.evidence], ensure_ascii=False),
             job["post_id"], job["token"]),
        )
        await db.execute(
            """UPDATE website_jobs SET status='ready', lease_until=NULL, last_error=NULL
               WHERE published_post_id=? AND status='generating' AND lease_token=?""",
            (job["post_id"], job["token"]),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def fail_job(job: dict, error_code: str) -> None:
    db = await sqlite._get_conn()
    await db.execute(
        """UPDATE website_jobs SET status=CASE WHEN attempts >= 3 THEN 'failed' ELSE 'pending' END,
           available_at=datetime('now', '+5 minutes'), lease_until=NULL, last_error=?
           WHERE published_post_id=? AND status='generating' AND lease_token=?""",
        (error_code[:100], job["post_id"], job["token"]),
    )
    await db.commit()


def _public_row(row: tuple) -> dict:
    return {"id": row[0], "published_at": row[2].replace(" ", "T") + "Z", **json.loads(row[1])}


async def list_articles(after_id: int = 0, limit: int = 20) -> dict:
    if after_id < 0 or not 1 <= limit <= 100:
        raise ValueError("Invalid pagination")
    db = await sqlite._get_conn()
    async with db.execute(
        "SELECT id, article_json, published_at FROM website_articles WHERE id > ? ORDER BY id LIMIT ?",
        (after_id, limit + 1),
    ) as cursor:
        rows = await cursor.fetchall()
    items = [_public_row(row) for row in rows[:limit]]
    return {"items": items, "next_cursor": items[-1]["id"] if items else after_id,
            "has_more": len(rows) > limit}


async def get_article(article_id: int) -> dict | None:
    db = await sqlite._get_conn()
    async with db.execute(
        "SELECT id, article_json, published_at FROM website_articles WHERE id=?", (article_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return _public_row(row) if row else None
