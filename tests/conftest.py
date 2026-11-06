"""Shared fixtures: a fake Europa Search endpoint built from golden files.

No test touches the network: the fake plugs into httpx as a MockTransport.
"""

import copy
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from nando_api import db
from nando_api.source import EuropaSearchClient

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

FIXTURES = Path(__file__).parent / "fixtures"
TEST_KEY = "test-key-not-real"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def results_of(name: str) -> list[dict[str, Any]]:
    return load_fixture(name)["results"]


def multipart_json(request: httpx.Request, part: str) -> Any:
    """Pull one JSON form part out of a multipart request body."""
    body = request.content.decode()
    match = re.search(rf'name="{part}"[^\n]*\n(?:[^\n]*\n)*?\r?\n(.*?)\r?\n--', body, re.DOTALL)
    return json.loads(match.group(1)) if match else None


def terms_of(query: dict[str, Any]) -> dict[str, str]:
    return {k: v for clause in query["bool"]["must"] for k, v in clause["term"].items()}


def set_doc_fields(result: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """Return a copy of a search result with jsonDoc fields replaced."""
    result = copy.deepcopy(result)
    doc = json.loads(result["metadata"]["jsonDoc"][0])
    doc.update(fields)
    result["metadata"]["jsonDoc"] = [json.dumps(doc)]
    return result


class FakeNando:
    """In-memory stand-in for the search endpoint, keyed by document type."""

    def __init__(self) -> None:
        latest = results_of("search_notification_latest.json")
        mdr = results_of("search_notification_mdr.json")
        self.docs: dict[str, list[dict[str, Any]]] = {
            "nando_legislation": results_of("search_legislation.json"),
            "nando_body": results_of("search_body_latest.json"),
            "nando_notification": latest + mdr,
            "history": results_of("search_notification_series.json"),
        }
        for cs_type in (
            "nando_procedure",
            "nando_prod_family",
            "nando_product",
            "nando_decision",
            "nando_horizontal_spec",
        ):
            self.docs[cs_type] = results_of(f"search_{cs_type}.json")
        self.calls: list[dict[str, Any]] = []
        self.status_code = 200
        self.total_override: dict[str, int] = {}

    def route(self, terms: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
        cs_type = terms["csType"]
        if cs_type == "nando_notification" and "notificationLegislationId" in terms:
            docs = [
                r
                for r in self.docs["history"] + self.docs["nando_notification"]
                if r["metadata"]["notificationLegislationId"][0]
                == terms["notificationLegislationId"]
            ]
            unique = {r["reference"]: r for r in docs}
            return cs_type, list(unique.values())
        return cs_type, self.docs[cs_type]

    def handler(self, request: httpx.Request) -> httpx.Response:
        query = multipart_json(request, "query")
        terms = terms_of(query)
        page = int(request.url.params["pageNumber"])
        size = int(request.url.params["pageSize"])
        self.calls.append(
            {
                "terms": terms,
                "page": page,
                "headers": dict(request.headers),
                "url": request.url,
            }
        )
        if self.status_code != 200:
            return httpx.Response(self.status_code, json={"error": "nope"})
        name, docs = self.route(terms)
        total = self.total_override.get(name, len(docs))
        chunk = docs[(page - 1) * size : page * size]
        return httpx.Response(
            200, json={"totalResults": total, "pageNumber": page, "results": chunk}
        )


@pytest.fixture
def fake() -> FakeNando:
    return FakeNando()


@pytest.fixture
def make_client(fake: FakeNando) -> Iterator[Callable[..., EuropaSearchClient]]:
    clients: list[EuropaSearchClient] = []

    def factory(**kwargs: Any) -> EuropaSearchClient:
        kwargs.setdefault("user_agent", "nando-api-test")
        kwargs.setdefault("request_interval", 0)
        client = EuropaSearchClient(TEST_KEY, transport=httpx.MockTransport(fake.handler), **kwargs)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close()


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[Any]:
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()
