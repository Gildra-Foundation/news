from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from html import escape
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo, Message

from gildranews.adapters.persistence import sqlite as db

log = logging.getLogger(__name__)

# Жёсткий лимит на body (видимых символов). Учитываем что в финальный пост
# добавляются: title, тематический эмодзи, опц. строка «Оригинальный пост»,
# опц. ссылка на репо, хэштег. Запас от лимита caption=1024.
# При срабатывании — _smart_truncate режет на границе предложения/абзаца.
BODY_HARD_LIMIT = 780

# Хэштеги канала. Ключи — короткое имя темы, которое выбирает AI-сервис.
HASHTAGS = {
    "новости": "#новости",
    "руководство": "#руководство",
    "советы": "#советы",
    "полезное": "#полезное",
    "обсуждения": "#обсуждения",
    "дайджест": "#дайджест",
}
DEFAULT_HASHTAG_KEY = "полезное"
_WOWHEAD_ENTITY_PATH_RE = re.compile(r"^/(?:zone|npc)=\d+$")


def _is_admin(message: Message, admin_id: int) -> bool:
    return admin_id != 0 and message.from_user is not None and message.from_user.id == admin_id


def make_bot(token: str) -> Bot:
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def make_dispatcher(admin_id: int, target_channel: str) -> Dispatcher:
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        uid = message.from_user.id if message.from_user else 0
        if admin_id == 0:
            await message.answer(
                f"Ваш Telegram user id: <code>{uid}</code>\n"
                f"Пропишите его в .env как ADMIN_USER_ID и перезапустите бота."
            )
            return
        if not _is_admin(message, admin_id):
            await message.answer("Этот бот приватный.")
            return
        await message.answer(
            "GildraNews бот готов.\n\n"
            "Команды:\n"
            "/sources — список источников\n"
            "/add @канал — добавить источник\n"
            "/remove @канал — удалить источник\n"
            "/status — последний прогон\n"
            "/run — запустить прогон вручную\n"
            "/test [@канал] — взять последний пост, рерайт и опубликовать\n"
            "/digest — еженедельный дайджест канала за 7 дней\n"
            "/emojiid — извлечь ID премиум-эмодзи из пересланного сообщения\n"
            "/cancel — отменить правку\n\n"
            "<b>Ссылки:</b>\n"
            "• <code>t.me/канал/ID</code> — проверка фильтра + дедуп + публикация\n"
            "• <code>x.com/.../status/ID</code> или <code>twitter.com/...</code> — "
            "парсинг твита, перевод, превью с кнопками\n"
            "• <code>reddit.com/r/.../comments/ID/...</code> — то же для Reddit-постов\n"
            "• <code>github.com/owner/repo</code> — описание репозитория: что делает + где применять\n\n"
            "В превью две кнопки: ✏️ Редактировать (опишите правку текстом) "
            "или ✅ Опубликовать."
        )

    @dp.message(Command("sources"))
    async def cmd_sources(message: Message) -> None:
        if not _is_admin(message, admin_id):
            return
        items = await db.list_sources()
        if not items:
            await message.answer("Источников нет. Добавьте через /add @канал")
            return
        text = "Источники:\n" + "\n".join(f"• @{u}" for u in items)
        await message.answer(text)

    # /add регистрируется в main.py с автоподпиской на канал

    @dp.message(Command("remove"))
    async def cmd_remove(message: Message) -> None:
        if not _is_admin(message, admin_id):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Использование: <code>/remove @channel</code>")
            return
        ok = await db.remove_source(parts[1])
        await message.answer("Удалён." if ok else "Такого источника нет.")

    @dp.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        if not _is_admin(message, admin_id):
            return
        run = await db.last_run()
        if not run:
            await message.answer("Прогонов ещё не было.")
            return
        lines = [
            f"Последний прогон: {run['finished_at'] or run['started_at']}",
            f"Получено: {run['fetched']}",
            f"Отобрано ИИ: {run['selected']}",
            f"Опубликовано: {run['published']}",
        ]
        if run["error"]:
            lines.append(f"Ошибка: {run['error']}")
        await message.answer("\n".join(lines))

    # /run и /test регистрируются в main.py
    return dp


def html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _safe_wowhead_url(url: str) -> bool:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "www.wowhead.com"
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and bool(_WOWHEAD_ENTITY_PATH_RE.fullmatch(parsed.path))
    )


def _linkify(
    text: str,
    links: list[tuple[str, str]],
) -> tuple[str, list[tuple[str, str]]]:
    remaining = list(links)
    parts: list[str] = []
    cursor = 0
    while remaining:
        matches = [
            (text.find(label, cursor), -len(label), index, label, url)
            for index, (label, url) in enumerate(remaining)
            if label and text.find(label, cursor) >= 0
        ]
        if not matches:
            break
        position, _negative_length, index, label, url = min(matches)
        parts.append(html_escape(text[cursor:position]))
        parts.append(
            f'<a href="{escape(url, quote=True)}">{html_escape(label)}</a>'
        )
        cursor = position + len(label)
        remaining.pop(index)
    parts.append(html_escape(text[cursor:]))
    return "".join(parts), remaining


def format_post(
    title: str,
    body: str,
    emoji_theme: str = "",
    emoji_map: dict | None = None,
    original_url: str | None = None,
    tail_url: str | None = None,
    hashtag_key: str = "",
    inline_links: Sequence[tuple[str, str]] | None = None,
) -> str:
    """Финальный пост:
    <b>Title</b> [theme-emoji]\\n\\n
    Body\\n\\n
    [tail_url]\\n\\n
    [<u><i><a>Оригинальный пост</a></i></u>]\\n\\n
    [#hashtag]
    """
    title = title.strip()
    body = body.strip()
    if len(body) > BODY_HARD_LIMIT:
        body = _smart_truncate(body, BODY_HARD_LIMIT)

    theme_prefix = ""
    if emoji_theme and emoji_map:
        info = emoji_map.get(emoji_theme)
        if info and info.get("id") and info.get("fallback"):
            fb = html_escape(info["fallback"])
            theme_prefix = f'<tg-emoji emoji-id="{info["id"]}">{fb}</tg-emoji> '

    safe_links = [
        (label.strip(), url.strip())
        for label, url in (inline_links or ())
        if label.strip() and _safe_wowhead_url(url.strip())
    ]
    title_html, safe_links = _linkify(title, safe_links)
    body_html, _unused_links = _linkify(body, safe_links)

    blocks: list[str] = []
    blocks.append(f"{theme_prefix}<b>{title_html}</b>")
    blocks.append(body_html)

    if tail_url:
        # Plain URL — Telegram сам делает его кликабельным
        blocks.append(tail_url)

    if original_url:
        blocks.append(
            f'<u><i><a href="{original_url}">Оригинальный пост</a></i></u>'
        )

    # Хэштег: ключ или, по умолчанию, «полезное»
    key = (hashtag_key or "").strip() or DEFAULT_HASHTAG_KEY
    tag = HASHTAGS.get(key) or HASHTAGS[DEFAULT_HASHTAG_KEY]
    blocks.append(tag)

    return "\n\n".join(blocks)


def _smart_truncate(body: str, limit: int) -> str:
    """Урезает body на ГРАНИЦЕ — приоритет абзаца, потом предложения,
    потом слова. Не оставляет обрубков посреди слова."""
    if len(body) <= limit:
        return body
    head = body[:limit]
    # 1) Последняя граница абзаца — если это >50% лимита (иначе слишком обрежется)
    para_break = head.rfind("\n\n")
    if para_break >= int(limit * 0.5):
        return body[:para_break].rstrip()
    # 2) Последняя граница предложения (. ! ? + пробел/перевод)
    best = -1
    for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
        pos = head.rfind(sep)
        if pos > best:
            best = pos + len(sep) - 1  # включая знак препинания
    if best >= int(limit * 0.5):
        return body[: best + 1].rstrip()
    # 3) Граница слова (пробел) + «…»
    space = head.rfind(" ")
    if space >= int(limit * 0.5):
        return body[:space].rstrip() + "…"
    # 4) Совсем худо — режем по символам
    return head.rstrip() + "…"


async def _publish_once(
    bot: Bot,
    target_channel: str,
    text: str,
    media_files: list[tuple[str, str]] | None,
) -> int | None:
    """Сам запрос в Telegram. Возвращает message_id опубликованного поста (для ссылок),
    или None если не удалось определить. TelegramAPIError проходит наверх — caller решит retry."""
    if not media_files:
        msg = await bot.send_message(
            chat_id=target_channel,
            text=text,
            disable_web_page_preview=True,
        )
        return msg.message_id

    caption = text

    if len(media_files) == 1:
        path, kind = media_files[0]
        f = FSInputFile(path)
        if kind == "photo":
            msg = await bot.send_photo(chat_id=target_channel, photo=f, caption=caption)
        else:
            msg = await bot.send_video(chat_id=target_channel, video=f, caption=caption)
        return msg.message_id

    # Альбом (до 10 элементов) — возвращает список сообщений
    media: list = []
    for i, (path, kind) in enumerate(media_files[:10]):
        f = FSInputFile(path)
        cap = caption if i == 0 else None
        if kind == "photo":
            media.append(InputMediaPhoto(media=f, caption=cap))
        else:
            media.append(InputMediaVideo(media=f, caption=cap))
    msgs = await bot.send_media_group(chat_id=target_channel, media=media)
    return msgs[0].message_id if msgs else None


async def publish(
    bot: Bot,
    target_channel: str,
    text: str,
    media_files: list[tuple[str, str]] | None = None,
) -> int | None:
    """Публикует пост. Возвращает message_id поста в канале (для построения ссылок)
    или None при отказе. Один retry при TelegramRetryAfter."""
    import asyncio as _asyncio

    for attempt in (0, 1):
        try:
            return await _publish_once(bot, target_channel, text, media_files)
        except TelegramRetryAfter as e:
            wait = min(int(e.retry_after) + 1, 60)
            log.warning(
                "Bot API rate limit, attempt=%d, retry_after=%ds (wait=%ds)",
                attempt, e.retry_after, wait,
            )
            if attempt == 0:
                await _asyncio.sleep(wait)
                continue
            return None
        except TelegramAPIError:
            log.exception("Не удалось опубликовать")
            return None
    return None
