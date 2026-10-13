"""T6 cli.main: each §6.2 subcommand is one API call; --json payload equals the API payload."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from fake_query import FakeQuery
from query_fixture import build

import botsworth
from botsworth import pointer
from botsworth.cli import main as cli
from botsworth.cli.render import TABLE_BYTES
from botsworth.store import location


@pytest.fixture
def dbs(tmp_path) -> tuple[Path, Path]:
    """(env db, --db db), both existing files."""
    env_db, cli_db = tmp_path / "env.sqlite", tmp_path / "cli.sqlite"
    env_db.touch()
    cli_db.touch()
    return env_db, cli_db


@pytest.fixture
def fake(monkeypatch, dbs) -> FakeQuery:
    # the real D20 resolver (store.location): no override, env set, so precedence is observable
    location.configure(None)
    monkeypatch.setenv("BOTSWORTH_DB", str(dbs[0]))
    return FakeQuery()


def run(fake: FakeQuery, capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv), api=fake)  # type: ignore[arg-type]
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["outline", "T12", "--depth", "2"], ("outline", ("T12",), {"depth": 2})),
        (["show", "s1"], ("show", ("s1",), {})),
        (["steps", "t1", "--limit", "5"], ("steps", ("t1",), {"limit": 5, "cursor": None})),
        (["timeline", "s1"], ("timeline", ("s1",), {"depth": 3})),
        (
            ["source", "T12.3", "--part", "output", "--max-bytes", "4", "--offset", "2"],
            ("source", ("T12.3",), {"part": "output", "offset": 2, "max_bytes": 4}),
        ),
    ],
)
def test_direct_subcommands_round_trip(fake, capsys, argv, expected):
    code, out, _ = run(fake, capsys, *argv, "--json")
    assert code == 0
    assert fake.calls == [expected]
    name, args, kw = expected
    assert json.loads(out) == getattr(FakeQuery(), name)(*args, **kw)


def test_query_prints_json_by_default_equal_to_api(fake, capsys):
    q = {"scope": {"all": True}, "select": "sessions"}
    code, out, _ = run(fake, capsys, "query", json.dumps(q))
    assert code == 0
    assert fake.calls == [("query", (q,), {})]
    assert json.loads(out) == FakeQuery().query(q)


def test_query_reads_stdin(fake, capsys, monkeypatch):
    monkeypatch.setattr(
        sys, "stdin", __import__("io").StringIO('{"scope": {"all": true}, "select": "steps"}')
    )
    assert run(fake, capsys, "query", "-")[0] == 0
    assert fake.calls[0][1][0]["select"] == "steps"


@pytest.mark.parametrize(
    "argv,expected",
    [
        (
            [
                "top",
                "--scope",
                "S",
                "--kind",
                "llm_call",
                "--metric",
                "cost_usd",
                "--own",
                "--where",
                "model=x",
                "-n",
                "5",
            ],
            {
                "scope": {"op": "S"},
                "select": "descendants",
                "where": [{"field": "kind", "in": ["llm_call"]}, {"field": "model", "eq": "x"}],
                "measure": "own",
                "metrics": ["cost_usd"],
                "order_by": {"metric": "cost_usd", "dir": "desc"},
                "limit": 5,
            },
        ),
        (
            ["children", "S"],
            {
                "scope": {"op": "S"},
                "select": "children",
                "measure": "incl",
                "metrics": ["cost_usd", "tokens.total", "wall_ms", "active_ms"],
                "order_by": {"metric": "start_ms", "dir": "asc"},
                "limit": 50,
            },
        ),
        (["limits", "--account", "a"], None),
        (["skills", "--skill", "grill-me"], None),
    ],
)
def test_query_builders(fake, capsys, argv, expected):
    assert run(fake, capsys, *argv)[0] == 0
    ((name, (q,), _),) = fake.calls
    assert name == "query"
    if expected:
        assert q == expected
    else:
        fields = {w["field"] for w in q["where"]}
        assert "event_kind" in fields and q["select"] == "events"


def test_since_and_filters_build_session_scope(fake, capsys, monkeypatch):
    monkeypatch.setattr(cli.time, "time", lambda: 10 * 86_400)
    run(fake, capsys, "stats", "--project", "P", "--since", "7d")
    run(fake, capsys, "stats", "--project", "P", "--by", "tool")
    run(fake, capsys, "trend", "--by", "model", "--bucket", "week")
    run(fake, capsys, "outliers", "--provider", "codex")
    sess, stats, trend, outl = (c[1][0] for c in fake.calls)
    assert sess["scope"] == {"sessions": {"project": "P", "from": 3 * 86_400_000}}
    assert sess["select"] == "sessions" and sess["agg"] == "distribution"
    assert stats["agg"] == "distribution" and stats["group_by"] == ["tool_name"]
    assert trend["bucket"] == {"time": "week"} and trend["group_by"] == ["model"]
    assert trend["where"][0] == {"field": "kind", "in": ["llm_call"]}
    assert outl["outliers"] == {"over": "p90", "with_top_child": True}
    assert outl["scope"]["sessions"]["provider"] == "codex"


@pytest.mark.parametrize(
    "argv",
    [
        ["query", '{"select": "bad"}'],
        ["query", "{not json"],
        ["show", "missing-1"],
        ["outline", "missing-1"],
        ["source", "gone-1"],
        ["top", "--kind", "turn", "--where", "novalue"],
    ],
)
def test_errors_exit_1_with_one_line(fake, capsys, argv):
    code, out, err = run(fake, capsys, *argv)
    assert code == 1 and out == ""
    assert err.startswith("error: ") and err.count("\n") == 1


def test_bad_since_is_usage_error(fake, capsys):
    with pytest.raises(SystemExit) as e:
        run(fake, capsys, "sessions", "--since", "7x")
    assert e.value.code == 2


def test_tables_are_capped_at_2kb(fake, capsys, monkeypatch):
    rows = [
        {"op": {"op_id": f"op{i}", "kind": "turn", "label": "x" * 50}, "metrics": {"cost_usd": 0.1}}
        for i in range(200)
    ]
    monkeypatch.setattr(
        fake, "query", lambda q, db=None: {"rows": rows, "scope_totals": {}, "next_cursor": None}
    )
    code, out, _ = run(fake, capsys, "top", "--kind", "turn", "--table")
    assert code == 0
    assert len(out.encode()) <= TABLE_BYTES
    assert "more lines (use --json)" in out


def test_outline_table_format(fake, capsys):
    code, out, _ = run(fake, capsys, "outline", "T12", "--table")
    assert code == 0
    assert out.splitlines() == ["T12.2  run  pytest  exit:1  4.2s", "T12  turn  Fixed  ok  41.0s  $0.3800"]


def test_db_flag_is_resolved_and_passed_to_every_call(fake, capsys, dbs):
    env_db, cli_db = dbs
    run(fake, capsys, "--db", str(cli_db), "show", "s1")
    run(fake, capsys, "--db", str(cli_db), "limits")
    assert fake.dbs == {cli_db}
    run(fake, capsys, "outline", "s1:T12")
    assert fake.dbs == {cli_db, env_db}


def test_missing_store_exits_2_except_ingest(fake, capsys, monkeypatch, tmp_path):
    gone = str(tmp_path / "none.sqlite")
    code, out, err = run(fake, capsys, "--db", gone, "show", "s1")
    assert (code, out) == (2, "")
    assert err == f"error: no store at {gone} — run 'botsworth ingest'\n"
    assert fake.calls == []


# ── gate: every §6.2 subcommand on T5's fixture store with the real query layer ─

SA = "claude_code/sA/session/-"
A_T1 = "claude_code/sA/turn/main#0"
STREAMS = Path(__file__).parent / "store_streams"


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("store")
    build(d / "store.db", d / "src")
    mp = pytest.MonkeyPatch()
    mp.setitem(pointer.DEFAULT_ROOTS, "claude_code", d / "src")
    yield d / "store.db"
    mp.undo()


def real(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


GATE = [
    ["outline", SA, "--depth", "2"],
    ["outline", "sA:T1"],
    ["show", SA],
    ["steps", A_T1, "--limit", "3"],
    ["timeline", SA, "--depth", "2"],
    ["source", A_T1, "--part", "text", "--max-bytes", "5"],
    ["children", SA],
    ["top", "--kind", "llm_call", "--metric", "cost_usd", "-n", "3"],
    ["top", "--scope", SA, "--kind", "tool_call", "--metric", "dur_ms", "--own", "--where", "tool_name=Bash"],
    ["stats", "--since", "3650d", "--by", "model"],
    ["outliers", "--since", "3650d"],
    ["trend", "--since", "3650d", "--bucket", "week"],
    ["limits"],
    ["skills", "--skill", "grill-me"],
]

DIRECT = {
    "outline": lambda a, db: botsworth.outline(a.id, a.depth, db=db),
    "show": lambda a, db: botsworth.show(a.id, db=db),
    "steps": lambda a, db: botsworth.steps(a.id, a.limit, a.cursor, db=db),
    "timeline": lambda a, db: botsworth.timeline(a.id, a.depth, db=db),
    "source": lambda a, db: botsworth.source(a.id, a.part, a.offset, a.max_bytes, db=db),
}


@pytest.mark.parametrize("argv", GATE, ids=lambda a: " ".join(a[:2]))
def test_gate_subcommand_json_equals_api(store, capsys, argv):
    code, out, err = real(capsys, "--db", str(store), *argv, "--json")
    assert (code, err) == (0, "")
    args = cli.build_parser().parse_args(argv)
    builder = cli.QUERY_BUILDERS.get(args.cmd)
    direct = botsworth.query(builder(args), db=store) if builder else DIRECT[args.cmd](args, store)
    got = json.loads(out)
    want = json.loads(json.dumps(direct, default=str))
    if args.cmd in ("stats", "outliers", "trend"):  # --since is relative to now
        got.pop("freshness"), want.pop("freshness")
    assert got == want
    code, table, _ = real(capsys, "--db", str(store), *argv, "--table")
    assert code == 0 and len(table.encode()) <= TABLE_BYTES


def test_gate_query_subcommand_equals_api(store, capsys):
    q = {"scope": {"op": SA}, "select": "children"}
    code, out, _ = real(capsys, "--db", str(store), "query", json.dumps(q))
    assert code == 0 and json.loads(out) == json.loads(json.dumps(botsworth.query(q, db=store)))


@pytest.mark.parametrize(
    "argv",
    [
        ["show", "nope-op"],  # NotFound
        ["outline", "sA:T99"],  # NotFound (display id)
        ["steps", SA],  # QueryError: a session is not a turn
        ["query", '{"scope": {"all": true}, "select": "bogus"}'],
    ],
)
def test_gate_errors_exit_1(store, capsys, argv):
    code, out, err = real(capsys, "--db", str(store), *argv)
    assert (code, out) == (1, "")
    assert err.startswith("error: ") and err.count("\n") == 1


def test_gate_source_pages_through_a_pointer(store, capsys):
    whole = json.loads(real(capsys, "--db", str(store), "source", A_T1, "--json")[1])
    text, offset = "", 0
    while offset < whole["total_bytes"]:
        argv = ["source", A_T1, "--offset", str(offset), "--max-bytes", "7", "--json"]
        page = json.loads(real(capsys, "--db", str(store), *argv)[1])
        text += page["text"]
        offset += 7
    assert text == whole["text"] and whole["total_bytes"] > 7


def test_gate_ingest_then_read(tmp_path, capsys, monkeypatch):
    root = tmp_path / "claude"
    shutil.copytree(Path(__file__).parents[1] / "fixtures" / "F2" / "project", root / "project")
    monkeypatch.setitem(pointer.DEFAULT_ROOTS, "claude_code", root)
    db = str(tmp_path / "data" / "s.sqlite")
    code, out, err = real(capsys, "--db", db, "ingest", "--provider", "claude_code")
    assert (code, err) == (0, "")
    result = json.loads(out)
    assert result["store"]["bundles_changed"] == 1 and result["store"]["rejected"] == []
    code, out, _ = real(capsys, "--db", db, "top", "--kind", "session", "--json")
    assert code == 0 and json.loads(out)["rows"]
    code, out, _ = real(capsys, "--db", db, "ingest", "--provider", "claude_code")
    assert code == 0 and json.loads(out)["store"]["bundles_changed"] == 0  # unchanged logs: a no-op run
    assert sorted(p.name for p in (tmp_path / "data").iterdir() if not p.name.startswith("s.sqlite")) == []


def test_ingest_runs_the_pipeline(tmp_path, capsys, monkeypatch):
    from botsworth import pipeline
    from botsworth.store import ingest

    calls = []

    def run(providers, **kw):
        calls.append((providers, kw))
        if len(calls) == 2:
            raise ingest.IngestLocked("another ingest holds x.lock")
        return {"providers": {}, "store": {}, "ms": {}}

    monkeypatch.setattr(pipeline, "run", run)
    db = str(tmp_path / "s.sqlite")
    code, out, _ = real(capsys, "--db", db, "ingest", "--provider", "omp", "--force")
    assert code == 0 and json.loads(out) == {"providers": {}, "store": {}, "ms": {}}
    assert calls == [(["omp"], {"force": True, "reprice": False, "rebuild": False, "db": Path(db)})]
    code, out, err = real(capsys, "--db", db, "ingest")
    assert (code, out, err) == (1, "", "error: another ingest holds x.lock\n")
    code, out, err = real(capsys, "--db", db, "ingest", "--rebuild", "--provider", "omp")
    assert (code, out) == (2, "") and "--rebuild" in err and len(calls) == 2  # a partial rebuild is refused
    code, _, _ = real(capsys, "--db", db, "ingest", "--rebuild")
    assert code == 0 and calls[2] == (
        None,
        {"force": False, "reprice": False, "rebuild": True, "db": Path(db)},
    )


def test_gate_missing_store_exit_2(tmp_path, capsys):
    code, out, err = real(capsys, "--db", str(tmp_path / "none.sqlite"), "sessions")
    assert (code, out) == (2, "") and "run 'botsworth ingest'" in err
