from __future__ import annotations

from types import SimpleNamespace

import pytest
from telethon.tl.types import MessageEntityBold, MessageEntityCustomEmoji

from gildranews.adapters.publishing import mtproto


class _Client:
    def __init__(self) -> None:
        self.message_call: dict | None = None
        self.file_call: dict | None = None

    async def send_message(self, entity, message, **kwargs):
        self.message_call = {"entity": entity, "message": message, **kwargs}
        return SimpleNamespace(id=41)

    async def send_file(self, entity, file, **kwargs):
        self.file_call = {"entity": entity, "file": file, **kwargs}
        return SimpleNamespace(id=42)


@pytest.mark.asyncio
async def test_publish_converts_html_and_custom_emoji_to_mtproto_entities() -> None:
    client = _Client()

    message_id = await mtproto.publish(
        client,
        "@gildrawow",
        '<tg-emoji emoji-id="5368324170671202286">⚔️</tg-emoji> '
        "<b>Заголовок</b>",
    )

    assert message_id == 41
    assert client.message_call is not None
    assert client.message_call["message"] == "⚔️ Заголовок"
    entities = client.message_call["formatting_entities"]
    assert any(
        isinstance(entity, MessageEntityCustomEmoji)
        and entity.document_id == 5368324170671202286
        for entity in entities
    )
    assert any(isinstance(entity, MessageEntityBold) for entity in entities)
    assert client.message_call["link_preview"] is False


@pytest.mark.asyncio
async def test_publish_sends_media_album_with_caption_entities() -> None:
    client = _Client()

    message_id = await mtproto.publish(
        client,
        "@gildrawow",
        "<b>Подпись</b>",
        [("/tmp/raid.jpg", "photo"), ("/tmp/fight.mp4", "video")],
    )

    assert message_id == 42
    assert client.file_call is not None
    assert client.file_call["file"] == ["/tmp/raid.jpg", "/tmp/fight.mp4"]
    assert client.file_call["caption"] == ["Подпись"]
    assert len(client.file_call["formatting_entities"]) == 1
    assert isinstance(client.file_call["formatting_entities"][0][0], MessageEntityBold)
