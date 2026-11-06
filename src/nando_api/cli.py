"""Command-line entry point: `nando-api <command>`."""

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click

from nando_api import db, exports, ingest
from nando_api.config import get_settings
from nando_api.source import (
    EuropaSearchClient,
    SourceBlockedError,
    fetch_key,
    write_env_value,
)

if TYPE_CHECKING:
    import sqlite3

log = logging.getLogger("nando_api")


def _client(max_requests: int | None = None) -> EuropaSearchClient:
    settings = get_settings()
    return EuropaSearchClient(
        settings.nando_es_api_key,
        user_agent=settings.user_agent,
        request_interval=settings.request_interval,
        max_requests=max_requests or settings.max_requests,
        record_dir=settings.record_dir,
    )


def _connect(path: Path) -> sqlite3.Connection:
    """Open the database and close it when the command ends."""
    conn = db.connect(path)
    click.get_current_context().call_on_close(conn.close)
    return conn


def _echo(result: dict) -> None:
    click.echo(json.dumps(result, indent=2, sort_keys=True))


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Log every source request.")
def main(verbose: bool) -> None:
    """Free, unofficial API over NANDO, the EU register of notified bodies."""
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full URLs, which contain the key. Keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)


@main.command("fetch-key")
@click.option("--env-file", type=click.Path(path_type=Path), default=Path(".env"))
def fetch_key_cmd(env_file: Path) -> None:
    """Read the public front-end key from the NANDO app bundle into .env."""
    settings = get_settings()
    key, requests = fetch_key(
        user_agent=settings.user_agent, request_interval=settings.request_interval
    )
    write_env_value(env_file, "NANDO_ES_API_KEY", key)
    click.echo(f"Key written to {env_file} ({requests} requests). The key is not printed.")


@main.command()
@click.option("--no-export", is_flag=True, help="Skip writing the daily export files.")
def snapshot(no_export: bool) -> None:
    """Fetch the latest version of every body, notification, and reference table."""
    settings = get_settings()
    conn = _connect(settings.database_path)
    with _client() as client:
        result = ingest.snapshot(conn, client)
    if not no_export:
        result["exports"] = [str(p) for p in exports.write_exports(conn, settings.export_dir)]
    _echo(result)


@main.command()
def incremental() -> None:
    """Fetch documents newer than the stored watermark."""
    settings = get_settings()
    conn = _connect(settings.database_path)
    with _client() as client:
        _echo(ingest.incremental(conn, client))


@main.command("backfill-history")
@click.option("--max-requests", type=int, default=400, show_default=True)
def backfill_history(max_requests: int) -> None:
    """Load every notification version once, partitioned by legislation."""
    settings = get_settings()
    conn = _connect(settings.database_path)
    with _client(max_requests) as client:
        _echo(ingest.backfill_history(conn, client))


@main.command("export")
def export_cmd() -> None:
    """Write today's JSONL and CSV export from the local database."""
    settings = get_settings()
    conn = _connect(settings.database_path)
    for path in exports.write_exports(conn, settings.export_dir):
        click.echo(str(path))


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8601, show_default=True, type=int)
def serve(host: str, port: int) -> None:
    """Run the API server."""
    import uvicorn

    uvicorn.run("nando_api.api:app", host=host, port=port)


@main.command()
@click.option("--interval", default=900, show_default=True, help="Seconds between incrementals.")
@click.option("--snapshot-hour", default=2, show_default=True, help="UTC hour for the snapshot.")
def worker(interval: int, snapshot_hour: int) -> None:  # pragma: no cover - long-running loop
    """Run incremental every 15 minutes and a snapshot once a night.

    Stops for good on any sign of blocking (401, 403, 429).
    """
    settings = get_settings()
    conn = _connect(settings.database_path)
    last_snapshot_day = db.get_meta(conn, "worker:last_snapshot_day")
    while True:
        now = datetime.now(UTC)
        today = now.date().isoformat()
        try:
            with _client() as client:
                if now.hour >= snapshot_hour and last_snapshot_day != today:
                    ingest.snapshot(conn, client)
                    exports.write_exports(conn, settings.export_dir)
                    last_snapshot_day = today
                    db.set_meta(conn, "worker:last_snapshot_day", today)
                    conn.commit()
                else:
                    ingest.incremental(conn, client)
        except SourceBlockedError:
            log.exception("source refused access; the worker stops and does not retry")
            raise
        except Exception:
            log.exception("sync failed; next attempt in %d s", interval)
        time.sleep(interval)


@main.command()
def status() -> None:
    """Print record counts and the last sync runs."""
    settings = get_settings()
    conn = _connect(settings.database_path)
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "body",
            "notification",
            "legislation",
            "reference",
            "change_event",
            "raw_version",
        )
    }
    runs = [
        dict(r)
        for r in conn.execute(
            "SELECT run_id, kind, started_at, finished_at, ok, requests FROM sync_run "
            "ORDER BY run_id DESC LIMIT 5"
        )
    ]
    _echo({"as_of": db.as_of(conn), "counts": counts, "runs": runs})
