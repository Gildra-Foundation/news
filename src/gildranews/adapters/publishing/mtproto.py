from __future__ import annotations

import re
from collections.abc import Sequence

from telethon import TelegramClient, functions, types
from telethon.extensions import html

SESSION_NAME = "data/userbot"
_CUSTOM_EMOJI_ID_RE = re.compile(r'<tg-emoji\s+emoji-id="([0-9]+)">')


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


async def restore_rich_message_custom_emojis(
    client: TelegramClient,
    target_channel: str,
    message_id: int,
    rich_html: str,
    media_files: Sequence[tuple[str, str]] | None = None,
) -> bool:
    """Reparse a Bot API Rich Message as the authorized Premium user.

    Telegram can accept ``tg-emoji`` in a channel Rich Message while silently
    storing only its Unicode alternative. Re-editing the same rich HTML through
    the Premium MTProto session preserves ``TextCustomEmoji`` entities.

    Source: https://core.telegram.org/bots/api#richtextcustomemoji
    """
    message = await client.get_messages(target_channel, ids=message_id)
    rich_message = getattr(message, "rich_message", None)
    if rich_message is None:
        return False

    photos = iter(getattr(rich_message, "photos", None) or ())
    documents = iter(getattr(rich_message, "documents", None) or ())
    files: list[types.TypeInputRichFile] = []
    for index, (_path, kind) in enumerate(media_files or ()):
        media_id = f"media_{index}"
        if kind == "photo":
            try:
                photo = next(photos)
            except StopIteration:
                return False
            files.append(
                types.InputRichFilePhoto(
                    media_id,
                    types.InputPhoto(
                        photo.id,
                        photo.access_hash,
                        photo.file_reference,
                    ),
                )
            )
        elif kind == "video":
            try:
                document = next(documents)
            except StopIteration:
                return False
            files.append(
                types.InputRichFileDocument(
                    media_id,
                    types.InputDocument(
                        document.id,
                        document.access_hash,
                        document.file_reference,
                    ),
                )
            )

    peer = await client.get_input_entity(target_channel)
    await client(
        functions.messages.EditMessageRequest(
            peer=peer,
            id=message_id,
            rich_message=types.InputRichMessageHTML(
                html=rich_html,
                files=files or None,
            ),
        )
    )
    expected_ids = {int(value) for value in _CUSTOM_EMOJI_ID_RE.findall(rich_html)}
    if not expected_ids:
        return True
    updated = await client.get_messages(target_channel, ids=message_id)
    return expected_ids.issubset(
        _rich_custom_emoji_ids(getattr(updated, "rich_message", None))
    )


def _rich_custom_emoji_ids(value: object) -> set[int]:
    found: set[int] = set()
    if isinstance(value, types.TextCustomEmoji):
        found.add(value.document_id)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(_rich_custom_emoji_ids(item))
    elif hasattr(value, "__dict__"):
        for item in vars(value).values():
            found.update(_rich_custom_emoji_ids(item))
    return found
