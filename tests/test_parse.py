"""Pure parsing helpers."""

import json
from typing import Any

import pytest

from nando_api import parse


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Winter: CET is UTC+1.
        ("2026-01-15T10:00:00Z", "2026-01-15T09:00:00Z"),
        # Summer: CEST is UTC+2.
        ("2026-07-15T10:00:00Z", "2026-07-15T08:00:00Z"),
        # Without the misleading Z, and across midnight.
        ("2026-07-01T01:30:00", "2026-06-30T23:30:00Z"),
        (None, None),
        ("", None),
    ],
)
def test_brussels_to_utc(value, expected):
    assert parse.brussels_to_utc(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-04T14:50:00.580+0100", "2026-11-04T13:50:00.580Z"),
        ("2026-10-21T16:20:03.352+0200", "2026-10-21T14:20:03.352Z"),
        ("2026-10-21T16:20:03.352+0000", "2026-10-21T16:20:03.352Z"),
        (None, None),
        ("", None),
    ],
)
def test_es_date_to_utc(value, expected):
    assert parse.es_date_to_utc(value) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Regulation (EU) 2017/745 ", "2017/745"),
        ("Regulation (EU) No 305/2011", "305/2011"),
        ("305/2011", "305/2011"),
        ("2014/68/EU", "2014/68/EU"),
        ("Directive 2006/42/ec on machinery", "2006/42/EC"),
        ("93/42/EEC", "93/42/EEC"),
        ("2014 / 53 / EU", "2014/53/EU"),
        ("junk", None),
        ("", None),
        (None, None),
    ],
)
def test_legal_number(text, expected):
    assert parse.legal_number(text) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            [{"language": "fr", "name": "Nom"}, {"language": "en", "name": " Name "}],
            "Name",
        ),
        (
            [{"language": "DE", "name": "Name DE"}, {"language": "FR", "name": "Nom"}],
            "Name DE",
        ),
        ([{"language": "EN", "value": "Value"}], "Value"),
        (["a", "b", "a"], "a; b"),
        (["  only  "], "only"),
        ([None, ""], None),
        ([], None),
        ([1, 2], 1),
        ({"b": 1, "a": "x"}, json.dumps({"a": "x", "b": 1})),
        ("  padded  ", "padded"),
        (5, 5),
        (None, None),
    ],
)
def test_clean(value, expected):
    assert parse.clean(value) == expected


def test_scalars_cleans_every_value():
    assert parse.scalars({"a": " x ", "b": ["y"], "c": 3}) == {
        "a": "x",
        "b": "y",
        "c": 3,
    }


@pytest.mark.parametrize(
    ("status_id", "expected"),
    [
        (1, "Active"),
        (2, "Expired/Withdrawn"),
        (3, "Expired"),
        (4, "Withdrawn"),
        (5, "Suspended"),
        (6, "Unknown (6)"),
        (None, None),
    ],
)
def test_status_label(status_id, expected):
    assert parse.status_label(status_id) == expected


@pytest.mark.parametrize(
    ("number", "expected"), [(297, "0297"), (1, "0001"), (12345, "12345"), (None, None)]
)
def test_body_number_str(number, expected):
    assert parse.body_number_str(number) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0123", 123),
        ("123", 123),
        ("NB 0123", 123),
        ("nb0123", 123),
        ("  NB   0297 ", 297),
        ("abc", None),
        ("NB", None),
        ("12a", None),
        ("", None),
    ],
)
def test_parse_body_number(value, expected):
    assert parse.parse_body_number(value) == expected


def test_body_number_round_trip():
    assert parse.parse_body_number(parse.body_number_str(42) or "") == 42


def test_content_hash_is_stable_and_order_independent():
    first = parse.content_hash({"a": 1, "b": ["x", "é"]})
    second = parse.content_hash({"b": ["x", "é"], "a": 1})
    assert first == second
    assert len(first) == 64
    assert int(first, 16) >= 0
    assert parse.content_hash({"a": 2, "b": ["x", "é"]}) != first


@pytest.mark.parametrize(
    ("doc", "expected"),
    [
        (
            {"organizationRefeCdAndType": "EPOS_1NB", "notificationLegislationId": 34},
            "EPOS_1NB:34",
        ),
        (
            {
                "organizationRefeCd": "EPOS_1",
                "bodyTypeAbbr": "NB",
                "notificationLegislationId": 34,
            },
            "EPOS_1NB:34",
        ),
        (
            {
                "organizationRefeCd": "EPOS_2",
                "bodyTypeAbbrev": "TAB",
                "notificationLegislationId": 1,
            },
            "EPOS_2TAB:1",
        ),
        ({"organizationRefeCd": "EPOS_3", "notificationLegislationId": 7}, "EPOS_3:7"),
    ],
)
def test_series_key(doc, expected):
    assert parse.series_key(doc) == expected


def test_parse_scope_shape():
    doc: dict[str, Any] = {
        "notificationProcedureIds": ["5", 3],
        "notificationProcedureKeyword": ["  Module   B  "],
        "notificationHTCKeyword": ["Not a known kind"],
        "notificationFooBarIds": [7],
        "notificationLegislationIds": [34],
        "notificationAnnexKeyword": "not a list",
        "notificationStatusId": 1,
        "organizationName": "ignored",
    }
    rows = parse.parse_scope(doc)
    assert rows == [
        ("foo_bar", 7, None),
        ("h_t_c", None, "Not a known kind"),
        ("procedure", None, "Module B"),
        ("procedure", 3, None),
        ("procedure", 5, None),
    ]
    for kind, value_id, label in rows:
        assert isinstance(kind, str)
        assert value_id is None or isinstance(value_id, int)
        assert label is None or isinstance(label, str)
        assert (value_id is None) != (label is None)


def test_parse_scope_deduplicates():
    doc = {
        "notificationProductIds": [1, "1"],
        "notificationProductKeyword": ["x", " x "],
    }
    assert parse.parse_scope(doc) == [("product", None, "x"), ("product", 1, None)]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(['{"a": 1}'], {"a": 1}), ('{"a": 2}', {"a": 2})],
)
def test_doc_json(raw, expected):
    assert parse.doc_json({"metadata": {"jsonDoc": raw}}) == expected


def test_doc_json_rejects_non_object():
    with pytest.raises(TypeError, match="not an object"):
        parse.doc_json({"metadata": {"jsonDoc": ["[1, 2]"]}})


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"x": ["a", "b"]}, "a"),
        ({"x": []}, None),
        ({"x": 5}, "5"),
        ({"x": None}, None),
        ({}, None),
    ],
)
def test_meta_value(metadata, expected):
    assert parse.meta_value({"metadata": metadata}, "x") == expected


def test_meta_value_without_metadata():
    assert parse.meta_value({}, "x") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("2031-01-01T00:00:00Z", "2031-01-01"), (None, None), ("", None)],
)
def test_date_only(value, expected):
    assert parse.date_only(value) == expected


def test_parse_legislation_number_from_name():
    row = parse.parse_legislation(
        {
            "legislationId": 9,
            "legislationCode": "MACH",
            "legislationName": " Directive 2006/42/EC on machinery ",
            "legislationStatus": "In force",
            "legislationEndDate": "2027-01-20T00:00:00Z",
        }
    )
    assert row["number"] == "2006/42/EC"
    assert row["name"] == "Directive 2006/42/EC on machinery"
    assert row["end_date"] == "2027-01-20"


@pytest.mark.parametrize(
    ("cs_type", "doc", "label", "legislation_id", "language"),
    [
        (
            "nando_procedure",
            {
                "procedureId": 1,
                "procedureName": " A  b ",
                "language": "en",
                "legislationId": 34,
            },
            "A b",
            34,
            "EN",
        ),
        (
            "nando_decision",
            {"decisionId": 2, "decisionCode": "D1", "decisionName": "Some   name"},
            "D1 - Some name",
            0,
            "EN",
        ),
        ("nando_product", {"productId": 3}, None, 0, "EN"),
    ],
)
def test_parse_reference(cs_type, doc, label, legislation_id, language):
    row = parse.parse_reference(cs_type, doc)
    assert row["cs_type"] == cs_type
    assert row["label"] == label
    assert row["legislation_id"] == legislation_id
    assert row["language"] == language


def test_parse_body_and_notification_drop_contact_fields():
    doc = {
        "organizationRefeCd": "EPOS_9",
        "bodyTypeAbbrev": "NB",
        "bodyNumber": 9,
        "organizationName": [" Body "],
        "organizationEmail": "x@example.org",
        "organizationIfLatestVersion": "Y",
        "notificationId": 1,
        "notificationLegislationId": 34,
        "notificationUpdateDate": "2026-01-15T10:00:00Z",
        "notificationIfLatestVersion": "N",
        "notificationTemplate": {"profile": "P"},
    }
    body = parse.parse_body(doc)
    assert body["body_ref"] == "EPOS_9NB"
    assert body["name"] == "Body"
    assert body["is_latest"] == 1
    assert "email" not in body
    note = parse.parse_notification(doc)
    assert note["series_key"] == "EPOS_9NB:34"
    assert note["update_date"] == "2026-01-15T09:00:00Z"
    assert note["is_latest"] == 0
    assert note["template_profile"] == "P"
    assert parse.body_from_notification(doc)["body_number"] == 9


@pytest.mark.parametrize(
    ("website", "expected"),
    [
        # Values seen in NANDO on 2026-11-06 (organisation websites, not personal).
        ("info@metcab.org", None),
        ("www.ingein@com", None),
        ("www.intertek-turkey.com / industry@intertek.com", "www.intertek-turkey.com"),
        # A phone or fax number goes the same way as an email address.
        ("+39 0574 123456", None),
        ("www.body.example tel. +31 20 123 4567", "www.body.example"),
        (
            "https://a.example/x, mail: x@example.invalid; www.b.example",
            "https://a.example/x / www.b.example",
        ),
        # No contact data: the value stays exactly as it is.
        ("www.kiwa.com/ee", "www.kiwa.com/ee"),
        (
            "www.dqsglobal.com/de-de/zertifizieren/verordnung-mdr-eu-2017-745",
            "www.dqsglobal.com/de-de/zertifizieren/verordnung-mdr-eu-2017-745",
        ),
        ("  www.padded.example ", "  www.padded.example "),
        ("", ""),
    ],
)
def test_website_without_contacts(website, expected):
    assert parse.website_without_contacts(website) == expected


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"organizationWebsite": "genoa-head-office@lr.org"}, {"organizationWebsite": None}),
        ({"organizationWebsite": "www.lr.org"}, {"organizationWebsite": "www.lr.org"}),
        ({"organizationEmail": "x@example.invalid", "organizationPhone": "+00 0"}, {}),
        # An "@" in a name is not contact data.
        (
            {"organizationName": "Holzforschung München (HFM@TUM)"},
            {"organizationName": "Holzforschung München (HFM@TUM)"},
        ),
        (
            {"organizationName": "ESTIA CONSULTING @ ENGINEERING SA"},
            {"organizationName": "ESTIA CONSULTING @ ENGINEERING SA"},
        ),
    ],
)
def test_strip_personal(fields, expected):
    assert parse.strip_personal({"bodyNumber": 1, **fields}) == {"bodyNumber": 1, **expected}
