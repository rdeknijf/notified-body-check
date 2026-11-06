# Notified Body Check

A free, unofficial JSON API and Atom change feeds over NANDO, the EU register of notified bodies, for every act in NANDO. The data comes from the Europa Search endpoint behind the Single Market Compliance Space. The service mirrors it into SQLite, derives change events between syncs, and serves it with FastAPI. There are no keys, tiers, prices, or webhooks.

Current state, decisions, and blockers live in `STATUS.md`. User-facing documentation is `README.md`.

## Commands

```zsh
uv sync
uv run prek install
uv run nando-api serve --port 8601      # API and landing page on http://127.0.0.1:8601
uv run nando-api status                 # record counts and sync runs
uv run nando-api export                 # write today's JSONL and CSV export
uv run nando-api fetch-key              # read the public search key from the SMCS app into .env
uv run nando-api snapshot               # LIVE: full pass, about 80 requests
uv run nando-api incremental            # LIVE: documents newer than the watermark
uv run nando-api backfill-history       # LIVE: every notification version, by act
uv run ruff check --fix .
uv run ruff format .
uv run ty check .
uv run pytest
uv run prek run --all-files
uv audit --locked
```

The commands marked LIVE call the Commission endpoint. Read the load discipline below before you run one.

## Layout

- `src/nando_api/source.py`: Europa Search client (throttle, request budget, blocking detection, page recording), key discovery.
- `src/nando_api/parse.py`: source documents to rows. Status names, Brussels time fix, legal numbers, English label selection.
- `src/nando_api/ingest.py`: snapshot, incremental, backfill, reconcile, change events.
- `src/nando_api/queries.py`, `api.py`: read side and HTTP routes. Every JSON answer goes through `envelope()`.
- `src/nando_api/static/`: landing page and terms. The design system is in `DESIGN.md`; product context in `PRODUCT.md`.
- `tests/fixtures/`: golden search responses, scrubbed of contact data and of the key (`scripts/scrub_fixture.py`).

## Rules

1. No deploy, no DNS, no `docker compose up`, and no started worker without Rutger's explicit go.
2. Load discipline for the live endpoint. Space requests at least 1 second apart (the client default is 1.5 s). Keep the User-Agent `NotifiedBodyCheck/1.0 (+https://nbcheck.deknijf.com/terms)` (`Settings.user_agent`): it names the service and links the terms page, with no email address. Never run continuous or scheduled polling before launch. Test `backfill-history` against fixtures only, until Rutger approves a live run.
3. On HTTP 401, 403, or 429, or any other sign of blocking, stop every live call and report. The client raises `SourceBlockedError` and the worker stops for good.
4. Never commit the key. It lives only in `.env` (gitignored, mode 600). Never print it; the httpx logger stays at WARNING because request URLs contain it.
5. Never call the service official. No Commission logos. Every JSON answer carries `as_of`, the source, the CC BY 4.0 attribution with the changes note, and the unofficial notice. Corrections and removals go through GitHub issues (https://github.com/rdeknijf/notified-body-check/issues). On a reasonable request from the Commission or a listed body, take the records concerned or the whole service offline (terms page).
6. Drop personal contact fields (email, phone, fax; `parse.DROPPED_FIELDS`) at ingest, before the raw document is hashed or stored. Keep the website field, but only its web addresses: `parse.website_without_contacts` removes email addresses and phone numbers that some bodies put there. The source has no contact-person field. `scripts/strip_raw_contacts.py` applies the current rule to a stored database.
7. Status names map ids to the labels of the NANDO UI status select (checked 2026-11-04; see `STATUS_LABELS` in `parse.py`). Id 6 has no UI label. "Notified" is status id 1 or 3 with an empty or not-passed end date (UTC), decided by Rutger on 2026-11-06; the rule lives only in `parse.NOTIFIED_STATUS_IDS`, `queries.notified_sql`, and `queries.shown_status`. Ids 1 and 3 show as "Active" or "Expired" by end date, like the NANDO UI lists; responses keep the raw `status_id` and add `status_basis` when the label differs from the NANDO label.
8. The service name is the single constant `SERVICE_NAME` in `src/nando_api/config.py`. The pages use the `{{SERVICE_NAME}}` placeholder.
9. Public copy: no em dashes or en dashes, no prices. Run `/humanizer` on changed copy.
10. Run the prek hooks and fix failures. Never use `--no-verify`. Tests never touch the network (pytest-socket).
11. Use the latest stable dependency versions without upper-bound pins. Modern typing, pathlib, absolute imports, no `__init__.py` in tests, parameterized tests.
12. Binary files (fonts, images) go through Git LFS (`.gitattributes`).

Keep `AGENTS.md` canonical. Keep `CLAUDE.md` as a relative symlink to it.
