from __future__ import annotations

from types import SimpleNamespace

import pytest
from telethon.tl import types
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


@pytest.mark.asyncio
async def test_restore_rich_message_custom_emojis_uses_premium_mtproto_html() -> None:
    photo = SimpleNamespace(id=10, access_hash=11, file_reference=b"photo")
    document = SimpleNamespace(id=20, access_hash=21, file_reference=b"video")
    sent = SimpleNamespace(
        rich_message=SimpleNamespace(
            photos=[photo],
            documents=[document],
        ),
    )

    class RichClient:
        def __init__(self) -> None:
            self.request = None
            self.reads = 0

        async def get_messages(self, entity, ids):
            assert (entity, ids) == ("@gildrawow", 51)
            self.reads += 1
            if self.reads == 1:
                return sent
            return SimpleNamespace(
                rich_message=SimpleNamespace(
                    blocks=[
                        types.PageBlockParagraph(
                            types.TextCustomEmoji(123, "🏆"),
                        ),
                    ],
                ),
            )

        async def get_input_entity(self, entity):
            assert entity == "@gildrawow"
            return "peer"

        async def __call__(self, request):
            self.request = request

    client = RichClient()
    html = (
        '<img src="tg://photo?id=media_0"/>'
        '<video src="tg://video?id=media_1"/>'
        '<aside><b><tg-emoji emoji-id="123">🏆</tg-emoji> Заголовок</b></aside>'
    )

    restored = await mtproto.restore_rich_message_custom_emojis(
        client,
        "@gildrawow",
        51,
        html,
        [("photo.jpg", "photo"), ("video.mp4", "video")],
    )

    assert restored is True
    assert client.request is not None
    rich_message = client.request.rich_message
    assert isinstance(rich_message, types.InputRichMessageHTML)
    assert rich_message.html == html
    assert isinstance(rich_message.files[0], types.InputRichFilePhoto)
    assert isinstance(rich_message.files[1], types.InputRichFileDocument)
    assert client.reads == 2
