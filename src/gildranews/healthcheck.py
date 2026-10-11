from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from gildranews.adapters.persistence.sqlite import CURRENT_SCHEMA_VERSION, DB_PATH


def check_health(
    database_path: Path = Path(DB_PATH),
    process_cmdline_path: Path = Path("/proc/1/cmdline"),
    *,
    expected_schema_version: int = CURRENT_SCHEMA_VERSION,
    now: datetime | None = None,
    max_heartbeat_age_seconds: int = 180,
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
            heartbeat_row = None
            if expected_schema_version >= 3:
                heartbeat_row = connection.execute(
                    "SELECT value, updated_at FROM service_state "
                    "WHERE key = 'scheduler_heartbeat'",
                ).fetchone()
    except (OSError, sqlite3.DatabaseError) as exc:
        return False, f"database check failed: {type(exc).__name__}"

    actual_version = int(row[0] or 0) if row else 0
    if actual_version != expected_schema_version:
        return (
            False,
            f"schema version {actual_version}, expected {expected_schema_version}",
        )
    if expected_schema_version >= 3:
        if not heartbeat_row or heartbeat_row[0] != "healthy":
            return False, "scheduler heartbeat is missing"
        try:
            heartbeat_at = datetime.strptime(
                str(heartbeat_row[1]),
                "%Y-%m-%d %H:%M:%S",
            ).replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return False, "scheduler heartbeat is invalid"
        current_time = (now or datetime.now(UTC)).astimezone(UTC)
        heartbeat_age = (current_time - heartbeat_at).total_seconds()
        if heartbeat_age > max(1, max_heartbeat_age_seconds):
            return False, "scheduler heartbeat is stale"
    return True, "ok"


def main() -> int:
    healthy, detail = check_health()
    print(detail)
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
