from __future__ import annotations

from gildranews.adapters.ai import gemini as ai
from gildranews.adapters.emoji import catalog as emoji_store
from gildranews.adapters.persistence import sqlite as db
from gildranews.adapters.publishing import telegram as tg_writer


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


def test_emoji_override_ignores_invalid_patterns_and_finds_valid_match() -> None:
    emoji_map = {
        "broken": {"id": "1", "fallback": "?", "match_pattern": "["},
        "openai": {"id": "2", "fallback": "🤖", "match_pattern": r"\bOpenAI\b"},
    }

    assert emoji_store.detect_override(emoji_map, "Новая модель OpenAI") == "openai"
