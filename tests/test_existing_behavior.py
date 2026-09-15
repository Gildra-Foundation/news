from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest

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

    assert result == (
        "<b>OpenAI &lt;news&gt;</b>\n\nA &amp; B\n\n#новости@gildrawow\n\n"
        '🛡️ <a href="https://t.me/gildrawow">Подписаться на Gildra</a>'
    )


def test_post_formatter_ends_with_branded_subscription_custom_emoji() -> None:
    result = tg_writer.format_post(
        title="Новая броня",
        body="Облики станут доступны всем классам.",
        hashtag_key="новости",
        subscribe_emoji_id="999",
    )

    assert result.endswith(
        '<tg-emoji emoji-id="999">🛡️</tg-emoji> '
        '<a href="https://t.me/gildrawow">Подписаться на Gildra</a>'
    )


def test_subscription_emoji_does_not_reduce_entity_emoji_limit() -> None:
    assets = [
        TelegramEmojiAsset(str(index), f"file-{index}", "set", "⚔️")
        for index in range(1, 4)
    ]

    result = tg_writer.format_post(
        title="Огненный шар усилят",
        body="Урон заклинания вырастет.",
        custom_emojis=assets,
        subscribe_emoji_id="999",
    )

    assert result.count("<tg-emoji ") == 3
    assert 'emoji-id="1"' in result
    assert 'emoji-id="2"' in result
    assert 'emoji-id="3"' not in result
    assert 'emoji-id="999"' in result


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


def test_post_formatter_places_entity_emoji_next_to_body_mention() -> None:
    mage = TelegramEmojiAsset(
        "123",
        "file-123",
        "set",
        "⚔️",
        placement_label="Маги",
    )

    result = tg_writer.format_post(
        title="В обновлении откроют все типы брони",
        body="Маги смогут использовать облик латных наплечников.",
        custom_emojis=[mage],
    )

    assert result.startswith("<b>В обновлении откроют все типы брони</b>")
    assert (
        '<tg-emoji emoji-id="123">⚔️</tg-emoji> Маги смогут использовать'
        in result
    )


def test_post_formatter_does_not_move_unmatched_entity_emoji_to_title() -> None:
    mage = TelegramEmojiAsset(
        "123",
        "file-123",
        "set",
        "⚔️",
        placement_label="Маги",
    )

    result = tg_writer.format_post(
        title="Откроют все типы брони",
        body="Ограничения снимут для всех персонажей.",
        custom_emojis=[mage],
    )

    assert "<tg-emoji" not in result


def test_post_formatter_places_emoji_outside_linked_entity_name() -> None:
    spell = TelegramEmojiAsset(
        "123",
        "file-123",
        "set",
        "✨",
        placement_label="Огненный шар",
    )

    result = tg_writer.format_post(
        title="Огненный шар усилят",
        body="Урон заклинания вырастет.",
        inline_links=(("Огненный шар", "https://www.wowhead.com/spell=133"),),
        custom_emojis=(spell,),
    )

    assert result.startswith(
        '<b><tg-emoji emoji-id="123">✨</tg-emoji> '
        '<a href="https://www.wowhead.com/spell=133">Огненный шар</a> усилят</b>'
    )


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
        async def __call__(self, method):
            texts.append(method.rich_message["html"])
            if len(texts) == 1:
                raise TelegramBadRequest(
                    method=method,
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
    assert texts[1] == "<aside><b>⚔️ Заголовок</b></aside>"
    assert states == [("fragment_integration", "faulty")]


@pytest.mark.asyncio
async def test_publish_marks_fragment_healthy_when_rich_message_is_accepted(
    monkeypatch,
) -> None:
    states: list[tuple[str, str, str]] = []
    restored: list[tuple[object, str, int, str, object]] = []
    client = object()

    async def set_state(key: str, value: str, detail: str = "") -> None:
        states.append((key, value, detail))

    async def restore(active_client, channel, message_id, html, media_files=None):
        restored.append((active_client, channel, message_id, html, media_files))
        return True

    class Bot:
        async def __call__(self, method):
            assert '<tg-emoji emoji-id="123">' in method.rich_message["html"]
            return SimpleNamespace(message_id=92)

    monkeypatch.setattr(tg_writer.db, "set_service_state", set_state)
    monkeypatch.setattr(
        tg_writer.mtproto,
        "restore_rich_message_custom_emojis",
        restore,
    )

    tg_writer.configure_mtproto_publisher(client)
    try:
        result = await tg_writer.publish(
            Bot(),
            "@channel",
            '<tg-emoji emoji-id="123">⚔️</tg-emoji> <b>Заголовок</b>',
        )
    finally:
        tg_writer.configure_mtproto_publisher(None)

    assert result == 92
    assert len(restored) == 1
    assert restored[0][0:3] == (client, "@channel", 92)
    assert '<tg-emoji emoji-id="123">⚔️</tg-emoji>' in restored[0][3]
    assert states == [
        (
            "fragment_integration",
            "healthy",
            "",
        )
    ]


@pytest.mark.asyncio
async def test_published_rich_message_survives_mtproto_restore_failure(
    monkeypatch,
) -> None:
    states: list[tuple[str, str, str]] = []

    async def set_state(key: str, value: str, detail: str = "") -> None:
        states.append((key, value, detail))

    async def restore(*args, **kwargs):
        raise ConnectionError("temporary MTProto outage")

    class Bot:
        async def __call__(self, method):
            return SimpleNamespace(message_id=96)

    monkeypatch.setattr(tg_writer.db, "set_service_state", set_state)
    monkeypatch.setattr(
        tg_writer.mtproto,
        "restore_rich_message_custom_emojis",
        restore,
    )
    tg_writer.configure_mtproto_publisher(object())
    try:
        result = await tg_writer.publish(
            Bot(),
            "@channel",
            '<tg-emoji emoji-id="123">⚔️</tg-emoji> <b>Заголовок</b>',
        )
    finally:
        tg_writer.configure_mtproto_publisher(None)

    assert result == 96
    assert states == [
        (
            "fragment_integration",
            "faulty",
            "MTProto Rich Message edit failed: ConnectionError",
        ),
    ]


@pytest.mark.asyncio
async def test_publish_falls_back_to_configured_mtproto_transport(monkeypatch) -> None:
    calls: list[tuple[object, str, str, object]] = []
    states: list[tuple[str, str]] = []
    client = object()

    async def publish_mtproto(active_client, channel, text, media_files=None):
        calls.append((active_client, channel, text, media_files))
        return 93

    async def set_state(key: str, value: str, detail: str = "") -> None:
        states.append((key, value))

    class Bot:
        async def __call__(self, method):
            raise TelegramBadRequest(
                method=method,
                message="Bad Request: method is not available",
            )

        async def send_message(self, **kwargs):
            raise AssertionError("Legacy Bot API must not be used when MTProto succeeds")

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
    assert states == [
        ("fragment_integration", "faulty"),
        ("fragment_integration", "healthy"),
    ]


@pytest.mark.asyncio
async def test_publish_bot_fallback_keeps_remote_media_url() -> None:
    received: list[object] = []

    class Bot:
        async def __call__(self, method):
            received.append(
                method.rich_message["media"][0]["media"]["media"]
            )
            return SimpleNamespace(message_id=94)

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
