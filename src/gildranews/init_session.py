"""Однократная авторизация Telethon-сессии.

Запустить РОВНО ОДИН РАЗ перед первым стартом бота:
    python -m gildranews.init_session

Понадобится номер телефона и код из Telegram.
Создаст файл userbot.session — после этого main.py запустится без интерактива.
"""
from __future__ import annotations

import asyncio
import getpass
import os
import sys

from telethon import TelegramClient

from gildranews.adapters.publishing.mtproto import SESSION_NAME
from gildranews.config import load


async def main() -> None:
    cfg = load()
    complete_2fa = "--complete-2fa" in sys.argv[1:]
    if not (
        cfg.telegram_reader_enabled
        or cfg.mtproto_publisher_enabled
        or complete_2fa
    ):
        raise RuntimeError(
            "Для MTProto задайте MTPROTO_PUBLISHER_ENABLED=true либо "
            "TELEGRAM_READER_ENABLED=true"
        )
    os.makedirs("data", exist_ok=True)
    client = TelegramClient(SESSION_NAME, cfg.tg_api_id, cfg.tg_api_hash)
    if complete_2fa:
        await client.connect()
        if not await client.is_user_authorized():
            password = getpass.getpass("Облачный пароль Telegram: ")
            await client.sign_in(password=password)
    else:
        await client.start()
    me = await client.get_me()
    print(f"OK: вошли как {me.first_name} (@{me.username}), id={me.id}")
    print(f"Сессия сохранена в {SESSION_NAME}.session")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
