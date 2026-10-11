from gildranews.adapters.publishing.telegram import (
    _format_duration_ms,
    _format_failed_retries,
)


def test_duration_formatting_stays_compact() -> None:
    assert _format_duration_ms(850) == "850 мс"
    assert _format_duration_ms(12_450) == "12,4 с"
    assert _format_duration_ms(125_000) == "2 мин 5 с"


def test_failed_retry_format_includes_safe_command_and_escaped_error() -> None:
    text = _format_failed_retries(
        [
            {
                "id": 17,
                "source": "wowhead",
                "external_id": 382961,
                "attempts": 5,
                "last_error": "presentation <issue>",
                "updated_at": "2026-09-20 18:00:00",
            },
        ],
    )

    assert "wowhead/382961" in text
    assert "presentation &lt;issue&gt;" in text
    assert "/retry_failed 17" in text
