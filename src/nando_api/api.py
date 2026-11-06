"""HTTP API, Atom feed, exports, and the landing and terms pages."""

import sqlite3
import xml.etree.ElementTree as ET
from collections.abc import Iterator  # noqa: TC003 - FastAPI inspects dependency signatures
from contextlib import asynccontextmanager, closing
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from nando_api import db, parse, queries
from nando_api.config import (
    ATTRIBUTION,
    CHANGES_INDICATED,
    LICENSE_NAME,
    LICENSE_URL,
    NOTICE,
    SERVICE_NAME,
    SOURCE,
    SOURCE_URL,
    get_settings,
)
from nando_api.exports import EXPORT_NAME_RE, list_exports

if TYPE_CHECKING:
    from pathlib import Path

BATCH_LIMIT = 500
STATUS_HELP = (
    "active: currently notified, the same rule as /v1/check. expired: status id 1 "
    "or 3 with a past end date. Other labels (suspended, withdrawn, "
    "expired/withdrawn) or a numeric status id filter on the status id the "
    "record carries."
)
CHANGE_TYPES = (
    "new",
    "status_changed",
    "suspended",
    "withdrawn",
    "restored",
    "end_date_changed",
    "scope_added",
    "scope_removed",
    "body_details_changed",
    "removed",
)
STATIC = files("nando_api").joinpath("static")


def _template(name: str, **values: str) -> str:
    html = STATIC.joinpath(name).read_text(encoding="utf-8")
    for key, value in {"SERVICE_NAME": SERVICE_NAME, **values}.items():
        html = html.replace(f"{{{{{key}}}}}", value)
    return html


def is_stale(as_of: str | None, max_age_hours: float, now: datetime | None = None) -> bool:
    """True when there is no successful sync, or the last one is too old."""
    if as_of is None:
        return True
    synced = datetime.fromisoformat(as_of)
    return (now or datetime.now(UTC)) - synced > timedelta(hours=max_age_hours)


def stale_text(as_of: str | None, max_age_hours: float) -> str:
    """The stale warning for the banner and the Atom feed."""
    if as_of is None:
        return "This server has no successful sync with NANDO yet, so data can be missing."
    return (
        f"The last successful sync with NANDO is more than {max_age_hours:g} hours old, "
        "so recent changes can be missing."
    )


def create_app(database_path: Path | None = None, export_dir: Path | None = None) -> FastAPI:
    settings = get_settings()
    db_path = database_path or settings.database_path
    exports_path = export_dir or settings.export_dir
    stale_hours = settings.stale_after_hours
    page_values = {"STALE_HOURS": f"{stale_hours:g}"}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        db.connect(db_path).close()  # create the schema once
        yield

    app = FastAPI(
        title=SERVICE_NAME,
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "Free, unofficial JSON API over NANDO, the EU register of notified bodies. "
            f"Source: {SOURCE}. {ATTRIBUTION} {CHANGES_INDICATED} {NOTICE}"
        ),
        license_info={"name": f"Data: {LICENSE_NAME}", "url": LICENSE_URL},
    )
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    def get_conn() -> Iterator[sqlite3.Connection]:
        # FastAPI may open and close a sync dependency on different threads.
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    Conn = Annotated[sqlite3.Connection, Depends(get_conn)]  # noqa: N806 - type alias

    def read_as_of() -> str | None:
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as c:
            c.row_factory = sqlite3.Row
            return db.as_of(c)

    def envelope(conn: sqlite3.Connection | None, data: Any, **extra: Any) -> dict[str, Any]:
        as_of = db.as_of(conn) if conn is not None else read_as_of()
        return {
            "as_of": as_of,
            "stale": is_stale(as_of, stale_hours),
            "source": SOURCE,
            "source_url": SOURCE_URL,
            "license": {"name": LICENSE_NAME, "url": LICENSE_URL},
            "attribution": ATTRIBUTION,
            "changes_indicated": CHANGES_INDICATED,
            "notice": NOTICE,
            **extra,
            "data": data,
        }

    def error_response(status: int, code: str, message: Any) -> JSONResponse:
        try:
            body = envelope(None, None, error={"code": code, "message": message})
        except sqlite3.Error:
            body = {
                "as_of": None,
                "stale": True,
                "source": SOURCE,
                "source_url": SOURCE_URL,
                "license": {"name": LICENSE_NAME, "url": LICENSE_URL},
                "attribution": ATTRIBUTION,
                "changes_indicated": CHANGES_INDICATED,
                "notice": NOTICE,
                "error": {"code": code, "message": message},
                "data": None,
            }
        return JSONResponse(body, status_code=status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "error")
        return error_response(exc.status_code, code, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return error_response(422, "invalid_request", details)

    # -- pages ---------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def landing() -> str:
        try:
            as_of = read_as_of()
        except sqlite3.Error:
            as_of = None
        return _template(
            "index.html",
            STALE_HIDDEN="" if is_stale(as_of, stale_hours) else " hidden",
            STALE_TEXT=stale_text(as_of, stale_hours),
            **page_values,
        )

    @app.get("/terms", response_class=HTMLResponse, include_in_schema=False)
    def terms() -> str:
        return _template("terms.html", **page_values)

    # -- helpers -------------------------------------------------------------

    def legislation_ids_for(conn: sqlite3.Connection, value: str | None) -> list[int] | None:
        if not value:
            return None
        acts = queries.resolve_legislation(conn, value)
        if not acts:
            raise HTTPException(404, f"legislation not found: {value}")
        if len(acts) > 1:
            codes = ", ".join(a["code"] for a in acts)
            raise HTTPException(404, f"legislation ambiguous: {value} matches {codes}")
        return [acts[0]["legislation_id"]]

    def status_filter(value: str | None, alias: str = "n") -> tuple[str, list[Any]]:
        """SQL condition for a status parameter; empty when there is none.

        "active" and "expired" follow the labels of `queries.shown_status`:
        "active" is currently notified (`queries.notified_sql`). Other labels
        and numeric ids filter on the status id the record carries.
        """
        if not value:
            return "", []
        wanted = value.strip().lower().replace("-", "/")
        if wanted == "active":
            return queries.notified_sql(alias)
        if wanted == "expired":
            return queries.expired_sql(alias)
        if wanted.isdigit():
            ids = [int(wanted)]
        else:
            ids = [i for i, label in parse.STATUS_LABELS.items() if label.lower() == wanted]
        if not ids:
            labels = ", ".join(sorted(label.lower() for label in parse.STATUS_LABELS.values()))
            raise HTTPException(404, f"unknown status: {value} (use one of: {labels})")
        prefix = f"{alias}." if alias else ""
        return f"{prefix}status_id IN ({','.join('?' * len(ids))})", ids

    def body_number_or_404(value: str) -> int:
        number = parse.parse_body_number(value)
        if number is None:
            raise HTTPException(404, f"not a body number: {value}")
        return number

    # -- API -----------------------------------------------------------------

    @app.get("/v1/status", tags=["meta"])
    def status(conn: Conn) -> dict[str, Any]:
        """Last sync, record counts, and source health.

        `counts.notified` uses the same rule as /v1/check.
        """
        notified, notified_params = queries.notified_sql("n")
        counts = {
            "bodies": conn.execute("SELECT COUNT(*) FROM body WHERE in_source = 1").fetchone()[0],
            "notifications_latest": conn.execute(
                "SELECT COUNT(*) FROM notification WHERE is_latest = 1 AND in_source = 1"
            ).fetchone()[0],
            "notification_versions": conn.execute("SELECT COUNT(*) FROM notification").fetchone()[
                0
            ],
            "legislation": conn.execute("SELECT COUNT(*) FROM legislation").fetchone()[0],
            "change_events": conn.execute("SELECT COUNT(*) FROM change_event").fetchone()[0],
            "notified": conn.execute(
                f"SELECT COUNT(*) FROM notification n WHERE {notified}",
                notified_params,
            ).fetchone()[0],
        }
        # Grouped by status id and end date passed; MIN(end_date) stands for
        # its group, so `shown_status` gives the label without a second rule.
        by_status: dict[str, int] = {}
        for r in conn.execute(
            "SELECT status_id, MIN(end_date) AS end_date, COUNT(*) AS n FROM notification "
            "WHERE is_latest = 1 AND in_source = 1 "
            "GROUP BY status_id, end_date < ? ORDER BY status_id",
            (queries.utc_today(),),
        ):
            label = queries.shown_status(r["status_id"], r["end_date"])[0] or "unknown"
            by_status[label] = by_status.get(label, 0) + r["n"]
        runs = [
            {
                "kind": r["kind"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "ok": bool(r["ok"]),
                "requests": r["requests"],
            }
            for r in conn.execute("SELECT * FROM sync_run ORDER BY run_id DESC LIMIT 5")
        ]
        # unknown: never synced. stale: no successful sync within the limit,
        # whatever the last run did. failing: the last run failed, but the data
        # is still recent. ok: the last run succeeded and the data is recent.
        if not runs:
            source_health = "unknown"
        elif is_stale(db.as_of(conn), stale_hours):
            source_health = "stale"
        else:
            source_health = "ok" if runs[0]["ok"] else "failing"
        return envelope(
            conn,
            {
                "counts": counts,
                "notifications_by_status": by_status,
                "last_runs": runs,
                "source_health": source_health,
                "stale_after_hours": stale_hours,
            },
        )

    @app.get("/v1/legislation", tags=["legislation"])
    def legislation_list(conn: Conn) -> dict[str, Any]:
        """All acts in NANDO, with the number of current notifications (as in the check)."""
        notified, params = queries.notified_sql("n")
        active = dict(
            conn.execute(
                f"SELECT n.legislation_id, COUNT(*) FROM notification n WHERE {notified} "
                "GROUP BY n.legislation_id",
                params,
            ).fetchall()
        )
        rows = conn.execute("SELECT * FROM legislation ORDER BY status, code").fetchall()
        data = [
            {
                **queries.legislation_dict(r),
                "active_notifications": active.get(r["legislation_id"], 0),
            }
            for r in rows
        ]
        return envelope(conn, data, total=len(data))

    @app.get("/v1/legislation/{code:path}/bodies", tags=["legislation"])
    def legislation_bodies(
        conn: Conn,
        code: str,
        status: Annotated[str | None, Query(description=STATUS_HELP)] = "active",
    ) -> dict[str, Any]:
        """Bodies notified under one act (by number such as 2017/745, code, or id)."""
        ids = legislation_ids_for(conn, code) or []
        condition, status_params = status_filter(status)
        params: list[Any] = [*ids, *status_params]
        status_clause = f"AND {condition}" if condition else ""
        rows = conn.execute(
            "SELECT b.*, n.notification_id, n.status_id, n.end_date AS notification_end_date "
            "FROM notification n JOIN body b USING (body_ref) "
            f"WHERE n.is_latest = 1 AND n.in_source = 1 AND n.legislation_id = ? {status_clause} "
            "ORDER BY b.body_number",
            params,
        ).fetchall()
        legislation = conn.execute(
            "SELECT * FROM legislation WHERE legislation_id = ?", (ids[0],)
        ).fetchone()
        data = [
            {
                **queries.body_dict(r),
                "notification_id": r["notification_id"],
                "status_id": r["status_id"],
                **queries.status_fields(r["status_id"], r["notification_end_date"]),
                "notification_end_date": r["notification_end_date"],
            }
            for r in rows
        ]
        return envelope(
            conn,
            data,
            legislation=queries.legislation_dict(legislation),
            total=len(data),
        )

    @app.get("/v1/bodies", tags=["bodies"])
    def bodies(
        conn: Conn,
        q: Annotated[str | None, Query(description="Text in the name, or a body number")] = None,
        country: Annotated[str | None, Query(description="Country name or id")] = None,
        legislation: Annotated[str | None, Query(description="Act number, code, or id")] = None,
        status: Annotated[str | None, Query(description=STATUS_HELP)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Search and filter bodies."""
        clauses = ["b.in_source = 1"]
        params: list[Any] = []
        if q:
            number = parse.parse_body_number(q)
            if number is not None:
                clauses.append("b.body_number = ?")
                params.append(number)
            else:
                clauses.append("lower(b.name) LIKE ?")
                params.append(f"%{q.lower()}%")
        if country:
            if country.isdigit():
                clauses.append("b.country_id = ?")
                params.append(int(country))
            else:
                clauses.append("lower(b.country) = lower(?)")
                params.append(country)
        leg_ids = legislation_ids_for(conn, legislation)
        condition, status_params = status_filter(status)
        if leg_ids or condition:
            sub = ["n.body_ref = b.body_ref", "n.is_latest = 1", "n.in_source = 1"]
            if leg_ids:
                sub.append(f"n.legislation_id IN ({','.join('?' * len(leg_ids))})")
                params.extend(leg_ids)
            if condition:
                sub.append(condition)
                params.extend(status_params)
            clauses.append(f"EXISTS (SELECT 1 FROM notification n WHERE {' AND '.join(sub)})")
        where = " AND ".join(clauses)
        total = conn.execute(f"SELECT COUNT(*) FROM body b WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT b.* FROM body b WHERE {where} ORDER BY b.body_number IS NULL, b.body_number, "
            "b.name LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return envelope(
            conn,
            [queries.body_dict(r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get("/v1/bodies/{number}", tags=["bodies"])
    def body_detail(conn: Conn, number: str) -> dict[str, Any]:
        """One body (by number: 0123, 123, or NB 0123) with a notification summary."""
        rows = queries.bodies_by_number(conn, body_number_or_404(number))
        if not rows:
            raise HTTPException(404, f"body not found: {number}")
        data = []
        for row in rows:
            summary = [
                {
                    "legislation_id": r["legislation_id"],
                    "legislation_code": r["code"],
                    "notification_id": r["notification_id"],
                    "status_id": r["status_id"],
                    **queries.status_fields(r["status_id"], r["end_date"]),
                    "end_date": r["end_date"],
                }
                for r in conn.execute(
                    "SELECT n.*, l.code FROM notification n LEFT JOIN legislation l "
                    "USING (legislation_id) WHERE n.body_ref = ? AND n.is_latest = 1 "
                    "AND n.in_source = 1 ORDER BY n.status_id, l.code",
                    (row["body_ref"],),
                )
            ]
            counts: dict[str, int] = {}
            for item in summary:
                counts[item["status"] or "unknown"] = counts.get(item["status"] or "unknown", 0) + 1
            data.append(
                {
                    **queries.body_dict(row),
                    "notifications_by_status": counts,
                    "notifications": summary,
                }
            )
        return envelope(conn, data[0] if len(data) == 1 else data)

    @app.get("/v1/bodies/{number}/notifications", tags=["bodies"])
    def body_notifications(
        conn: Conn,
        number: str,
        status: Annotated[str | None, Query(description=STATUS_HELP)] = None,
        legislation: Annotated[str | None, Query()] = None,
        include: Annotated[str | None, Query(description="scope")] = None,
    ) -> dict[str, Any]:
        """All latest notifications of a body."""
        num = body_number_or_404(number)
        if not queries.bodies_by_number(conn, num):
            raise HTTPException(404, f"body not found: {number}")
        clauses = ["body_number = ?", "is_latest = 1", "in_source = 1"]
        params: list[Any] = [num]
        condition, status_params = status_filter(status, alias="")
        if condition:
            clauses.append(condition)
            params.extend(status_params)
        leg_ids = legislation_ids_for(conn, legislation)
        if leg_ids:
            clauses.append("legislation_id = ?")
            params.append(leg_ids[0])
        rows = conn.execute(
            f"SELECT * FROM notification WHERE {' AND '.join(clauses)} ORDER BY legislation_id",
            params,
        ).fetchall()
        with_scope = "scope" in (include or "")
        data = []
        for r in rows:
            item = queries.notification_dict(conn, r)
            if with_scope:
                item["scope"] = queries.scope_for(conn, r["notification_id"])
            data.append(item)
        return envelope(conn, data, total=len(data))

    @app.get("/v1/notifications/{notification_id}", tags=["notifications"])
    def notification_detail(
        conn: Conn,
        notification_id: int,
        include: Annotated[
            str, Query(description="Comma-separated: scope, history")
        ] = "scope,history",
    ) -> dict[str, Any]:
        """One notification version, with scope and the version history of its series."""
        row = conn.execute(
            "SELECT * FROM notification WHERE notification_id = ?", (notification_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"notification not found: {notification_id}")
        parts = {p.strip() for p in include.split(",")}
        data = queries.notification_dict(conn, row)
        body = conn.execute("SELECT * FROM body WHERE body_ref = ?", (row["body_ref"],)).fetchone()
        data["body"] = queries.body_dict(body) if body else None
        if "scope" in parts:
            data["scope"] = queries.scope_for(conn, notification_id)
        if "history" in parts:
            data["history"] = queries.history_for(conn, row["series_key"])
        return envelope(conn, data)

    @app.get("/v1/check", tags=["check"])
    def check(
        conn: Conn,
        body: Annotated[str, Query(description="Body number: 0123, 123, or NB 0123")],
        legislation: Annotated[str, Query(description="Act: 2017/745, a code, or an id")],
        procedure: Annotated[str | None, Query(description="Procedure id or text")] = None,
        product: Annotated[str | None, Query(description="Product id, code, or text")] = None,
    ) -> dict[str, Any]:
        """Is this body notified under this act, as of the last sync?"""
        return envelope(conn, queries.check(conn, body, legislation, procedure, product))

    class CheckItem(BaseModel):
        body: str
        legislation: str
        procedure: str | None = None
        product: str | None = None

    class CheckBatch(BaseModel):
        checks: list[CheckItem] = Field(max_length=BATCH_LIMIT)

    @app.post("/v1/check/batch", tags=["check"])
    def check_batch(conn: Conn, payload: CheckBatch) -> dict[str, Any]:
        """Up to 500 checks in one call."""
        data = [
            queries.check(conn, c.body, c.legislation, c.procedure, c.product)
            for c in payload.checks
        ]
        return envelope(conn, data, total=len(data))

    def change_filters(
        conn: sqlite3.Connection,
        legislation: str | None,
        body: str | None,
        change_type: str | None,
    ) -> tuple[list[int] | None, int | None, list[str] | None]:
        leg_ids = legislation_ids_for(conn, legislation)
        body_number = body_number_or_404(body) if body else None
        types = None
        if change_type:
            types = [t.strip() for t in change_type.split(",") if t.strip()]
            unknown = sorted(set(types) - set(CHANGE_TYPES))
            if unknown:
                raise HTTPException(404, f"unknown change_type: {', '.join(unknown)}")
        return leg_ids, body_number, types

    FilterLegislation = Annotated[  # noqa: N806 - type alias
        str | None, Query(description="Act number, code, or id")
    ]
    FilterBody = Annotated[str | None, Query(description="Body number")]  # noqa: N806 - type alias
    FilterCountry = Annotated[str | None, Query(description="Country name or id")]  # noqa: N806 - type alias
    FilterType = Annotated[  # noqa: N806 - type alias
        str | None, Query(description="Comma-separated: " + ", ".join(CHANGE_TYPES))
    ]

    @app.get("/v1/changes", tags=["changes"])
    def changes(
        conn: Conn,
        since: Annotated[str | None, Query(description="ISO timestamp (UTC)")] = None,
        after: Annotated[int | None, Query(description="Cursor: the last event_id you saw")] = None,
        legislation: FilterLegislation = None,
        body: FilterBody = None,
        country: FilterCountry = None,
        change_type: FilterType = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
    ) -> dict[str, Any]:
        """The change feed, oldest first. Page with `after` = `next_after`.

        `order=desc` returns the newest events first (no paging cursor).
        """
        leg_ids, body_number, types = change_filters(conn, legislation, body, change_type)
        rows = queries.changes(
            conn,
            since=since,
            after_id=after,
            legislation_ids=leg_ids,
            body_number=body_number,
            country=country,
            change_types=types,
            limit=limit,
            newest_first=order == "desc",
        )
        data = [queries.change_dict(r) for r in rows]
        next_after = data[-1]["event_id"] if len(data) == limit and order == "asc" else None
        return envelope(conn, data, next_after=next_after)

    @app.get(
        "/v1/changes.atom",
        tags=["changes"],
        response_class=Response,
        responses={200: {"content": {"application/atom+xml": {}}}},
    )
    def changes_atom(
        request: Request,
        conn: Conn,
        legislation: FilterLegislation = None,
        body: FilterBody = None,
        country: FilterCountry = None,
        change_type: FilterType = None,
    ) -> Response:
        """Atom feed of the latest 100 matching changes. Subscribe in any feed reader."""
        leg_ids, body_number, types = change_filters(conn, legislation, body, change_type)
        rows = queries.changes(
            conn,
            legislation_ids=leg_ids,
            body_number=body_number,
            country=country,
            change_types=types,
            limit=100,
            newest_first=True,
        )
        as_of = db.as_of(conn)
        warning = stale_text(as_of, stale_hours) if is_stale(as_of, stale_hours) else None
        xml = atom_feed(str(request.url), str(request.base_url), rows, as_of, warning)
        return Response(xml, media_type="application/atom+xml; charset=utf-8")

    @app.get("/v1/exports", tags=["exports"])
    def exports_list(request: Request) -> dict[str, Any]:
        """Daily snapshot exports, newest first. Free for everyone."""
        base = str(request.base_url).rstrip("/")
        data = [
            {
                "date": day,
                "jsonl": f"{base}/v1/exports/{day}.jsonl",
                "csv": f"{base}/v1/exports/{day}.csv",
            }
            for day in list_exports(exports_path)
        ]
        return envelope(None, data)

    @app.get("/v1/exports/{name}", tags=["exports"], response_class=FileResponse)
    def export_file(name: str) -> FileResponse:
        """One export file: YYYY-MM-DD.jsonl or YYYY-MM-DD.csv."""
        stem, _, suffix = name.rpartition(".")
        if suffix not in {"jsonl", "csv"} or not EXPORT_NAME_RE.match(stem):
            raise HTTPException(404, f"no such export: {name}")
        path = exports_path / name
        if not path.is_file():
            raise HTTPException(404, f"no such export: {name}")
        media = "application/x-ndjson" if suffix == "jsonl" else "text/csv"
        return FileResponse(
            path,
            media_type=media,
            filename=f"nando-{name}",
            headers={
                "X-Data-Source": SOURCE,
                "X-Data-License": f"{LICENSE_NAME} {LICENSE_URL}",
                "X-Notice": NOTICE,
            },
        )

    return app


ATOM_NS = "http://www.w3.org/2005/Atom"


def _entry_title(row: sqlite3.Row) -> str:
    who = (
        f"NB {parse.body_number_str(row['body_number'])}" if row["body_number"] else row["body_ref"]
    )
    name = f" ({row['body_name']})" if row["body_name"] else ""
    act = f" under {row['legislation_code']}" if row["legislation_code"] else ""
    words = {
        "new": "newly notified",
        "status_changed": "status changed",
        "suspended": "suspended",
        "withdrawn": "withdrawn",
        "restored": "restored",
        "end_date_changed": "end date changed",
        "scope_added": "scope extended",
        "scope_removed": "scope reduced",
        "body_details_changed": "details changed",
        "removed": "removed from the register",
    }
    return f"{who}{name}: {words.get(row['change_type'], row['change_type'])}{act}"


def atom_feed(
    self_url: str,
    base_url: str,
    rows: list[sqlite3.Row],
    as_of: str | None,
    stale_warning: str | None = None,
) -> bytes:
    ET.register_namespace("", ATOM_NS)

    def sub(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
        element = ET.SubElement(parent, f"{{{ATOM_NS}}}{tag}", attrs)
        if text is not None:
            element.text = text
        return element

    base = base_url.rstrip("/")
    host = base.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    feed = ET.Element(f"{{{ATOM_NS}}}feed")
    sub(feed, "id", self_url)
    sub(feed, "title", f"{SERVICE_NAME}: NANDO changes")
    stale = f" Stale data: {stale_warning}" if stale_warning else ""
    sub(feed, "subtitle", f"{NOTICE} Source: {SOURCE}.{stale}")
    sub(
        feed,
        "updated",
        (rows[0]["observed_at"] if rows else as_of) or "1970-01-01T00:00:00Z",
    )
    sub(feed, "link", rel="self", href=self_url)
    sub(feed, "link", rel="alternate", href=f"{base}/")
    sub(feed, "rights", f"{ATTRIBUTION} {CHANGES_INDICATED}")
    author = sub(feed, "author")
    sub(author, "name", SERVICE_NAME)
    for row in rows:
        entry = sub(feed, "entry")
        sub(entry, "id", f"tag:{host},2026-11-04:change/{row['event_id']}")
        sub(entry, "title", _entry_title(row))
        sub(entry, "updated", row["observed_at"])
        if row["notification_id"]:
            href = f"{base}/v1/notifications/{row['notification_id']}"
        elif row["body_number"]:
            href = f"{base}/v1/bodies/{parse.body_number_str(row['body_number'])}"
        else:
            href = f"{base}/v1/changes?after={row['event_id'] - 1}&limit=1"
        sub(entry, "link", href=href)
        detail = []
        if row["field"]:
            detail.append(f"Field: {row['field']}.")
        if row["old_value"] is not None:
            detail.append(f"Before: {row['old_value']}.")
        if row["new_value"] is not None:
            detail.append(f"After: {row['new_value']}.")
        if row["source_update_date"]:
            detail.append(f"Source update: {row['source_update_date']}.")
        detail.append(f"Observed: {row['observed_at']}. {NOTICE}")
        sub(entry, "summary", " ".join(detail))
        sub(entry, "category", term=row["change_type"])
    return ET.tostring(feed, encoding="utf-8", xml_declaration=True)


app = create_app()
