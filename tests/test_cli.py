"""CLI commands, with the network client replaced by the fake source."""

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner, Result

from nando_api import cli
from nando_api.config import get_settings
from nando_api.source import EuropaSearchClient, SourceError

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run in an empty folder; settings point into it (no real .env or data/)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db" / "nando.db"))
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("REQUEST_INTERVAL", "0")
    monkeypatch.delenv("NANDO_ES_API_KEY", raising=False)
    monkeypatch.delenv("RECORD_DIR", raising=False)
    return tmp_path


@pytest.fixture
def budgets(
    workdir: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_client: Callable[..., EuropaSearchClient],
) -> list[int | None]:
    """Replace the live client factory; record the budget each command asks for."""
    asked: list[int | None] = []

    def fake_client(max_requests: int | None = None) -> EuropaSearchClient:
        asked.append(max_requests)
        return make_client(max_requests=max_requests or 200)

    monkeypatch.setattr(cli, "_client", fake_client)
    return asked


def run(*args: str) -> Result:
    return CliRunner().invoke(cli.main, list(args), catch_exceptions=True)


def run_json(*args: str) -> dict[str, Any]:
    result = run(*args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_snapshot_writes_db_and_exports(budgets, workdir):
    out = run_json("snapshot")
    assert out["baseline"] is True
    assert out["counts"]["nando_notification"] == {"total": 4, "stored": 4}
    assert len(out["exports"]) == 2
    for path in out["exports"]:
        assert Path(path).is_file()
        assert Path(path).parent == workdir / "exports"
    assert (workdir / "db" / "nando.db").is_file()
    assert budgets == [None]


def test_snapshot_no_export(budgets, workdir):
    out = run_json("snapshot", "--no-export")
    assert "exports" not in out
    assert not (workdir / "exports").exists()


def test_incremental_after_snapshot(budgets):
    run_json("snapshot", "--no-export")
    out = run_json("incremental")
    assert out["counts"] == {"nando_body": 0, "nando_notification": 0}


def test_incremental_without_baseline_fails(budgets):
    result = run("incremental")
    assert result.exit_code != 0
    assert isinstance(result.exception, SourceError)


@pytest.mark.parametrize(("args", "budget"), [((), 400), (("--max-requests", "120"), 120)])
def test_backfill_history(budgets, args, budget):
    run_json("snapshot", "--no-export")
    out = run_json("backfill-history", *args)
    assert out["partitions"]["34"]["stored"] == 5
    assert budgets[-1] == budget


def test_export_and_status(budgets, workdir):
    run_json("snapshot", "--no-export")
    result = run("export")
    assert result.exit_code == 0, result.output
    paths = [Path(line) for line in result.output.splitlines()]
    assert sorted(p.suffix for p in paths) == [".csv", ".jsonl"]
    assert all(p.is_file() and p.parent == workdir / "exports" for p in paths)
    out = run_json("-v", "status")
    assert out["as_of"] is not None
    assert out["counts"]["notification"] == 4
    assert out["counts"]["body"] == 5
    assert [r["kind"] for r in out["runs"]] == ["snapshot"]
    assert out["runs"][0]["ok"] == 1


def test_status_on_empty_database(workdir):
    out = run_json("status")
    assert out["as_of"] is None
    assert out["runs"] == []
    assert set(out["counts"].values()) == {0}


def test_fetch_key_writes_env_and_hides_key(workdir, monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake_fetch_key(**kwargs: Any) -> tuple[str, int]:
        calls.append(kwargs)
        return "secret-abc123", 3

    monkeypatch.setattr(cli, "fetch_key", fake_fetch_key)
    env = workdir / "custom.env"
    result = run("fetch-key", "--env-file", str(env))
    assert result.exit_code == 0, result.output
    assert "secret-abc123" not in result.output
    assert "3 requests" in result.output
    assert env.read_text(encoding="utf-8") == "NANDO_ES_API_KEY=secret-abc123\n"
    assert calls[0]["request_interval"] == 0


def test_fetch_key_default_env_file_is_in_cwd(workdir, monkeypatch):
    monkeypatch.setattr(cli, "fetch_key", lambda **_: ("k", 2))
    assert run("fetch-key").exit_code == 0
    assert (workdir / ".env").read_text(encoding="utf-8") == "NANDO_ES_API_KEY=k\n"


@pytest.mark.parametrize("command", ["snapshot", "incremental", "backfill-history"])
def test_missing_key_stops_before_any_request(workdir, command):
    # The real client factory: it refuses an empty key in its constructor.
    result = run(command)
    assert result.exit_code != 0
    assert isinstance(result.exception, SourceError)
    assert "fetch-key" in str(result.exception)


def test_client_factory_uses_settings(workdir, monkeypatch):
    monkeypatch.setenv("NANDO_ES_API_KEY", "from-env")
    monkeypatch.setenv("MAX_REQUESTS", "17")
    with cli._client() as client:
        assert client.remaining == 17
    with cli._client(5) as client:
        assert client.remaining == 5


def test_serve_runs_uvicorn(monkeypatch):
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: calls.append((a, k)))
    result = run("serve", "--port", "9999")
    assert result.exit_code == 0, result.output
    assert calls == [(("nando_api.api:app",), {"host": "127.0.0.1", "port": 9999})]


def test_default_user_agent_names_the_service_and_the_terms(workdir, monkeypatch):
    monkeypatch.delenv("USER_AGENT", raising=False)
    assert get_settings().user_agent == "NotifiedBodyCheck/1.0 (+https://nbcheck.deknijf.com/terms)"


def test_the_api_never_loads_the_source_client():
    """The HTTP API serves only its own database: it never calls the Commission endpoint."""
    probe = (
        "import sys, nando_api.api; "
        "print(sorted(m for m in ('nando_api.source', 'nando_api.ingest') if m in sys.modules))"
    )
    result = subprocess.run(  # noqa: S603 - fixed arguments
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"
