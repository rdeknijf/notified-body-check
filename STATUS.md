# Status

Attention: maintain

Work items are tracked outside this repo (beads epic `idea-machine-ns8`).

## Goal

A free, unofficial public API over NANDO (every act in NANDO) with a changes feed, Atom alerts, daily exports, a landing page, and terms. Live at https://nbcheck.deknijf.com; public repo https://github.com/rdeknijf/notified-body-check.

## Verified state (2026-11-06)

- Live since 2026-11-06 12:03 UTC on host buckland (`~/services/nbcheck`, Docker Compose behind Traefik and Cloudflare; DNS CNAME `nbcheck` proxied). `api` and `worker` run; the stack is in the host's `update.sh` list and in its site probe. Deploy: `git ls-files | rsync -a --files-from=- ./ buckland:services/nbcheck/`, then `docker compose up -d --build` there. `.env` and `data/` are copied once, not from git.
- Live sync: the first snapshot ran 12:03 to 12:05 UTC (81 requests, ok). Incremental runs failed from 12:20 UTC on the 10,000-hit cap (13,363 body versions); fixed in `source.iter_documents`, redeployed, and run 13 at 14:38 UTC was ok (2 requests, 14 notifications). The worker has `restart: "no"`: after a host reboot, start it with `docker compose up -d worker`.
- Personal data: email, phone, and fax fields are dropped at ingest, and `parse.website_without_contacts` keeps only web addresses in the website field (6 bodies had an email address there). `scripts/strip_raw_contacts.py` ran on `data/nando.db`: 23 raw rows and 6 body websites changed, 0 left, hashes consistent. The 2026-11-04 export was regenerated. A curl over every `/v1/bodies` page, the Atom feed, and both export files finds no email address. Backups (gitignored): `data/nando-pre-website-strip.db` (integrity ok) and `data/exports-pre-website-strip/`.
- The User-Agent to the source is `NotifiedBodyCheck/1.0 (+https://nbcheck.deknijf.com/terms)`. A test proves that the HTTP API never imports the source client or the ingest code.
- The terms page says how to ask for a correction or removal (GitHub issue) and that records or the whole service go offline on a reasonable request from the Commission or a listed body.
- CI workflow `.github/workflows/ci.yml` runs prek, pytest and `uv audit` on GitHub.
- "Notified" is status id 1 or 3 with an empty or not-passed end date (UTC). One rule: `parse.NOTIFIED_STATUS_IDS`, `queries.notified_sql`, `queries.is_notified`, `queries.shown_status`. The local data has 2,633 notified notifications (51 under 2017/745).
- Every JSON answer has `stale` (true when the last successful sync is more than `STALE_AFTER_HOURS`, default 6, old). The local data is from 2026-11-04, so the local server answers `stale: true`.
- Gates pass: ruff check, ruff format, ty, pytest (322 tests, 96.8% coverage), prek, `uv audit --locked`.

## Verified state (2026-11-04)

- One complete live snapshot ran (81 requests, 0 errors, no 401, 403, or 429). With a first attempt that stopped on a parse bug and 17 exploratory calls, the total is 117 requests to the endpoint.
- Local database `data/nando.db` (gitignored): 65 acts, 3,803 body rows, 8,583 notifications, 433,042 scope rows, reference tables. The first snapshot is the quiet baseline, so there are 0 change events.
- `backfill-history` is tested only against recorded fixtures. It has not run live.

## Decisions

- Code licence MIT (Rutger, 2026-11-06).
- Free use: no keys, tiers, billing, or prices (Rutger, 2026-11-04).
- No webhooks. `GET /v1/changes` and `GET /v1/changes.atom` with filters instead (Rutger, 2026-11-04).
- Every act in NANDO in scope (Rutger, 2026-11-04).
- Name "Notified Body Check", host nbcheck.deknijf.com (Rutger, 2026-11-06), set in `config.SERVICE_NAME` and `config.SITE_URL`.
- No permission request to the Commission (Rutger, 2026-11-06). The service stays unofficial, says so in every answer and page, and takes records or the whole service offline on a reasonable request (terms page).
- Change detection compares our own SHA-256 of each stripped source document; the source `checksum` field is the hash of an empty string and is ignored.
- Status names are the labels of the NANDO UI status select (checked 2026-11-04: 1 Active, 2 Expired/Withdrawn, 3 Expired, 4 Withdrawn, 5 Suspended; id 6 has no label). Notified rule (Rutger, 2026-11-06): status id 1 or 3 and an end date that is empty or not passed. Differences from NANDO are stated on the landing page.
- `notificationUpdateDate` is Brussels local time despite its `Z` suffix; it is converted with `Europe/Brussels`.

## Blockers


## Resume path

1. Read `AGENTS.md` (rules and load discipline).
2. `uv sync && uv run pytest` to confirm the gates.
3. `uv run nando-api serve --port 8601` to see the site with the local data.
4. Deploy: see the first bullet of the 2026-11-06 state.
