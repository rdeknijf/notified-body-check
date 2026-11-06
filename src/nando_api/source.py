"""Client for the Europa Search endpoint behind the NANDO app.

Load discipline: one client instance counts every request, waits at least
`request_interval` seconds between requests, and stops at `max_requests`.
Any 401, 403, or 429 raises `SourceBlockedError`; callers must stop, not retry.
"""

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any

import httpx

from nando_api.config import APP_URL, SEARCH_ENDPOINT

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

log = logging.getLogger(__name__)

PAGE_SIZE = 200
# The index returns no results past 10,000 hits per query (page 51 at 200 rows).
RESULT_CAP = 10_000

_CHUNK_RE = re.compile(r"""(?:href|src)="([^"]*chunk-[A-Za-z0-9]+\.js)\"""")
_KEY_RE = re.compile(r'defaultCSApiKeys:\{nando:"([^"]+)"')


class SourceError(RuntimeError):
    """The source answered in a way we cannot use."""


class SourceBlockedError(SourceError):
    """The source refused us (401, 403, 429). Stop all live calls."""


class RequestBudgetError(SourceError):
    """The run reached its request budget."""


class EuropaSearchClient:
    """Paged, rate-limited access to the Europa Search REST endpoint."""

    def __init__(
        self,
        api_key: str,
        *,
        user_agent: str,
        request_interval: float = 1.5,
        max_requests: int = 200,
        record_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key:
            msg = "NANDO_ES_API_KEY is empty. Run `nando-api fetch-key` first."
            raise SourceError(msg)
        self._api_key = api_key
        self._interval = request_interval
        self._max_requests = max_requests
        self._record_dir = record_dir
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self.requests = 0
        self._http = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=httpx.Timeout(90.0),
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> EuropaSearchClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def remaining(self) -> int:
        return self._max_requests - self.requests

    def _throttle(self) -> None:
        if self.requests >= self._max_requests:
            msg = f"request budget of {self._max_requests} reached"
            raise RequestBudgetError(msg)
        if self._last is not None:
            wait = self._interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()
        self.requests += 1

    def search(
        self,
        query: dict[str, Any],
        *,
        page: int = 1,
        page_size: int = PAGE_SIZE,
        sort: list[dict[str, str]] | None = None,
        label: str = "query",
    ) -> dict[str, Any]:
        """Run one search request and return the decoded JSON body."""
        self._throttle()
        files: dict[str, tuple[str | None, bytes, str]] = {
            "query": (None, json.dumps(query).encode(), "application/json"),
        }
        if sort:
            files["sort"] = (None, json.dumps(sort).encode(), "application/json")
        params = {
            "apiKey": self._api_key,
            "text": "*",
            "pageSize": str(page_size),
            "pageNumber": str(page),
        }
        response = self._http.post(SEARCH_ENDPOINT, params=params, files=files)
        log.info("POST search %s page %d -> HTTP %d", label, page, response.status_code)
        if response.status_code in {401, 403, 429}:
            msg = f"source refused the request: HTTP {response.status_code} ({label})"
            raise SourceBlockedError(msg)
        if response.status_code != httpx.codes.OK:
            msg = f"source error: HTTP {response.status_code} ({label})"
            raise SourceError(msg)
        body = response.json()
        if not isinstance(body, dict) or "results" not in body:
            msg = f"unexpected response shape ({label})"
            raise SourceError(msg)
        if self._record_dir is not None:
            self._record_dir.mkdir(parents=True, exist_ok=True)
            path = self._record_dir / f"{label}.json"
            path.write_text(json.dumps(body), encoding="utf-8")
        return body

    def count(self, query: dict[str, Any], *, label: str = "count") -> int:
        """Return the total number of hits for a query (one small request)."""
        body = self.search(query, page_size=1, label=f"{label}-count")
        return int(body.get("totalResults", 0))

    def iter_documents(
        self,
        query: dict[str, Any],
        *,
        sort: list[dict[str, str]],
        label: str,
        stop: Callable[[dict[str, Any]], bool] | None = None,
        max_pages: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield every result of a query, page by page.

        Raises `SourceError` when the query has more hits than the index can
        page through; the caller must partition such a query.
        `stop` ends the iteration at the first result for which it is true.
        """
        page = 1
        while True:
            body = self.search(query, page=page, sort=sort, label=f"{label}-p{page}")
            total = int(body.get("totalResults", 0))
            if page == 1 and total > RESULT_CAP:
                msg = f"{label}: {total} hits exceed the {RESULT_CAP} cap; partition it"
                raise SourceError(msg)
            results = body.get("results") or []
            for result in results:
                if stop is not None and stop(result):
                    return
                yield result
            if len(results) < PAGE_SIZE or page * PAGE_SIZE >= total:
                return
            if max_pages is not None and page >= max_pages:
                return
            page += 1


def term_query(**terms: str | int) -> dict[str, Any]:
    """Build a bool/must query of exact term matches."""
    return {"bool": {"must": [{"term": {k: str(v)}} for k, v in terms.items()]}}


def fetch_key(
    *,
    user_agent: str,
    request_interval: float = 1.5,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, int]:
    """Read the public front-end key from the NANDO app bundle.

    Returns (key, number of requests made). The caller writes the key to
    `.env`; never print or log it.
    """
    requests = 0
    with httpx.Client(
        headers={"User-Agent": user_agent},
        timeout=httpx.Timeout(60.0),
        transport=transport,
        follow_redirects=True,
    ) as http:
        index = http.get(APP_URL)
        requests += 1
        if index.status_code in {401, 403, 429}:
            msg = f"app page refused: HTTP {index.status_code}"
            raise SourceBlockedError(msg)
        index.raise_for_status()
        chunks = list(dict.fromkeys(_CHUNK_RE.findall(index.text)))
        base = str(index.url).rsplit("/", 1)[0] + "/"
        for chunk in chunks:
            sleep(request_interval)
            response = http.get(httpx.URL(base).join(chunk.lstrip("/").split("/")[-1]))
            requests += 1
            if response.status_code in {401, 403, 429}:
                msg = f"bundle refused: HTTP {response.status_code}"
                raise SourceBlockedError(msg)
            match = _KEY_RE.search(response.text)
            if match:
                return match.group(1), requests
    msg = "defaultCSApiKeys.nando not found in the app bundle"
    raise SourceError(msg)


def write_env_value(env_path: Path, name: str, value: str) -> None:
    """Set NAME=value in a dotenv file, keeping the other lines."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    lines = [line for line in lines if not line.startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_path.chmod(0o600)
