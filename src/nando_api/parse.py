"""Pure functions that turn source documents into rows.

Every function here works on one decoded search result or one `jsonDoc`
and does no I/O, so the golden-file tests cover them fully.
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

BRUSSELS = ZoneInfo("Europe/Brussels")

# Status labels for `notificationStatusId`. The search source publishes ids only.
# Checked on 2026-11-04 in the NANDO UI (Single Market Compliance Space, body
# notifications page): its "Notification status" select lists exactly these
# id/label pairs. Id 6 (11 notifications, all past their end date) is not in
# that select, so the UI has no label for it; `status_label` returns "Unknown (6)".
# The UI's "Active" and "Expired" lists use the end date as well as the id.
# Checked 2026-11-06: status 1 and 3 with an empty or future end date are
# under "Active"; status 3 with a past end date is under "Expired"; status 1
# with a past end date is under neither; status 5 with a future end date is
# under "Active", not "Suspended". `queries.shown_status` derives the shown
# label for ids 1 and 3 from the end date; other ids keep these labels.
STATUS_LABELS: dict[int, str] = {
    1: "Active",
    2: "Expired/Withdrawn",
    3: "Expired",
    4: "Withdrawn",
    5: "Suspended",
}
# Status ids that count as notified while the end date has not passed.
NOTIFIED_STATUS_IDS = (1, 3)

# Personal contact fields of a `jsonDoc`. `strip_personal` removes them before
# the document is hashed or stored, so they are never kept or served. The search
# metadata repeats them as orgEmail, orgPhone, and orgFax; that is never stored.
DROPPED_FIELDS = frozenset({"organizationEmail", "organizationPhone", "organizationFax"})
# Some bodies put an email address (and could put a phone number) in the
# website field. `strip_personal` keeps only the web addresses in that field.
WEBSITE_FIELD = "organizationWebsite"
_CONTACT_RE = re.compile(r"@|\+?\d[\d\s().-]{6,}\d")
_WEB_ADDRESS_RE = re.compile(r"^(https?://)?[\w-]+(\.[\w-]+)+([/?#]\S*)?$")

# Source scope keys (`notification<X>Ids` / `notification<X>Keyword`) to API names.
SCOPE_KINDS: dict[str, str] = {
    "Annex": "annex",
    "Avcp": "avcp_system",
    "BodyFunction": "body_function",
    "Decision": "decision",
    "HN": "harmonised_standard_reference",
    "HSpec": "horizontal_specification",
    "HStandard": "harmonised_standard",
    "Htc": "horizontal_technical_competence",
    "IntendedUse": "intended_use",
    "ProdFamily": "product_family",
    "Procedure": "procedure",
    "Product": "product",
    "Ts": "technical_specification",
}
_SCOPE_RE = re.compile(r"^notification(?P<kind>[A-Za-z]+?)(?P<suffix>Ids|Keyword)$")
_NOT_SCOPE = {"Legislation"}

# Product-like scope kinds that `check?product=` searches.
PRODUCT_KINDS = ("product", "product_family", "horizontal_technical_competence")
PROCEDURE_KINDS = ("procedure", "annex", "avcp_system")

_LEGAL_NUMBER_RE = re.compile(
    r"(\d{2,4})\s*/\s*(\d{1,4})(?:\s*/\s*(EEC|EC|EU|EURATOM))?", re.IGNORECASE
)


def doc_json(result: dict[str, Any]) -> dict[str, Any]:
    """Decode the `jsonDoc` metadata field of a search result."""
    raw = result["metadata"]["jsonDoc"]
    text = raw[0] if isinstance(raw, list) else raw
    doc = json.loads(text)
    if not isinstance(doc, dict):
        msg = "jsonDoc is not an object"
        raise TypeError(msg)
    return doc


def meta_value(result: dict[str, Any], name: str) -> str | None:
    """Return the first value of a metadata field, or None."""
    value = result.get("metadata", {}).get(name)
    if isinstance(value, list):
        return str(value[0]) if value else None
    return None if value is None else str(value)


def strip_personal(doc: dict[str, Any]) -> dict[str, Any]:
    """Return the document without its personal contact fields.

    Drops `DROPPED_FIELDS` and removes email addresses and phone numbers from
    the website field (see `website_without_contacts`).
    """
    kept = {key: value for key, value in doc.items() if key not in DROPPED_FIELDS}
    if isinstance(kept.get(WEBSITE_FIELD), str):
        kept[WEBSITE_FIELD] = website_without_contacts(kept[WEBSITE_FIELD])
    return kept


def website_without_contacts(value: str) -> str | None:
    """Keep only the web addresses of a website value that holds contact data.

    A value without an email address or phone number comes back unchanged, so
    its content hash does not change. Otherwise the result is the web addresses
    in the value, joined with " / ", or None when there is no web address.
    """
    if not _CONTACT_RE.search(value):
        return value
    tokens = re.split(r"[\s,;]+", value)
    addresses = [t for t in tokens if "@" not in t and _WEB_ADDRESS_RE.match(t)]
    return " / ".join(addresses) or None


def content_hash(doc: dict[str, Any]) -> str:
    """SHA-256 over the canonical JSON of a document."""
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def es_date_to_utc(value: str | None) -> str | None:
    """Convert an index timestamp like 2026-11-04T14:50:00.580+0100 to UTC ISO."""
    if not value:
        return None
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def brussels_to_utc(value: str | None) -> str | None:
    """Fix `notificationUpdateDate`: it ends in Z but holds Brussels local time."""
    if not value:
        return None
    naive = datetime.fromisoformat(value.removesuffix("Z")).replace(tzinfo=None)
    local = naive.replace(tzinfo=BRUSSELS)
    return local.astimezone(UTC).isoformat().replace("+00:00", "Z")


def date_only(value: str | None) -> str | None:
    """Reduce a date-valued source timestamp (00:00:00Z) to YYYY-MM-DD."""
    return value[:10] if value else None


def clean(value: Any) -> Any:
    """Make a source value storable: strip strings, flatten lists.

    The source pads some codes and names with spaces, and a few documents hold
    a list where most hold a single value.
    """
    if isinstance(value, list):
        localized = [v for v in value if isinstance(v, dict) and "language" in v]
        if localized:
            english = [v for v in localized if str(v["language"]).upper() == "EN"]
            chosen = (english or localized)[0]
            return clean(chosen.get("name") or chosen.get("value"))
        items = [clean(item) for item in value if item not in (None, "")]
        if not items:
            return None
        if len(items) == 1 or not all(isinstance(item, str) for item in items):
            return items[0]
        return "; ".join(dict.fromkeys(items))
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return value.strip() if isinstance(value, str) else value


def scalars(row: dict[str, Any]) -> dict[str, Any]:
    """Apply `clean` to every value of a parsed row."""
    return {key: clean(value) for key, value in row.items()}


def body_number_str(number: int | None) -> str | None:
    """Format a body number the way the source displays it: four digits."""
    return None if number is None else f"{int(number):04d}"


def parse_body_number(value: str) -> int | None:
    """Accept 0123, 123, NB 0123, or nb0123 and return 123."""
    digits = re.sub(r"^[A-Za-z]+\s*", "", value.strip())
    return int(digits) if digits.isdigit() else None


def legal_number(text: str | None) -> str | None:
    """Extract the act number: 'Regulation (EU) No 305/2011' -> '305/2011'."""
    if not text:
        return None
    match = _LEGAL_NUMBER_RE.search(text)
    if not match:
        return None
    first, second, suffix = match.groups()
    number = f"{first}/{second}"
    return f"{number}/{suffix.upper()}" if suffix else number


def status_label(status_id: int | None) -> str | None:
    if status_id is None:
        return None
    return STATUS_LABELS.get(status_id, f"Unknown ({status_id})")


def body_ref(doc: dict[str, Any]) -> str:
    """Stable body key: organisation reference plus body type, e.g. EPOS_43978NB."""
    if doc.get("organizationRefeCdAndType"):
        return str(doc["organizationRefeCdAndType"])
    body_type = doc.get("bodyTypeAbbrev") or doc.get("bodyTypeAbbr") or ""
    return f"{doc['organizationRefeCd']}{body_type}"


def series_key(doc: dict[str, Any]) -> str:
    """A notification's versions share body and legislation, not notificationId.

    Verified 2026-11-04: body EPOS_43805NB under legislation 34 has versions
    1 to 4 with four different notificationIds.
    """
    return f"{body_ref(doc)}:{doc['notificationLegislationId']}"


def parse_body(doc: dict[str, Any]) -> dict[str, Any]:
    return scalars(
        {
            "body_ref": body_ref(doc),
            "body_number": doc.get("bodyNumber"),
            "body_type": clean(doc.get("bodyTypeAbbrev") or doc.get("bodyTypeAbbr")),
            "body_type_id": doc.get("bodyTypeId"),
            "organisation_id": doc.get("organizationId"),
            "organisation_ref": doc.get("organizationRefeCd"),
            "organisation_version": doc.get("organizationVersion"),
            "name": clean(doc.get("organizationName")),
            "country_id": doc.get("organizationCountryId"),
            "country": clean(doc.get("organizationCountryName")),
            "street": clean(doc.get("organizationStreetAndNumber")),
            "postcode_city": clean(doc.get("organizationZipCodeAndCity")),
            "website": clean(doc.get("organizationWebsite")),
            "last_approval_date": date_only(doc.get("bodyLastApprovalDate")),
            "end_date": date_only(doc.get("bodyEndDate")),
            "is_latest": 1 if doc.get("organizationIfLatestVersion") == "Y" else 0,
        }
    )


def body_from_notification(doc: dict[str, Any]) -> dict[str, Any]:
    """The body fields that every notification document repeats."""
    return scalars(
        {
            "body_ref": body_ref(doc),
            "body_number": doc.get("bodyNumber"),
            "body_type": clean(doc.get("bodyTypeAbbr")),
            "body_type_id": doc.get("bodyTypeId"),
            "organisation_ref": doc.get("organizationRefeCd"),
            "organisation_version": doc.get("organizationVersion"),
            "name": clean(doc.get("organizationName")),
            "country_id": doc.get("organizationCountryId"),
            "country": clean(doc.get("organizationCountryName")),
            "street": clean(doc.get("organizationStreetAndNumber")),
            "postcode_city": clean(doc.get("organizationZipCodeAndCity")),
            "website": clean(doc.get("organizationWebsite")),
            "end_date": date_only(doc.get("bodyEndDate")),
        }
    )


def parse_notification(doc: dict[str, Any]) -> dict[str, Any]:
    template = doc.get("notificationTemplate") or {}
    return scalars(
        {
            "notification_id": doc["notificationId"],
            "series_key": series_key(doc),
            "body_ref": body_ref(doc),
            "body_number": doc.get("bodyNumber"),
            "legislation_id": doc["notificationLegislationId"],
            "version": doc.get("notificationVersion"),
            "is_latest": 1 if doc.get("notificationIfLatestVersion") == "Y" else 0,
            "status_id": doc.get("notificationStatusId"),
            "end_date": date_only(doc.get("notificationEndDate")),
            "last_approval_date": date_only(doc.get("notificationLastApprovalDate")),
            "update_date": brussels_to_utc(doc.get("notificationUpdateDate")),
            "accreditation": doc.get("notificationAccreditationFl"),
            "accreditation_expiry_date": date_only(doc.get("notificationAccreditationExpiryDate")),
            "template_profile": template.get("profile"),
        }
    )


def parse_scope(doc: dict[str, Any]) -> list[tuple[str, int | None, str | None]]:
    """Return scope rows (kind, value_id, label).

    The source lists ids and labels per kind in separate arrays that are not
    paired by position, so ids and labels become separate rows.
    """
    rows: list[tuple[str, int | None, str | None]] = []
    for key, value in doc.items():
        match = _SCOPE_RE.match(key)
        if not match or match["kind"] in _NOT_SCOPE or not isinstance(value, list):
            continue
        kind = SCOPE_KINDS.get(match["kind"], _snake(match["kind"]))
        if match["suffix"] == "Ids":
            rows.extend((kind, int(item), None) for item in value)
        else:
            rows.extend((kind, None, " ".join(str(item).split())) for item in value)
    return sorted(set(rows), key=lambda row: (row[0], row[1] or 0, row[2] or ""))


def parse_legislation(doc: dict[str, Any]) -> dict[str, Any]:
    code = clean(doc.get("legislationCode"))
    return scalars(
        {
            "legislation_id": doc["legislationId"],
            "code": code,
            "number": legal_number(code) or legal_number(doc.get("legislationName")),
            "name": clean(doc.get("legislationName")),
            "status": clean(doc.get("legislationStatus")),
            "profile": clean(doc.get("legislationProfile")),
            "end_date": date_only(doc.get("legislationEndDate")),
        }
    )


# Reference document types: (id field, label field).
REFERENCE_TYPES: dict[str, tuple[str, str]] = {
    "nando_procedure": ("procedureId", "procedureName"),
    "nando_prod_family": ("prodFamilyId", "prodFamilyName"),
    "nando_product": ("productId", "productName"),
    "nando_decision": ("decisionId", "decisionCode"),
    "nando_horizontal_spec": ("specificationId", "specificationDescription"),
}

# Scope kinds whose ids resolve against a reference type.
SCOPE_REFERENCE: dict[str, str] = {
    "procedure": "nando_procedure",
    "product_family": "nando_prod_family",
    "product": "nando_product",
    "horizontal_technical_competence": "nando_product",
    "decision": "nando_decision",
    "horizontal_specification": "nando_horizontal_spec",
}


def parse_reference(cs_type: str, doc: dict[str, Any]) -> dict[str, Any]:
    id_field, label_field = REFERENCE_TYPES[cs_type]
    label = clean(doc.get(label_field))
    if cs_type == "nando_decision" and doc.get("decisionName"):
        label = f"{label} - {' '.join(str(doc['decisionName']).split())}"
    return {
        "cs_type": cs_type,
        "ref_id": doc[id_field],
        "legislation_id": doc.get("legislationId") or 0,
        "language": (doc.get("language") or "EN").upper(),
        "label": " ".join(str(label).split()) if label else None,
    }


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
