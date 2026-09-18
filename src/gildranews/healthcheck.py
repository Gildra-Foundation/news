from __future__ import annotations

import sqlite3
from pathlib import Path

from gildranews.adapters.persistence.sqlite import CURRENT_SCHEMA_VERSION, DB_PATH


def check_health(
    database_path: Path = Path(DB_PATH),
    process_cmdline_path: Path = Path("/proc/1/cmdline"),
    *,
    expected_schema_version: int = CURRENT_SCHEMA_VERSION,
) -> tuple[bool, str]:
    try:
        command = (
            process_cmdline_path.read_bytes()
            .replace(b"\0", b" ")
            .decode(
                "utf-8",
                errors="replace",
            )
        )
    except OSError as exc:
        return False, f"cannot inspect main process: {type(exc).__name__}"
    if "gildranews" not in command:
        return False, "main process is not GildraNews"

    try:
        uri = database_path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as connection:
            quick_check = connection.execute("PRAGMA quick_check(1)").fetchone()
            if not quick_check or quick_check[0] != "ok":
                return False, "sqlite quick check failed"
            row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    except (OSError, sqlite3.DatabaseError) as exc:
        return False, f"database check failed: {type(exc).__name__}"

    actual_version = int(row[0] or 0) if row else 0
    if actual_version != expected_schema_version:
        return (
            False,
            f"schema version {actual_version}, expected {expected_schema_version}",
        )
    return True, "ok"


def main() -> int:
    healthy, detail = check_health()
    print(detail)
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
