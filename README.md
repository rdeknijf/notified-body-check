# Notified Body Check

[![ci](https://github.com/rdeknijf/notified-body-check/actions/workflows/ci.yml/badge.svg)](https://github.com/rdeknijf/notified-body-check/actions/workflows/ci.yml)

A free, unofficial JSON API and Atom change feeds over NANDO, the EU register of notified bodies.

NANDO can tell you whether body 0123 is notified under the Medical Devices Regulation today, but only through a web app: it has no public API documentation, no bulk export, and no change feed. This service answers the same question in one HTTP call for every act in NANDO, and tells you when a body you rely on is suspended, withdrawn, or changes scope.

Site and API docs: <https://nbcheck.deknijf.com>

## Quick start

```bash
curl "https://nbcheck.deknijf.com/v1/check?body=0123&legislation=2017/745"
```

```json
{
  "as_of": "2026-11-04T19:52:33Z",
  "notice": "Unofficial service. Not affiliated with or endorsed by the European Commission. Data as published by the Commission at the time shown in as_of; no warranty.",
  "data": {
    "notified": true,
    "status": "Active",
    "status_id": 1,
    "body": {
      "body_number": "0123",
      "name": "TÜV SÜD Product Service GmbH",
      "country": "Germany"
    },
    "legislation": {
      "number": "2017/745",
      "name": "Regulation (EU) 2017/745 on medical devices"
    },
    "notification_id": 1020162,
    "since": "2025-09-03T09:27:17Z",
    "end_date": null
  }
}
```

The answer is trimmed here. Every JSON answer also carries `source`, `license`, `attribution`, and `changes_indicated`. Add `procedure=` or `product=` to check the scope as well.

To get an alert when something changes under an act, subscribe a feed reader to:

```text
https://nbcheck.deknijf.com/v1/changes.atom?legislation=2017/745
```

## Endpoints

The API needs no key or account, and there is no paid plan.

| Call | Answers |
| --- | --- |
| `GET /v1/check?body=0123&legislation=2017/745` | Notified or not, status, end date, procedure and product scope |
| `POST /v1/check/batch` | Up to 500 checks in one call |
| `GET /v1/bodies` | Search and filter bodies (`q`, `country`, `legislation`, `status`) |
| `GET /v1/bodies/{number}` | One body with its notifications |
| `GET /v1/bodies/{number}/notifications` | Current notifications of a body |
| `GET /v1/notifications/{id}?include=scope,history` | One notification with scope and version history |
| `GET /v1/legislation` | Every act in NANDO with its number of active notifications |
| `GET /v1/legislation/{code}/bodies` | Bodies under one act, notified ones by default |
| `GET /v1/changes` | Paged change feed (`legislation`, `body`, `country`, `change_type`, `since`, `after`, `order`) |
| `GET /v1/changes.atom` | The same changes as Atom |
| `GET /v1/exports` | Daily JSONL and CSV snapshot files |
| `GET /v1/status` | Last sync and record counts |

The OpenAPI schema is at `/openapi.json` and the interactive docs are at `/docs`.

## Data source and licence

The data comes from NANDO, published by the European Commission in the [Single Market Compliance Space](https://webgate.ec.europa.eu/single-market-compliance-space/notified-bodies). This service reuses it under the Commission's reuse policy ([Decision 2011/833/EU](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32011D0833)). The Commission adopted [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) as the open licence for that policy (Commission Decision C(2019) 1655 of 22 February 2019), and its [legal notice](https://commission.europa.eu/legal-notice_en) applies it to Commission content unless otherwise indicated.

This service changes the data: it restructures the records, removes personal contact data (email addresses, phone and fax numbers, even when the source puts one in the website field), converts times to UTC, adds status names, and derives change events.

This service is unofficial: it is not affiliated with or endorsed by the European Commission, and it comes with no warranty. NANDO is the official source, so check a result there before you rely on it for a legal or commercial decision.

To ask for a correction or for the removal of a record, [open an issue](https://github.com/rdeknijf/notified-body-check/issues). On a reasonable request from the European Commission or from a body listed in NANDO, the maintainer takes the records concerned, or the whole service, offline. The full terms are at [/terms](https://nbcheck.deknijf.com/terms).

## How it works

A worker reads the public search endpoint behind the NANDO pages of the Single Market Compliance Space: a short incremental pass every 15 minutes and a full snapshot each night. It sends at most one request every 1.5 seconds, names the service in its User-Agent, and stops for good on HTTP 401, 403, or 429. The worker strips the contact data from each source document, hashes it with SHA-256, and stores it in SQLite. When the hash of a notification or body changes between syncs, the service records typed change events (`suspended`, `withdrawn`, `restored`, `end_date_changed`, `scope_added`, and more). The API reads only that database, so a request to the API never causes a request to the Commission.

A body counts as notified under an act when its current notification has status id 1 or 3 and an end date that is empty or not passed (UTC). NANDO itself sorts a few edge cases differently, for example status 5 (suspended) with a future end date; the landing page lists those differences. Every answer keeps the raw `status_id` and adds `status_basis` when the shown label differs from the NANDO label.

## Local development

Python 3.14 with [uv](https://docs.astral.sh/uv/), FastAPI, SQLite, and click.

```bash
uv sync
uv run prek install                       # git hooks: ruff, ty, pytest
uv run nando-api serve --port 8601        # API and landing page on http://127.0.0.1:8601
uv run nando-api status                   # record counts and sync runs
uv run pytest                             # tests run offline against recorded fixtures
uv run prek run --all-files
uv audit --locked
```

`uv run nando-api fetch-key` reads the public search key from the NANDO web app into `.env`. After that, `uv run nando-api snapshot` fills a local database from the live endpoint (about 80 requests). The commands that call the live endpoint and the rules for them are in [AGENTS.md](AGENTS.md).

## Licence

The code has no licence yet. The data is CC BY 4.0, as described above.
