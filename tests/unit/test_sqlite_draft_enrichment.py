from __future__ import annotations

import pytest

from gildranews.adapters.persistence import sqlite
from gildranews.domain.models import TelegramEmojiAsset


@pytest.mark.asyncio
async def test_draft_round_trips_warcraft_enrichment(monkeypatch, tmp_path) -> None:
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "newsbot.db"))
    await sqlite.init()
    try:
        assets = tuple(
            TelegramEmojiAsset(
                str(index), "file", "set", "✨", placement_label=f"Сущность {index}",
            )
            for index in range(1, 6)
        )
        draft_id = await sqlite.create_draft(
            "https://x.com/example/status/1",
            "Заголовок",
            "Текст",
            None,
            "Original",
            inline_links=(("Огненный шар", "https://www.wowhead.com/spell=133"),),
            custom_emojis=assets,
        )

        draft = await sqlite.get_draft(draft_id)

        assert draft is not None
        assert draft["inline_links"] == [
            ("Огненный шар", "https://www.wowhead.com/spell=133")
        ]
        assert draft["custom_emojis"] == list(assets[:4])

        await sqlite.set_service_state("fragment_integration", "faulty", "rejected")
        state = await sqlite.get_service_state("fragment_integration")
        assert state is not None
        assert state["value"] == "faulty"
        assert state["detail"] == "rejected"
    finally:
        await sqlite.close()
