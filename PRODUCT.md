# Product

<!-- impeccable:product-schema 1 -->

Status: written on 2026-11-04, updated on 2026-11-06. The users and the positioning are inferred; no user research took place.

## Platform

web

## Users

- Developers and technical leads at CE-marking, product-compliance, MedTech QMS, and construction-product software firms who want to check notified body status and scope inside their own tools (inferred).
- Compliance and regulatory staff who want an alert when a notified body they rely on is suspended, withdrawn, or changes scope, without signing up for anything (inferred from the Atom feed requirement).

## Product Purpose

A free, unofficial JSON API and Atom feed over NANDO, the EU register of notified bodies. It answers "is body 0123 notified under act X today, and for this procedure or product?", serves bodies, notifications with scope and version history, legislation, a change feed, and daily exports for every act in NANDO. Success: a developer understands what it does and makes a first working call within a minute of opening the page.

## Positioning

The official NANDO app has no documented API, no bulk export, and no change feed. This service adds stable lookups, a one-call `check`, a change feed derived from content hashes, and an Atom feed anyone can subscribe to with no account.

## Operating Context

- Readers arrive from a search or a link, read the page on a desktop browser at work, and copy a `curl` line into a terminal.
- Data refreshes every 15 minutes (incremental) and nightly (full snapshot); every response carries `as_of`.

## Capabilities and Constraints

- Free: no keys, no tiers, no prices anywhere.
- Unofficial: never call it official; no Commission logos; every page and response carries the attribution (CC BY 4.0, changes indicated) and an "unofficial, not affiliated with the European Commission" notice.
- Personal contact fields are not published.
- Product name "Notified Body Check" (Rutger, 2026-11-06), held in one constant (`SERVICE_NAME`).
- Corrections and removal on request through GitHub issues. On a reasonable request from the Commission or a listed body, the records concerned or the whole service go offline (terms page).

## Brand Commitments

The name is Notified Body Check. No logo. The palette and the type are in `DESIGN.md`.

## Evidence on Hand

- Real data in the local SQLite database after one snapshot; the landing page shows a live `check` against it.
- No customers, testimonials, usage numbers, or press. Do not invent any.

## Product Principles

1. Show the real call and the real answer before any explanation.
2. State provenance and limits plainly: source, `as_of`, unofficial, no warranty.
3. No signup anywhere: every feature works with a URL.
4. Plain facts over marketing claims.

## Accessibility & Inclusion

WCAG 2.2 AA as the default bar (inferred; no product-specific requirement given).
