from __future__ import annotations

import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from gildranews.config import Config
from gildranews.domain.models import ProcessResult

log = logging.getLogger(__name__)

STATUS_EMOJI = {
    "published": "✅",
    "filtered": "🚫",
    "already_published": "🔁",
    "publish_failed": "⚠️",
    "ai_error": "⚠️",
    "error": "⚠️",
}

STATUS_LABEL = {
    "published": "Опубликовано",
    "filtered": "Отклонено",
    "already_published": "Уже публиковали",
    "publish_failed": "Ошибка публикации",
    "ai_error": "Ошибка AI",
    "error": "Ошибка обработки",
}


def _status_parts(result: ProcessResult) -> tuple[str, str]:
    emoji = STATUS_EMOJI.get(result.status, "•")
    label = STATUS_LABEL.get(result.status, result.status)
    return emoji, label


def format_status(result: ProcessResult) -> str:
    emoji, label = _status_parts(result)
    return f"{emoji} {label}"


def _html_escape(value: str) -> str:
    return html.escape(value)


def format_admin_notice(result: ProcessResult) -> str:
    emoji, label = _status_parts(result)
    if result.source_url:
        source_line = (
            f'<a href="{html.escape(result.source_url, quote=True)}">'
            "Исходная новость</a>"
        )
    else:
        link = f"https://t.me/{result.channel}/{result.message_id}"
        source_line = f'<a href="{link}">@{result.channel}/{result.message_id}</a>'
    lines = [f"{emoji} <b>{label}</b>", source_line]
    if result.title:
        lines.append(f"\n📌 {_html_escape(result.title)}")
    if result.reason:
        lines.append(f"\n💭 {_html_escape(result.reason)}")
    return "\n".join(lines)


async def notify_admin(bot: Bot, cfg: Config, result: ProcessResult) -> None:
    if cfg.admin_user_id == 0 or result.status == "duplicate":
        return
    try:
        await bot.send_message(
            chat_id=cfg.admin_user_id,
            text=format_admin_notice(result),
            disable_web_page_preview=True,
        )
    except TelegramAPIError as error:
        log.warning("Не удалось отправить уведомление админу: %s", error)
