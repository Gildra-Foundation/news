from __future__ import annotations

import pytest

from gildranews.adapters.persistence import sqlite


class _Cursor:
    rowcount = 1


class _Connection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.commits = 0

    async def execute(self, statement: str) -> _Cursor:
        self.statements.append(statement)
        return _Cursor()

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_cleanup_does_not_vacuum_shared_live_connection(monkeypatch) -> None:
    connection = _Connection()

    async def get_connection() -> _Connection:
        return connection

    monkeypatch.setattr(sqlite, "_get_conn", get_connection)

    stats = await sqlite.cleanup_old_data()

    assert all("VACUUM" not in statement for statement in connection.statements)
    assert connection.commits == 1
    assert stats == {
        "drafts": 1,
        "edits": 1,
        "seen": 1,
        "runs": 1,
        "published": 1,
        "retries": 1,
    }
