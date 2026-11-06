"""Source client: load discipline, paging, errors, and the key helpers."""

import stat
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from conftest import TEST_KEY
from nando_api import config
from nando_api.source import (
    PAGE_SIZE,
    RESULT_CAP,
    EuropaSearchClient,
    RequestBudgetError,
    SourceBlockedError,
    SourceError,
    fetch_key,
    term_query,
    write_env_value,
)

if TYPE_CHECKING:
    from pathlib import Path

Handler = Callable[[httpx.Request], httpx.Response]
QUERY = term_query(csType="nando_body")
SORT = [{"field": "es_SortDate", "order": "DESC"}]


class Clock:
    """A fake monotonic clock that only moves when told to."""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def ok(total: int = 0, results: list[Any] | None = None) -> Handler:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"totalResults": total, "results": results or []})

    return handler


def paged(total: int) -> tuple[Handler, list[int]]:
    """Serve `total` numbered results in pages; record the page numbers asked."""
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["pageNumber"])
        size = int(request.url.params["pageSize"])
        pages.append(page)
        ids = range((page - 1) * size, min(page * size, total))
        return httpx.Response(
            200, json={"totalResults": total, "results": [{"id": i} for i in ids]}
        )

    return handler, pages


def make(handler: Handler, clock: Clock | None = None, **kwargs: Any) -> EuropaSearchClient:
    clock = clock or Clock()
    kwargs.setdefault("request_interval", 1.5)
    return EuropaSearchClient(
        TEST_KEY,
        user_agent="nando-api-test/1.0",
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        clock=clock,
        **kwargs,
    )


def test_empty_key_is_refused():
    with pytest.raises(SourceError, match="fetch-key"):
        EuropaSearchClient("", user_agent="x")


def test_search_sends_user_agent_key_and_query():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"totalResults": 0, "results": []})

    with make(handler) as client:
        client.search(QUERY, page=3, page_size=7, sort=SORT)
    request = seen[0]
    assert request.headers["user-agent"] == "nando-api-test/1.0"
    assert str(request.url).startswith(config.SEARCH_ENDPOINT)
    assert request.url.params["apiKey"] == TEST_KEY
    assert request.url.params["pageNumber"] == "3"
    assert request.url.params["pageSize"] == "7"
    content = request.content.decode()
    assert 'name="query"' in content
    assert 'name="sort"' in content


def test_throttle_waits_with_injected_sleep():
    clock = Clock()
    with make(ok(), clock) as client:
        client.search(QUERY)
        client.search(QUERY)
        clock.now += 0.5
        client.search(QUERY)
        clock.now += 10
        client.search(QUERY)
    assert clock.sleeps == [1.5, 1.0]
    assert client.requests == 4


def test_request_budget():
    with make(ok(), max_requests=2) as client:
        client.search(QUERY)
        assert client.remaining == 1
        client.search(QUERY)
        assert client.remaining == 0
        with pytest.raises(RequestBudgetError, match="budget of 2"):
            client.search(QUERY)
    assert client.requests == 2


@pytest.mark.parametrize("status", [401, 403, 429])
def test_refusal_raises_blocked(status):
    with (
        make(lambda _: httpx.Response(status)) as client,
        pytest.raises(SourceBlockedError),
    ):
        client.search(QUERY, label="lbl")


@pytest.mark.parametrize("status", [500, 404, 302])
def test_other_http_errors_raise_source_error(status):
    with (
        make(lambda _: httpx.Response(status)) as client,
        pytest.raises(SourceError, match=f"HTTP {status}") as info,
    ):
        client.search(QUERY)
    assert not isinstance(info.value, SourceBlockedError)


@pytest.mark.parametrize("body", [[1, 2], {"totalResults": 3}])
def test_unexpected_shape(body):
    with (
        make(lambda _: httpx.Response(200, json=body)) as client,
        pytest.raises(SourceError, match="shape"),
    ):
        client.search(QUERY)


def test_count():
    with make(ok(total=1234)) as client:
        assert client.count(QUERY) == 1234


def test_record_dir_writes_label_json(tmp_path):
    record = tmp_path / "rec"
    with make(ok(total=1, results=[{"a": 1}]), record_dir=record) as client:
        client.search(QUERY, label="nando_body-p1")
    path = record / "nando_body-p1.json"
    assert path.is_file()
    assert '"totalResults": 1' in path.read_text(encoding="utf-8")


def test_iter_documents_refuses_over_cap():
    with (
        make(ok(total=10_001)) as client,
        pytest.raises(SourceError, match="10000 cap"),
    ):
        list(client.iter_documents(QUERY, sort=SORT, label="big"))


@pytest.mark.parametrize("max_pages", [None, RESULT_CAP // PAGE_SIZE + 1])
def test_iter_documents_refuses_over_cap_when_reads_can_pass_it(max_pages):
    with (
        make(ok(total=RESULT_CAP + 1)) as client,
        pytest.raises(SourceError, match="10000 cap"),
    ):
        list(client.iter_documents(QUERY, sort=SORT, label="big", max_pages=max_pages))


def test_iter_documents_reads_newest_pages_over_cap_when_bounded():
    handler, asked = paged(13_363)
    with make(handler, request_interval=0) as client:
        docs = list(client.iter_documents(QUERY, sort=SORT, label="p", max_pages=10))
    assert len(docs) == 10 * PAGE_SIZE
    assert asked == list(range(1, 11))


@pytest.mark.parametrize(
    ("total", "max_pages", "count", "pages"),
    [
        (0, None, 0, [1]),
        (150, None, 150, [1]),
        (PAGE_SIZE, None, PAGE_SIZE, [1]),
        (450, None, 450, [1, 2, 3]),
        (450, 2, 400, [1, 2]),
        (10_000, 1, PAGE_SIZE, [1]),
    ],
)
def test_iter_documents_pages(total, max_pages, count, pages):
    handler, asked = paged(total)
    with make(handler, request_interval=0) as client:
        docs = list(client.iter_documents(QUERY, sort=SORT, label="p", max_pages=max_pages))
    assert len(docs) == count
    assert [d["id"] for d in docs] == list(range(count))
    assert asked == pages


def test_iter_documents_stop_callback():
    handler, asked = paged(450)
    with make(handler, request_interval=0) as client:
        docs = list(
            client.iter_documents(QUERY, sort=SORT, label="p", stop=lambda r: r["id"] == 205)
        )
    assert [d["id"] for d in docs] == list(range(205))
    assert asked == [1, 2]


def test_term_query():
    assert term_query(csType="x", notificationLegislationId=34) == {
        "bool": {
            "must": [
                {"term": {"csType": "x"}},
                {"term": {"notificationLegislationId": "34"}},
            ]
        }
    }


# -- fetch_key -----------------------------------------------------------------

APP_PATH = "/single-market-compliance-space/notified-bodies"
INDEX = (
    '<html><head><link href="/single-market-compliance-space/chunk-ABC.js">'
    '<script src="chunk-XYZ9.js"></script>'
    '<link href="/single-market-compliance-space/chunk-ABC.js"></head></html>'
)


def app_site(chunks: dict[str, str], index_status: int = 200) -> tuple[Handler, list[str]]:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        paths.append(path)
        if path == APP_PATH:
            return httpx.Response(index_status, text=INDEX)
        name = path.rsplit("/", 1)[-1]
        if name in chunks:
            body = chunks[name]
            if body.isdigit():
                return httpx.Response(int(body))
            return httpx.Response(200, text=body)
        return httpx.Response(404)

    return handler, paths


def test_fetch_key_reads_key_from_bundle():
    handler, paths = app_site({"chunk-ABC.js": 'x={defaultCSApiKeys:{nando:"abc123",other:"zzz"}}'})
    sleeps: list[float] = []
    key, requests = fetch_key(
        user_agent="t",
        request_interval=0.25,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )
    assert key == "abc123"
    assert requests == 2
    assert sleeps == [0.25]
    assert paths == [APP_PATH, "/single-market-compliance-space/chunk-ABC.js"]


def test_fetch_key_searches_every_chunk_once():
    handler, paths = app_site(
        {
            "chunk-ABC.js": "nothing here",
            "chunk-XYZ9.js": 'defaultCSApiKeys:{nando:"k2"',
        }
    )
    key, requests = fetch_key(
        user_agent="t", transport=httpx.MockTransport(handler), sleep=lambda _: None
    )
    assert (key, requests) == ("k2", 3)
    assert paths.count("/single-market-compliance-space/chunk-ABC.js") == 1


def test_fetch_key_fails_when_key_is_absent():
    handler, _ = app_site({"chunk-ABC.js": "no key", "chunk-XYZ9.js": "still none"})
    with pytest.raises(SourceError, match="not found"):
        fetch_key(user_agent="t", transport=httpx.MockTransport(handler), sleep=lambda _: None)


@pytest.mark.parametrize(
    ("chunks", "index_status", "match"),
    [
        ({}, 403, "app page refused"),
        ({}, 429, "app page refused"),
        ({"chunk-ABC.js": "401"}, 200, "bundle refused"),
    ],
)
def test_fetch_key_blocked(chunks, index_status, match):
    handler, _ = app_site(chunks, index_status)
    with pytest.raises(SourceBlockedError, match=match):
        fetch_key(user_agent="t", transport=httpx.MockTransport(handler), sleep=lambda _: None)


def test_fetch_key_other_index_error():
    handler, _ = app_site({}, 500)
    with pytest.raises(httpx.HTTPStatusError):
        fetch_key(user_agent="t", transport=httpx.MockTransport(handler), sleep=lambda _: None)


# -- write_env_value -----------------------------------------------------------


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_write_env_value_creates_file(tmp_path):
    env = tmp_path / ".env"
    write_env_value(env, "NANDO_ES_API_KEY", "abc")
    assert env.read_text(encoding="utf-8") == "NANDO_ES_API_KEY=abc\n"
    assert _mode(env) == 0o600


def test_write_env_value_replaces_line_and_keeps_others(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\nNANDO_ES_API_KEY=old\nNANDO_ES_API_KEY_OTHER=x\nB=2\n", encoding="utf-8")
    env.chmod(0o644)
    write_env_value(env, "NANDO_ES_API_KEY", "new")
    assert env.read_text(encoding="utf-8").splitlines() == [
        "A=1",
        "NANDO_ES_API_KEY_OTHER=x",
        "B=2",
        "NANDO_ES_API_KEY=new",
    ]
    assert _mode(env) == 0o600
