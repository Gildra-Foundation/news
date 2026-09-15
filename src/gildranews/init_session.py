"""Однократная авторизация Telethon-сессии.

Запустить РОВНО ОДИН РАЗ перед первым стартом бота:
    python -m gildranews.init_session --qr

QR автоматически обновляется до успешного сканирования. При включённой
двухэтапной аутентификации пароль запрашивается скрыто в том же процессе.
Создаст файл userbot.session — после этого main.py запустится без интерактива.
"""
from __future__ import annotations

import asyncio
import getpass
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from telethon import TelegramClient, errors

from gildranews.adapters.publishing.mtproto import SESSION_NAME
from gildranews.config import load


def render_qr_in_terminal(url: str) -> None:
    """Показать QR без передачи одноразового токена в аргументах процесса."""
    print("\nОткройте Telegram → Настройки → Устройства → Подключить устройство")
    print("QR действителен около 30 секунд и обновится автоматически.\n")
    try:
        subprocess.run(
            ["qrencode", "-t", "ANSIUTF8", "-m", "2"],
            input=url,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Установите qrencode для входа по QR") from exc


def read_cloud_password() -> str:
    return getpass.getpass("Облачный пароль Telegram: ")


async def authorize_with_qr(
    client: Any,
    *,
    render_qr: Callable[[str], None] = render_qr_in_terminal,
    read_password: Callable[[], str] = read_cloud_password,
) -> None:
    """Авторизовать MTProto-сессию с обновляемым QR и поддержкой 2FA."""
    while True:
        try:
            qr = await client.qr_login()
            break
        except errors.AuthRestartError:
            continue

    while True:
        # Telethon требует начать ожидание UpdateLoginToken до сканирования.
        wait_task = asyncio.create_task(qr.wait())
        await asyncio.sleep(0)
        render_qr(qr.url)
        try:
            await wait_task
            return
        except TimeoutError:
            print("QR истёк — показываю новый…")
            await qr.recreate()
        except errors.SessionPasswordNeededError:
            while True:
                try:
                    await client.sign_in(password=read_password())
                    return
                except errors.PasswordHashInvalidError:
                    print("Неверный облачный пароль. Попробуйте ещё раз.")


async def main() -> None:
    cfg = load()
    complete_2fa = "--complete-2fa" in sys.argv[1:]
    qr_mode = "--qr" in sys.argv[1:]
    if not (
        cfg.telegram_reader_enabled
        or cfg.mtproto_publisher_enabled
        or complete_2fa
        or qr_mode
    ):
        raise RuntimeError(
            "Для MTProto задайте MTPROTO_PUBLISHER_ENABLED=true либо "
            "TELEGRAM_READER_ENABLED=true"
        )
    os.makedirs("data", exist_ok=True)
    client = TelegramClient(SESSION_NAME, cfg.tg_api_id, cfg.tg_api_hash)
    if qr_mode:
        await client.connect()
        if not await client.is_user_authorized():
            await authorize_with_qr(client)
    elif complete_2fa:
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
