from __future__ import annotations

import pytest

from gildranews import config


def test_load_reads_recent_post_context_limits(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("TARGET_CHANNEL", "@channel")
    monkeypatch.setenv("DEDUP_CONTEXT_HOURS", "72")
    monkeypatch.setenv("DEDUP_CONTEXT_LIMIT", "25")

    cfg = config.load()

    assert cfg.dedup_context_hours == 72
    assert cfg.dedup_context_limit == 25


@pytest.mark.parametrize(
    ("key", "value"),
    [("DEDUP_CONTEXT_HOURS", "0"), ("DEDUP_CONTEXT_LIMIT", "101")],
)
def test_load_rejects_unbounded_recent_post_context(monkeypatch, key, value) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("TARGET_CHANNEL", "@channel")
    monkeypatch.setenv(key, value)

    with pytest.raises(RuntimeError, match=key):
        config.load()
