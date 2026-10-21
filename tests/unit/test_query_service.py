"""botsworth.query on the 51-op hand-built store: one test per design §5.1 row plus the §5 grammar.

Expected numbers are computed by hand from the literals in query_fixture.py.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from query_fixture import D0, DAY, WEEK0, build

import botsworth
from botsworth import pointer
from botsworth.query import QueryError, analytics

SA = "claude_code/sA/session/-"
SB = "codex/sB/session/-"
A_T1 = "claude_code/sA/turn/main#0"
A_T2 = "claude_code/sA/turn/main#1"


@pytest.fixture(scope="module", autouse=True)
def utc_buckets():
    """The fixture's expected day and week buckets are UTC edges: the request zone is UTC (D50)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("BOTSWORTH_TZ", "UTC")
        yield


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d = tmp_path_factory.mktemp("store")
    build(d / "store.db", d / "src")
    return d / "store.db"


@pytest.fixture(scope="module")
def roots(db):
    return {"claude_code": db.parent / "src"}


def Q(db, **q):
    return botsworth.query(q, db=db)


def ids(resp):
    return [r["op"]["op_id"] for r in resp["rows"]]


# ── §5.1 rows ───────────────────────────────────────────────────────────────


def test_why_so_many_tokens_children_then_drill(db):
    r = Q(
        db,
        scope={"op": SA},
        select="children",
        metrics=["tokens.total", "cost_usd"],
        order_by={"metric": "tokens.total", "dir": "desc"},
    )
    assert ids(r) == [A_T1, A_T2, "claude_code/sA/engagement/y#0"]
    assert [x["metrics"]["tokens.total"] for x in r["rows"]] == [7620, 3680, 120]
    assert r["scope_totals"] == {"tokens.total": 11420, "cost_usd": pytest.approx(0.40)}
    assert sum(x["share"] for x in r["rows"]) == pytest.approx(1.0)
    top = Q(db, scope={"op": A_T1}, select="children", metrics=["tokens.total"])
    assert ids(top)[0] == "claude_code/sA/llm_call/req_a2"  # 1500 own + 1180 in the spawned agent
    assert top["rows"][0]["metrics"]["tokens.total"] == 2680
    assert all(x["share"] <= 1 for x in top["rows"])
    shown = botsworth.show(ids(top)[0], db=db)
    assert shown["op"]["tok_total"] == 2680 and shown["op"]["in_tok"] == 120


def test_where_did_time_go(db):
    metrics = ["wall_ms", "active_ms", "busy_sum_ms", "blocked_ms", "idle_ms", "max_parallel"]
    r = Q(
        db,
        scope={"op": SA},
        select="children",
        metrics=metrics,
        order_by={"metric": "wall_ms", "dir": "desc"},
    )
    t1 = r["rows"][0]
    assert t1["op"]["op_id"] == A_T1
    assert t1["metrics"] == {
        "wall_ms": 35000,
        "active_ms": 35000,
        "busy_sum_ms": 40000,
        "blocked_ms": 0,
        "idle_ms": 0,
        "max_parallel": 2,
    }
    for row in r["rows"]:
        m = row["metrics"]
        assert m["busy_sum_ms"] >= m["active_ms"] <= m["wall_ms"]
        assert row["share"] is None  # wall is not additive
    own = Q(db, scope={"op": SA}, select="descendants", measure="own", metrics=["dur_ms"], limit=3)
    assert [x["metrics"]["dur_ms"] for x in own["rows"]] == [21000, 8000, 4000]


def test_most_expensive_last_week(db):
    r = Q(
        db,
        scope={"sessions": {"project": "/p/alpha", "from": WEEK0, "to": WEEK0 + 7 * DAY}},
        select="descendants",
        where=[{"field": "kind", "in": ["session", "engagement", "llm_call"]}],
        metrics=["cost_usd"],
        limit=3,
    )
    assert ids(r) == [SA, SB, "codex/sB/llm_call/resp_b1"]
    assert [x["metrics"]["cost_usd"] for x in r["rows"]] == pytest.approx([0.40, 0.34, 0.20])
    assert r["rows"][1]["op"]["cost_lower_bound"] is True
    assert r["rows"][1]["prov"]["cost_usd"] == "missing"  # lower bound: B2 unpriced
    assert r["cost_lower_bound"] is True
    assert r["scope_totals"]["cost_usd"] == pytest.approx(0.74)


def test_tests_slow_by_command_key(db):
    r = Q(
        db,
        scope={"sessions": {"project": "/p/alpha"}},
        select="descendants",
        measure="own",
        where=[{"field": "kind", "eq": "tool_call"}, {"field": "command_key", "eq": "uv run pytest"}],
        metrics=["dur_ms"],
        group_by=["command_key"],
        agg="distribution",
    )
    [row] = r["rows"]
    assert row["key"] == {"command_key": "uv run pytest"}
    assert row["dist"]["dur_ms"] == {"p50": 8000, "p90": pytest.approx(11200), "max": 12000, "n": 3}
    assert row["counts"]["n"] == 3 and row["counts"]["sessions"] == 2
    drill = Q(
        db,
        scope={"sessions": {"project": "/p/alpha"}},
        select="descendants",
        where=[{"field": "command_norm", "contains": "tests"}],
        metrics=["dur_ms"],
    )
    assert ids(drill) == ["codex/sB/tool_call/call_b1", "claude_code/sA/tool_call/toolu_a1"]


def test_per_session_distribution(db):
    r = Q(db, scope={"all": True}, select="sessions", metrics=["cost_usd", "wall_ms"], agg="distribution")
    [row] = r["rows"]
    assert row["key"] == {}
    assert row["dist"]["cost_usd"] == {
        "p50": pytest.approx(0.275),
        "p90": pytest.approx(0.382),
        "max": pytest.approx(0.40),
        "n": 4,
    }
    assert row["counts"]["n"] == 4 and row["counts"]["sessions"] == 4
    by_model = Q(db, **analytics.distribution(by="model"))
    assert {x["key"]["model"] for x in by_model["rows"]} >= {"gpt-5", "claude-sonnet-4-5"}


def test_outliers_with_top_child(db):
    r = Q(db, **analytics.outliers())
    assert ids(r) == [SA]  # p90 of [.05,.21,.34,.40] = .382
    assert r["rows"][0]["top_child"]["op_id"] == A_T1


def test_getting_slower_trend(db):
    r = Q(db, **analytics.trend(bucket="day", by="model"))
    rows = {(x["key"]["model"], x["bucket"]): x for x in r["rows"]}
    b = rows[("gpt-5", (D0 + DAY) // DAY * DAY)]
    assert b["metrics"]["dur_ms"] == 3000  # p50 of [4000, 2000, 4000, 1000]
    assert b["metrics"]["ttft_ms"] is None and b["prov"]["ttft_ms"] == "missing"
    assert b["prov"]["out_tok_per_s"] == "estimated"
    c = rows[("gpt-5", (D0 + 8 * DAY) // DAY * DAY)]
    assert c["metrics"]["ttft_ms"] == 350 and c["prov"]["ttft_ms"] == "present"
    assert c["prov"]["out_tok_per_s"] == "derived"
    honest = Q(db, **analytics.trend(present_only=True))
    assert {x["key"]["model"] for x in honest["rows"]} == {"gpt-5"}


def test_usage_over_time_week_compare(db):
    r = Q(db, **analytics.usage(bucket="week", by="model"))
    rows = {(x["key"]["model"], x["bucket"]): x for x in r["rows"]}
    w0 = rows[("gpt-5", WEEK0)]
    w1 = rows[("gpt-5", WEEK0 + 7 * DAY)]
    assert w0["metrics"]["cost_usd"] == pytest.approx(0.34)
    assert w1["metrics"]["cost_usd"] == pytest.approx(0.05)
    assert w1["delta"]["cost_usd"] == pytest.approx(-0.29)
    assert w0["delta"]["cost_usd"] is None
    assert all("delta" in x for x in r["rows"])
    assert [x["bucket"] for x in r["rows"]] == sorted(x["bucket"] for x in r["rows"])
    week0 = [x for x in r["rows"] if x["bucket"] == WEEK0]
    assert sum(x["metrics"]["tokens.total"] for x in week0) == 11420 + 9990  # sA calls (dup excluded) + sB


def test_skill_used_events(db):
    r = Q(db, **analytics.skills())
    assert r["rows"][0]["key"] == {"skill": "grill-me"} and r["rows"][0]["counts"]["n"] == 2
    per = Q(
        db,
        scope={"all": True},
        select="events",
        where=[{"field": "skill", "eq": "grill-me"}],
        group_by=["session"],
        bucket={"time": "week"},
    )
    assert {(x["key"]["session"], x["bucket"]) for x in per["rows"]} == {
        ("sA", WEEK0),
        ("sD", WEEK0 + 7 * DAY),
    }
    ev = Q(db, **analytics.skill_events("grill-me"))
    assert [x["event"]["session_id"] for x in ev["rows"]] == ["sA", "sD"]
    assert ev["rows"][0]["event"]["fields"]["via"] == "tool"


def test_compactions(db):
    r = Q(db, **analytics.compactions())
    assert [(x["key"], x["counts"]["n"]) for x in r["rows"]] == [({"session": "sA"}, 1)]
    ev = Q(db, scope={"op": SA}, select="events", where=[{"field": "event_kind", "eq": "compaction"}])
    assert ev["rows"][0]["event"]["fields"]["pre_tokens"] == 150000
    assert botsworth.show(ev["rows"][0]["event"]["op_id"], db=db)["op"]["kind"] == "turn"


def test_steps_of_a_turn(db):
    r = botsworth.steps(A_T1, db=db)
    assert [s["ordinal"] for s in r["steps"]] == [0, 1, 2, 3]
    assert [s["verb"] for s in r["steps"]] == ["run", "spawn", "edit", "answer"]
    assert r["steps"][0]["step_id"] == f"{A_T1}#0"
    assert r["turn"]["op_id"] == A_T1 and r["next_cursor"] is None
    p1 = botsworth.steps(A_T1, limit=3, db=db)
    p2 = botsworth.steps(A_T1, limit=3, cursor=p1["next_cursor"], db=db)
    assert [s["ordinal"] for s in p1["steps"] + p2["steps"]] == [0, 1, 2, 3]
    with pytest.raises(QueryError):
        botsworth.steps(SA, db=db)


# ── §5 grammar ──────────────────────────────────────────────────────────────


def test_range_filter_drills_into_one_bucket(db):
    r = Q(
        db,
        scope={"all": True},
        select="descendants",
        where=[
            {"field": "kind", "eq": "llm_call"},
            {"field": "start_ms", "gte": D0 + DAY, "lt": D0 + 2 * DAY},
        ],
        metrics=["cost_usd"],
        limit=100,
    )
    assert {x["op"]["session_id"] for x in r["rows"]} == {"sB"}
    assert len(r["rows"]) == 5


def test_order_by_start_ms_and_any_metric(db):
    r = Q(
        db,
        scope={"op": A_T1},
        select="children",
        metrics=["cost_usd"],
        order_by={"metric": "start_ms", "dir": "asc"},
    )
    assert [x["op"]["start_ms"] for x in r["rows"]] == sorted(x["op"]["start_ms"] for x in r["rows"])
    r2 = Q(
        db,
        scope={"op": A_T1},
        select="children",
        metrics=["cost_usd"],
        order_by={"metric": "tokens.out", "dir": "desc"},
    )
    assert ids(r2)[0] == "claude_code/sA/llm_call/req_a3"
    assert r2["rows"][0]["share"] == pytest.approx(200 / 460)  # A_T1 incl out: 50 + 80+40+30 + 200 + 60


def test_group_by_provider_kind_and_n_order(db):
    r = Q(
        db,
        scope={"all": True},
        select="descendants",
        group_by=["provider", "kind"],
        metrics=[],
        order_by={"metric": "n", "dir": "desc"},
        limit=100,
    )
    counts = {(x["key"]["provider"], x["key"]["kind"]): x["counts"]["n"] for x in r["rows"]}
    assert (
        counts[("claude_code", "llm_call")] == 9 + 6
    )  # sA: 6 main + 2 Explore + 1 teammate (dup excluded); sD: 6
    assert sum(x["share"] for x in r["rows"]) == pytest.approx(1.0)


def test_duplicates_excluded_except_self(db):
    r = Q(
        db,
        scope={"op": SA},
        select="descendants",
        where=[{"field": "kind", "eq": "llm_call"}],
        metrics=["cost_usd"],
        limit=100,
    )
    assert "claude_code/sA/llm_call/req_a6dup" not in ids(r)
    assert len(r["rows"]) == 9
    me = Q(db, scope={"op": "claude_code/sA/llm_call/req_a6dup"}, select="self", metrics=["cost_usd"])
    assert me["rows"][0]["op"]["duplicate_of"] == "claude_code/sA/llm_call/req_a6"


def test_missing_cost_is_null_with_missing_prov(db):
    r = Q(
        db,
        scope={"op": "codex/sB/llm_call/resp_b2"},
        select="self",
        measure="own",
        metrics=["cost_usd", "tokens.total"],
    )
    row = r["rows"][0]
    assert row["metrics"]["cost_usd"] is None and row["prov"]["cost_usd"] == "missing"
    assert row["metrics"]["tokens.total"] == 3100 and row["prov"]["tokens.total"] == "present"


def test_ratio_metrics(db):
    r = Q(
        db,
        scope={"op": "claude_code/sA/llm_call/req_a1"},
        select="self",
        measure="own",
        metrics=["cache_hit_ratio", "context_tokens", "out_tok_per_s"],
    )
    m = r["rows"][0]["metrics"]
    assert m["context_tokens"] == 1300
    assert m["cache_hit_ratio"] == pytest.approx(1000 / 1300)
    assert m["out_tok_per_s"] == pytest.approx(25.0)


def test_cursor_paging_and_binding(db):
    q = dict(
        scope={"all": True},
        select="descendants",
        where=[{"field": "kind", "eq": "llm_call"}],
        metrics=["cost_usd"],
        limit=5,
    )
    full = botsworth.query({**q, "limit": 100}, db=db)
    pages, cursor = [], None
    while True:
        r = botsworth.query({**q, "cursor": cursor}, db=db)
        pages += ids(r)
        cursor = r["next_cursor"]
        if cursor is None:
            break
    assert len(pages) == 22
    assert pages == ids(full)
    first = botsworth.query(q, db=db)
    with pytest.raises(QueryError, match="different query"):
        botsworth.query({**q, "metrics": ["tokens.total"], "cursor": first["next_cursor"]}, db=db)
    grouped = dict(
        scope={"all": True}, select="descendants", group_by=["kind"], metrics=["cost_usd"], limit=2
    )
    g1 = botsworth.query(grouped, db=db)
    g2 = botsworth.query({**grouped, "cursor": g1["next_cursor"]}, db=db)
    g3 = botsworth.query({**grouped, "cursor": g2["next_cursor"]}, db=db)
    assert len(g1["rows"] + g2["rows"] + g3["rows"]) == 5 and g3["next_cursor"] is None


def test_freshness_on_every_response(db, roots):
    fresh = {
        "last_ingest_ms": D0 + 30 * DAY,
        "parser_version": "fixture-1",
        "price_fingerprint": "fp-1",
        "schema_version": 11,
        "api_version": 1,
    }
    assert Q(db, scope={"all": True}, select="sessions")["freshness"] == fresh
    assert botsworth.show(SA, db=db)["freshness"] == fresh
    assert botsworth.timeline(SA, db=db)["freshness"] == fresh
    assert botsworth.steps(A_T1, db=db)["freshness"] == fresh
    assert botsworth.outline(SA, db=db)["freshness"] == fresh
    assert botsworth.source(SA, db=db, roots=roots)["freshness"] == fresh
    assert botsworth.sessions(db=db)["freshness"] == fresh


def test_show(db):
    r = botsworth.show("claude_code/sA/llm_call/req_x1", db=db)
    assert [a["kind"] for a in r["ancestors"]] == [
        "session",
        "turn",
        "llm_call",
        "tool_call",
        "engagement",
        "turn",
    ]
    assert r["children_counts"] == {"tool_call": 1}
    ev = botsworth.show("claude_code/sA/llm_call/req_a5", db=db)["events"]
    assert [e["kind"] for e in ev] == ["skill_invocation"]


def test_timeline_depth_counts_engagement_nesting(db):
    d0 = {o["op_id"] for o in botsworth.timeline(SA, depth=0, db=db)["ops"]}
    d1 = {o["op_id"] for o in botsworth.timeline(SA, depth=1, db=db)["ops"]}
    assert "claude_code/sA/engagement/x#0" not in d0 and "claude_code/sA/tool_call/toolu_a2" in d0
    assert {"claude_code/sA/engagement/x#0", "claude_code/sA/llm_call/req_x1"} <= d1
    assert "claude_code/sA/llm_call/req_a6dup" not in d1
    t = botsworth.timeline(SA, depth=3, db=db)
    assert all(o["end_ms"] >= o["start_ms"] for o in t["ops"])
    assert [e["kind"] for e in t["events"]] == ["compaction", "skill_invocation"]


def test_source_pages_through_pointer(db, roots, tmp_path):
    r = botsworth.source(SA, db=db, roots=roots)
    assert "hello world" in r["text"] and r["drift"] is False and r["locator"] == "L1-L1"
    t = botsworth.source(SA, part="text", max_bytes=5, db=db, roots=roots)
    assert t["text"] == "hello" and t["total_bytes"] == 11
    src = pointer.format("claude_code", "proj/sA.jsonl", pointer.Lines(2, 3), "message.content.0")
    # DM-7: the input of a Bash call is its command, not the JSON of the input object
    assert botsworth.source(src, part="input", db=db, roots=roots)["text"] == "uv run pytest"
    assert botsworth.source(src, part="output", db=db, roots=roots)["text"] == "3 failed"
    with pytest.raises(QueryError):
        botsworth.source(src, part="bogus", db=db, roots=roots)
    with pytest.raises(pointer.SourceMissing):
        botsworth.source("src://claude_code/proj/gone.jsonl#L1-L1", db=db, roots=roots)


def test_sessions_and_projects_listings(db):
    s = botsworth.sessions(project="/p/alpha", db=db)["sessions"]
    assert [x["session_id"] for x in s] == ["sB", "sA"]
    assert s[1]["title"] == "alpha fix" and s[1]["git_branch"] == "main"
    listing = botsworth.projects(db=db)
    assert listing["freshness"] == botsworth.sessions(db=db)["freshness"]
    p = {x["project"]: x for x in listing["projects"]}
    assert p["/p/alpha"]["n_sessions"] == 2 and p["/p/alpha"]["providers"] == ["claude_code", "codex"]
    assert (p["/p/alpha"]["project_name"], p["/p/alpha"]["n_automated"]) == ("alpha", 0)
    assert p["/p/beta"]["incl_cost_usd"] == pytest.approx(0.26)


def test_connection_is_left_open(db):
    conn = sqlite3.connect(db)
    botsworth.query({"scope": {"all": True}, "select": "sessions"}, db=conn)
    assert conn.execute("SELECT 1").fetchone()[0] == 1
    conn.close()


@pytest.mark.parametrize(
    "q, msg",
    [
        ({"scope": {"all": True}}, "scope and select"),
        ({"scope": {"all": True}, "select": "x"}, "select"),
        ({"scope": {"op": "x", "all": True}, "select": "self"}, "exactly one"),
        ({"scope": {"all": True}, "select": "self", "bogus": 1}, "unknown query keys"),
        ({"scope": {"all": True}, "select": "self", "metrics": ["nope"]}, "not available"),
        ({"scope": {"all": True}, "select": "self", "group_by": ["verb"]}, "not available"),
        (
            {"scope": {"all": True}, "select": "self", "where": [{"field": "x;drop", "eq": 1}]},
            "unknown where field",
        ),
        (
            {"scope": {"all": True}, "select": "self", "where": [{"field": "kind", "eq": 1, "in": [1]}]},
            "combine",
        ),
        ({"scope": {"all": True}, "select": "self", "bucket": {"time": "5h"}}, "rolling"),
        ({"scope": {"all": True}, "select": "self", "compare": "previous_bucket"}, "needs bucket"),
        ({"scope": {"all": True}, "select": "self", "outliers": {"over": "p90"}, "agg": "sum"}, "op rows"),
        ({"scope": {"all": True}, "select": "self", "limit": 0}, "limit"),
        ({"scope": {"all": True}, "select": "self", "order_by": {"metric": "n"}}, "needs group_by"),
        ({"scope": {"op": "nope"}, "select": "self"}, "unknown id"),
        ({"scope": {"all": True}, "select": "self", "cursor": "!!"}, "malformed cursor"),
    ],
)
def test_invalid_queries_raise_query_error(db, q, msg):
    with pytest.raises(QueryError, match=msg):
        botsworth.query(q, db=db)


def test_missing_store_is_a_query_error(tmp_path):
    with pytest.raises(QueryError, match="no store"):
        botsworth.query({"scope": {"all": True}, "select": "sessions"}, db=tmp_path / "none.db")


ALPHA_WEEK = {"sessions": {"project": "/p/alpha", "from": WEEK0, "to": WEEK0 + 7 * DAY}}
Q1_Q19 = {
    "Q1": lambda db: Q(
        db,
        scope={"op": SA},
        select="children",
        metrics=["tokens.total", "tokens.in", "tokens.out", "tokens.cache_read", "tokens.cache_write"],
        limit=500,
    ),
    "Q2": lambda db: Q(
        db, scope={"op": SA}, select="descendants", where=[{"field": "kind", "eq": "llm_call"}]
    ),
    "Q3": lambda db: botsworth.timeline(SA, db=db),
    "Q4": lambda db: Q(
        db, scope=ALPHA_WEEK, select="descendants", where=[{"field": "kind", "eq": "llm_call"}], limit=10
    ),
    "Q5": lambda db: Q(db, scope={"all": True}, select="sessions", limit=10),
    "Q6": lambda db: Q(db, **analytics.by_model()),
    "Q7": lambda db: [
        Q(
            db,
            scope={"all": True},
            select="descendants",
            measure="own",
            where=[{"field": "kind", "eq": "tool_call"}],
            metrics=["dur_ms"],
            group_by=["tool_name"],
            agg=a,
        )
        for a in ("sum", "count", "max")
    ],
    "Q8": lambda db: Q(
        db,
        scope={"sessions": {"project": "/p/alpha"}},
        select="descendants",
        where=[
            {"field": "tool_name", "in": ["Bash", "exec_command"]},
            {"field": "command", "contains": "pytest"},
        ],
        metrics=["dur_ms"],
    ),
    "Q9": lambda db: botsworth.show("claude_code/sA/tool_call/toolu_a1", db=db),
    "Q10": lambda db: Q(db, scope={"op": "claude_code/sA/engagement/x#0"}, select="children"),
    "Q11": lambda db: Q(db, **analytics.distribution(project="/p/alpha")),
    "Q12": lambda db: Q(db, **analytics.outliers(project="/p/alpha")),
    "Q13": lambda db: Q(db, **analytics.trend()),
    "Q14": lambda db: Q(db, **analytics.usage()),
    "Q15": lambda db: Q(db, **analytics.skills()),
    "Q16": lambda db: Q(db, **analytics.commands(project="/p/alpha")),
    "Q17": lambda db: Q(db, **analytics.compactions()),
    "Q18": lambda db: botsworth.steps(A_T1, db=db),
    "Q19": lambda db: [
        botsworth.steps(r["event"]["fields"]["step_id"].rpartition("#")[0], db=db)
        for r in Q(db, **analytics.skill_events("grill-me"))["rows"]
        if "step_id" in r["event"]["fields"]
    ],
}


@pytest.mark.parametrize("name", list(Q1_Q19))
def test_q1_to_q19_run_and_return_rows(db, name):
    out = Q1_Q19[name](db)
    for r in out if isinstance(out, list) else [out]:
        assert "freshness" in r
        assert r.get("rows") or r.get("ops") or r.get("steps") or r.get("op"), name


# ── T6 contract items: NotFound, session rows, event order alias, segments, store choice ──


@pytest.mark.parametrize(
    "call",
    [
        lambda db: botsworth.show("claude_code/sA/llm_call/nope", db=db),
        lambda db: botsworth.timeline("nope", db=db),
        lambda db: botsworth.steps(f"{A_T1}#99", db=db),
        lambda db: botsworth.source("nope", db=db),
        lambda db: botsworth.outline("sA:T9", db=db),
        lambda db: botsworth.show("nope:T1", db=db),
        lambda db: botsworth.query({"scope": {"op": "nope"}, "select": "self"}, db=db),
    ],
)
def test_unknown_ids_raise_not_found(db, call):
    with pytest.raises(botsworth.NotFound):
        call(db)


def test_bad_grammar_is_not_not_found(db):
    with pytest.raises(QueryError) as exc:
        botsworth.show("sA:T1/1", db=db)
    assert not isinstance(exc.value, botsworth.NotFound)


def test_select_sessions_rows_carry_session_table_fields(db):
    r = Q(
        db,
        scope={"sessions": {"project": "/p/alpha"}},
        select="sessions",
        order_by={"metric": "start_ms", "dir": "asc"},
    )
    op = r["rows"][0]["op"]
    assert (op["session_id"], op["title"], op["git_branch"], op["harness_version"], op["status"]) == (
        "sA", "alpha fix", "main", "1.0", "settled",
    )  # fmt: skip
    plain = Q(db, scope={"op": SA}, select="self")["rows"][0]["op"]
    assert "title" not in plain


def test_events_order_by_start_ms_is_ts_ms(db):
    r = Q(db, scope={"all": True}, select="events", order_by={"metric": "start_ms", "dir": "desc"})
    ts = [x["event"]["ts_ms"] for x in r["rows"]]
    assert ts == sorted(ts, reverse=True) and len(ts) == 5


def test_timeline_container_ops_carry_segments(db):
    t = botsworth.timeline(SA, db=db)
    turn = next(o for o in t["ops"] if o["op_id"] == A_T1)
    assert turn["segments"] == [[turn["start_ms"], turn["end_ms"]]]
    assert all(o["segments"] is None for o in t["ops"] if o["kind"] in ("llm_call", "tool_call"))
    assert t["root"]["segments"] is not None


def test_store_choice_goes_through_location(db):
    botsworth.configure(db)
    try:
        assert botsworth.query({"scope": {"all": True}, "select": "sessions"})["rows"]
        botsworth.configure(db.parent / "none.sqlite")
        with pytest.raises(QueryError, match="no store"):
            botsworth.show(SA)
        assert botsworth.show(SA, db=db)["op"]["op_id"] == SA  # explicit db= wins
    finally:
        botsworth.configure(None)


def test_source_missing_is_reexported_from_query():
    from botsworth.query import SourceMissing

    assert SourceMissing is pointer.SourceMissing


def test_copied_events_never_count(db):
    r = Q(db, scope={"all": True}, select="events", where=[{"field": "kind", "eq": "api_error"}])
    assert [x["event"]["session_id"] for x in r["rows"]] == ["sC"]  # sA's copy on req_a6dup excluded
    g = Q(db, scope={"op": SA}, select="events", group_by=["kind"])
    assert {x["key"]["kind"]: x["counts"]["n"] for x in g["rows"]} == {"compaction": 1, "skill_invocation": 1}
    assert "api_error" not in [e["kind"] for e in botsworth.timeline(SA, db=db)["events"]]
    assert [e["kind"] for e in botsworth.show("claude_code/sA/llm_call/req_a6dup", db=db)["events"]] == [
        "api_error"
    ]


def test_plain_op_rows_do_not_carry_session_fields(db):
    shown = botsworth.show(SA, db=db)["op"]
    assert "title" not in shown and "git_branch" not in shown


def _r9(v):
    return round(v, 9) if isinstance(v, float) else v


@pytest.mark.parametrize(
    "q",
    [
        {**analytics.by_model(), "metrics": ["cost_usd"]},
        {**analytics.usage(bucket="week", by="model"), "metrics": ["cost_usd"]},
        {**analytics.usage(bucket="day", by="project", project="/p/alpha"), "metrics": ["cost_usd"]},
        {**analytics.by_model(), "group_by": ["provider", "account"], "metrics": ["cost_usd"]},
        analytics.by_model(),
        analytics.usage(bucket="day", by="model"),
        {
            **analytics.by_model(),
            "group_by": ["provider"],
            "metrics": [
                *(f"tokens.{k}" for k in ("in", "out", "cache_read", "cache_write", "reasoning")),
                "llm_ms",
            ],
        },
    ],
)
def test_rollup_fast_path_equals_the_op_scan(db, q, monkeypatch):
    from botsworth.query import service

    used = []
    real = service._rollup_sql
    monkeypatch.setattr(service, "_rollup_sql", lambda *a: used.append(real(*a)) or used[-1])
    fast = botsworth.query(q, db=db)
    assert used[-1] is not None  # the rollup path served it
    # an extra no-op predicate makes the query ineligible for rollup_time, forcing the op scan
    slow = botsworth.query({**q, "where": [*q["where"], {"field": "start_ms", "gte": 0}]}, db=db)

    def norm(resp):
        return sorted(
            (json.dumps(r["key"], sort_keys=True), r.get("bucket"), r["counts"]["n"], r["counts"]["sessions"],
             tuple((m, round(v, 9) if isinstance(v, float) else v) for m, v in sorted(r["metrics"].items())),
             tuple(sorted(r["prov"].items())),
             json.dumps({k: _r9(v) for k, v in (r.get("delta") or {}).items()}, sort_keys=True))
            for r in resp["rows"]
        )  # fmt: skip

    assert fast["rows"] and norm(fast) == norm(slow)


def test_limit_cap_is_10000_default_50(db):
    q = {"scope": {"all": True}, "select": "descendants"}
    assert (
        len(botsworth.query({**q, "limit": 10_000}, db=db)["rows"]) == 51 - 1
    )  # all live ops (dup excluded)
    assert len(botsworth.query(q, db=db)["rows"]) == 50
    with pytest.raises(QueryError, match="1..10000"):
        botsworth.query({**q, "limit": 10_001}, db=db)


# ── D50: bucket.time edges are local in the request zone `tz` ────────────────


def test_bucket_day_follows_the_request_zone(db):
    """The rollup fast path and the ops scan both cut days at local midnight of `tz`."""
    base = {"scope": {"all": True}, "select": "descendants", "measure": "own", "metrics": ["cost_usd"]}
    base["where"] = [{"field": "kind", "eq": "llm_call"}]
    kolkata = Q(db, **base, bucket={"time": "day"}, tz="Asia/Kolkata")
    utc = Q(db, **base, bucket={"time": "day"}, tz="UTC")
    assert {r["bucket"] % (15 * 60_000) for r in kolkata["rows"]} == {0}
    assert {r["bucket"] for r in kolkata["rows"]} != {r["bucket"] for r in utc["rows"]}
    assert all(b % DAY == 0 for b in (r["bucket"] for r in utc["rows"]))
    assert all((b + 19_800_000) % DAY == 0 for b in (r["bucket"] for r in kolkata["rows"]))
    total = lambda r: sum(x["metrics"]["cost_usd"] for x in r["rows"])  # noqa: E731
    assert total(kolkata) == pytest.approx(total(utc))
    ops_path = Q(db, **{**base, "measure": "incl"}, bucket={"time": "day"}, tz="Asia/Kolkata")
    assert all((b + 19_800_000) % DAY == 0 for b in (r["bucket"] for r in ops_path["rows"]))


def test_bucket_week_starts_on_local_monday_and_compare_uses_the_previous_local_week(db):
    r = Q(db, **analytics.usage(bucket="week", by="model"), tz="Asia/Kolkata")
    assert all((x["bucket"] + 19_800_000 - WEEK0) % (7 * DAY) == 0 for x in r["rows"])
    r = Q(
        db,
        scope={"all": True},
        select="descendants",
        metrics=["cost_usd"],
        bucket={"time": "hour"},
        tz="Asia/Kathmandu",
    )
    assert {x["bucket"] % 900_000 for x in r["rows"]} == {0}
    assert {(x["bucket"] + 20_700_000) % 3_600_000 for x in r["rows"]} == {0}


def test_unknown_zone_is_a_query_error(db):
    with pytest.raises(QueryError, match="unknown time zone"):
        Q(db, scope={"all": True}, select="sessions", tz="Not/AZone")
