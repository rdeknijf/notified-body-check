"""Settings and fixed texts.

`SERVICE_NAME` is the one place that names the service. Rename it here.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_NAME = "Notified Body Check"
SITE_URL = "https://nbcheck.deknijf.com"

SOURCE = "European Commission, NANDO, via the Single Market Compliance Space"
SOURCE_URL = "https://webgate.ec.europa.eu/single-market-compliance-space/notified-bodies"
LICENSE_NAME = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = (
    "Contains data from NANDO, published by the European Commission "
    "(https://webgate.ec.europa.eu/single-market-compliance-space/notified-bodies), "
    "reused under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)."
)
CHANGES_INDICATED = (
    "Changes made: the records are restructured into JSON and CSV, personal contact "
    "fields (email, phone, fax) are removed, update times are "
    "converted from Brussels local time to UTC, status names are added, and change "
    "events are worked out by comparing versions over time."
)
NOTICE = (
    "Unofficial service. Not affiliated with or endorsed by the European Commission. "
    "Data as published by the Commission at the time shown in as_of; no warranty."
)

SEARCH_ENDPOINT = "https://webgate.ec.europa.eu/es/search-api/rest/search"
APP_URL = "https://webgate.ec.europa.eu/single-market-compliance-space/notified-bodies"


class Settings(BaseSettings):
    """Runtime settings from the environment or `.env`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    nando_es_api_key: str = ""
    database_path: Path = Path("data/nando.db")
    export_dir: Path = Path("data/exports")
    # Names the service and its terms page (how to ask for a correction or removal).
    user_agent: str = f"NotifiedBodyCheck/1.0 (+{SITE_URL}/terms)"
    # Minimum seconds between two requests to the source.
    request_interval: float = 1.5
    # Hard stop for one command run. A full snapshot needs about 60 to 180.
    max_requests: int = 200
    # Optional: save the first page of each query here (for golden fixtures).
    record_dir: Path | None = None
    # Answers carry `stale: true` when the last successful sync is older than this.
    stale_after_hours: float = 6.0


def get_settings() -> Settings:
    """Return fresh settings (read at call time, so tests can set env vars)."""
    return Settings()
