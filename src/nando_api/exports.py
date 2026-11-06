"""Daily snapshot exports (JSONL and CSV), free for everyone."""

import csv
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from nando_api.queries import export_rows

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

EXPORT_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def write_exports(conn: sqlite3.Connection, export_dir: Path, day: str | None = None) -> list[Path]:
    """Write <day>.jsonl and <day>.csv; return their paths."""
    day = day or datetime.now(UTC).date().isoformat()
    export_dir.mkdir(parents=True, exist_ok=True)
    rows = export_rows(conn)
    jsonl = export_dir / f"{day}.jsonl"
    csv_path = export_dir / f"{day}.csv"
    tmp_jsonl = jsonl.with_suffix(".jsonl.tmp")
    tmp_csv = csv_path.with_suffix(".csv.tmp")
    with tmp_jsonl.open("w", encoding="utf-8") as handle:
        handle.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    with tmp_csv.open("w", encoding="utf-8", newline="") as handle:
        fields = list(rows[0]) if rows else ["notification_id"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    tmp_jsonl.replace(jsonl)
    tmp_csv.replace(csv_path)
    return [jsonl, csv_path]


def list_exports(export_dir: Path) -> list[str]:
    """Dates for which both export files exist, newest first."""
    if not export_dir.is_dir():
        return []
    days = {
        path.stem
        for path in export_dir.glob("*.jsonl")
        if EXPORT_NAME_RE.match(path.stem) and path.with_suffix(".csv").exists()
    }
    return sorted(days, reverse=True)
