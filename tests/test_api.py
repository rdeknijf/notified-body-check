"""HTTP API tests against a database built offline from the fake source."""

import json
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import TEST_KEY, FakeNando, set_doc_fields
from nando_api import config, db, exports, ingest, queries
from nando_api.api import ATOM_NS, create_app, is_stale
from nando_api.source import EuropaSearchClient

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator

EXPORT_DAY = "2026-11-04"
ENVELOPE_KEYS = {
    "as_of",
    "stale",
    "source",
    "license",
    "attribution",
    "changes_indicated",
    "notice",
}
DASHES = (chr(0x2014), chr(0x2013))  # em dash, en dash


def _snapshot(conn: sqlite3.Connection, fake: FakeNando) -> None:
    with EuropaSearchClient(
        TEST_KEY,
        user_agent="nando-api-test",
        request_interval=0,
        transport=httpx.MockTransport(fake.handler),
    ) as client:
        ingest.snapshot(conn, client)


@pytest.fixture(scope="module")
def paths(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Baseline snapshot, then a second snapshot with three changes, then exports.

    Changes: notification 1028568 (NB 0483, 2017/745, Germany) is suspended,
    notification 1028684 (NB 0656, 305/2011, Greece) gets a new end date, and
    body NB 1544 (Czech Republic, no notifications) gets a new website.
    """
    root = tmp_path_factory.mktemp("api")
    database = root / "nando.db"
    export_dir = root / "exports"
    fake = FakeNando()
    conn = db.connect(database)
    _snapshot(conn, fake)
    notes = fake.docs["nando_notification"]
    notes[2] = set_doc_fields(notes[2], notificationStatusId=5)
    notes[0] = set_doc_fields(notes[0], notificationEndDate="2031-01-01T00:00:00Z")
    fake.docs["nando_body"][0] = set_doc_fields(
        fake.docs["nando_body"][0], organizationWebsite="www.changed.example"
    )
    _snapshot(conn, fake)
    exports.write_exports(conn, export_dir, day=EXPORT_DAY)
    conn.close()
    return database, export_dir


@pytest.fixture(scope="module")
def client(paths: tuple[Path, Path]) -> Iterator[TestClient]:
    database, export_dir = paths
    with TestClient(create_app(database_path=database, export_dir=export_dir)) as test_client:
        yield test_client


def _data(response: httpx.Response, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


def _events(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    return _data(client.get("/v1/changes", params=params))


# -- envelope ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url", "status", "payload"),
    [
        ("GET", "/v1/status", 200, None),
        ("GET", "/v1/legislation", 200, None),
        ("GET", "/v1/legislation/2017/745/bodies", 200, None),
        ("GET", "/v1/bodies", 200, None),
        ("GET", "/v1/bodies/0297", 200, None),
        ("GET", "/v1/bodies/0297/notifications", 200, None),
        ("GET", "/v1/notifications/1028566", 200, None),
        ("GET", "/v1/check?body=297&legislation=2017/745", 200, None),
        (
            "POST",
            "/v1/check/batch",
            200,
            {"checks": [{"body": "297", "legislation": "2017/745"}]},
        ),
        ("GET", "/v1/changes", 200, None),
        ("GET", "/v1/exports", 200, None),
        ("GET", "/v1/nope", 404, None),
        ("GET", "/v1/bodies/9999", 404, None),
        ("GET", "/v1/bodies/not-a-number", 404, None),
        ("GET", "/v1/notifications/1", 404, None),
        ("GET", "/v1/legislation/9999/9999/bodies", 404, None),
        ("GET", "/v1/changes?change_type=exploded", 404, None),
        ("GET", "/v1/bodies?status=dormant", 404, None),
        ("GET", "/v1/exports/notes.txt", 404, None),
        ("GET", "/v1/bodies?limit=0", 422, None),
        ("GET", "/v1/check?body=297", 422, None),
        ("GET", "/v1/changes?order=sideways", 422, None),
        ("POST", "/v1/check/batch", 422, {"checks": "nope"}),
        ("POST", "/v1/status", 405, None),
    ],
)
def test_every_json_response_carries_the_envelope(client, method, url, status, payload):
    response = client.request(method, url, json=payload)
    assert response.status_code == status, response.text
    body = response.json()
    assert body.keys() >= ENVELOPE_KEYS
    assert body["source"] == config.SOURCE
    assert body["attribution"] == config.ATTRIBUTION
    assert body["license"] == {"name": config.LICENSE_NAME, "url": config.LICENSE_URL}
    assert body["changes_indicated"] == config.CHANGES_INDICATED
    assert body["notice"] == config.NOTICE
    assert body["as_of"] is not None
    assert body["stale"] is False
    if status >= 400:
        assert body["data"] is None
        assert body["error"]["code"] == {
            404: "not_found",
            405: "method_not_allowed",
        }.get(status, "invalid_request")


def test_error_without_database_still_carries_notice(tmp_path):
    # No `with`: the lifespan does not run, so the database file never exists.
    app = create_app(database_path=tmp_path / "missing.db", export_dir=tmp_path)
    response = TestClient(app).get("/v1/nope")
    assert response.status_code == 404
    body = response.json()
    assert {
        "as_of",
        "source",
        "attribution",
        "changes_indicated",
        "notice",
    } <= body.keys()
    assert body["as_of"] is None
    assert body["stale"] is True
    assert body["error"]["code"] == "not_found"
    page = TestClient(app).get("/").text
    assert '<div class="stale" id="stale" role="alert">' in page
    assert "no successful sync with NANDO yet" in page


def test_status_of_empty_database_is_unknown(tmp_path):
    with TestClient(create_app(database_path=tmp_path / "empty.db", export_dir=tmp_path)) as c:
        data = _data(c.get("/v1/status"))
    assert data["source_health"] == "unknown"
    assert data["last_runs"] == []
    assert data["counts"]["bodies"] == 0


# -- check ---------------------------------------------------------------------


def test_check_found(client):
    data = _data(client.get("/v1/check", params={"body": "NB 0297", "legislation": "2017/745"}))
    assert data["notified"] is True
    assert data["status"] == "Active"
    assert data["reason"] is None
    assert data["notification_id"] == 1028566
    assert data["body"]["body_number"] == "0297"
    assert data["legislation"]["legislation_id"] == 34
    assert data["since"] is not None
    assert data["procedure_in_scope"] is None


def test_check_suspended_is_not_notified(client):
    data = _data(client.get("/v1/check", params={"body": "483", "legislation": "34"}))
    assert data["notified"] is False
    assert data["status"] == "Suspended"
    assert data["reason"] == "not_active"


def _notified_views(client: TestClient) -> dict[str, Any]:
    """Every answer that says how many or which notifications are current."""
    acts = {a["legislation_id"]: a for a in _data(client.get("/v1/legislation"))}
    return {
        "check_0297": _data(client.get("/v1/check?body=297&legislation=34"))["notified"],
        "check_0483": _data(client.get("/v1/check?body=483&legislation=34"))["notified"],
        "count_mdr": acts[34]["active_notifications"],
        "count_all": sum(a["active_notifications"] for a in acts.values()),
        "status_count": _data(client.get("/v1/status"))["counts"]["notified"],
        "mdr_bodies": [b["body_number"] for b in _data(client.get("/v1/legislation/34/bodies"))],
        "bodies_active": [
            b["body_number"] for b in _data(client.get("/v1/bodies", params={"status": "active"}))
        ],
        "notifications_0297": len(
            _data(client.get("/v1/bodies/0297/notifications", params={"status": "active"}))
        ),
    }


@pytest.mark.parametrize(
    ("end_date", "expected"),
    [
        (
            None,
            {
                "check_0297": True,
                "check_0483": True,
                "count_mdr": 2,
                "count_all": 4,
                "status_count": 4,
                "mdr_bodies": ["0297", "0483"],
                "bodies_active": ["0297", "0483", "0656", "1783"],
                "notifications_0297": 1,
            },
        ),
        (
            "2020-01-20",
            {
                "check_0297": False,
                "check_0483": True,
                "count_mdr": 1,
                "count_all": 3,
                "status_count": 3,
                "mdr_bodies": ["0483"],
                "bodies_active": ["0483", "0656", "1783"],
                "notifications_0297": 0,
            },
        ),
    ],
)
def test_counts_filters_and_check_use_one_notified_rule(
    conn, make_client, tmp_path, end_date, expected
):
    ingest.snapshot(conn, make_client())
    conn.execute(
        "UPDATE notification SET end_date = ? WHERE notification_id = 1028566", (end_date,)
    )
    conn.commit()
    with TestClient(create_app(database_path=tmp_path / "test.db", export_dir=tmp_path)) as c:
        assert _notified_views(c) == expected


def test_active_counts_match_the_check_for_every_notification(client):
    legislation = _data(client.get("/v1/legislation"))
    acts = {a["legislation_id"]: a["active_notifications"] for a in legislation}
    notified: dict[int, int] = {}
    for body in _data(client.get("/v1/bodies", params={"limit": 500})):
        for item in _data(client.get(f"/v1/bodies/{body['body_number']}/notifications")):
            leg = item["legislation"]["legislation_id"]
            check = _data(client.get(f"/v1/check?body={body['body_number']}&legislation={leg}"))
            notified[leg] = notified.get(leg, 0) + check["notified"]
    assert notified
    assert {leg: n for leg, n in acts.items() if n or leg in notified} == notified


PASSED = "end date passed, so shown as Expired"
NOT_PASSED = "end date not passed, so shown as Active"


@pytest.mark.parametrize(
    ("status_id", "end_date", "notified", "status", "basis", "reason"),
    [
        (1, None, True, "Active", None, None),
        (1, "2026-11-06", True, "Active", None, None),
        (1, "2030-01-01", True, "Active", None, None),
        (1, "2026-11-05", False, "Expired", PASSED, "end_date_passed"),
        (1, "2020-01-20", False, "Expired", PASSED, "end_date_passed"),
        (3, None, True, "Active", NOT_PASSED, None),
        (3, "2026-11-06", True, "Active", NOT_PASSED, None),
        (3, "2028-06-19", True, "Active", NOT_PASSED, None),
        (3, "2026-11-05", False, "Expired", None, "end_date_passed"),
        (5, "2030-01-01", False, "Suspended", None, "not_active"),
        (4, None, False, "Withdrawn", None, "not_active"),
    ],
)
def test_check_follows_status_and_end_date(
    conn, make_client, status_id, end_date, notified, status, basis, reason
):
    ingest.snapshot(conn, make_client())
    conn.execute(
        "UPDATE notification SET status_id = ?, end_date = ? WHERE notification_id = 1028566",
        (status_id, end_date),
    )
    data = queries.check(conn, "297", "2017/745", today="2026-11-06")
    assert data["status_id"] == status_id
    assert data["end_date"] == end_date
    assert (data["notified"], data["status"], data["status_basis"], data["reason"]) == (
        notified,
        status,
        basis,
        reason,
    )


@pytest.mark.parametrize(
    ("status_id", "end_date", "since_is_end_date"),
    [(1, "2020-01-20", True), (3, "2020-01-20", False), (3, "2030-01-01", False)],
)
def test_since_is_the_end_date_when_the_end_date_makes_it_expired(
    conn, make_client, status_id, end_date, since_is_end_date
):
    """Like 0743 under 94/25/EC: status id 1, end date 2020-01-20, since 2020-01-21T00:00:00Z."""
    ingest.snapshot(conn, make_client())
    conn.execute(
        "UPDATE notification SET status_id = ?, end_date = ? WHERE notification_id = 1028566",
        (status_id, end_date),
    )
    data = queries.check(conn, "297", "2017/745", today="2026-11-06")
    assert (data["since"] == "2020-01-21T00:00:00Z") is since_is_end_date
    assert data["since"] is not None


def test_shown_label_is_active_exactly_when_notified(client):
    """Every notification: the check says notified iff the shown label is Active."""
    seen = 0
    for body in _data(client.get("/v1/bodies", params={"limit": 500})):
        number = body["body_number"]
        for item in _data(client.get(f"/v1/bodies/{number}/notifications")):
            leg = item["legislation"]["legislation_id"]
            check = _data(client.get(f"/v1/check?body={number}&legislation={leg}"))
            assert check["notified"] is (item["status"] == "Active")
            assert (check["status"], check["status_basis"]) == (
                item["status"],
                item["status_basis"],
            )
            seen += 1
    assert seen == 4


@pytest.mark.parametrize(
    ("body", "legislation", "reason"),
    [
        ("abc", "2017/745", "invalid_body_number"),
        ("9999", "2017/745", "body_not_found"),
        ("297", "9999/9999", "legislation_not_found"),
        ("1544", "2017/745", "no_notification"),
    ],
)
def test_check_not_found_reasons(client, body, legislation, reason):
    data = _data(client.get("/v1/check", params={"body": body, "legislation": legislation}))
    assert data["notified"] is False
    assert data["reason"] == reason


@pytest.mark.parametrize(
    ("param", "value", "in_scope", "match"),
    [
        (
            "procedure",
            "quality management system",
            True,
            "Conformity assessment based on a quality management system",
        ),
        ("procedure", "158707", True, "procedure:158707"),
        ("procedure", "Annex IX(I)", True, "Annex IX(I)"),
        ("procedure", "full quantum assurance", False, None),
        ("product", "active implantable", True, "1. Active implantable devices"),
        ("product", "MDS 1005", True, "MDS 1005 Devices in sterile condition"),
        ("product", "spaceships", False, None),
    ],
)
def test_check_scope_matching(client, param, value, in_scope, match):
    params = {"body": "297", "legislation": "2017/745", param: value}
    data = _data(client.get("/v1/check", params=params))
    assert data[f"{param}_in_scope"] is in_scope
    if match:
        assert match in data[f"{param}_matches"]
    else:
        assert data[f"{param}_matches"] == []


def test_check_batch(client):
    payload = {
        "checks": [
            {
                "body": "297",
                "legislation": "2017/745",
                "procedure": "quality management",
            },
            {"body": "9999", "legislation": "2017/745"},
        ]
    }
    response = client.post("/v1/check/batch", json=payload)
    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 2
    assert [d["notified"] for d in body["data"]] == [True, False]
    assert body["data"][0]["procedure_in_scope"] is True
    assert body["data"][1]["reason"] == "body_not_found"


def test_check_batch_over_limit_is_rejected(client):
    checks = [{"body": "297", "legislation": "2017/745"}] * 501
    response = client.post("/v1/check/batch", json={"checks": checks})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


# -- bodies --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "numbers"),
    [
        ({}, ["0297", "0483", "0656", "1544", "1783"]),
        ({"q": "dqs"}, ["0297"]),
        ({"q": "NB 0483"}, ["0483"]),
        ({"country": "germany"}, ["0297", "0483"]),
        ({"country": "300"}, ["0656"]),
        ({"legislation": "2017/745"}, ["0297", "0483"]),
        ({"legislation": "Regulation (EU) 305/2011"}, ["0656", "1783"]),
        ({"status": "active"}, ["0297", "0656", "1783"]),
        ({"status": "expired"}, []),
        ({"status": "suspended"}, ["0483"]),
        ({"status": "3"}, ["0656", "1783"]),
        ({"legislation": "2017/745", "status": "active"}, ["0297"]),
        ({"limit": 2, "offset": 1}, ["0483", "0656"]),
    ],
)
def test_bodies_filters(client, params, numbers):
    response = client.get("/v1/bodies", params=params)
    data = _data(response)
    assert [b["body_number"] for b in data] == numbers
    if "limit" in params:
        assert response.json()["total"] == 5
        assert response.json()["offset"] == 1


def test_body_detail(client):
    data = _data(client.get("/v1/bodies/297"))
    assert data["body_number"] == "0297"
    assert data["name"] == "DQS Medizinprodukte GmbH"
    assert data["notifications_by_status"] == {"Active": 1}
    assert data["notifications"][0]["legislation_code"] == "Regulation (EU) 2017/745"
    assert not {"email", "phone", "fax"} & data.keys()


def test_derived_label_keeps_raw_status_id(client, paths):
    """0656 has status id 3 and a future end date: shown as Active, id kept, basis set."""
    expected = {"status_id": 3, "status": "Active", "status_basis": NOT_PASSED}
    summary = _data(client.get("/v1/bodies/656"))["notifications"][0]
    listed = _data(client.get("/v1/legislation/305/2011/bodies"))[0]
    rows = [json.loads(line) for line in next(paths[1].glob("*.jsonl")).read_text().splitlines()]
    exported = next(row for row in rows if row["body_number"] == "0656")
    for item in (summary, listed, exported):
        assert {k: item[k] for k in expected} == expected


def test_body_detail_changed_website(client):
    assert _data(client.get("/v1/bodies/1544"))["website"] == "www.changed.example"


@pytest.mark.parametrize(
    ("params", "count", "has_scope"),
    [
        ({}, 1, False),
        ({"include": "scope"}, 1, True),
        ({"status": "suspended"}, 0, False),
        ({"legislation": "2017/745"}, 1, False),
        ({"legislation": "305/2011"}, 0, False),
    ],
)
def test_body_notifications(client, params, count, has_scope):
    data = _data(client.get("/v1/bodies/0297/notifications", params=params))
    assert len(data) == count
    for item in data:
        assert item["body_number"] == "0297"
        assert item["legislation"]["code"] == "Regulation (EU) 2017/745"
        assert ("scope" in item) is has_scope


def test_body_notifications_leave_out_removed_ones(conn, make_client, fake, tmp_path):
    ingest.snapshot(conn, make_client())
    mdr_0297 = fake.docs["nando_notification"].pop(3)
    assert json.loads(mdr_0297["metadata"]["jsonDoc"][0])["bodyNumber"] == 297
    ingest.snapshot(conn, make_client())
    row = conn.execute("SELECT * FROM notification WHERE notification_id = 1028566").fetchone()
    assert (row["is_latest"], row["in_source"]) == (1, 0)
    with TestClient(create_app(database_path=tmp_path / "test.db", export_dir=tmp_path)) as c:
        assert _data(c.get("/v1/bodies/0297/notifications")) == []


@pytest.mark.parametrize("url", ["/v1/bodies/9999/notifications", "/v1/bodies/x/notifications"])
def test_body_notifications_unknown_body(client, url):
    assert client.get(url).status_code == 404


# -- notifications -------------------------------------------------------------


def test_notification_with_scope_and_history(client):
    data = _data(client.get("/v1/notifications/1028568", params={"include": "scope,history"}))
    assert data["status"] == "Suspended"
    assert data["body"]["body_number"] == "0483"
    assert data["legislation"]["number"] == "2017/745"
    procedure = data["scope"]["procedure"]
    assert procedure.keys() == {"ids", "labels"}
    assert all({"id", "label"} == entry.keys() for entry in procedure["ids"])
    assert data["history"][0]["notification_id"] == 1028568
    assert data["history"][0]["is_latest"] is True


@pytest.mark.parametrize(
    ("include", "keys", "absent"),
    [
        ("scope", {"scope"}, {"history"}),
        ("history", {"history"}, {"scope"}),
        ("none", set(), {"scope", "history"}),
    ],
)
def test_notification_include(client, include, keys, absent):
    data = _data(client.get("/v1/notifications/1028566", params={"include": include}))
    assert keys <= data.keys()
    assert not absent & data.keys()


# -- legislation ---------------------------------------------------------------


def test_legislation_list(client):
    response = client.get("/v1/legislation")
    data = _data(response)
    assert response.json()["total"] == len(data) == 65
    mdr = next(item for item in data if item["legislation_id"] == 34)
    assert mdr["number"] == "2017/745"
    assert mdr["active_notifications"] == 1


@pytest.mark.parametrize(
    ("code", "params", "numbers"),
    [
        ("2017/745", {}, ["0297"]),
        ("34", {"status": "suspended"}, ["0483"]),
        ("2017/745", {"status": ""}, ["0297", "0483"]),
        ("Regulation (EU) 305/2011", {"status": "Expired"}, []),
        ("Regulation (EU) 305/2011", {}, ["0656", "1783"]),
        ("Regulation (EU) 305/2011", {"status": "3"}, ["0656", "1783"]),
    ],
)
def test_legislation_bodies(client, code, params, numbers):
    response = client.get(f"/v1/legislation/{code}/bodies", params=params)
    data = _data(response)
    assert [b["body_number"] for b in data] == numbers
    assert response.json()["legislation"]["legislation_id"] in {33, 34}


# -- changes -------------------------------------------------------------------


def test_changes_cover_the_three_edits(client):
    events = _events(client)
    assert sorted(e["change_type"] for e in events) == [
        "body_details_changed",
        "end_date_changed",
        "suspended",
    ]
    ids = [e["event_id"] for e in events]
    assert ids == sorted(ids)


@pytest.mark.parametrize(
    ("params", "types"),
    [
        ({"legislation": "2017/745"}, ["suspended"]),
        ({"legislation": "33"}, ["end_date_changed"]),
        ({"body": "NB 0483"}, ["suspended"]),
        ({"body": "1544"}, ["body_details_changed"]),
        ({"country": "Greece"}, ["end_date_changed"]),
        ({"country": "276"}, ["suspended"]),
        ({"change_type": "suspended,withdrawn"}, ["suspended"]),
        ({"change_type": "body_details_changed"}, ["body_details_changed"]),
        ({"since": "2999-01-01T00:00:00Z"}, []),
    ],
)
def test_changes_filters(client, params, types):
    assert [e["change_type"] for e in _events(client, **params)] == types


def test_changes_order_desc_reverses(client):
    ascending = [e["event_id"] for e in _events(client, order="asc")]
    descending = [e["event_id"] for e in _events(client, order="desc")]
    assert descending == ascending[::-1]
    response = client.get("/v1/changes", params={"order": "desc", "limit": 1})
    assert response.json()["next_after"] is None
    assert [e["event_id"] for e in response.json()["data"]] == descending[:1]


def test_changes_paging(client):
    seen: list[int] = []
    after = None
    for _ in range(10):
        params: dict[str, Any] = {"limit": 1}
        if after is not None:
            params["after"] = after
        body = client.get("/v1/changes", params=params).json()
        seen.extend(e["event_id"] for e in body["data"])
        after = body["next_after"]
        if after is None:
            break
    assert seen == [e["event_id"] for e in _events(client)]
    assert len(seen) == 3


def test_change_values_are_decoded(client):
    event = _events(client, change_type="suspended")[0]
    assert event["old_value"] == "Active"
    assert event["new_value"] == "Suspended"
    assert event["body_name"] == "mdc medical device certification GmbH"
    assert event["legislation_code"] == "Regulation (EU) 2017/745"


def _atom(client: TestClient, **params: Any) -> ET.Element:
    response = client.get("/v1/changes.atom", params=params)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/atom+xml")
    return ET.fromstring(response.content)  # noqa: S314 - our own output


def _text(element: ET.Element, path: str) -> str:
    text = element.findtext(path, namespaces={"a": ATOM_NS})
    assert text is not None, path
    return text


def _attr(element: ET.Element, path: str, name: str) -> str:
    child = element.find(path, {"a": ATOM_NS})
    assert child is not None, path
    value = child.get(name)
    assert value is not None, name
    return value


def test_atom_feed_is_well_formed(client):
    feed = _atom(client)
    ns = {"a": ATOM_NS}
    assert feed.tag == f"{{{ATOM_NS}}}feed"
    assert config.SERVICE_NAME in _text(feed, "a:title")
    assert feed.findtext("a:updated", namespaces=ns)
    assert config.NOTICE in _text(feed, "a:subtitle")
    entries = feed.findall("a:entry", ns)
    assert len(entries) == 3
    terms = {_attr(e, "a:category", "term") for e in entries}
    assert terms == {"suspended", "end_date_changed", "body_details_changed"}
    links = {_attr(e, "a:link", "href") for e in entries}
    assert "http://testserver/v1/notifications/1028568" in links
    assert "http://testserver/v1/bodies/1544" in links
    titles = [_text(e, "a:title") for e in entries]
    assert (
        "NB 0483 (mdc medical device certification GmbH): "
        "suspended under Regulation (EU) 2017/745" in titles
    )
    ids = [_text(e, "a:id") for e in entries]
    assert all(i.startswith("tag:testserver,") for i in ids)


@pytest.mark.parametrize(
    ("params", "terms"),
    [
        ({"legislation": "2017/745"}, ["suspended"]),
        ({"body": "1544"}, ["body_details_changed"]),
        ({"country": "Greece"}, ["end_date_changed"]),
        ({"change_type": "restored"}, []),
    ],
)
def test_atom_feed_honors_filters(client, params, terms):
    feed = _atom(client, **params)
    ns = {"a": ATOM_NS}
    found = [_attr(e, "a:category", "term") for e in feed.findall("a:entry", ns)]
    assert found == terms
    assert feed.findtext("a:updated", namespaces=ns)


def test_atom_feed_rejects_unknown_filter(client):
    assert client.get("/v1/changes.atom", params={"change_type": "nope"}).status_code == 404


# -- exports -------------------------------------------------------------------


def test_exports_list(client):
    data = _data(client.get("/v1/exports"))
    assert data == [
        {
            "date": EXPORT_DAY,
            "jsonl": f"http://testserver/v1/exports/{EXPORT_DAY}.jsonl",
            "csv": f"http://testserver/v1/exports/{EXPORT_DAY}.csv",
        }
    ]


@pytest.mark.parametrize(
    ("suffix", "media"), [("jsonl", "application/x-ndjson"), ("csv", "text/csv")]
)
def test_export_download(client, suffix, media):
    response = client.get(f"/v1/exports/{EXPORT_DAY}.{suffix}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media)
    assert response.headers["x-data-source"] == config.SOURCE
    assert response.headers["x-notice"] == config.NOTICE
    assert config.LICENSE_NAME in response.headers["x-data-license"]
    assert f"nando-{EXPORT_DAY}.{suffix}" in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    if suffix == "jsonl":
        rows = [json.loads(line) for line in lines]
        assert len(rows) == 4
        assert {r["body_number"] for r in rows} == {"0297", "0483", "0656", "1783"}
    else:
        assert lines[0].startswith("notification_id,body_number")
        assert len(lines) == 5


@pytest.mark.parametrize(
    "name",
    ["2026-11-05.csv", "2026-11-04.txt", "latest.csv", "2026-11-04", "..%2F.env"],
)
def test_bad_export_name_is_404(client, name):
    assert client.get(f"/v1/exports/{name}").status_code == 404


def test_exports_list_empty_without_dir(tmp_path):
    with TestClient(create_app(database_path=tmp_path / "x.db", export_dir=tmp_path / "none")) as c:
        assert _data(c.get("/v1/exports")) == []


# -- status --------------------------------------------------------------------


def test_status(client):
    data = _data(client.get("/v1/status"))
    assert data["counts"]["bodies"] == 5
    assert data["counts"]["notifications_latest"] == 4
    assert data["counts"]["legislation"] == 65
    assert data["counts"]["change_events"] == 3
    assert data["counts"]["notified"] == 3
    assert data["notifications_by_status"] == {"Active": 3, "Suspended": 1}
    assert data["source_health"] == "ok"
    assert data["stale_after_hours"] == 6
    assert [r["kind"] for r in data["last_runs"]] == ["snapshot", "snapshot"]


@pytest.mark.parametrize(
    ("as_of", "stale"),
    [
        (None, True),
        ("2026-11-06T06:00:00Z", False),
        ("2026-11-06T05:59:00Z", True),
        ("2026-11-06T11:00:00Z", False),
    ],
)
def test_is_stale(as_of, stale):
    now = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    assert is_stale(as_of, 6, now=now) is stale


def test_old_sync_marks_every_answer_stale(conn, make_client, tmp_path):
    ingest.snapshot(conn, make_client())
    old = (datetime.now(UTC) - timedelta(hours=7)).isoformat(timespec="seconds")
    conn.execute("UPDATE sync_run SET finished_at = ?", (old.replace("+00:00", "Z"),))
    conn.commit()
    with TestClient(create_app(database_path=tmp_path / "test.db", export_dir=tmp_path)) as c:
        status = c.get("/v1/status").json()
        assert status["stale"] is True
        assert status["data"]["source_health"] == "stale"
        assert c.get("/v1/check", params={"body": "297", "legislation": "34"}).json()["stale"]
        assert c.get("/v1/nope").json()["stale"] is True
        page = c.get("/").text
        assert '<div class="stale" id="stale" role="alert">' in page
        assert "more than 6 hours old" in page
        assert "Stale data: The last successful sync" in _text(_atom(c), "a:subtitle")


def test_stale_limit_comes_from_the_environment(conn, make_client, tmp_path, monkeypatch):
    ingest.snapshot(conn, make_client())
    old = (datetime.now(UTC) - timedelta(hours=7)).isoformat(timespec="seconds")
    conn.execute("UPDATE sync_run SET finished_at = ?", (old.replace("+00:00", "Z"),))
    conn.commit()
    monkeypatch.setenv("STALE_AFTER_HOURS", "8")
    with TestClient(create_app(database_path=tmp_path / "test.db", export_dir=tmp_path)) as c:
        status = c.get("/v1/status").json()
        assert status["stale"] is False
        assert status["data"]["stale_after_hours"] == 8
        assert "more than 8 hours old" in c.get("/").text


def test_fresh_landing_page_hides_the_stale_banner(client):
    page = client.get("/").text
    assert '<div class="stale" id="stale" role="alert" hidden>' in page
    assert "more than 6 hours old" in page


# -- pages and static files ----------------------------------------------------


@pytest.mark.parametrize("url", ["/", "/terms"])
def test_pages(client, url):
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert config.SERVICE_NAME in response.text
    assert "{{" not in response.text
    for dash in DASHES:
        assert dash not in response.text


@pytest.mark.parametrize(("name", "media"), [("site.js", "javascript"), ("site.css", "text/css")])
def test_static_assets(client, name, media):
    response = client.get(f"/static/{name}")
    assert response.status_code == 200
    assert media in response.headers["content-type"]
    assert response.text


def test_static_text_files_have_no_dashes():
    static = Path(str(files("nando_api").joinpath("static")))
    checked = [p for p in static.rglob("*") if p.suffix in {".html", ".css", ".js", ".svg"}]
    assert checked
    offenders = [
        f"{p.name}: {dash!r}" for p in checked for dash in DASHES if dash in p.read_text("utf-8")
    ]
    assert offenders == []


# -- personal data -------------------------------------------------------------

CONTACT_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+|\+31 20 123 4567")


@pytest.fixture(scope="module")
def contact_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """Every source document has an email address as its website.

    A second snapshot gives body NB 1544 a website with a web address, a phone
    number, and an email address, so the change feed carries a website event.
    """
    root = tmp_path_factory.mktemp("contacts")
    fake = FakeNando()
    for cs_type in ("nando_body", "nando_notification"):
        fake.docs[cs_type] = [
            set_doc_fields(r, organizationWebsite="info@body.example") for r in fake.docs[cs_type]
        ]
    conn = db.connect(root / "nando.db")
    _snapshot(conn, fake)
    fake.docs["nando_body"][0] = set_doc_fields(
        fake.docs["nando_body"][0],
        organizationWebsite="www.body.example / +31 20 123 4567 / sales@body.example",
    )
    _snapshot(conn, fake)
    exports.write_exports(conn, root / "exports", day=EXPORT_DAY)
    conn.close()
    app = create_app(database_path=root / "nando.db", export_dir=root / "exports")
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "url",
    [
        "/v1/bodies?limit=500",
        "/v1/bodies/1544",
        "/v1/bodies/0483",
        "/v1/bodies/0483/notifications",
        "/v1/check?body=0483&legislation=2017/745",
        "/v1/legislation/2017/745/bodies",
        "/v1/changes",
        "/v1/changes.atom",
        f"/v1/exports/{EXPORT_DAY}.jsonl",
        f"/v1/exports/{EXPORT_DAY}.csv",
    ],
)
def test_no_contact_data_in_any_output(contact_client, url):
    response = contact_client.get(url)
    assert response.status_code == 200
    assert CONTACT_RE.search(response.text) is None


def test_website_keeps_only_the_web_address(contact_client):
    body = _data(contact_client.get("/v1/bodies/1544"))
    assert body["website"] == "www.body.example"
    events = _data(contact_client.get("/v1/changes"))
    assert {"field": "website", "old_value": None, "new_value": "www.body.example"}.items() <= next(
        e for e in events if e.get("field") == "website"
    ).items()
