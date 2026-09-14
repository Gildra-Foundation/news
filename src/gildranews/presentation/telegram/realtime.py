from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot
from telethon import TelegramClient, events

from gildranews.adapters.persistence import sqlite as db
from gildranews.adapters.sources import telegram as telegram_source
from gildranews.application import process_news
from gildranews.config import Config
from gildranews.domain.models import ProcessResult

log = logging.getLogger(__name__)

ALBUM_DEBOUNCE_SECONDS = 2.5
ResultCallback = Callable[[ProcessResult], Awaitable[None]]


def register_realtime_handler(
    client: TelegramClient,
    bot: Bot,
    cfg: Config,
    on_result: ResultCallback,
) -> None:
    album_buffers: dict[int, list] = {}
    album_tasks: set[asyncio.Task] = set()

    async def process_messages(channel: str, messages: list) -> None:
        post = telegram_source.build_post_from_messages(channel, messages)
        if not post:
            return
        try:
            result = await process_news.process_post(client, bot, cfg, post)
            log.info("real-time @%s/%s -> %s", post.channel, post.message_id, result.status)
            await on_result(result)
        except Exception:
            log.exception("Ошибка real-time обработки @%s/%s", post.channel, post.message_id)

    async def flush_album(grouped_id: int, channel: str) -> None:
        await asyncio.sleep(ALBUM_DEBOUNCE_SECONDS)
        messages = album_buffers.pop(grouped_id, [])
        if messages:
            await process_messages(channel, messages)

    @client.on(events.NewMessage(incoming=True))
    async def realtime_handler(event) -> None:
        try:
            chat = await event.get_chat()
            username = (getattr(chat, "username", None) or "").lower()
            if not username or username not in set(await db.list_sources()):
                return

            message = event.message
            if not message:
                return

            if message.grouped_id:
                first_in_group = message.grouped_id not in album_buffers
                album_buffers.setdefault(message.grouped_id, []).append(message)
                if first_in_group:
                    task = asyncio.create_task(flush_album(message.grouped_id, username))
                    album_tasks.add(task)
                    task.add_done_callback(album_tasks.discard)
            else:
                await process_messages(username, [message])
        except Exception:
            log.exception("Ошибка в realtime_handler")

    log.info("Real-time обработчик зарегистрирован")
