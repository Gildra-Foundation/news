from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from html import escape
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import MessageEntityType, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo, Message
from telethon import TelegramClient
from telethon.errors import RPCError

from gildranews.adapters.persistence import sqlite as db
from gildranews.adapters.publishing import mtproto
from gildranews.adapters.publishing.rich_message import (
    SUBSCRIBE_LABEL,
    SUBSCRIBE_URL,
    SendRichMessage,
    build_rich_message,
)
from gildranews.domain.models import MAX_ENTITY_EMOJIS_PER_POST, TelegramEmojiAsset

log = logging.getLogger(__name__)

# Жёсткий лимит на body (видимых символов). Учитываем что в финальный пост
# добавляются: title, тематический эмодзи, опц. строка «Оригинальный пост»,
# опц. ссылка на репо, хэштег. Запас от лимита caption=1024.
# При срабатывании — _smart_truncate режет на границе предложения/абзаца.
BODY_HARD_LIMIT = 780

# Хэштеги канала. Суффикс @gildrawow открывает подборку этой темы именно
# внутри канала, а не глобальную выдачу Telegram.
HASHTAGS = {
    "новости": "#новости@gildrawow",
    "руководство": "#руководство@gildrawow",
    "советы": "#советы@gildrawow",
    "полезное": "#полезное@gildrawow",
    "обсуждения": "#обсуждения@gildrawow",
    "дайджест": "#дайджест@gildrawow",
}
DEFAULT_HASHTAG_KEY = "полезное"
SUBSCRIBE_FALLBACK = "🛡️"
_WOWHEAD_ENTITY_PATH_RE = re.compile(
    r"^/(?:classic/)?(?:"
    r"(?:achievement|item|npc|spell|transmog-set|zone)=\d+"
    r"|class=\d+(?:/[a-z0-9-]+)?"
    r")$"
)
_WOWHEAD_SPELL_PATH_RE = re.compile(r"^/(?:classic/)?spell=\d+$")
_CUSTOM_EMOJI_RE = re.compile(
    r'<tg-emoji\s+emoji-id="[0-9]+">(.*?)</tg-emoji>',
    re.DOTALL,
)
_mtproto_client: TelegramClient | None = None


def configure_mtproto_publisher(client: TelegramClient | None) -> None:
    global _mtproto_client
    _mtproto_client = client


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
            "/emojis — состояние игровых эмодзи\n"
            "/emoji_retry ID — повторить загрузку\n"
            "/emoji_disable ID — отключить загрузку\n"
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

    @dp.message(Command("emojis"))
    async def cmd_emojis(message: Message) -> None:
        if not _is_admin(message, admin_id):
            return
        assets = await db.list_emoji_assets(limit=20)
        fragment = await db.get_service_state("fragment_integration")
        if not assets:
            state = fragment["value"] if fragment else "не проверена"
            await message.answer(
                f"Игровых Custom Emoji в реестре пока нет. Fragment: {state}."
            )
            return
        state = fragment["value"] if fragment else "не проверена"
        lines = [f"Fragment: {state}", "Последние игровые Custom Emoji:"]
        for asset in assets:
            suffix = f" — {html_escape(asset['last_error'])}" if asset["last_error"] else ""
            lines.append(
                f"<code>{asset['id']}</code> {asset['fallback']} "
                f"{asset['status']} ({asset['attempts']}/5){suffix}"
            )
        await message.answer("\n".join(lines))

    @dp.message(Command("emoji_retry"))
    async def cmd_emoji_retry(message: Message) -> None:
        if not _is_admin(message, admin_id):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("Использование: <code>/emoji_retry ID</code>")
            return
        changed = await db.retry_emoji_asset(int(parts[1]))
        await message.answer("Поставлен в очередь." if changed else "Эмодзи не найден.")

    @dp.message(Command("emoji_disable"))
    async def cmd_emoji_disable(message: Message) -> None:
        if not _is_admin(message, admin_id):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("Использование: <code>/emoji_disable ID</code>")
            return
        changed = await db.disable_emoji_asset(int(parts[1]))
        await message.answer("Отключён." if changed else "Эмодзи не найден.")

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
        linked = f'<a href="{escape(url, quote=True)}">{html_escape(label)}</a>'
        if _WOWHEAD_SPELL_PATH_RE.fullmatch(urlparse(url).path):
            linked = f"<i>{linked}</i>"
        parts.append(linked)
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
    custom_emojis: Sequence[TelegramEmojiAsset] | None = None,
    subscribe_emoji_id: str = "",
) -> str:
    """Финальный пост:
    <b>Title</b> [theme-emoji]\\n\\n
    Body\\n\\n
    [tail_url]\\n\\n
    [<u><i><a>Оригинальный пост</a></i></u>]\\n\\n
    [#hashtag]\n\n
    [brand emoji] Подписаться на Gildra
    """
    title = title.strip()
    body = body.strip()
    if len(body) > BODY_HARD_LIMIT:
        body = _smart_truncate(body, BODY_HARD_LIMIT)
    title = _without_typographic_quotes(title)
    body = _without_typographic_quotes(body)

    display_emojis = [
        asset for asset in (custom_emojis or ())
        if asset.fallback
    ][:MAX_ENTITY_EMOJIS_PER_POST]
    for asset in display_emojis:
        if asset.placement_label:
            title = _without_entity_quotes(title, asset.placement_label)
            body = _without_entity_quotes(body, asset.placement_label)
    theme_prefix = ""
    if not display_emojis and emoji_theme and emoji_map:
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

    labeled_emojis = [asset for asset in display_emojis if asset.placement_label]
    if labeled_emojis:
        for asset in labeled_emojis:
            token = _custom_emoji_html(asset) + " "
            title_html, placed = _insert_before_visible_label(
                title_html, asset.placement_label, token,
            )
            if not placed:
                body_html, _placed = _insert_before_visible_label(
                    body_html, asset.placement_label, token,
                )
    elif display_emojis:
        # Backwards compatibility for drafts created before placement labels existed.
        theme_prefix = _custom_emoji_html(display_emojis[0]) + " "
        if len(display_emojis) > 1:
            body_html = _custom_emoji_html(display_emojis[1]) + " " + body_html

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
    subscribe_icon = SUBSCRIBE_FALLBACK
    if subscribe_emoji_id.isdigit():
        subscribe_icon = (
            f'<tg-emoji emoji-id="{subscribe_emoji_id}">'
            f"{SUBSCRIBE_FALLBACK}</tg-emoji>"
        )
    blocks.append(
        f'{subscribe_icon} <a href="{SUBSCRIBE_URL}">{SUBSCRIBE_LABEL}</a>'
    )

    return "\n\n".join(blocks)


def _custom_emoji_html(asset: TelegramEmojiAsset) -> str:
    if not asset.custom_emoji_id.isdigit():
        return html_escape(asset.fallback)
    return (
        f'<tg-emoji emoji-id="{asset.custom_emoji_id}">'
        f"{html_escape(asset.fallback)}</tg-emoji>"
    )


def _without_entity_quotes(text: str, label: str) -> str:
    """Remove quotes made redundant by a contextual icon."""
    visible_label = label.strip()
    if not visible_label:
        return text
    pattern = re.compile(
        rf"[«„“\"]({re.escape(visible_label)})[»“”\"]",
    )
    return pattern.sub(r"\1", text)


def _without_typographic_quotes(text: str) -> str:
    """Keep the channel's clean house style without decorative quotation marks."""
    return text.translate(str.maketrans("", "", "«»„“”"))


def _insert_before_visible_label(
    rendered: str,
    label: str,
    prefix: str,
) -> tuple[str, bool]:
    needle = html_escape(label.strip())
    if not needle:
        return rendered, False
    folded = rendered.casefold()
    folded_needle = needle.casefold()
    cursor = 0
    while (position := folded.find(folded_needle, cursor)) >= 0:
        last_open = rendered.rfind("<", 0, position)
        last_close = rendered.rfind(">", 0, position)
        if last_open <= last_close:
            anchor_open = rendered.rfind("<a ", 0, position)
            anchor_close = rendered.rfind("</a>", 0, position)
            insertion = anchor_open if anchor_open > anchor_close else position
            italic_open = rendered.rfind("<i>", 0, insertion)
            italic_close = rendered.rfind("</i>", 0, insertion)
            if italic_open > italic_close:
                insertion = italic_open
            return rendered[:insertion] + prefix + rendered[insertion:], True
        cursor = position + len(needle)
    return rendered, False


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


def without_custom_emojis(text: str) -> str:
    """Keep readable Unicode fallbacks while removing Bot API custom entities."""
    return _CUSTOM_EMOJI_RE.sub(r"\1", text)


async def _publish_rich_once(
    bot: Bot,
    target_channel: str,
    text: str,
    media_files: list[tuple[str, str]] | None,
    table_rows: Sequence[tuple[str, str]] | None = None,
) -> int | None:
    method = SendRichMessage(
        chat_id=target_channel,
        rich_message=build_rich_message(
            text,
            media_files,
            table_rows=table_rows,
        ),
    )
    message = await bot(method)
    return message.message_id


async def _publish_once(
    bot: Bot,
    target_channel: str,
    text: str,
    media_files: list[tuple[str, str]] | None,
) -> tuple[int | None, bool]:
    """Return the message id and whether Telegram preserved a Custom Emoji entity."""
    if not media_files:
        msg = await bot.send_message(
            chat_id=target_channel,
            text=text,
            disable_web_page_preview=True,
        )
        return msg.message_id, message_has_custom_emoji(msg)

    caption = text

    if len(media_files) == 1:
        path, kind = media_files[0]
        f = _media_input(path)
        if kind == "photo":
            msg = await bot.send_photo(chat_id=target_channel, photo=f, caption=caption)
        else:
            msg = await bot.send_video(chat_id=target_channel, video=f, caption=caption)
        return msg.message_id, message_has_custom_emoji(msg)

    # Альбом (до 10 элементов) — возвращает список сообщений
    media: list = []
    for i, (path, kind) in enumerate(media_files[:10]):
        f = _media_input(path)
        cap = caption if i == 0 else None
        if kind == "photo":
            media.append(InputMediaPhoto(media=f, caption=cap))
        else:
            media.append(InputMediaVideo(media=f, caption=cap))
    msgs = await bot.send_media_group(chat_id=target_channel, media=media)
    return (
        (msgs[0].message_id, message_has_custom_emoji(msgs[0]))
        if msgs else (None, False)
    )


def message_has_custom_emoji(message: Message) -> bool:
    entities = list(getattr(message, "entities", None) or []) + list(
        getattr(message, "caption_entities", None) or []
    )
    return any(entity.type == MessageEntityType.CUSTOM_EMOJI for entity in entities)


def _media_input(path: str) -> str | FSInputFile:
    if path.startswith(("https://", "http://")):
        return path
    return FSInputFile(path)


async def _publish_legacy(
    bot: Bot,
    target_channel: str,
    text: str,
    media_files: list[tuple[str, str]] | None = None,
) -> int | None:
    """Publish once, retrying rate limits or invalid Custom Emoji safely."""
    import asyncio as _asyncio

    current_text = text
    if _mtproto_client is not None:
        try:
            message_id = await mtproto.publish(
                _mtproto_client, target_channel, current_text, media_files,
            )
            if _CUSTOM_EMOJI_RE.search(current_text):
                try:
                    await db.set_service_state("fragment_integration", "healthy", "")
                except Exception:
                    log.warning("Could not persist MTProto health", exc_info=True)
            return message_id
        except RPCError as error:
            log.warning(
                "MTProto rejected publication (%s); falling back to Bot API",
                type(error).__name__,
            )
            if _CUSTOM_EMOJI_RE.search(current_text):
                current_text = without_custom_emojis(current_text)
                try:
                    await db.set_service_state(
                        "fragment_integration",
                        "faulty",
                        f"MTProto rejected publication: {type(error).__name__}",
                    )
                except Exception:
                    log.warning("Could not persist MTProto failure", exc_info=True)
    rate_retried = False
    emoji_retried = False
    for attempt in range(3):
        try:
            message_id, delivered_custom_emoji = await _publish_once(
                bot, target_channel, current_text, media_files,
            )
            if _CUSTOM_EMOJI_RE.search(current_text):
                try:
                    await db.set_service_state(
                        "fragment_integration",
                        "healthy" if delivered_custom_emoji else "faulty",
                        "" if delivered_custom_emoji else (
                            "Telegram accepted the post but removed Custom Emoji"
                        ),
                    )
                except Exception:
                    log.warning("Could not persist Fragment health", exc_info=True)
            return message_id
        except TelegramRetryAfter as e:
            wait = min(int(e.retry_after) + 1, 60)
            log.warning(
                "Bot API rate limit, attempt=%d, retry_after=%ds (wait=%ds)",
                attempt, e.retry_after, wait,
            )
            if not rate_retried:
                rate_retried = True
                await _asyncio.sleep(wait)
                continue
            return None
        except TelegramAPIError:
            if not emoji_retried and _CUSTOM_EMOJI_RE.search(current_text):
                emoji_retried = True
                try:
                    await db.set_service_state(
                        "fragment_integration",
                        "faulty",
                        "Telegram rejected Custom Emoji formatting",
                    )
                except Exception:
                    log.warning("Could not persist Fragment failure", exc_info=True)
                current_text = without_custom_emojis(current_text)
                log.exception(
                    "Telegram rejected a post with Custom Emoji; retrying with Unicode"
                )
                continue
            log.exception("Не удалось опубликовать")
            return None
    return None


async def publish(
    bot: Bot,
    target_channel: str,
    text: str,
    media_files: list[tuple[str, str]] | None = None,
    *,
    table_rows: Sequence[tuple[str, str]] | None = None,
) -> int | None:
    """Choose a compact post or data-rich article, then publish safely."""
    import asyncio as _asyncio

    if not should_publish_as_article(table_rows):
        return await _publish_legacy(bot, target_channel, text, media_files)

    current_text = text
    rate_retried = False
    emoji_retried = False
    for attempt in range(3):
        try:
            message_id = await _publish_rich_once(
                bot,
                target_channel,
                current_text,
                media_files,
                table_rows,
            )
            if message_id is not None and _CUSTOM_EMOJI_RE.search(current_text):
                restored = False
                detail = "MTProto Premium session is not configured"
                if _mtproto_client is not None:
                    try:
                        rich_html = build_rich_message(
                            current_text,
                            media_files,
                            table_rows=table_rows,
                        )["html"]
                        restored = await mtproto.restore_rich_message_custom_emojis(
                            _mtproto_client,
                            target_channel,
                            message_id,
                            rich_html,
                            media_files,
                        )
                        if not restored:
                            detail = "Published message is not a Rich Message"
                    except Exception as error:
                        detail = f"MTProto Rich Message edit failed: {type(error).__name__}"
                        log.warning(detail, exc_info=True)
                try:
                    await db.set_service_state(
                        "fragment_integration",
                        "healthy" if restored else "faulty",
                        "" if restored else detail,
                    )
                except Exception:
                    log.warning("Could not persist Rich Message health", exc_info=True)
            if message_id is not None:
                return message_id
            break
        except TelegramRetryAfter as error:
            if rate_retried:
                break
            rate_retried = True
            wait = min(int(error.retry_after) + 1, 60)
            log.warning(
                "Rich Message rate limit, attempt=%d, retry_after=%ds (wait=%ds)",
                attempt,
                error.retry_after,
                wait,
            )
            await _asyncio.sleep(wait)
        except TelegramAPIError as error:
            if not emoji_retried and _CUSTOM_EMOJI_RE.search(current_text):
                emoji_retried = True
                current_text = without_custom_emojis(current_text)
                try:
                    await db.set_service_state(
                        "fragment_integration",
                        "faulty",
                        "Telegram rejected Custom Emoji in a Rich Message",
                    )
                except Exception:
                    log.warning("Could not persist Rich Message failure", exc_info=True)
                log.warning(
                    "Telegram rejected Rich Message Custom Emoji (%s); retrying with Unicode",
                    type(error).__name__,
                )
                continue
            log.warning(
                "Telegram rejected Rich Message (%s); using legacy publication",
                type(error).__name__,
            )
            break

    return await _publish_legacy(bot, target_channel, text, media_files)


def should_publish_as_article(
    table_rows: Sequence[tuple[str, str]] | None,
) -> bool:
    """Use an article only when a table has enough data to aid comparison."""
    meaningful_rows = [
        (label, value)
        for label, value in (table_rows or ())
        if label.strip() and value.strip()
    ]
    return len(meaningful_rows) >= 3
