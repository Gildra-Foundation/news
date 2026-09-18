from __future__ import annotations

import sqlite3

from gildranews.healthcheck import check_health


def test_healthcheck_accepts_running_bot_with_current_database(tmp_path) -> None:
    database = tmp_path / "newsbot.db"
    process = tmp_path / "cmdline"
    process.write_bytes(b"/sbin/docker-init\0--\0python\0-m\0gildranews\0")
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT)")
        connection.executemany(
            "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
            [(1, "baseline"), (2, "audit")],
        )

    healthy, detail = check_health(database, process, expected_schema_version=2)

    assert healthy is True
    assert detail == "ok"


def test_healthcheck_rejects_stale_schema(tmp_path) -> None:
    database = tmp_path / "newsbot.db"
    process = tmp_path / "cmdline"
    process.write_bytes(b"python\0-m\0gildranews\0")
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO schema_migrations(version, name) VALUES (1, 'baseline')")

    healthy, detail = check_health(database, process, expected_schema_version=2)

    assert healthy is False
    assert detail == "schema version 1, expected 2"


def test_healthcheck_rejects_wrong_main_process(tmp_path) -> None:
    process = tmp_path / "cmdline"
    process.write_bytes(b"sleep\01000\0")

    healthy, detail = check_health(
        tmp_path / "missing.db",
        process,
        expected_schema_version=2,
    )

    assert healthy is False
    assert detail == "main process is not GildraNews"
