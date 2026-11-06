"""Snapshot, incremental, and history ingestion plus change detection."""

import json
import logging
import sqlite3
from typing import TYPE_CHECKING, Any

from nando_api import parse, queries
from nando_api.db import finish_run, get_meta, set_meta, start_run, utc_now
from nando_api.source import (
    PAGE_SIZE,
    RESULT_CAP,
    EuropaSearchClient,
    SourceBlockedError,
    SourceError,
    term_query,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger(__name__)

SORT_DESC = [{"field": "es_SortDate", "order": "DESC"}]
NOTIFICATION = "nando_notification"
BODY = "nando_body"
LEGISLATION = "nando_legislation"
INCREMENTAL_MAX_PAGES = 10
LANGUAGE_FILTERED = frozenset({"nando_procedure", "nando_prod_family", "nando_product"})

BODY_DETAIL_FIELDS = (
    "name",
    "body_type",
    "country",
    "street",
    "postcode_city",
    "website",
    "end_date",
)


class Ingestor:
    """Writes source documents into the database and records change events."""

    def __init__(self, conn: sqlite3.Connection, *, emit_events: bool) -> None:
        self.conn = conn
        self.emit_events = emit_events
        self.now = utc_now()
        self.events = 0
        self.seen_notifications: set[int] = set()
        self.seen_bodies: set[str] = set()
        self.max_sort_date: dict[str, str] = {}
        self.errors: list[str] = []

    # -- raw store ---------------------------------------------------------

    def _store_raw(
        self,
        cs_type: str,
        result: dict[str, Any],
        doc: dict[str, Any],
        key: str,
        sha: str,
    ) -> None:
        sort_date = parse.es_date_to_utc(parse.meta_value(result, "es_SortDate"))
        if sort_date and sort_date > self.max_sort_date.get(cs_type, ""):
            self.max_sort_date[cs_type] = sort_date
        self.conn.execute(
            "INSERT OR IGNORE INTO raw_version (cs_type, doc_id, entity_key, version, sha256, "
            "json_doc, es_sort_date, ingest_date, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cs_type,
                doc.get("csId") or result.get("reference") or key,
                key,
                doc.get("notificationVersion") or doc.get("organizationVersion"),
                sha,
                json.dumps(doc, ensure_ascii=False, sort_keys=True),
                sort_date,
                parse.es_date_to_utc(parse.meta_value(result, "esDA_IngestDate")),
                self.now,
            ),
        )

    # -- events ------------------------------------------------------------

    def _event(self, change_type: str, **fields: Any) -> None:
        if not self.emit_events:
            return
        self.events += 1
        self.conn.execute(
            "INSERT INTO change_event (entity, entity_key, notification_id, body_ref, body_number, "
            "legislation_id, country_id, change_type, field, old_value, new_value, "
            "source_update_date, observed_at) "
            "VALUES (:entity, :entity_key, :notification_id, :body_ref, :body_number, "
            ":legislation_id, :country_id, :change_type, :field, :old_value, :new_value, "
            ":source_update_date, :observed_at)",
            {
                "notification_id": None,
                "body_ref": None,
                "body_number": None,
                "legislation_id": None,
                "country_id": None,
                "field": None,
                "old_value": None,
                "new_value": None,
                "source_update_date": None,
                **fields,
                "change_type": change_type,
                "observed_at": self.now,
            },
        )

    def _country_of(self, body_ref: str) -> int | None:
        row = self.conn.execute(
            "SELECT country_id FROM body WHERE body_ref = ?", (body_ref,)
        ).fetchone()
        return None if row is None else row["country_id"]

    # -- bodies ------------------------------------------------------------

    def body(self, result: dict[str, Any]) -> None:
        doc = parse.strip_personal(parse.doc_json(result))
        sha = parse.content_hash(doc)
        row = parse.parse_body(doc)
        self._store_raw(BODY, result, doc, row["body_ref"], sha)
        if not row["is_latest"]:
            return
        self.seen_bodies.add(row["body_ref"])
        old = self.conn.execute(
            "SELECT * FROM body WHERE body_ref = ?", (row["body_ref"],)
        ).fetchone()
        del row["is_latest"]
        if old is None:
            self.conn.execute(
                f"INSERT INTO body ({', '.join(row)}, in_source, sha256, first_seen, last_seen) "
                f"VALUES ({', '.join('?' * len(row))}, 1, ?, ?, ?)",
                (*row.values(), sha, self.now, self.now),
            )
            return
        if old["sha256"] != sha:
            for field in BODY_DETAIL_FIELDS:
                if old[field] != row[field]:
                    self._event(
                        "body_details_changed",
                        entity="body",
                        entity_key=row["body_ref"],
                        body_ref=row["body_ref"],
                        body_number=row["body_number"],
                        country_id=row["country_id"],
                        field=field,
                        old_value=old[field],
                        new_value=row[field],
                    )
            if not old["in_source"]:
                self._event(
                    "restored",
                    entity="body",
                    entity_key=row["body_ref"],
                    body_ref=row["body_ref"],
                    body_number=row["body_number"],
                    country_id=row["country_id"],
                )
        assignments = ", ".join(f"{k} = ?" for k in row)
        self.conn.execute(
            f"UPDATE body SET {assignments}, in_source = 1, sha256 = ?, last_seen = ? "
            "WHERE body_ref = ?",
            (*row.values(), sha, self.now, row["body_ref"]),
        )

    def _ensure_body(self, doc: dict[str, Any]) -> None:
        """Create a body row from notification data when the body is unknown."""
        row = parse.body_from_notification(doc)
        exists = self.conn.execute(
            "SELECT sha256 FROM body WHERE body_ref = ?", (row["body_ref"],)
        ).fetchone()
        if exists and exists["sha256"] is not None:
            return  # a body document owns this row
        if exists:
            assignments = ", ".join(f"{k} = ?" for k in row)
            self.conn.execute(
                f"UPDATE body SET {assignments}, in_source = 1, last_seen = ? WHERE body_ref = ?",
                (*row.values(), self.now, row["body_ref"]),
            )
            return
        self.conn.execute(
            f"INSERT INTO body ({', '.join(row)}, in_source, first_seen, last_seen) "
            f"VALUES ({', '.join('?' * len(row))}, 1, ?, ?)",
            (*row.values(), self.now, self.now),
        )

    # -- notifications -----------------------------------------------------

    def notification(self, result: dict[str, Any]) -> None:
        doc = parse.strip_personal(parse.doc_json(result))
        sha = parse.content_hash(doc)
        row = parse.parse_notification(doc)
        nid = row["notification_id"]
        self._store_raw(NOTIFICATION, result, doc, str(nid), sha)
        self._ensure_body(doc)
        if row["is_latest"]:
            self.seen_notifications.add(nid)

        existing = self.conn.execute(
            "SELECT * FROM notification WHERE notification_id = ?", (nid,)
        ).fetchone()
        current = self.conn.execute(
            "SELECT * FROM notification WHERE series_key = ? AND is_latest = 1 "
            "ORDER BY version DESC LIMIT 1",
            (row["series_key"],),
        ).fetchone()

        becomes_latest = bool(row["is_latest"]) and (
            current is None
            or current["notification_id"] == nid
            or (row["version"] or 0) >= (current["version"] or 0)
        )
        row["is_latest"] = 1 if becomes_latest else 0
        scope = parse.parse_scope(doc)

        if becomes_latest:
            baseline = existing if existing is not None and existing["is_latest"] else current
            if baseline is None or not baseline["in_source"]:
                if baseline is None:
                    self._event("new", **self._event_ref(row, doc))
                else:
                    self._event("restored", **self._event_ref(row, doc))
            if baseline is not None and baseline["sha256"] != sha:
                self._diff(baseline, row, scope, doc)
            if current is not None and current["notification_id"] != nid:
                self.conn.execute(
                    "UPDATE notification SET is_latest = 0 WHERE notification_id = ?",
                    (current["notification_id"],),
                )

        if existing is None:
            self.conn.execute(
                f"INSERT INTO notification ({', '.join(row)}, in_source, sha256, first_seen, "
                f"last_seen) VALUES ({', '.join('?' * len(row))}, 1, ?, ?, ?)",
                (*row.values(), sha, self.now, self.now),
            )
            self._write_scope(nid, scope)
        else:
            assignments = ", ".join(f"{k} = ?" for k in row)
            self.conn.execute(
                f"UPDATE notification SET {assignments}, in_source = 1, sha256 = ?, "
                "last_seen = ? WHERE notification_id = ?",
                (*row.values(), sha, self.now, nid),
            )
            if existing["sha256"] != sha:
                self._write_scope(nid, scope)

    def _write_scope(self, nid: int, scope: list[tuple[str, int | None, str | None]]) -> None:
        self.conn.execute("DELETE FROM notification_scope WHERE notification_id = ?", (nid,))
        self.conn.executemany(
            "INSERT INTO notification_scope (notification_id, kind, value_id, label) "
            "VALUES (?, ?, ?, ?)",
            [(nid, kind, value_id, label) for kind, value_id, label in scope],
        )

    def _event_ref(self, row: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity": "notification",
            "entity_key": row["series_key"],
            "notification_id": row["notification_id"],
            "body_ref": row["body_ref"],
            "body_number": row["body_number"],
            "legislation_id": row["legislation_id"],
            "country_id": doc.get("organizationCountryId"),
            "source_update_date": row["update_date"],
        }

    def _diff(
        self,
        old: sqlite3.Row,
        new: dict[str, Any],
        scope: list[tuple[str, int | None, str | None]],
        doc: dict[str, Any],
    ) -> None:
        ref = self._event_ref(new, doc)
        if old["status_id"] != new["status_id"]:
            self._event(
                status_change_type(
                    old["status_id"], old["end_date"], new["status_id"], new["end_date"]
                ),
                field="status",
                old_value=parse.status_label(old["status_id"]),
                new_value=parse.status_label(new["status_id"]),
                **ref,
            )
        if old["end_date"] != new["end_date"]:
            self._event(
                "end_date_changed",
                field="end_date",
                old_value=old["end_date"],
                new_value=new["end_date"],
                **ref,
            )
        old_scope = {
            (r["kind"], r["value_id"], r["label"])
            for r in self.conn.execute(
                "SELECT kind, value_id, label FROM notification_scope WHERE notification_id = ?",
                (old["notification_id"],),
            )
        }
        new_scope = set(scope)
        for change_type, items in (
            ("scope_added", new_scope - old_scope),
            ("scope_removed", old_scope - new_scope),
        ):
            by_kind: dict[str, list[str]] = {}
            for kind, value_id, label in sorted(items, key=str):
                by_kind.setdefault(kind, []).append(label if label is not None else str(value_id))
            for kind, values in sorted(by_kind.items()):
                self._event(
                    change_type,
                    field=kind,
                    new_value=json.dumps(values, ensure_ascii=False),
                    **ref,
                )

    # -- legislation and reference tables ------------------------------------

    def legislation(self, result: dict[str, Any]) -> None:
        doc = parse.strip_personal(parse.doc_json(result))
        sha = parse.content_hash(doc)
        row = parse.parse_legislation(doc)
        self._store_raw(LEGISLATION, result, doc, str(row["legislation_id"]), sha)
        self.conn.execute(
            "INSERT INTO legislation (legislation_id, code, number, name, status, profile, "
            "end_date, sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (legislation_id) DO "
            "UPDATE SET code = excluded.code, number = excluded.number, name = excluded.name, "
            "status = excluded.status, profile = excluded.profile, end_date = excluded.end_date, "
            "sha256 = excluded.sha256",
            (*row.values(), sha),
        )

    def reference(self, cs_type: str, result: dict[str, Any]) -> None:
        doc = parse.doc_json(result)
        row = parse.parse_reference(cs_type, doc)
        if row["language"] != "EN":
            return
        self.conn.execute(
            "INSERT INTO reference (cs_type, ref_id, legislation_id, language, label) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (cs_type, ref_id, legislation_id) DO UPDATE SET "
            "label = excluded.label, language = excluded.language",
            (
                row["cs_type"],
                row["ref_id"],
                row["legislation_id"],
                row["language"],
                row["label"],
            ),
        )

    # -- reconcile -----------------------------------------------------------

    def reconcile_notifications(self) -> int:
        """Mark latest notifications that the full snapshot no longer returned."""
        missing = [
            r
            for r in self.conn.execute(
                "SELECT n.*, b.country_id FROM notification n LEFT JOIN body b USING (body_ref) "
                "WHERE n.is_latest = 1 AND n.in_source = 1"
            )
            if r["notification_id"] not in self.seen_notifications
        ]
        for r in missing:
            self.conn.execute(
                "UPDATE notification SET in_source = 0 WHERE notification_id = ?",
                (r["notification_id"],),
            )
            self._event(
                "removed",
                entity="notification",
                entity_key=r["series_key"],
                notification_id=r["notification_id"],
                body_ref=r["body_ref"],
                body_number=r["body_number"],
                legislation_id=r["legislation_id"],
                country_id=r["country_id"],
            )
        return len(missing)

    def reconcile_bodies(self) -> int:
        missing = [
            r
            for r in self.conn.execute(
                "SELECT * FROM body WHERE in_source = 1 AND sha256 IS NOT NULL"
            )
            if r["body_ref"] not in self.seen_bodies
        ]
        for r in missing:
            self.conn.execute("UPDATE body SET in_source = 0 WHERE body_ref = ?", (r["body_ref"],))
            self._event(
                "removed",
                entity="body",
                entity_key=r["body_ref"],
                body_ref=r["body_ref"],
                body_number=r["body_number"],
                country_id=r["country_id"],
            )
        return len(missing)


def status_change_type(
    old_status: int | None,
    old_end: str | None,
    new_status: int | None,
    new_end: str | None,
    today: str | None = None,
) -> str:
    """Name a status transition by the notified state (`queries.is_notified`).

    "restored": not notified before, notified now. "suspended" or "withdrawn":
    notified before, not now, with that new status. Every other transition,
    such as 1 to 3 while still notified, is "status_changed".
    """
    was = queries.is_notified(old_status, old_end, today)
    now = queries.is_notified(new_status, new_end, today)
    if now and not was:
        return "restored"
    if was and not now:
        label = parse.status_label(new_status) or ""
        if label == "Suspended":
            return "suspended"
        if "Withdrawn" in label:
            return "withdrawn"
    return "status_changed"


def _drain(client: EuropaSearchClient, query: dict[str, Any], label: str) -> tuple[list[dict], int]:
    """Fetch all pages of a query; return (unique results, reported total)."""
    seen: dict[str, dict[str, Any]] = {}
    total = 0
    page = 1
    while True:
        body = client.search(query, page=page, sort=SORT_DESC, label=f"{label}-p{page}")
        total = int(body.get("totalResults", 0))
        if total > RESULT_CAP:
            msg = f"{label}: {total} hits exceed the {RESULT_CAP} cap"
            raise SourceError(msg)
        results = body.get("results") or []
        for result in results:
            seen[result.get("reference") or json.dumps(result, sort_keys=True)] = result
        if len(results) < PAGE_SIZE or page * PAGE_SIZE >= total:
            return list(seen.values()), total
        page += 1


def _apply(ing: Ingestor, cs_type: str, results: Iterable[dict[str, Any]]) -> int:
    """Store results; a document that fails to parse is logged and skipped."""
    handler = {
        NOTIFICATION: ing.notification,
        BODY: ing.body,
        LEGISLATION: ing.legislation,
    }
    count = 0
    for result in results:
        try:
            if cs_type in handler:
                handler[cs_type](result)
            else:
                ing.reference(cs_type, result)
        except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
            reference = result.get("reference", "?")
            log.warning("skipped %s document %s: %r", cs_type, reference, exc)
            ing.errors.append(f"{cs_type} {reference}: {exc!r}")
            continue
        count += 1
    return count


def snapshot(conn: sqlite3.Connection, client: EuropaSearchClient) -> dict[str, Any]:
    """Fetch the latest version of everything and reconcile.

    The first snapshot is the baseline and records no change events.
    """
    run_id = start_run(conn, "snapshot")
    baseline_done = get_meta(conn, "baseline_done") == "1"
    ing = Ingestor(conn, emit_events=baseline_done)
    detail: dict[str, Any] = {
        "baseline": not baseline_done,
        "counts": {},
        "skipped": [],
    }
    try:
        plan = [
            (LEGISLATION, term_query(csType=LEGISLATION)),
            (BODY, term_query(csType=BODY, organizationIfLatestVersion="Y")),
            (
                NOTIFICATION,
                term_query(csType=NOTIFICATION, notificationIfLatestVersion="Y"),
            ),
        ]
        complete: dict[str, bool] = {}
        for cs_type, query in plan:
            results, total = _drain(client, query, cs_type)
            errors_before = len(ing.errors)
            detail["counts"][cs_type] = {
                "total": total,
                "stored": _apply(ing, cs_type, results),
            }
            complete[cs_type] = len(results) == total and len(ing.errors) == errors_before
            conn.commit()

        if complete[NOTIFICATION]:
            detail["removed_notifications"] = ing.reconcile_notifications()
        if complete[BODY]:
            detail["removed_bodies"] = ing.reconcile_bodies()
        detail["complete"] = complete
        conn.commit()

        detail["reference"] = refresh_reference(ing, client, detail["skipped"])
        for cs_type, value in ing.max_sort_date.items():
            set_meta(conn, f"watermark:{cs_type}", value)
        set_meta(conn, "baseline_done", "1")
        detail["events"] = ing.events
        detail["errors"] = ing.errors[:50]
        detail["error_count"] = len(ing.errors)
        conn.commit()
    except SourceBlockedError as exc:
        detail["error"] = str(exc)
        detail["blocked"] = True
        conn.commit()
        finish_run(conn, run_id, ok=False, requests=client.requests, detail=detail)
        raise
    except SourceError as exc:
        detail["error"] = str(exc)
        conn.commit()
        finish_run(conn, run_id, ok=False, requests=client.requests, detail=detail)
        raise
    finish_run(conn, run_id, ok=True, requests=client.requests, detail=detail)
    detail["requests"] = client.requests
    return detail


def refresh_reference(
    ing: Ingestor, client: EuropaSearchClient, skipped: list[str]
) -> dict[str, int]:
    """Load English reference labels. Skip a type that would break the budget."""
    counts: dict[str, int] = {}
    for cs_type in parse.REFERENCE_TYPES:
        # Procedures, product families, and products exist once per language;
        # decisions and horizontal specifications carry no csLanguage field.
        if cs_type in LANGUAGE_FILTERED:
            query = term_query(csType=cs_type, csLanguage="EN")
        else:
            query = term_query(csType=cs_type)
        first = client.search(query, page=1, sort=SORT_DESC, label=f"{cs_type}-p1")
        total = int(first.get("totalResults", 0))
        pages = -(-total // PAGE_SIZE)
        if total > RESULT_CAP or pages - 1 > client.remaining:
            skipped.append(f"{cs_type}: {total} documents, {pages} pages, not fetched")
            continue
        stored = _apply(ing, cs_type, first.get("results") or [])
        for page in range(2, pages + 1):
            body = client.search(query, page=page, sort=SORT_DESC, label=f"{cs_type}-p{page}")
            stored += _apply(ing, cs_type, body.get("results") or [])
        counts[cs_type] = stored
        ing.conn.commit()
    return counts


def incremental(conn: sqlite3.Connection, client: EuropaSearchClient) -> dict[str, Any]:
    """Fetch documents newer than the stored watermark (all versions)."""
    if get_meta(conn, "baseline_done") != "1":
        msg = "no baseline: run `nando-api snapshot` first"
        raise SourceError(msg)
    run_id = start_run(conn, "incremental")
    ing = Ingestor(conn, emit_events=True)
    detail: dict[str, Any] = {"counts": {}}
    try:
        for cs_type in (BODY, NOTIFICATION):
            watermark = get_meta(conn, f"watermark:{cs_type}") or ""

            def at_watermark(result: dict[str, Any], mark: str = watermark) -> bool:
                value = parse.es_date_to_utc(parse.meta_value(result, "es_SortDate")) or ""
                return value <= mark

            fresh = list(
                client.iter_documents(
                    term_query(csType=cs_type),
                    sort=SORT_DESC,
                    label=f"{cs_type}-incremental",
                    stop=at_watermark,
                    max_pages=INCREMENTAL_MAX_PAGES,
                )
            )
            # Oldest first, so later versions of a series win.
            detail["counts"][cs_type] = _apply(ing, cs_type, reversed(fresh))
            if len(fresh) >= INCREMENTAL_MAX_PAGES * PAGE_SIZE:
                detail.setdefault("warnings", []).append(
                    f"{cs_type}: backlog above {len(fresh)} documents; the nightly snapshot "
                    "reconciles the rest"
                )
            if cs_type in ing.max_sort_date:
                set_meta(conn, f"watermark:{cs_type}", ing.max_sort_date[cs_type])
            conn.commit()
        detail["events"] = ing.events
    except SourceError as exc:
        detail["error"] = str(exc)
        detail["blocked"] = isinstance(exc, SourceBlockedError)
        conn.commit()
        finish_run(conn, run_id, ok=False, requests=client.requests, detail=detail)
        raise
    finish_run(conn, run_id, ok=True, requests=client.requests, detail=detail)
    detail["requests"] = client.requests
    return detail


def backfill_history(conn: sqlite3.Connection, client: EuropaSearchClient) -> dict[str, Any]:
    """Load every notification version, partitioned by legislation.

    Each legislation stays below the 10,000-hit cap; a legislation above it is
    split again by status. History rows record no change events.
    """
    legislation_ids = [
        r["legislation_id"] for r in conn.execute("SELECT legislation_id FROM legislation")
    ]
    if not legislation_ids:
        msg = "no legislation rows: run `nando-api snapshot` first"
        raise SourceError(msg)
    run_id = start_run(conn, "backfill-history")
    ing = Ingestor(conn, emit_events=False)
    detail: dict[str, Any] = {"partitions": {}}
    try:
        for legislation_id in sorted(legislation_ids):
            query = term_query(csType=NOTIFICATION, notificationLegislationId=legislation_id)
            try:
                results, total = _drain(client, query, f"history-{legislation_id}")
                parts = {str(legislation_id): (results, total)}
            except SourceError:
                parts = {}
                for status_id in sorted(parse.STATUS_LABELS):
                    sub = term_query(
                        csType=NOTIFICATION,
                        notificationLegislationId=legislation_id,
                        notificationStatusId=status_id,
                    )
                    parts[f"{legislation_id}/{status_id}"] = _drain(
                        client, sub, f"history-{legislation_id}-{status_id}"
                    )
            for name, (results, total) in parts.items():
                # Older versions first, so the latest flag settles correctly.
                ordered = sorted(
                    results,
                    key=lambda r: int(parse.doc_json(r).get("notificationVersion") or 0),
                )
                stored = _apply(ing, NOTIFICATION, ordered)
                detail["partitions"][name] = {"total": total, "stored": stored}
            conn.commit()
    except SourceError as exc:
        detail["error"] = str(exc)
        conn.commit()
        finish_run(conn, run_id, ok=False, requests=client.requests, detail=detail)
        raise
    finish_run(conn, run_id, ok=True, requests=client.requests, detail=detail)
    detail["requests"] = client.requests
    return detail
