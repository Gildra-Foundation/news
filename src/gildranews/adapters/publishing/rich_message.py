from __future__ import annotations

import re
from collections.abc import Sequence
from html import escape
from typing import Any

from aiogram.methods.base import TelegramMethod
from aiogram.types import FSInputFile, Message

SUBSCRIBE_URL = "https://t.me/gildrawow"
SUBSCRIBE_LABEL = "Подписаться на Gildra"


class SendRichMessage(TelegramMethod[Message]):
    """Bot API 10.3 method not yet exposed by the pinned aiogram release.

    Source: https://core.telegram.org/bots/api#sendrichmessage
    """

    __returning__ = Message
    __api_method__ = "sendRichMessage"

    chat_id: int | str
    rich_message: dict[str, Any]
    disable_notification: bool | None = None
    protect_content: bool | None = None


def _media_input(path: str) -> str | FSInputFile:
    if path.startswith(("https://", "http://")):
        return path
    return FSInputFile(path)


def _table_html(table_rows: Sequence[tuple[str, str]] | None) -> str:
    safe_rows = [
        (label.strip(), value.strip())
        for label, value in (table_rows or ())
        if label.strip() and value.strip()
    ][:8]
    if not safe_rows:
        return ""

    rows = [
        (
            '<tr><th align="left">Показатель</th>'
            '<th align="center">Значение</th></tr>'
        )
    ]
    rows.extend(
        '<tr><td align="left">'
        f"{escape(label)}</td><td align=\"center\">{escape(value)}</td></tr>"
        for label, value in safe_rows
    )
    return (
        "<table bordered striped compact><caption>Ключевые данные</caption>"
        f"{''.join(rows)}</table>"
    )


def build_rich_message(
    text: str,
    media_files: list[tuple[str, str]] | None = None,
    *,
    table_rows: Sequence[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Turn the existing escaped HTML post into a native Telegram article."""
    source_blocks = [block.strip() for block in re.split(r"\n{2,}", text) if block.strip()]
    if not source_blocks:
        raise ValueError("Rich Message cannot be empty")

    rich_media: list[dict[str, Any]] = []
    html_blocks: list[str] = []
    for index, (path, kind) in enumerate((media_files or [])[:10]):
        if kind not in {"photo", "video"}:
            continue
        media_id = f"media_{index}"
        tag = "img" if kind == "photo" else "video"
        html_blocks.append(f'<{tag} src="tg://{kind}?id={media_id}"/>')
        rich_media.append(
            {
                "id": media_id,
                "media": {"type": kind, "media": _media_input(path)},
            }
        )

    title = source_blocks.pop(0)
    title_match = re.fullmatch(r"(.*?)<b>(.*)</b>", title, flags=re.DOTALL)
    if title_match:
        title = f"{title_match.group(1)}{title_match.group(2)}"
    html_blocks.append(f"<h1>{title}</h1>")

    footer_parts: list[str] = []
    subscribe_button = ""
    content_blocks: list[str] = []
    for block in source_blocks:
        if SUBSCRIBE_URL in block and SUBSCRIBE_LABEL in block:
            icon = block.split("<a ", 1)[0].strip()
            label = f"{icon} {SUBSCRIBE_LABEL}" if icon else SUBSCRIBE_LABEL
            subscribe_button = (
                '<tg-button-row align="center"><tg-button type="url" '
                f'url="{SUBSCRIBE_URL}">{label}</tg-button>'
                "</tg-button-row>"
            )
        elif block.startswith("#"):
            footer_parts.append(block)
        else:
            content_blocks.append(block)

    if content_blocks:
        html_blocks.append(f"<p>{content_blocks[0]}</p>")
    if table := _table_html(table_rows):
        html_blocks.append(table)
    html_blocks.extend(f"<p>{block}</p>" for block in content_blocks[1:])

    if footer_parts:
        html_blocks.append(f"<footer>{'<br/>'.join(footer_parts)}</footer>")
    if subscribe_button:
        html_blocks.append(subscribe_button)

    rich_message: dict[str, Any] = {"html": "\n".join(html_blocks)}
    if rich_media:
        rich_message["media"] = rich_media
    return rich_message
