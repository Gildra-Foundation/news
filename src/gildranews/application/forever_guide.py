from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from html import escape

from telethon import TelegramClient

from gildranews.adapters.persistence import sqlite as db
from gildranews.adapters.publishing import mtproto
from gildranews.config import Config

STATE_KEY = "forever_guide_render"
MAX_CAPTION_CHARS = 950
BASE_LINKS = (
    (40, "Бета: даты, доступ и сброс прогресса"),
    (41, "Мир, зоны и прокачка"),
    (42, "Рейды и расписание"),
    (43, "Серверы, наследие и другие системы"),
)


def _emoji(emoji_id: str, fallback: str) -> str:
    if emoji_id.isdigit():
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback


def build_guide_text(
    posts: list[dict[str, object]],
    *,
    forever_emoji_id: str = "",
    subscribe_emoji_id: str = "",
) -> str:
    icon = _emoji(forever_emoji_id, "🎮")
    subscribe = _emoji(subscribe_emoji_id, "🛡️")
    base_links = "\n".join(
        f'• <a href="https://t.me/gildrawow/{message_id}">{escape(title)}</a>'
        for message_id, title in BASE_LINKS
    )
    prefix = (
        f"{icon} <b>Все подробности WoW: Forever в одном месте</b>\n\n"
        "Пропустили анонсы? Здесь собраны короткие разборы по каждой теме:\n\n"
        f"{base_links}"
    )
    suffix = (
        "\n\nСохраните этот пост: список будет пополняться до выхода игры."
        "\n\n#гайд@gildrawow"
        f"\n\n{subscribe} <a href=\"https://t.me/gildrawow\">"
        "Подписаться на Gildra</a>"
    )
    selected: list[str] = []
    for post in posts:
        message_id = int(post.get("target_message_id") or 0)
        title = escape(str(post.get("title") or "").strip())
        if message_id <= BASE_LINKS[-1][0] or not title:
            continue
        line = f'• <a href="https://t.me/gildrawow/{message_id}">{title}</a>'
        candidate = "\n".join((*selected, line))
        block = f"\n\n<b>Новые материалы</b>\n{candidate}"
        if len(prefix + block + suffix) > MAX_CAPTION_CHARS:
            break
        selected.append(line)
    fresh = "\n\n<b>Новые материалы</b>\n" + "\n".join(selected) if selected else ""
    return prefix + fresh + suffix


async def refresh(
    client: TelegramClient,
    cfg: Config,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if current.date() >= cfg.forever_release_date:
        return {"updated": False, "links": 0, "reason": "release_reached"}
    if cfg.forever_guide_message_id <= 0:
        return {"updated": False, "links": 0, "reason": "disabled"}

    posts = await db.published_posts_after(
        after_message_id=cfg.forever_guide_message_id,
        search="WoW: Forever",
        limit=30,
    )
    text = build_guide_text(
        posts,
        forever_emoji_id=await db.ready_custom_emoji_id("WoW: Forever"),
        subscribe_emoji_id=cfg.subscribe_emoji_id,
    )
    version = hashlib.sha256(text.encode()).hexdigest()
    state = await db.get_service_state(STATE_KEY)
    if state and state.get("value") == version:
        return {"updated": False, "links": len(posts), "reason": "unchanged"}
    if not await mtproto.edit(
        client,
        cfg.target_channel,
        cfg.forever_guide_message_id,
        text,
    ):
        return {"updated": False, "links": len(posts), "reason": "emoji_lost"}
    await db.set_service_state(
        STATE_KEY,
        version,
        f"message_id={cfg.forever_guide_message_id}; links={len(posts)}",
    )
    return {"updated": True, "links": len(posts), "reason": ""}
