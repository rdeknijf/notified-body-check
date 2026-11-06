"""Turn a recorded search response into a golden-file fixture.

Usage: uv run python scripts/scrub_fixture.py SOURCE.json DEST.json [--limit N]

Replaces contact fields with placeholders, keeps at most N results, and
refuses to write anything that still contains the API key from `.env`.
"""

import argparse
import json
import os
from pathlib import Path

CONTACT_META = {"orgEmail": "x@example.invalid", "orgPhone": "+00 0", "orgFax": "+00 0"}
CONTACT_DOC = {
    "organizationEmail": "x@example.invalid",
    "organizationPhone": "+00 0",
    "organizationFax": "+00 0",
}


def scrub(body: dict, limit: int | None) -> dict:
    results = body["results"][:limit] if limit else body["results"]
    for result in results:
        meta = result["metadata"]
        for key, value in CONTACT_META.items():
            if key in meta:
                meta[key] = [value]
        doc = json.loads(meta["jsonDoc"][0])
        for key, value in CONTACT_DOC.items():
            if key in doc:
                doc[key] = value
        meta["jsonDoc"] = [json.dumps(doc, ensure_ascii=False)]
    body["results"] = results
    return body


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("dest", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    body = scrub(json.loads(args.source.read_text(encoding="utf-8")), args.limit)
    text = json.dumps(body, ensure_ascii=False, indent=1)
    key = os.environ.get("NANDO_ES_API_KEY", "")
    env = Path(".env")
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("NANDO_ES_API_KEY="):
                key = line.split("=", 1)[1]
    if key and key in text:
        raise SystemExit("refusing to write: the fixture contains the API key")
    args.dest.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
