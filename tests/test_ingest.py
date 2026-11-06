import json

import pytest

from conftest import FakeNando, set_doc_fields
from nando_api import ingest, parse
from nando_api.source import SourceBlockedError, SourceError


def _events(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM change_event ORDER BY event_id")]


def test_first_snapshot_is_a_quiet_baseline(conn, make_client):
    result = ingest.snapshot(conn, make_client())
    assert result["baseline"] is True
    assert result["counts"]["nando_notification"] == {"total": 4, "stored": 4}
    assert result["counts"]["nando_legislation"]["stored"] == 65
    assert result["complete"]["nando_notification"] is True
    assert _events(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM notification").fetchone()[0] == 4
    # Reference labels: only English documents are kept.
    langs = {r[0] for r in conn.execute("SELECT DISTINCT language FROM reference")}
    assert langs <= {"EN"}


def test_snapshot_stores_raw_with_hash_and_no_contact_fields(conn, make_client):
    ingest.snapshot(conn, make_client())
    row = conn.execute(
        "SELECT * FROM raw_version WHERE cs_type = 'nando_notification' LIMIT 1"
    ).fetchone()
    assert len(row["sha256"]) == 64
    assert parse.content_hash(json.loads(row["json_doc"])) == row["sha256"]
    columns = {r[1] for r in conn.execute("PRAGMA table_info(body)")}
    assert not columns & {"email", "phone", "fax"}


def test_raw_documents_are_stored_without_personal_contact_fields(conn, make_client):
    ingest.snapshot(conn, make_client())
    docs = [json.loads(r[0]) for r in conn.execute("SELECT json_doc FROM raw_version")]
    assert docs
    assert not any(parse.DROPPED_FIELDS & doc.keys() for doc in docs)
    assert all("organizationWebsite" in d for d in docs if "organizationName" in d)


def test_contact_only_change_is_no_change(conn, make_client, fake: FakeNando):
    ingest.snapshot(conn, make_client())
    raw_rows = conn.execute("SELECT COUNT(*) FROM raw_version").fetchone()[0]
    notes = fake.docs["nando_notification"]
    notes[2] = set_doc_fields(notes[2], organizationEmail="new@example.invalid")
    fake.docs["nando_body"][0] = set_doc_fields(
        fake.docs["nando_body"][0], organizationPhone="+00 1", organizationFax="+00 2"
    )
    result = ingest.snapshot(conn, make_client())
    assert result["events"] == 0
    assert conn.execute("SELECT COUNT(*) FROM raw_version").fetchone()[0] == raw_rows
    # A real change still counts.
    notes[2] = set_doc_fields(notes[2], notificationStatusId=5)
    ingest.snapshot(conn, make_client())
    assert [e["change_type"] for e in _events(conn)] == ["suspended"]
    assert conn.execute("SELECT COUNT(*) FROM raw_version").fetchone()[0] == raw_rows + 1


def test_unchanged_second_snapshot_records_no_events(conn, make_client):
    ingest.snapshot(conn, make_client())
    result = ingest.snapshot(conn, make_client())
    assert result["baseline"] is False
    assert result["events"] == 0


@pytest.mark.parametrize(
    ("new_status", "expected"),
    [(5, "suspended"), (4, "withdrawn"), (2, "withdrawn"), (3, "status_changed")],
)
def test_status_change_creates_typed_event(
    conn, make_client, fake: FakeNando, new_status, expected
):
    ingest.snapshot(conn, make_client())
    target = fake.docs["nando_notification"][2]  # an active MDR notification
    fake.docs["nando_notification"][2] = set_doc_fields(target, notificationStatusId=new_status)
    ingest.snapshot(conn, make_client())
    events = _events(conn)
    assert [e["change_type"] for e in events] == [expected]
    assert events[0]["old_value"] == "Active"
    assert events[0]["legislation_id"] == 34


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ((1, None), (3, None), "status_changed"),
        ((3, "2028-06-19"), (1, None), "status_changed"),
        ((5, "2030-01-01"), (1, None), "restored"),
        ((3, "2020-01-20"), (1, None), "restored"),
        ((3, "2020-01-20"), (3, "2030-01-01"), "restored"),
        ((1, None), (5, "2030-01-01"), "suspended"),
        ((3, "2028-06-19"), (4, None), "withdrawn"),
        ((1, None), (2, None), "withdrawn"),
        ((1, None), (3, "2020-01-20"), "status_changed"),
        ((3, "2020-01-20"), (5, "2030-01-01"), "status_changed"),
        ((5, "2030-01-01"), (4, None), "status_changed"),
    ],
)
def test_status_change_type_follows_the_notified_rule(old, new, expected):
    assert ingest.status_change_type(*old, *new, today="2026-11-06") == expected


def test_status_1_to_3_while_notified_is_a_plain_status_change(conn, make_client, fake: FakeNando):
    ingest.snapshot(conn, make_client())
    # Notification 1028684 (NB 0656) has status 3 and a future end date.
    index = next(
        i
        for i, d in enumerate(fake.docs["nando_notification"])
        if json.loads(d["metadata"]["jsonDoc"][0])["notificationId"] == 1028684
    )
    target = fake.docs["nando_notification"][index]
    fake.docs["nando_notification"][index] = set_doc_fields(target, notificationStatusId=1)
    ingest.snapshot(conn, make_client())
    events = _events(conn)
    assert [(e["change_type"], e["old_value"], e["new_value"]) for e in events] == [
        ("status_changed", "Expired", "Active")
    ]


def test_restore_and_scope_and_end_date_changes(conn, make_client, fake: FakeNando):
    first = set_doc_fields(fake.docs["nando_notification"][2], notificationStatusId=5)
    fake.docs["nando_notification"][2] = first
    ingest.snapshot(conn, make_client())
    doc = json.loads(first["metadata"]["jsonDoc"][0])
    fake.docs["nando_notification"][2] = set_doc_fields(
        first,
        notificationStatusId=1,
        notificationEndDate="2031-01-01T00:00:00Z",
        notificationProcedureKeyword=[
            *doc["notificationProcedureKeyword"][:2],
            "New procedure",
        ],
    )
    ingest.snapshot(conn, make_client())
    types = sorted(e["change_type"] for e in _events(conn))
    assert types == ["end_date_changed", "restored", "scope_added", "scope_removed"]
    added = next(e for e in _events(conn) if e["change_type"] == "scope_added")
    assert json.loads(added["new_value"]) == ["New procedure"]


def test_new_version_replaces_latest_and_removed_series_is_reported(
    conn, make_client, fake: FakeNando
):
    ingest.snapshot(conn, make_client())
    old = fake.docs["nando_notification"][2]
    newer = set_doc_fields(old, notificationId=99999999, notificationVersion=99)
    newer["reference"] = "new-version-ref"
    fake.docs["nando_notification"][2] = newer
    removed = fake.docs["nando_notification"].pop(0)
    ingest.snapshot(conn, make_client())
    old_id = json.loads(old["metadata"]["jsonDoc"][0])["notificationId"]
    assert (
        conn.execute(
            "SELECT is_latest FROM notification WHERE notification_id = ?", (old_id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT is_latest FROM notification WHERE notification_id = 99999999"
        ).fetchone()[0]
        == 1
    )
    events = _events(conn)
    assert [e["change_type"] for e in events] == ["removed"]
    assert (
        events[0]["notification_id"]
        == json.loads(removed["metadata"]["jsonDoc"][0])["notificationId"]
    )
    # It comes back: restored.
    fake.docs["nando_notification"].insert(0, removed)
    ingest.snapshot(conn, make_client())
    assert _events(conn)[-1]["change_type"] == "restored"


def test_incomplete_snapshot_does_not_reconcile(conn, make_client, fake: FakeNando):
    ingest.snapshot(conn, make_client())
    fake.docs["nando_notification"].pop(0)
    fake.total_override["nando_notification"] = 4  # source claims more than it returns
    result = ingest.snapshot(conn, make_client())
    assert result["complete"]["nando_notification"] is False
    assert "removed_notifications" not in result
    assert _events(conn) == []


def test_body_detail_change(conn, make_client, fake: FakeNando):
    ingest.snapshot(conn, make_client())
    fake.docs["nando_body"][0] = set_doc_fields(
        fake.docs["nando_body"][0], organizationWebsite="www.changed.example"
    )
    ingest.snapshot(conn, make_client())
    events = _events(conn)
    assert [(e["change_type"], e["field"]) for e in events] == [("body_details_changed", "website")]


def test_body_removed(conn, make_client, fake: FakeNando):
    ingest.snapshot(conn, make_client())
    fake.docs["nando_body"].pop(0)
    ingest.snapshot(conn, make_client())
    assert [(e["entity"], e["change_type"]) for e in _events(conn)] == [("body", "removed")]


def test_incremental_needs_baseline(conn, make_client):
    with pytest.raises(SourceError, match="baseline"):
        ingest.incremental(conn, make_client())


def test_incremental_stops_at_watermark(conn, make_client, fake: FakeNando):
    ingest.snapshot(conn, make_client())
    client = make_client()
    result = ingest.incremental(conn, client)
    # Every document is at or below the watermark: one request per type.
    assert client.requests == 2
    assert result["counts"] == {"nando_body": 0, "nando_notification": 0}
    newer = set_doc_fields(fake.docs["nando_notification"][2], notificationStatusId=5)
    newer["metadata"]["es_SortDate"] = ["2099-01-01T00:00:00.000+0100"]
    fake.docs["nando_notification"].insert(0, newer)
    result = ingest.incremental(conn, make_client())
    assert result["counts"]["nando_notification"] == 1
    assert [e["change_type"] for e in _events(conn)] == ["suspended"]


def test_snapshot_skips_reference_type_over_budget(conn, make_client, fake: FakeNando):
    fake.total_override["nando_product"] = 14_986
    result = ingest.snapshot(conn, make_client())
    assert any(s.startswith("nando_product") for s in result["skipped"])
    assert "nando_product" not in result["reference"]


def test_blocked_source_stops_and_records_failure(conn, make_client, fake: FakeNando):
    fake.status_code = 403
    client = make_client()
    with pytest.raises(SourceBlockedError):
        ingest.snapshot(conn, client)
    assert client.requests == 1
    run = conn.execute("SELECT ok, detail FROM sync_run").fetchone()
    assert run["ok"] == 0
    assert json.loads(run["detail"])["blocked"] is True


def test_snapshot_fails_cleanly_on_cap(conn, make_client, fake: FakeNando):
    fake.total_override["nando_body"] = 20_000
    with pytest.raises(SourceError, match="cap"):
        ingest.snapshot(conn, make_client())


def test_backfill_history_partitions_by_legislation(conn, make_client, fake: FakeNando):
    ingest.snapshot(conn, make_client())
    client = make_client()
    result = ingest.backfill_history(conn, client)
    # One request per legislation (all partitions fit in one page here).
    assert client.requests == 65
    asked = {c["terms"].get("notificationLegislationId") for c in fake.calls[-65:]}
    assert len(asked) == 65
    assert result["partitions"]["34"]["stored"] == 5
    series = conn.execute(
        "SELECT version, is_latest FROM notification "
        "WHERE series_key = 'EPOS_43805NB:34' ORDER BY version"
    ).fetchall()
    assert [(r[0], r[1]) for r in series] == [(1, 0), (2, 0), (3, 0), (4, 1)]
    assert _events(conn) == []


def test_backfill_splits_partition_above_cap(conn, make_client, fake: FakeNando, monkeypatch):
    ingest.snapshot(conn, make_client())
    original = fake.route

    def route(terms):
        name, docs = original(terms)
        if terms.get("notificationLegislationId") == "34" and "notificationStatusId" not in terms:
            return "big", docs
        if "notificationStatusId" in terms:
            docs = [
                d
                for d in docs
                if d["metadata"]["notificationStatusId"][0] == terms["notificationStatusId"]
            ]
        return name, docs

    monkeypatch.setattr(fake, "route", route)
    fake.total_override["big"] = 12_000
    result = ingest.backfill_history(conn, make_client())
    assert "34/1" in result["partitions"]
    assert result["partitions"]["34/1"]["stored"] == 5


def test_backfill_needs_legislation(conn, make_client):
    with pytest.raises(SourceError, match="legislation"):
        ingest.backfill_history(conn, make_client())
