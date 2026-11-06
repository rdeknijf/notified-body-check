"""Apply the current `parse.strip_personal` to stored data, with correct hashes.

Usage: back up first, then run it while no sync runs:

    sqlite3 data/nando.db ".backup data/nando-pre-strip.db"
    uv run python scripts/strip_raw_contacts.py data/nando.db

For each raw_version row that `strip_personal` changes (contact fields, or an
email address or phone number in the website field): store the stripped
document, recompute sha256 the way ingest does, and move body/notification/
legislation sha256 from the old to the new hash, so the next sync sees no
change and adds no duplicate raw rows. Then clean `body.website` the same way.
One transaction.
"""

import json
import sqlite3
import sys

from nando_api import parse

conn = sqlite3.connect(sys.argv[1])
changed = 0
with conn:
    rows = conn.execute("SELECT rowid, sha256, json_doc FROM raw_version").fetchall()
    for rowid, old_sha, text in rows:
        doc = json.loads(text)
        stripped = parse.strip_personal(doc)
        if stripped == doc:
            continue
        changed += 1
        new_sha = parse.content_hash(stripped)
        conn.execute(
            "UPDATE OR REPLACE raw_version SET json_doc = ?, sha256 = ? WHERE rowid = ?",
            (json.dumps(stripped, ensure_ascii=False, sort_keys=True), new_sha, rowid),
        )
        for table in ("body", "notification", "legislation"):
            conn.execute(f"UPDATE {table} SET sha256 = ? WHERE sha256 = ?", (new_sha, old_sha))
    websites = conn.execute("SELECT body_ref, website FROM body WHERE website IS NOT NULL")
    cleaned = [
        (parse.website_without_contacts(website), ref)
        for ref, website in websites.fetchall()
        if parse.website_without_contacts(website) != website
    ]
    conn.executemany("UPDATE body SET website = ? WHERE body_ref = ?", cleaned)
left = sum(
    parse.strip_personal(json.loads(text)) != json.loads(text)
    for (text,) in conn.execute("SELECT json_doc FROM raw_version")
)
print(f"raw rows changed: {changed}; body websites cleaned: {len(cleaned)}; raw rows left: {left}")
