from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    FloodWaitError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import (
    MessageEntityTextUrl,
    MessageMediaDocument,
    MessageMediaPhoto,
)

log = logging.getLogger(__name__)

SESSION_NAME = "data/userbot"
MAX_VIDEO_BYTES = 50 * 1024 * 1024  # лимит Bot API на send_video


@dataclass
class FetchedPost:
    channel: str
    message_id: int
    text: str
    date: datetime
    media_messages: list = field(default_factory=list)  # Telethon Message objs c медиа


def _expand_inline_links(text: str, entities) -> str:
    """Раскрывает скрытые гиперссылки: 'забрать' с href=https://… превращает в
    'забрать (https://…)' — иначе Gemini не видит URL и теряет его при рерайте."""
    if not text or not entities:
        return text or ""
    inserts: list[tuple[int, str]] = []
    for ent in entities:
        if isinstance(ent, MessageEntityTextUrl) and ent.url:
            inserts.append((ent.offset + ent.length, ent.url))
    if not inserts:
        return text
    inserts.sort(key=lambda x: x[0], reverse=True)
    for pos, url in inserts:
        text = text[:pos] + f" ({url})" + text[pos:]
    return text


def _media_kind(msg) -> str | None:
    """Возвращает 'photo'/'video' или None если медиа нет/не поддерживается."""
    if isinstance(msg.media, MessageMediaPhoto):
        return "photo"
    if isinstance(msg.media, MessageMediaDocument):
        doc = msg.media.document
        if doc is None:
            return None
        mime = (doc.mime_type or "").lower()
        if mime.startswith("video/"):
            if (doc.size or 0) > MAX_VIDEO_BYTES:
                return None
            return "video"
        if mime.startswith("image/"):
            return "photo"
    return None


def make_client(api_id: int, api_hash: str) -> TelegramClient:
    return TelegramClient(SESSION_NAME, api_id, api_hash)


def _build_post(channel: str, msgs: list, seen_ids: set[int] | None = None) -> FetchedPost | None:
    """Из набора сообщений (одиночка или альбом) собирает FetchedPost."""
    msgs_sorted = sorted(msgs, key=lambda m: m.id)
    msg_with_text = next((m for m in msgs_sorted if m.message), None)
    if msg_with_text is None:
        return None
    text = _expand_inline_links(msg_with_text.message, msg_with_text.entities).strip()
    if not text or len(text) < 30:
        return None
    first = msgs_sorted[0]
    media = [m for m in msgs_sorted if _media_kind(m)]
    return FetchedPost(
        channel=channel,
        message_id=first.id,
        text=text,
        date=first.date,
        media_messages=media,
    )


async def fetch_recent(
    client: TelegramClient,
    channels: list[str],
    lookback_minutes: int,
    seen_check,
) -> list[FetchedPost]:
    cutoff = datetime.now(UTC) - timedelta(minutes=lookback_minutes)
    out: list[FetchedPost] = []

    for username in channels:
        try:
            groups: dict[int, list] = {}
            singles: list = []
            async for msg in client.iter_messages(username, limit=80):
                if msg.date < cutoff:
                    break
                if msg.grouped_id:
                    groups.setdefault(msg.grouped_id, []).append(msg)
                else:
                    singles.append(msg)

            count = 0
            for msgs in groups.values():
                post = _build_post(username, msgs)
                if not post:
                    continue
                if await seen_check(username, post.message_id):
                    continue
                out.append(post)
                count += 1

            for msg in singles:
                post = _build_post(username, [msg])
                if not post:
                    continue
                if await seen_check(username, post.message_id):
                    continue
                out.append(post)
                count += 1

            if count > 0:
                log.info("Канал @%s: %d новых постов", username, count)
        except FloodWaitError as e:
            log.warning("FloodWait %ds на @%s — пропуск", e.seconds, username)
        except (ChannelPrivateError, UsernameInvalidError, UsernameNotOccupiedError) as e:
            log.warning("Канал @%s недоступен: %s", username, type(e).__name__)
        except Exception:
            log.exception("Ошибка чтения @%s", username)

    out.sort(key=lambda p: p.date)
    return out


async def fetch_post_by_link(
    client: TelegramClient,
    channel: str,
    message_id: int,
) -> FetchedPost | None:
    """Получить конкретный пост по ссылке. Если это альбом — собрать все элементы."""
    try:
        msg = await client.get_messages(channel, ids=message_id)
        if msg is None:
            return None

        if msg.grouped_id is None:
            return _build_post(channel, [msg])

        # Альбом — посты идут пачкой подряд, забираем диапазон вокруг
        ids = list(range(max(message_id - 9, 1), message_id + 10))
        msgs = await client.get_messages(channel, ids=ids)
        siblings = [m for m in msgs if m is not None and m.grouped_id == msg.grouped_id]
        if not siblings:
            siblings = [msg]
        return _build_post(channel, siblings)
    except (ChannelPrivateError, UsernameInvalidError, UsernameNotOccupiedError) as e:
        log.warning("Канал @%s недоступен по ссылке: %s", channel, type(e).__name__)
        return None
    except Exception:
        log.exception("Ошибка получения поста @%s/%s", channel, message_id)
        return None


async def fetch_latest_one(client: TelegramClient, channel: str) -> FetchedPost | None:
    """Самый свежий пост (одиночный или альбом). Игнорирует cutoff и seen — для /test."""
    try:
        groups: dict[int, list] = {}
        singles: list = []
        async for msg in client.iter_messages(channel, limit=30):
            if msg.grouped_id:
                groups.setdefault(msg.grouped_id, []).append(msg)
            else:
                singles.append(msg)

        candidates: list[FetchedPost] = []
        for msgs in groups.values():
            post = _build_post(channel, msgs)
            if post:
                candidates.append(post)
        for msg in singles:
            post = _build_post(channel, [msg])
            if post:
                candidates.append(post)

        candidates.sort(key=lambda p: p.date, reverse=True)
        return candidates[0] if candidates else None
    except (ChannelPrivateError, UsernameInvalidError, UsernameNotOccupiedError) as e:
        log.warning("Канал @%s недоступен: %s", channel, type(e).__name__)
    except Exception:
        log.exception("Ошибка чтения @%s", channel)
    return None


async def ensure_joined(client: TelegramClient, channel: str) -> bool:
    """Подписать userbot на канал. True если подписан/уже был, False при ошибке."""
    try:
        entity = await client.get_entity(channel)
        await client(JoinChannelRequest(entity))
        log.info("Подписка на @%s OK", channel)
        return True
    except FloodWaitError as e:
        log.warning("FloodWait %ds при подписке на @%s", e.seconds, channel)
        return False
    except (ChannelPrivateError, UsernameInvalidError, UsernameNotOccupiedError) as e:
        log.warning("Не удалось подписаться на @%s: %s", channel, type(e).__name__)
        return False
    except Exception:
        log.exception("Ошибка подписки на @%s", channel)
        return False


def build_post_from_messages(channel: str, msgs: list) -> FetchedPost | None:
    """Собрать FetchedPost из набора сообщений (одиночка или альбом). Используется real-time."""
    return _build_post(channel, msgs)


async def download_post_media(
    client: TelegramClient,
    post: FetchedPost,
    dest_dir: str,
) -> list[tuple[str, str]]:
    """Скачивает медиа поста. Возвращает [(path, kind)]."""
    if not post.media_messages:
        return []
    os.makedirs(dest_dir, exist_ok=True)
    out: list[tuple[str, str]] = []
    for msg in post.media_messages:
        kind = _media_kind(msg)
        if not kind:
            continue
        try:
            path = await client.download_media(msg, file=dest_dir)
            if path:
                out.append((path, kind))
        except Exception as e:  # noqa: BLE001 - Telethon media errors vary by media type
            log.warning(
                "Не удалось скачать медиа из @%s/%s: %s",
                post.channel, msg.id, e,
            )
    return out
