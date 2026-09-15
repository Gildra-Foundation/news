from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from gildranews.adapters.ai import gemini as ai
from gildranews.adapters.emoji import catalog as emoji_store
from gildranews.adapters.persistence import sqlite as db
from gildranews.adapters.publishing import telegram as tg_writer
from gildranews.domain.models import TelegramEmojiAsset


def test_source_normalization_accepts_common_telegram_formats() -> None:
    assert db._normalize(" @Example_Channel ") == "example_channel"
    assert db._normalize("https://t.me/Example_Channel/42") == "example_channel"
    assert db._normalize("t.me/Example_Channel") == "example_channel"


def test_ai_json_parser_accepts_fenced_and_embedded_json() -> None:
    assert ai._parse_json('```json\n{"is_news": true}\n```') == {"is_news": True}
    assert ai._parse_json('Ответ: {"title": "Новость"}.') == {"title": "Новость"}
    assert ai._parse_json("не JSON") is None


def test_post_formatter_escapes_content_and_uses_selected_hashtag() -> None:
    result = tg_writer.format_post(
        title="OpenAI <news>",
        body="A & B",
        hashtag_key="новости",
    )

    assert result == "<b>OpenAI &lt;news&gt;</b>\n\nA &amp; B\n\n#новости"


def test_post_formatter_uses_premium_emoji_when_available() -> None:
    result = tg_writer.format_post(
        title="Релиз",
        body="Описание",
        emoji_theme="release",
        emoji_map={"release": {"id": "123", "fallback": "🚀"}},
    )

    assert result.startswith('<tg-emoji emoji-id="123">🚀</tg-emoji> <b>Релиз</b>')


def test_post_formatter_embeds_only_safe_wowhead_reference() -> None:
    result = tg_writer.format_post(
        title="В «Ядовитой Бездне» ослабят боссов",
        body="Игрокам станет проще пройти рейд & получить добычу.",
        hashtag_key="новости",
        inline_links=[
            ("Ядовитой Бездне", "https://www.wowhead.com/zone=16915"),
            ("рейд", "https://evil.example/phishing"),
        ],
    )

    assert (
        '<a href="https://www.wowhead.com/zone=16915">Ядовитой Бездне</a>'
        in result
    )
    assert "evil.example" not in result
    assert "рейд &amp; получить" in result


def test_post_formatter_uses_at_most_two_warcraft_custom_emojis() -> None:
    assets = [
        TelegramEmojiAsset(str(index), f"file-{index}", "set", "⚔️")
        for index in range(1, 4)
    ]

    result = tg_writer.format_post(
        title="Огненный шар усилят",
        body="Урон заклинания вырастет.",
        custom_emojis=assets,
    )

    assert result.count("<tg-emoji ") == 2
    assert result.startswith('<tg-emoji emoji-id="1">⚔️</tg-emoji> <b>')
    assert '\n\n<tg-emoji emoji-id="2">⚔️</tg-emoji> Урон' in result
    assert 'emoji-id="3"' not in result


@pytest.mark.asyncio
async def test_publish_retries_without_custom_emoji_when_telegram_rejects_it(
    monkeypatch,
) -> None:
    texts: list[str] = []
    states: list[tuple[str, str]] = []

    async def set_state(key: str, value: str, detail: str = "") -> None:
        states.append((key, value))

    monkeypatch.setattr(tg_writer.db, "set_service_state", set_state)

    class Bot:
        async def send_message(self, **kwargs):
            texts.append(kwargs["text"])
            if len(texts) == 1:
                raise TelegramBadRequest(
                    method=SendMessage(chat_id="@channel", text=kwargs["text"]),
                    message="Bad Request: can't parse entities",
                )
            return SimpleNamespace(message_id=91)

    result = await tg_writer.publish(
        Bot(),
        "@channel",
        '<tg-emoji emoji-id="123">⚔️</tg-emoji> <b>Заголовок</b>',
    )

    assert result == 91
    assert len(texts) == 2
    assert texts[1] == "⚔️ <b>Заголовок</b>"
    assert states == [("fragment_integration", "faulty")]


@pytest.mark.asyncio
async def test_publish_marks_fragment_faulty_when_telegram_silently_removes_entity(
    monkeypatch,
) -> None:
    states: list[tuple[str, str, str]] = []

    async def set_state(key: str, value: str, detail: str = "") -> None:
        states.append((key, value, detail))

    class Bot:
        async def send_message(self, **kwargs):
            return SimpleNamespace(message_id=92, entities=[], caption_entities=[])

    monkeypatch.setattr(tg_writer.db, "set_service_state", set_state)

    result = await tg_writer.publish(
        Bot(),
        "@channel",
        '<tg-emoji emoji-id="123">⚔️</tg-emoji> <b>Заголовок</b>',
    )

    assert result == 92
    assert states == [
        (
            "fragment_integration",
            "faulty",
            "Telegram accepted the post but removed Custom Emoji",
        )
    ]


@pytest.mark.asyncio
async def test_publish_prefers_configured_mtproto_transport(monkeypatch) -> None:
    calls: list[tuple[object, str, str, object]] = []
    states: list[tuple[str, str]] = []
    client = object()

    async def publish_mtproto(active_client, channel, text, media_files=None):
        calls.append((active_client, channel, text, media_files))
        return 93

    async def set_state(key: str, value: str, detail: str = "") -> None:
        states.append((key, value))

    class Bot:
        async def send_message(self, **kwargs):
            raise AssertionError("Bot API must not be used when MTProto succeeds")

    monkeypatch.setattr(tg_writer.mtproto, "publish", publish_mtproto)
    monkeypatch.setattr(tg_writer.db, "set_service_state", set_state)
    tg_writer.configure_mtproto_publisher(client)
    try:
        result = await tg_writer.publish(
            Bot(),
            "@channel",
            '<tg-emoji emoji-id="123">⚔️</tg-emoji> <b>Заголовок</b>',
        )
    finally:
        tg_writer.configure_mtproto_publisher(None)

    assert result == 93
    assert calls == [
        (
            client,
            "@channel",
            '<tg-emoji emoji-id="123">⚔️</tg-emoji> <b>Заголовок</b>',
            None,
        )
    ]
    assert states == [("fragment_integration", "healthy")]


@pytest.mark.asyncio
async def test_publish_bot_fallback_keeps_remote_media_url() -> None:
    received: list[object] = []

    class Bot:
        async def send_photo(self, **kwargs):
            received.append(kwargs["photo"])
            return SimpleNamespace(message_id=94, entities=[], caption_entities=[])

    result = await tg_writer.publish(
        Bot(),
        "@channel",
        "<b>Заголовок</b>",
        [("https://cdn.example/raid.jpg", "photo")],
    )

    assert result == 94
    assert received == ["https://cdn.example/raid.jpg"]


def test_emoji_override_ignores_invalid_patterns_and_finds_valid_match() -> None:
    emoji_map = {
        "broken": {"id": "1", "fallback": "?", "match_pattern": "["},
        "openai": {"id": "2", "fallback": "🤖", "match_pattern": r"\bOpenAI\b"},
    }

    assert emoji_store.detect_override(emoji_map, "Новая модель OpenAI") == "openai"
