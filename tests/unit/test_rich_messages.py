from __future__ import annotations

from types import SimpleNamespace

import pytest

from gildranews.adapters.publishing import telegram as tg_writer


def test_build_rich_message_structures_article_media_table_and_footer() -> None:
    text = (
        '<tg-emoji emoji-id="123">⚔️</tg-emoji> <b>Изменения рейда</b>\n\n'
        'Первый абзац.\n\nВторой абзац.\n\n'
        '#новости@gildrawow\n\n'
        '🛡️ <a href="https://t.me/gildrawow">Подписаться на Gildra</a>'
    )

    rich_message = tg_writer.build_rich_message(
        text,
        [
            ("https://cdn.example/raid.jpg", "photo"),
            ("https://cdn.example/raid.mp4", "video"),
        ],
        table_rows=(("Снижение урона", "25%"), ("Время", "30 секунд")),
    )

    html = rich_message["html"]
    assert html.startswith('<img src="tg://photo?id=media_0"/>')
    assert '<video src="tg://video?id=media_1"/>' in html
    assert (
        '<aside><b><tg-emoji emoji-id="123">⚔️</tg-emoji> '
        'Изменения рейда</b></aside>'
        in html
    )
    assert "<p>Первый абзац.</p>" in html
    assert '<table bordered striped compact>' in html
    assert '<th align="left">Показатель</th>' in html
    assert '<td align="center">25%</td>' in html
    assert '<footer>#новости@gildrawow</footer>' in html
    assert (
        '<tg-button-row align="center"><tg-button type="url" '
        'url="https://t.me/gildrawow">🛡️ Подписаться на Gildra</tg-button>'
        '</tg-button-row>' in html
    )
    assert html.index("<p>Первый абзац.</p>") < html.index("<table bordered")
    assert html.index("<table bordered") < html.index("<p>Второй абзац.</p>")
    assert rich_message["media"] == [
        {
            "id": "media_0",
            "media": {"type": "photo", "media": "https://cdn.example/raid.jpg"},
        },
        {
            "id": "media_1",
            "media": {"type": "video", "media": "https://cdn.example/raid.mp4"},
        },
    ]


@pytest.mark.asyncio
async def test_publish_uses_rich_message_before_configured_mtproto(monkeypatch) -> None:
    calls: list[str] = []
    client = object()

    async def publish_rich_once(*_args, **_kwargs):
        calls.append("rich")
        return 501

    async def publish_legacy(*_args, **_kwargs):
        calls.append("legacy")
        return 502

    monkeypatch.setattr(tg_writer, "_publish_rich_once", publish_rich_once)
    monkeypatch.setattr(tg_writer, "_publish_legacy", publish_legacy)
    tg_writer.configure_mtproto_publisher(client)
    try:
        result = await tg_writer.publish(
            SimpleNamespace(),
            "@gildrawow",
            "<b>Заголовок</b>\n\nТекст",
            table_rows=(
                ("Босс", "20%"),
                ("Второй босс", "15%"),
                ("Третий босс", "10%"),
            ),
        )
    finally:
        tg_writer.configure_mtproto_publisher(None)

    assert result == 501
    assert calls == ["rich"]


@pytest.mark.asyncio
async def test_publish_keeps_short_summary_as_regular_post(monkeypatch) -> None:
    calls: list[str] = []

    async def publish_rich_once(*_args, **_kwargs):
        calls.append("rich")
        return 501

    async def publish_legacy(*_args, **_kwargs):
        calls.append("legacy")
        return 502

    monkeypatch.setattr(tg_writer, "_publish_rich_once", publish_rich_once)
    monkeypatch.setattr(tg_writer, "_publish_legacy", publish_legacy)

    result = await tg_writer.publish(
        SimpleNamespace(),
        "@gildrawow",
        "<b>Калькулятор WoW: Forever доступен</b>\n\n"
        "Игроки могут заранее распределить очки наследия.",
        table_rows=(("очков на старте", "16"), ("ноября — запуск", "4")),
    )

    assert result == 502
    assert calls == ["legacy"]


def test_format_post_keeps_entity_fallback_when_custom_emoji_is_queued() -> None:
    queued = tg_writer.TelegramEmojiAsset(
        custom_emoji_id="",
        file_id="",
        sticker_set_name="",
        fallback="🏆",
        placement_label="Покоритель проклятий",
    )

    text = tg_writer.format_post(
        "Проклятые всплески появляются чаще",
        "Для достижения «Покоритель проклятий» потребуется 150 всплесков.",
        custom_emojis=(queued,),
    )

    assert "🏆 Покоритель проклятий" in text
