"""Q3a: the v4 `botsworth` CLI (interfaces.md §5.1, CLI-1..5, CLI-10, CLI-11; TST-2 CLI parity).

Every test passes an explicit tmp_path or fixture db; none touches `~/.old-profiler`.
"""

# ruff: noqa: E501
from __future__ import annotations

import importlib
import json
import shutil
import socket
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from cli_plain import plain_top
from fake_query import FakeQuery
from qstore_ids import S2DAY, turn

from botsworth import pipeline
from botsworth.cli import main as cli
from botsworth.cli import scope as cli_scope
from botsworth.cli import serve as cli_serve
from botsworth.cli.render import TABLE_BYTES
from botsworth.http.server import make_server
from botsworth.query import NotFound, QueryError
from botsworth.query.scope import Scope
from botsworth.query.views import STUBS
from botsworth.store import ingest, schema

# `import botsworth.query` yields the function: botsworth/__init__ rebinds the name.
Q = importlib.import_module("botsworth.query")
REPO = Path(__file__).resolve().parents[2]
CLAUDE_FIXTURE = REPO / "tests" / "fixtures" / "F2" / "project"  # synthetic Claude Code logs
ENC = lambda s: quote(s, safe="")  # noqa: E731


def run(capsys, *argv: str, api: Any = Q) -> tuple[int, str, str]:
    code = cli.main(list(argv), api=api)
    out = capsys.readouterr()
    return code, out.out, out.err


class Spy:
    """The real query layer for everything but the v4 views, which it records instead of running."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.dbs: list[Any] = []
        self.raises = raises

    def __getattr__(self, name: str) -> Any:
        if name not in {fn.__name__ for fn in Q.views.VIEWS}:
            return getattr(Q, name)

        def view(**kw: Any) -> dict[str, Any]:
            if self.raises:
                raise self.raises
            self.dbs.append(kw.pop("db"))
            self.calls.append((name, kw))
            return {"view": name}

        return view


# ── output ───────────────────────────────────────────────────────────────────


def test_json_default_one_line_utf8(capsys, tmp_path):
    db = tmp_path / "s.sqlite"
    db.touch()

    class Api(FakeQuery):
        def query(self, q, db=None):
            return {"rows": [{"label": "café ☕"}], "freshness": {}}

    code, out, _ = run(capsys, "--db", str(db), "top", "--kind", "turn", api=Api())
    assert code == 0
    assert out.endswith("\n") and out.count("\n") == 1
    assert "café ☕" in out  # UTF-8, not \u escapes (CLI-3)
    assert json.loads(out)["rows"][0]["label"] == "café ☕"


def test_json_flag_noop(qstore, capsys):
    plain = run(capsys, "--db", str(qstore), "status")
    flagged = run(capsys, "--db", str(qstore), "status", "--json")
    assert plain[0] == 0 and plain == flagged


def test_json_equals_view_result(qstore, capsys):
    code, out, err = run(capsys, "--db", str(qstore), "status")
    assert (code, err) == (0, "")
    assert plain_top(json.loads(out)) == json.loads(Q.wire.to_json(Q.ingest_state(qstore)))


def test_table_cap_2kb_tail(capsys, tmp_path):
    db = tmp_path / "s.sqlite"
    db.touch()
    rows = [{"id": f"s{i}", "cost_usd": 0.5, "title": "x" * 40} for i in range(300)]

    class Api(Spy):
        def __getattr__(self, name: str) -> Any:
            if name == "list_sessions":
                return lambda **kw: {"rows": rows, "total": 300}
            return super().__getattr__(name)

    code, out, _ = run(capsys, "--db", str(db), "sessions", "--table", api=Api())
    assert code == 0
    assert len(out.encode()) <= TABLE_BYTES
    assert out.rstrip().splitlines()[-1].endswith("more lines (use --json)")
    assert "rows (300):" in out


# ── arguments reach the views the way the HTTP routes build them ─────────────

NONE = Scope()
CASES = [
    (["status"], "ingest_state", {}),
    (["period"], "period_summary", {"scope": NONE}),
    (
        ["period", "--from", "2026-10-01", "--to", "2026-10-31", "--q", "fix", "--provider", "omp"]
        + ["--provider", "codex", "--model", "m", "--branch", "b", "--project", "/u/d/helper", "--automated"],
        "period_summary",
        {
            "scope": Scope(
                "2026-10-01", "2026-10-31", "fix", ("omp", "codex"), ("m",), ("b",), ("/u/d/helper",), True
            )
        },
    ),
    (
        ["days", "--window-from", "2026-10-01", "--window-to", "2026-10-07", "--project", "helper"],
        "day_series",
        {
            "scope": Scope(projects=("/u/d/helper",)),
            "window_from": "2026-10-01",
            "window_to": "2026-10-07",
            "bucket": "day",
        },
    ),
    (
        ["sessions", "--sort", "cost", "--dir", "asc", "--offset", "40", "-n", "5", "--group", "day"],
        "list_sessions",
        {
            "scope": NONE,
            "sort": "cost",
            "dir": "asc",
            "offset": 40,
            "limit": 5,
            "group": "day",
            "group_key": None,
            "group_limit": 5,
        },
    ),
    (
        ["sessions"],
        "list_sessions",
        {
            "scope": NONE,
            "sort": "start",
            "dir": "desc",
            "offset": 0,
            "limit": 20,
            "group": "none",
            "group_key": None,
            "group_limit": 5,
        },
    ),
    (["facets", "--q", "x"], "facets", {"scope": Scope(q="x")}),
    (["search", "needle", "--model", "m"], "palette", {"text": "needle", "scope": Scope(models=("m",))}),
    (
        ["session", "claude_code/s2day"],
        "session_detail",
        {"session": "claude_code/s2day", "tz": "Asia/Kolkata"},
    ),
    (
        ["session", "claude_code/s2day", "--tz", "UTC"],
        "session_detail",
        {"session": "claude_code/s2day", "tz": "UTC"},
    ),
    (["turns", "abcdef12"], "session_turns", {"session": "abcdef12", "min_gap_ms": 120_000}),
    (
        ["turns", "abcdef12", "--min-gap", "90s"],
        "session_turns",
        {"session": "abcdef12", "min_gap_ms": 90_000},
    ),
    (
        ["turns", "abcdef12", "--min-gap", "1h"],
        "session_turns",
        {"session": "abcdef12", "min_gap_ms": 3_600_000},
    ),
    (["turn", "abcdef12:T12"], "turn_rows", {"turn": "abcdef12:T12", "from_ms": None, "to_ms": None}),
    (["turn", "t", "--from-ms", "5", "--to-ms", "9"], "turn_rows", {"turn": "t", "from_ms": 5, "to_ms": 9}),
    (["detail", "a/b#1"], "detail", {"op": "a/b#1"}),
    (
        ["find", "s", "--kind", "search", "--text", "hi", "--measure", "time", "-n", "7"],
        "find",
        {"session": "s", "kind": "search", "measure": "time", "text": "hi", "limit": 7},
    ),
]


@pytest.mark.parametrize("argv,view,kwargs", CASES, ids=lambda x: x[0] if isinstance(x, str) else None)
def test_dispatch_matches_route_arguments(qstore, capsys, argv, view, kwargs):
    spy = Spy()
    code, out, err = run(capsys, "--db", str(qstore), *argv, api=spy)
    assert (code, err) == (0, "")
    assert spy.calls == [(view, kwargs)] and plain_top(json.loads(out)) == {"view": view}
    assert spy.dbs == [qstore]  # D20: the resolved --db reaches every view


@pytest.mark.parametrize(
    "argv",
    [
        ["sessions", "--sort", "bogus"],
        ["sessions", "-n", "0"],
        ["turns", "s", "--min-gap", "30s"],
        ["turn", "t", "--from-ms", "5"],
        ["find", "s"],
        ["find", "s", "--kind", "search"],
        ["days"],
        ["period", "--from", "2026-13-40", "--to", "2026-13-41"],
    ],
)
def test_route_validation_is_exit_1(qstore, capsys, argv):
    code, out, err = run(capsys, "--db", str(qstore), *argv, api=Spy())
    assert (code, out) == (1, "")
    assert err.startswith("error: ") and err.count("\n") == 1


# ── CLI-5 scope flags ────────────────────────────────────────────────────────


def _since(monkeypatch, capsys, qstore, *flags: str) -> tuple[Scope, list[str]]:
    zones: list[str] = []

    def today(tz: str) -> date:
        zones.append(tz)
        return date(2026, 10, 12)

    monkeypatch.setattr(cli_scope, "today", today)
    spy = Spy()
    code, _, err = run(capsys, "--db", str(qstore), "sessions", *flags, api=spy)
    assert (code, err) == (0, "")
    return spy.calls[0][1]["scope"], zones


def test_since_days_machine_zone(monkeypatch, capsys, qstore):
    """TST-8 (f): without `--tz`, "today" is in the machine zone; the store holds none."""
    monkeypatch.delenv("BOTSWORTH_TZ", raising=False)
    monkeypatch.setenv("TZ", "America/New_York")
    scope, zones = _since(monkeypatch, capsys, qstore, "--since", "7d")
    assert zones == ["America/New_York"]
    assert (scope.from_day, scope.to_day, scope.tz) == ("2026-10-06", "2026-10-12", "America/New_York")


def test_since_days_tz_flag_wins_over_the_machine_zone(monkeypatch, capsys, qstore):
    scope, zones = _since(monkeypatch, capsys, qstore, "--since", "7d", "--tz", "Asia/Kathmandu")
    assert zones == ["Asia/Kathmandu"] and scope.tz == "Asia/Kathmandu"


def test_cli_sends_tz_in_the_http_query_mapping(qstore):
    ns = cli.build_parser().parse_args(["period", "--tz", "Asia/Kathmandu"])
    assert cli_scope.scope_query(ns, cli_scope.cli_zone(ns))["tz"] == ["Asia/Kathmandu"]
    ns = cli.build_parser().parse_args(["period"])
    assert cli_scope.scope_query(ns, "UTC")["tz"] == ["UTC"]


@pytest.mark.parametrize(
    "argv", [["sessions"], ["period"], ["days"], ["session", "s2day"], ["tasks", "--by", "tool"]]
)
def test_unknown_tz_is_exit_2(qstore, capsys, argv):
    """TST-8 (d): `--tz Not/AZone` is an argparse error."""
    with pytest.raises(SystemExit) as e:
        run(capsys, "--db", str(qstore), *argv, "--tz", "Not/AZone", api=Spy())
    assert e.value.code == 2
    assert "unknown time zone" in capsys.readouterr().err


@pytest.mark.parametrize("since,first", [("1d", "2026-10-12"), ("2w", "2026-09-29"), ("30d", "2026-09-13")])
def test_since_spans_include_today(monkeypatch, capsys, qstore, since, first):
    scope, _ = _since(monkeypatch, capsys, qstore, "--since", since)
    assert (scope.from_day, scope.to_day) == (first, "2026-10-12")


def test_scenario_sessions_since_7d_sort_cost(monkeypatch, capsys, qstore):
    spy = Spy()
    monkeypatch.setattr(cli_scope, "today", lambda tz: date(2026, 10, 12))
    run(capsys, "--db", str(qstore), "sessions", "--since", "7d", "--sort", "cost", "-n", "5", api=spy)
    ((_, kw),) = spy.calls
    assert (kw["scope"].from_day, kw["scope"].to_day, kw["sort"], kw["limit"]) == (
        "2026-10-06",
        "2026-10-12",
        "cost",
        5,
    )


@pytest.mark.parametrize(
    "flags",
    [
        ["--since", "7d", "--from", "2026-10-01"],
        ["--since", "7d", "--to", "2026-10-01"],
        ["--from", "2026-10-01"],
    ],
)
def test_since_with_from_is_exit_2(qstore, capsys, flags):
    code, out, err = run(capsys, "--db", str(qstore), "sessions", *flags, api=Spy())
    assert (code, out) == (2, "")
    assert err.startswith("error: ") and err.count("\n") == 1


def test_since_hours_only_on_legacy(qstore, capsys):
    with pytest.raises(SystemExit) as e:
        run(capsys, "--db", str(qstore), "sessions", "--since", "3h")
    assert e.value.code == 2
    assert run(capsys, "--db", str(qstore), "stats", "--since", "3h")[0] == 0


def test_days_ignores_the_scope_period(qstore, capsys):
    spy = Spy()
    run(capsys, "--db", str(qstore), "days", "--window-from", "2026-10-01", "--window-to", "2026-10-02",
        "--from", "2026-01-01", "--to", "2026-01-02", api=spy)  # fmt: skip
    assert spy.calls[0][1]["scope"] == NONE


def test_to_scope_is_the_route_scope(qstore):
    ns = cli.build_parser().parse_args(["period", "--from", "2026-10-01", "--to", "2026-10-02", "--q", "z"])
    assert cli_scope.to_scope(ns, "Asia/Kolkata") == Scope("2026-10-01", "2026-10-02", "z", tz="Asia/Kolkata")


# ── CLI-4 exit codes ─────────────────────────────────────────────────────────


@pytest.fixture
def v6_store(tmp_path, monkeypatch) -> Path:
    db = tmp_path / "v6.sqlite"
    with monkeypatch.context() as m:
        m.setattr(schema, "SCHEMA_VERSION", schema.SCHEMA_VERSION - 1)
        ingest.ingest(db=db)
    return db


def test_exit_codes(qstore, v6_store, tmp_path, capsys):
    gone = tmp_path / "none.sqlite"
    cases = [
        (1, ["--db", str(qstore), "show", "claude_code/nope/session/-"], Q),  # unknown id
        (
            1,
            ["--db", str(qstore), "session", "ab"],
            Spy(raises=QueryError("session 'ab' is ambiguous: a, b")),
        ),
        (1, ["--db", str(qstore), "detail", "x"], Spy(raises=NotFound("no op 'x'"))),
        (
            1,
            ["--db", str(qstore), "turn", "x"],
            Spy(raises=NotImplementedError("turn_rows is not built yet")),
        ),
        (2, ["--db", str(gone), "facets"], Q),  # no store
        (2, ["--db", str(v6_store), "show", "x"], Q),  # schema mismatch, legacy command
        (2, ["--db", str(v6_store), "sessions", "--since", "7d"], Q),  # and a v4 command
    ]
    for want, argv, api in cases:
        code, out, err = run(capsys, *argv, api=api)
        assert (code, out) == (want, ""), argv
        assert err.startswith("error: ") and err.count("\n") == 1, argv
    assert "botsworth ingest" in err


def test_exit_code_ingest_locked(tmp_path, capsys):
    db = tmp_path / "s.sqlite"
    with ingest.lock(db):
        code, out, err = run(capsys, "--db", str(db), "ingest", "--provider", "claude_code")
    assert (code, out) == (1, "")
    assert err.startswith("error: ") and err.count("\n") == 1


def test_exit_130_on_ctrl_c(qstore, capsys):
    code, out, err = run(capsys, "--db", str(qstore), "facets", api=Spy(raises=KeyboardInterrupt()))
    assert (code, out, err) == (130, "", "")


def test_status_without_a_store_is_a_value(tmp_path, capsys):
    code, out, _ = run(capsys, "--db", str(tmp_path / "none.sqlite"), "status")
    assert code == 0 and json.loads(out)["store_state"] == "missing"


# ── parity with HTTP (TST-2) ─────────────────────────────────────────────────

T0 = turn(S2DAY, 0)
S2DAY_OP = S2DAY + "/session/-"  # QRY-6: the session op_id
PARITY = [
    ("status", ["status"], "/api/ingest/status", "ingest_state"),
    ("period", ["period", "--from", "2026-10-01", "--to", "2026-10-31"], "/api/explorer/summary?from=2026-10-01&to=2026-10-31", "period_summary"),
    ("days", ["days", "--window-from", "2026-10-01", "--window-to", "2026-10-07"], "/api/explorer/days?window_from=2026-10-01&window_to=2026-10-07", "day_series"),
    ("sessions", ["sessions", "--sort", "cost", "-n", "5"], "/api/explorer/sessions?sort=cost&limit=5", "list_sessions"),
    ("facets", ["facets"], "/api/explorer/facets", "facets"),
    ("search", ["search", "day"], "/api/palette?text=day", "palette"),
    ("session", ["session", S2DAY_OP], f"/api/sessions/{ENC(S2DAY_OP)}", "session_detail"),
    ("turns", ["turns", S2DAY_OP, "--min-gap", "90s"], f"/api/sessions/{ENC(S2DAY_OP)}/turns?min_gap_ms=90000", "session_turns"),
    ("turn", ["turn", T0], f"/api/turns/{ENC(T0)}/rows", "turn_rows"),
    ("detail", ["detail", T0], f"/api/ops/{ENC(T0)}/detail", "detail"),
    ("find", ["find", S2DAY_OP, "--kind", "biggest"], f"/api/sessions/{ENC(S2DAY_OP)}/find?kind=biggest", "find"),
]  # fmt: skip


@pytest.fixture
def http_base(qstore):
    server = make_server("127.0.0.1", 0, db=qstore)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("name,argv,path,view", PARITY, ids=[c[0] for c in PARITY])
def test_parity(qstore, capsys, http_base, name, argv, path, view):
    if view in STUBS:
        pytest.skip(f"view stubbed: {view}")
    code, out, err = run(capsys, "--db", str(qstore), *argv)
    assert (code, err) == (0, "")
    with urllib.request.urlopen(http_base + path) as r:
        http_body = json.loads(r.read())
    assert plain_top(json.loads(out)) == http_body


# ── serve (CLI-10) ───────────────────────────────────────────────────────────


@pytest.fixture
def running(monkeypatch, qstore):
    """Run `botsworth serve` in a thread on port 0; yields (base, stderr_lines_getter)."""
    made: list[Any] = []
    ready = threading.Event()

    def spy_make(*a: Any, **kw: Any):
        server = make_server(*a, **kw)
        made.append(server)
        ready.set()
        return server

    monkeypatch.setattr(cli_serve, "make_server", spy_make)

    def start(*extra: str, db: Path = qstore) -> tuple[str, threading.Thread]:
        result: list[int] = []
        t = threading.Thread(
            target=lambda: result.append(cli.main(["--db", str(db), "serve", "--port", "0", *extra]))
        )
        t.start()
        assert ready.wait(10)
        t.result = result  # type: ignore[attr-defined]
        return f"http://127.0.0.1:{made[0].server_address[1]}", t

    yield start
    for s in made:
        s.shutdown()


def test_serve_binds_and_serves_api_and_ui(running, tmp_path, capfd):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<html>botsworth-ui</html>")
    base, t = running("--ui", str(ui))
    with urllib.request.urlopen(base + "/api/ingest/status") as r:
        assert r.status == 200 and json.loads(r.read())["store_state"] == "ready"
    with urllib.request.urlopen(base + "/") as r:
        assert r.status == 200 and b"botsworth-ui" in r.read()
    err = capfd.readouterr().err
    assert f"botsworth UI on {base}" in err and "warning" not in err


def test_serve_no_ui_warns_once(running, tmp_path, monkeypatch, capfd):
    monkeypatch.setattr(cli_serve, "BUNDLED", tmp_path / "no-bundle")
    monkeypatch.setattr(cli_serve, "DIST", tmp_path / "no-dist")
    monkeypatch.delenv("BOTSWORTH_UI_DIR", raising=False)
    monkeypatch.setattr(cli_serve, "find_ui_dir", lambda explicit: None)
    base, _ = running()
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + "/")
    assert e.value.code == 404
    with urllib.request.urlopen(base + "/api/ingest/status") as r:
        assert r.status == 200
    err = capfd.readouterr().err
    assert err.count("warning:") == 1 and "UI assets not found" in err


def test_serve_never_ingests_and_runs_without_a_store(running, tmp_path):
    db = tmp_path / "absent.sqlite"
    base, _ = running(db=db)
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + "/api/explorer/facets")
    assert e.value.code == 503
    assert not db.exists() and not Path(f"{db}.lock").exists()


def test_serve_port_in_use_exit_3(qstore, capsys, tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        code, out, err = run(capsys, "--db", str(qstore), "serve", "--ui", str(tmp_path), "--port", str(port))
    # --ui keeps the test independent of whether frontend/dist exists (CLI-10 warns when no UI is found)
    assert (code, out) == (3, "")
    assert err == f"error: port {port} in use\n"


def test_ui_dir_search_order(tmp_path):
    d = {n: tmp_path / n for n in ("flag", "env", "bundled", "repo")}
    for p in d.values():
        p.mkdir()

    def find(flag: bool = True, env: bool = True, bundled: bool = True, repo: bool = True) -> Path | None:
        return cli_serve.find_ui_dir(
            str(d["flag"]) if flag else None,
            {"BOTSWORTH_UI_DIR": str(d["env"])} if env else {},
            d["bundled"] if bundled else tmp_path / "x1",
            d["repo"] if repo else tmp_path / "x2",
        )

    assert find() == d["flag"]
    assert find(flag=False) == d["env"]
    assert find(flag=False, env=False) == d["bundled"]
    assert find(flag=False, env=False, bundled=False) == d["repo"]
    assert find(flag=False, env=False, bundled=False, repo=False) is None
    # a named directory that does not exist is skipped, the next one wins
    assert cli_serve.find_ui_dir(str(tmp_path / "nope"), {}, d["bundled"], d["repo"]) == d["bundled"]


# ── CLI-1: one entry point ───────────────────────────────────────────────────


def test_botsworth_http_entry_removed():
    import tomllib

    scripts = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts == {"botsworth": "botsworth.cli.main:main"}
    import botsworth.http.server as server

    assert not hasattr(server, "main")


# ── ingest swaps (DM-4, D52) ─────────────────────────────────────────────────


@pytest.fixture
def logs(tmp_path, monkeypatch) -> Path:
    """A synthetic Claude Code log root that `botsworth ingest --provider claude_code` reads."""
    root = tmp_path / "logs"
    shutil.copytree(CLAUDE_FIXTURE, root / "project")
    monkeypatch.setitem(pipeline.DEFAULT_ROOTS, "claude_code", root)
    return root


def _meta(db: Path) -> dict[str, str]:
    conn = sqlite3.connect(db)
    try:
        return dict(conn.execute("SELECT key, value FROM meta"))
    finally:
        conn.close()


def _leftovers(db: Path) -> list[str]:
    return sorted(p.name for p in db.parent.glob(f"{db.name}*") if p.name not in (db.name, f"{db.name}.lock"))


def _ingest(capsys, db: Path, *extra: str) -> tuple[int, str, str]:
    return run(capsys, "--db", str(db), "ingest", "--provider", "claude_code", *extra)


def test_ingest_on_old_store_swaps(tmp_path, logs, monkeypatch, capsys):
    db = tmp_path / "s.sqlite"
    with monkeypatch.context() as m:
        m.setattr(schema, "SCHEMA_VERSION", schema.SCHEMA_VERSION - 1)
        code, out, _ = _ingest(capsys, db)
        assert code == 0 and json.loads(out)["mode"] == "rebuild"
    assert _meta(db)["schema_version"] == str(schema.SCHEMA_VERSION - 1)

    seen: list[Path] = []
    real_ingest = ingest.ingest

    def spy(*a: Any, **kw: Any):
        seen.append(Path(kw["db"]))
        return real_ingest(*a, **kw)

    monkeypatch.setattr(ingest, "ingest", spy)
    code, out, err = _ingest(capsys, db)
    assert (code, err) == (0, "")
    assert json.loads(out)["mode"] == "rebuild"
    assert seen == [Path(f"{db}.rebuild")]  # the old file was never opened for writing
    assert _meta(db)["schema_version"] == str(schema.SCHEMA_VERSION)
    assert _leftovers(db) == []
    code, out, _ = _ingest(capsys, db)
    assert code == 0 and json.loads(out)["mode"] == "incremental"


@pytest.fixture
def all_logs(tmp_path, monkeypatch, logs) -> dict[str, Path]:
    """Every provider root is a test folder: Claude Code holds the F2 fixture (`logs`), the others are empty.
    `--rebuild` reads all providers (it takes no `--provider`), so a root left at its default would read the
    user's real ~/.codex or ~/.omp."""
    roots = {"claude_code": logs}
    for provider in pipeline.DEFAULT_ROOTS:
        if provider not in roots:
            roots[provider] = tmp_path / f"empty-{provider}"
            roots[provider].mkdir()
            monkeypatch.setitem(pipeline.DEFAULT_ROOTS, provider, roots[provider])
    assert dict(pipeline.DEFAULT_ROOTS) == roots
    return roots


def test_ingest_rebuild_flag_swaps_a_current_store(tmp_path, all_logs, capsys):
    # D52: `--rebuild` on a store that is already current builds a new store from every provider's logs and
    # renames it over the old one. It loads what a full ingest of the same logs loads and leaves no files.
    db = tmp_path / "s.sqlite"
    code, out, _ = run(capsys, "--db", str(db), "ingest")
    first = json.loads(out)
    assert code == 0 and first["mode"] == "rebuild"  # a missing store is built new
    assert _meta(db)["schema_version"] == str(schema.SCHEMA_VERSION)  # so the next run sees a current store
    code, out, err = run(capsys, "--db", str(db), "ingest", "--rebuild")
    assert (code, err) == (0, "")
    report = json.loads(out)
    assert report["mode"] == "rebuild"
    assert set(report["providers"]) == set(all_logs)  # every provider is read, each from its test folder
    for provider, root in all_logs.items():
        got = report["providers"][provider]
        assert Path(got["root"]) == root, provider
        assert got["rebuilt"] == got["discovered"] == (1 if provider == "claude_code" else 0), provider
    assert report["store"]["loaded"] == first["store"]["loaded"] == 1
    assert _meta(db)["schema_version"] == str(schema.SCHEMA_VERSION)
    assert _leftovers(db) == []


def test_ingest_rebuild_with_provider_is_a_usage_error(tmp_path, capsys):
    code, out, err = _ingest(capsys, tmp_path / "s.sqlite", "--rebuild")
    assert (code, out) == (2, "") and "--rebuild" in err
    assert not (tmp_path / "s.sqlite").exists()


def test_ingest_failure_leaves_the_old_store(tmp_path, logs, monkeypatch, capsys):
    db = tmp_path / "s.sqlite"
    with monkeypatch.context() as m:
        m.setattr(schema, "SCHEMA_VERSION", schema.SCHEMA_VERSION - 1)
        assert _ingest(capsys, db)[0] == 0
    before = db.read_bytes()

    def boom(*a: Any, **kw: Any):
        raise RuntimeError("boom")

    monkeypatch.setattr(ingest, "ingest", boom)
    with pytest.raises(RuntimeError):
        cli.main(["--db", str(db), "ingest", "--provider", "claude_code"])
    assert db.read_bytes() == before and _leftovers(db) == []


def test_paths_prints_store_folder_without_a_store(tmp_path, capsys):
    db = tmp_path / "data" / "botsworth.sqlite"
    assert cli.main(["--db", str(db), "paths"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "data_dir": str(tmp_path / "data"),
        "store": str(db),
    }
    assert not db.parent.exists()
