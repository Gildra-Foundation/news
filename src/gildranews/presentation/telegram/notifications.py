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
    "filtered": "ℹ️",
    "duplicate": "🔁",
    "already_published": "🔁",
    "daily_limit": "⏸️",
    "publish_failed": "⚠️",
    "ai_error": "⚠️",
    "error": "⚠️",
}

STATUS_LABEL = {
    "published": "Новость опубликована",
    "filtered": "Материал пропущен",
    "duplicate": "Повторный материал",
    "already_published": "Материал уже опубликован",
    "daily_limit": "Дневной лимит публикаций достигнут",
    "publish_failed": "Не удалось опубликовать новость",
    "ai_error": "Ошибка обработки ИИ",
    "error": "Ошибка обработки",
}

_TYPESAFE_REJECTION = "TypeSafe:"


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
        source_link = (
            f'<a href="{html.escape(result.source_url, quote=True)}">'
            "Открыть материал</a>"
        )
    else:
        link = f"https://t.me/{result.channel}/{result.message_id}"
        source_link = f'<a href="{link}">@{result.channel}/{result.message_id}</a>'
    lines = [f"{emoji} <b>{label}</b>"]
    if result.title:
        lines.append(f"📌 <b>Заголовок:</b> {_html_escape(result.title)}")
    lines.append(f"🔗 <b>Источник:</b> {source_link}")
    if result.reason:
        if result.status == "filtered":
            reason_label = "Причина"
            reason_emoji = "📝"
        elif result.status in {"publish_failed", "ai_error", "error"}:
            reason_label = "Подробности"
            reason_emoji = "🧾"
        else:
            reason_label = "Примечание"
            reason_emoji = "📝"
        lines.append(
            f"{reason_emoji} <b>{reason_label}:</b> {_html_escape(result.reason)}",
        )
    return "\n\n".join(lines)


def should_notify_admin(result: ProcessResult) -> bool:
    if result.status == "duplicate":
        return False
    return not (
        result.status == "filtered"
        and result.reason.startswith(_TYPESAFE_REJECTION)
    )


async def notify_admin(bot: Bot, cfg: Config, result: ProcessResult) -> None:
    if cfg.admin_user_id == 0 or not should_notify_admin(result):
        return
    try:
        await bot.send_message(
            chat_id=cfg.admin_user_id,
            text=format_admin_notice(result),
            disable_web_page_preview=True,
        )
    except TelegramAPIError as error:
        log.warning("Не удалось отправить уведомление админу: %s", error)
