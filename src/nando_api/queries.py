"""Read-side queries shared by the API, the feeds, and the exports."""

import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from nando_api import parse

if TYPE_CHECKING:
    import sqlite3

PUBLIC_NOTIFICATION_FIELDS = (
    "notification_id",
    "version",
    "is_latest",
    "in_source",
    "status_id",
    "end_date",
    "last_approval_date",
    "update_date",
    "accreditation",
    "accreditation_expiry_date",
    "template_profile",
)


def resolve_legislation(conn: sqlite3.Connection, value: str) -> list[sqlite3.Row]:
    """Find legislation by id, exact code, or act number (2017/745, 305/2011).

    Returns every match so that callers can report ambiguity.
    """
    text = value.strip()
    if text.isdigit():
        rows = conn.execute(
            "SELECT * FROM legislation WHERE legislation_id = ?", (int(text),)
        ).fetchall()
        if rows:
            return rows
    rows = conn.execute(
        "SELECT * FROM legislation WHERE lower(code) = lower(?)", (text,)
    ).fetchall()
    if rows:
        return rows
    number = parse.legal_number(text)
    if number is None:
        return []
    candidates = conn.execute("SELECT * FROM legislation WHERE number IS NOT NULL").fetchall()
    exact = [r for r in candidates if r["number"].lower() == number.lower()]
    if exact:
        return exact
    # Allow 2006/42 to match 2006/42/EC.
    return [r for r in candidates if r["number"].lower().startswith(number.lower() + "/")]


def legislation_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "legislation_id": row["legislation_id"],
        "code": row["code"],
        "number": row["number"],
        "name": row["name"],
        "status": row["status"],
        "profile": row["profile"],
        "end_date": row["end_date"],
    }


def body_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "body_number": parse.body_number_str(row["body_number"]),
        "body_ref": row["body_ref"],
        "body_type": row["body_type"],
        "name": row["name"],
        "country": row["country"],
        "country_id": row["country_id"],
        "street": row["street"],
        "postcode_city": row["postcode_city"],
        "website": row["website"],
        "last_approval_date": row["last_approval_date"],
        "end_date": row["end_date"],
        "in_source": bool(row["in_source"]),
    }


def bodies_by_number(conn: sqlite3.Connection, number: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM body WHERE body_number = ? ORDER BY body_type = 'NB' DESC, body_ref",
        (number,),
    ).fetchall()


def notification_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    legislation = conn.execute(
        "SELECT * FROM legislation WHERE legislation_id = ?", (row["legislation_id"],)
    ).fetchone()
    data = {field: row[field] for field in PUBLIC_NOTIFICATION_FIELDS}
    data["is_latest"] = bool(data["is_latest"])
    data["in_source"] = bool(data["in_source"])
    data["status"], data["status_basis"] = shown_status(row["status_id"], row["end_date"])
    data["body_number"] = parse.body_number_str(row["body_number"])
    data["body_ref"] = row["body_ref"]
    data["legislation"] = (
        legislation_dict(legislation) if legislation else {"legislation_id": row["legislation_id"]}
    )
    return data


def scope_for(conn: sqlite3.Connection, notification_id: int) -> dict[str, dict[str, list]]:
    """Scope grouped by kind; ids resolve to English labels where known."""
    rows = conn.execute(
        "SELECT s.kind, s.value_id, s.label, n.legislation_id FROM notification_scope s "
        "JOIN notification n USING (notification_id) WHERE s.notification_id = ? "
        "ORDER BY s.kind, s.value_id, s.label",
        (notification_id,),
    ).fetchall()
    scope: dict[str, dict[str, list]] = {}
    for r in rows:
        entry = scope.setdefault(r["kind"], {"ids": [], "labels": []})
        if r["value_id"] is None:
            entry["labels"].append(r["label"])
            continue
        ref_type = parse.SCOPE_REFERENCE.get(r["kind"])
        label = None
        if ref_type:
            hit = conn.execute(
                "SELECT label FROM reference WHERE cs_type = ? AND ref_id = ? "
                "ORDER BY legislation_id = ? DESC LIMIT 1",
                (ref_type, r["value_id"], r["legislation_id"]),
            ).fetchone()
            label = hit["label"] if hit else None
        entry["ids"].append({"id": r["value_id"], "label": label})
    return scope


def history_for(conn: sqlite3.Connection, series_key: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM notification WHERE series_key = ? ORDER BY version DESC",
        (series_key,),
    ).fetchall()
    return [
        {
            "notification_id": r["notification_id"],
            "version": r["version"],
            "is_latest": bool(r["is_latest"]),
            "status_id": r["status_id"],
            **status_fields(r["status_id"], r["end_date"]),
            "end_date": r["end_date"],
            "update_date": r["update_date"],
            "first_seen": r["first_seen"],
        }
        for r in rows
    ]


def utc_today() -> str:
    return datetime.now(UTC).date().isoformat()


def _ids(alias: str) -> tuple[str, str]:
    """Column prefix and the placeholders for the notified status ids."""
    return (f"{alias}." if alias else ""), ",".join("?" * len(parse.NOTIFIED_STATUS_IDS))


def is_notified(status_id: int | None, end_date: str | None, today: str | None = None) -> bool:
    """`notified_sql` for one record's status id and end date (latest, in source)."""
    if status_id not in parse.NOTIFIED_STATUS_IDS:
        return False
    return end_date is None or end_date >= (today or utc_today())


def notified_sql(alias: str = "", today: str | None = None) -> tuple[str, list[Any]]:
    """The one rule for "currently notified", as an SQL condition and its parameters.

    Every answer that says notified, active count, or status=active uses this.
    The rule: the latest version is in the source, its status id is in
    `parse.NOTIFIED_STATUS_IDS`, and its end date is empty or today or later
    (UTC). `shown_status` labels exactly these rows "Active".
    """
    a, marks = _ids(alias)
    condition = (
        f"({a}is_latest = 1 AND {a}in_source = 1 AND {a}status_id IN ({marks}) "
        f"AND ({a}end_date IS NULL OR {a}end_date >= ?))"
    )
    return condition, [*parse.NOTIFIED_STATUS_IDS, today or utc_today()]


def expired_sql(alias: str = "", today: str | None = None) -> tuple[str, list[Any]]:
    """Records that `shown_status` labels "Expired": a notified status id, end date passed."""
    a, marks = _ids(alias)
    condition = f"({a}status_id IN ({marks}) AND {a}end_date < ?)"
    return condition, [*parse.NOTIFIED_STATUS_IDS, today or utc_today()]


STATUS_BASIS = {
    False: "end date not passed, so shown as Active",
    True: "end date passed, so shown as Expired",
}


def shown_status(
    status_id: int | None, end_date: str | None, today: str | None = None
) -> tuple[str | None, str | None]:
    """The status label this service shows, and a note when the end date decided it.

    Ids in `parse.NOTIFIED_STATUS_IDS` are "Active" while the end date is empty
    or not passed, and "Expired" after it, as on the NANDO site. Other ids keep
    their NANDO label. The note is None when the shown label is the label of
    the raw `status_id`.
    """
    label = parse.status_label(status_id)
    if status_id not in parse.NOTIFIED_STATUS_IDS:
        return label, None
    passed = not is_notified(status_id, end_date, today)
    shown = "Expired" if passed else "Active"
    return shown, None if shown == label else STATUS_BASIS[passed]


def status_fields(status_id: int | None, end_date: str | None) -> dict[str, str | None]:
    """`status` and `status_basis` for a response, from `shown_status`."""
    status, basis = shown_status(status_id, end_date)
    return {"status": status, "status_basis": basis}


def latest_for_body_legislation(
    conn: sqlite3.Connection, number: int, legislation_ids: list[int], today: str | None = None
) -> list[sqlite3.Row]:
    """Latest versions, currently notified ones first; `notified` is 0 or 1."""
    marks = ",".join("?" * len(legislation_ids))
    notified, params = notified_sql(today=today)
    return conn.execute(
        f"SELECT *, {notified} AS notified FROM notification WHERE is_latest = 1 "
        f"AND body_number = ? AND legislation_id IN ({marks}) "
        "ORDER BY notified DESC, status_id = 1 DESC, version DESC",
        (*params, number, *legislation_ids),
    ).fetchall()


def _scope_match(
    conn: sqlite3.Connection, notification_id: int, kinds: tuple[str, ...], wanted: str
) -> list[str]:
    """Return the scope entries that match `wanted` (an id or a text fragment)."""
    marks = ",".join("?" * len(kinds))
    rows = conn.execute(
        f"SELECT kind, value_id, label FROM notification_scope WHERE notification_id = ? "
        f"AND kind IN ({marks})",
        (notification_id, *kinds),
    ).fetchall()
    needle = wanted.strip().lower()
    hits: list[str] = []
    for r in rows:
        if r["value_id"] is not None and needle.isdigit() and int(needle) == r["value_id"]:
            hits.append(f"{r['kind']}:{r['value_id']}")
        elif r["label"] and needle and needle in r["label"].lower():
            hits.append(r["label"])
    if not needle.isdigit():
        # Ids without inline labels: match against the reference labels.
        for kind in kinds:
            ref_type = parse.SCOPE_REFERENCE.get(kind)
            if not ref_type:
                continue
            hits.extend(
                r["label"]
                for r in conn.execute(
                    "SELECT DISTINCT ref.label FROM notification_scope s JOIN reference ref "
                    "ON ref.cs_type = ? AND ref.ref_id = s.value_id "
                    "WHERE s.notification_id = ? AND s.kind = ? AND lower(ref.label) LIKE ?",
                    (ref_type, notification_id, kind, f"%{needle}%"),
                )
            )
    return sorted(set(hits))


def check(
    conn: sqlite3.Connection,
    body: str,
    legislation: str,
    procedure: str | None = None,
    product: str | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """Is body X notified under legislation Y today (as of the last sync)?

    `notified` follows `notified_sql`. `today` (YYYY-MM-DD) overrides the date.
    """
    result: dict[str, Any] = {
        "query": {
            "body": body,
            "legislation": legislation,
            "procedure": procedure,
            "product": product,
        },
        "notified": False,
        "status": None,
        "status_id": None,
        "status_basis": None,
        "body": None,
        "legislation": None,
        "notification_id": None,
        "since": None,
        "end_date": None,
        "last_approval_date": None,
        "procedure_in_scope": None,
        "procedure_matches": [],
        "product_in_scope": None,
        "product_matches": [],
        "reason": None,
    }
    number = parse.parse_body_number(body)
    if number is None:
        result["reason"] = "invalid_body_number"
        return result
    bodies = bodies_by_number(conn, number)
    if not bodies:
        result["reason"] = "body_not_found"
        return result
    result["body"] = {
        "body_number": parse.body_number_str(number),
        "name": bodies[0]["name"],
        "country": bodies[0]["country"],
    }
    acts = resolve_legislation(conn, legislation)
    if not acts:
        result["reason"] = "legislation_not_found"
        return result
    if len(acts) > 1:
        result["reason"] = "legislation_ambiguous"
        result["legislation_candidates"] = [legislation_dict(a) for a in acts]
        return result
    result["legislation"] = legislation_dict(acts[0])
    today = today or utc_today()
    rows = latest_for_body_legislation(conn, number, [acts[0]["legislation_id"]], today)
    if not rows:
        result["reason"] = "no_notification"
        return result
    row = rows[0]
    notified = bool(row["notified"])
    reason = None
    status, basis = shown_status(row["status_id"], row["end_date"], today)
    if not notified:
        expired = status == "Expired" and row["status_id"] in parse.NOTIFIED_STATUS_IDS
        reason = "end_date_passed" if expired and row["in_source"] else "not_active"
    if not row["in_source"]:
        status, basis = "Removed from source", None
    result.update(
        notified=notified,
        status=status,
        status_id=row["status_id"],
        status_basis=basis,
        notification_id=row["notification_id"],
        since=day_after(row["end_date"])
        if basis == STATUS_BASIS[True]
        else status_since(conn, row),
        end_date=row["end_date"],
        last_approval_date=row["last_approval_date"],
        reason=reason,
    )
    for name, kinds, value in (
        ("procedure", parse.PROCEDURE_KINDS, procedure),
        ("product", parse.PRODUCT_KINDS, product),
    ):
        if value:
            matches = _scope_match(conn, row["notification_id"], kinds, value)
            result[f"{name}_in_scope"] = bool(matches)
            result[f"{name}_matches"] = matches
    return result


def day_after(end_date: str) -> str:
    """Start (UTC) of the first day a notification is past its end date."""
    return f"{date.fromisoformat(end_date) + timedelta(days=1)}T00:00:00Z"


def status_since(conn: sqlite3.Connection, row: sqlite3.Row) -> str | None:
    """Earliest stored version in an unbroken run of the current status id.

    The check uses the end date instead when the end date decides the shown
    "Expired" label. Uses the update date of that version; this is a lower bound limited to the
    versions the database holds (all versions after a history backfill).
    """
    versions = conn.execute(
        "SELECT status_id, update_date FROM notification WHERE series_key = ? "
        "ORDER BY version DESC",
        (row["series_key"],),
    ).fetchall()
    since = None
    for v in versions:
        if v["status_id"] != row["status_id"]:
            break
        since = v["update_date"]
    return since


def change_dict(row: sqlite3.Row) -> dict[str, Any]:
    new_value = row["new_value"]
    if row["change_type"] in {"scope_added", "scope_removed"} and new_value:
        new_value = json.loads(new_value)
    return {
        "event_id": row["event_id"],
        "observed_at": row["observed_at"],
        "entity": row["entity"],
        "change_type": row["change_type"],
        "body_number": parse.body_number_str(row["body_number"]),
        "body_ref": row["body_ref"],
        "body_name": row["body_name"],
        "country": row["country"],
        "legislation_id": row["legislation_id"],
        "legislation_code": row["legislation_code"],
        "notification_id": row["notification_id"],
        "field": row["field"],
        "old_value": row["old_value"],
        "new_value": new_value,
        "source_update_date": row["source_update_date"],
    }


def changes(
    conn: sqlite3.Connection,
    *,
    since: str | None = None,
    after_id: int | None = None,
    legislation_ids: list[int] | None = None,
    body_number: int | None = None,
    country: str | None = None,
    change_types: list[str] | None = None,
    limit: int = 100,
    newest_first: bool = False,
) -> list[sqlite3.Row]:
    clauses = ["1 = 1"]
    params: list[Any] = []
    if since:
        clauses.append("e.observed_at >= ?")
        params.append(since)
    if after_id is not None:
        clauses.append("e.event_id > ?")
        params.append(after_id)
    if legislation_ids:
        clauses.append(f"e.legislation_id IN ({','.join('?' * len(legislation_ids))})")
        params.extend(legislation_ids)
    if body_number is not None:
        clauses.append("e.body_number = ?")
        params.append(body_number)
    if country:
        if country.isdigit():
            clauses.append("e.country_id = ?")
            params.append(int(country))
        else:
            clauses.append("lower(b.country) = lower(?)")
            params.append(country)
    if change_types:
        clauses.append(f"e.change_type IN ({','.join('?' * len(change_types))})")
        params.extend(change_types)
    order = "DESC" if newest_first else "ASC"
    return conn.execute(
        "SELECT e.*, b.name AS body_name, b.country AS country, l.code AS legislation_code "
        "FROM change_event e LEFT JOIN body b ON b.body_ref = e.body_ref "
        "LEFT JOIN legislation l ON l.legislation_id = e.legislation_id "
        f"WHERE {' AND '.join(clauses)} ORDER BY e.event_id {order} LIMIT ?",
        (*params, limit),
    ).fetchall()


def export_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Flat rows: one per latest notification, joined with body and legislation."""
    rows = conn.execute(
        "SELECT n.*, b.name AS body_name, b.body_type, b.country, b.website, "
        "l.code AS legislation_code, l.name AS legislation_name "
        "FROM notification n LEFT JOIN body b USING (body_ref) "
        "LEFT JOIN legislation l USING (legislation_id) "
        "WHERE n.is_latest = 1 AND n.in_source = 1 ORDER BY n.body_number, n.legislation_id"
    ).fetchall()
    return [
        {
            "notification_id": r["notification_id"],
            "body_number": parse.body_number_str(r["body_number"]),
            "body_type": r["body_type"],
            "body_name": r["body_name"],
            "country": r["country"],
            "website": r["website"],
            "legislation_id": r["legislation_id"],
            "legislation_code": r["legislation_code"],
            "legislation_name": r["legislation_name"],
            "status_id": r["status_id"],
            **status_fields(r["status_id"], r["end_date"]),
            "version": r["version"],
            "end_date": r["end_date"],
            "last_approval_date": r["last_approval_date"],
            "update_date": r["update_date"],
        }
        for r in rows
    ]
