"""SQLite access: connection, schema, and small helpers."""

import json
import sqlite3
from datetime import UTC, datetime
from importlib.resources import files
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def connect(path: Path) -> sqlite3.Connection:
    """Open the database and create the schema when needed."""
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(files("nando_api").joinpath("schema.sql").read_text(encoding="utf-8"))
    return conn


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def start_run(conn: sqlite3.Connection, kind: str) -> int:
    cursor = conn.execute(
        "INSERT INTO sync_run (kind, started_at) VALUES (?, ?)", (kind, utc_now())
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    ok: bool,
    requests: int,
    detail: dict[str, Any],
) -> None:
    conn.execute(
        "UPDATE sync_run SET finished_at = ?, ok = ?, requests = ?, detail = ? WHERE run_id = ?",
        (utc_now(), int(ok), requests, json.dumps(detail, sort_keys=True), run_id),
    )
    conn.commit()


def as_of(conn: sqlite3.Connection) -> str | None:
    """Finish time of the last successful sync of any kind."""
    row = conn.execute(
        "SELECT MAX(finished_at) AS t FROM sync_run WHERE ok = 1 AND kind != 'backfill-history'"
    ).fetchone()
    return None if row is None else row["t"]
