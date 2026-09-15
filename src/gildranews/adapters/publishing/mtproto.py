from __future__ import annotations

from telethon import TelegramClient
from telethon.extensions import html

SESSION_NAME = "data/userbot"


def make_client(api_id: int, api_hash: str) -> TelegramClient:
    return TelegramClient(SESSION_NAME, api_id, api_hash)


async def publish(
    client: TelegramClient,
    target_channel: str,
    text: str,
    media_files: list[tuple[str, str]] | None = None,
) -> int | None:
    """Publish HTML with Custom Emoji through a Premium user session."""
    plain_text, entities = html.parse(text)
    if not media_files:
        sent = await client.send_message(
            target_channel,
            plain_text,
            formatting_entities=entities,
            link_preview=False,
        )
    else:
        paths = [path for path, _kind in media_files[:10]]
        if len(paths) == 1:
            sent = await client.send_file(
                target_channel,
                paths[0],
                caption=plain_text,
                formatting_entities=entities,
                supports_streaming=media_files[0][1] == "video",
            )
        else:
            sent = await client.send_file(
                target_channel,
                paths,
                caption=[plain_text],
                formatting_entities=[entities],
                supports_streaming=True,
            )
    first = sent[0] if isinstance(sent, list) and sent else sent
    return getattr(first, "id", None)
