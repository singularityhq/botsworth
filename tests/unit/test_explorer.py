# ruff: noqa: F811
"""Q1 explorer views (QRY-3..QRY-16, QRY-23..QRY-28, TST-3 items 1-3 and 8, TST-4) on the QF store.

Expected numbers come from tests/fixtures/qstore/expected.json and the timeline in make.py (local days are
Asia/Kolkata). The fixture days: 6 Oct s2day 3.00 (first call), sunpriced 3.00 (one unpriced), sauto 0.50
(automated); 7 Oct s2day 5.00, sslow1 1.00, sslow2 0.50; 8 Oct sagents 4.70, sharness 0.60; 9 Oct stools 0.90,
sfork 0.20, sskills 1.30; 10 Oct sstretch 1.40, somp 0.50, scodex 0.15. szero has no llm call.
"""

from __future__ import annotations

import sqlite3

import pytest
from explorer_store import xstore, xstore_null_tok, xstore_tie  # noqa: F401  (pytest fixtures)
from qstore import expected

from botsworth.query import QueryError, wire
from botsworth.query.scope import Scope
from botsworth.query.views import explorer as ex

COSTS = {k: v["incl_cost_usd"] for k, v in expected()["sessions"].items()}
BIZ, AGENTS, APP, IDLE = "/u/a/bizdemo/src", "/u/e/agentsdemo", "/u/h/slowproj/app", "/u/i/idleproj"
SKILL, HARNESS, OMP, CODEX, HELPER = (
    "/u/g/skillproj",
    "/u/f/harnessproj",
    "/u/j/ompproj",
    "/u/k/codexproj",
    "/u/d/helper",
)
IN_SCOPE = ["s2day", "sagents", "scodex", "sfork", "sharness", "somp", "sskills", "sslow1", "sslow2",
            "sstretch", "stools", "sunpriced"]  # fmt: skip


def ids(page: wire.SessionPage) -> list[str]:
    return [r.session_id for r in page.rows]


def listed(db, scope=None, **kw) -> list[str]:
    return sorted(ids(ex.list_sessions(scope or Scope(), db=db, limit=1000, **kw)))


# ── validation (no store state needed) ───────────────────────────────────────


@pytest.mark.parametrize(
    "kw",
    [
        {"sort": "model"},
        {"dir": "up"},
        {"group": "week"},
        {"group_key": "x"},
        {"offset": -1},
        {"limit": 1001},
        {"limit": 0},
        {"group": "day", "group_limit": 51},
    ],
)
def test_list_sessions_rejects_bad_arguments(qstore, kw):
    with pytest.raises(QueryError):
        ex.list_sessions(Scope(), db=qstore, **kw)


def test_day_series_window_validation(qstore):
    with pytest.raises(QueryError, match="400"):
        ex.day_series(Scope(), "2025-01-01", "2026-02-05", db=qstore)  # 401 days
    with pytest.raises(QueryError):
        ex.day_series(Scope(), "2026-10-09", "2026-10-08", db=qstore)
    with pytest.raises(QueryError):
        ex.day_series(Scope(), "2026-10-09", "2026-13-01", db=qstore)
    with pytest.raises(QueryError):
        ex.day_series(Scope(), "2026-10-01", "2026-10-02", bucket="month", db=qstore)


def test_day_series_zero_days_and_window_limit(xstore):
    series = ex.day_series(Scope(), "2026-10-05", "2026-10-14", db=xstore)
    assert [b.day for b in series.days] == [f"2026-10-{d:02d}" for d in range(5, 15)]
    zero = {b.day: b for b in series.days}
    for day in ("2026-10-05", "2026-10-11", "2026-10-14"):
        b = zero[day]
        assert (b.cost_usd, b.tok_total, b.n_sessions, b.by_project, b.cost_lower_bound) == (
            0,
            0,
            0,
            [],
            False,
        )
        assert b.from_day == b.to_day == day
    assert len(ex.day_series(Scope(), "2025-10-01", "2026-11-04", db=xstore).days) == 400  # the limit itself


# ── membership and full vs day figures (QRY-5, QRY-9, QRY-10) ────────────────


def test_membership_by_llm_day(xstore):
    # s2day has its second call on 7 Oct (a day row with n_llm_calls > 0), though it started on 6 Oct
    assert listed(xstore, Scope("2026-10-07", "2026-10-07")) == ["s2day", "sslow1", "sslow2"]
    assert "s2day" not in listed(xstore, Scope("2026-10-08", "2026-10-09"))
    # szero has turns and no llm_call: it is in no scope, all time included (DM-54)
    assert "szero" not in listed(xstore)
    assert "szero" not in listed(xstore, Scope(show_automated=True))
    assert listed(xstore) == IN_SCOPE


def test_full_figures_vs_day_split(xstore):
    page = ex.list_sessions(Scope("2026-10-07", "2026-10-07"), db=xstore)
    row = next(r for r in page.rows if r.session_id == "s2day")
    assert row.cost_usd == pytest.approx(8.00)
    assert [(d.day, d.cost_usd) for d in row.day_split] == [("2026-10-06", 3.0), ("2026-10-07", 5.0)]
    assert [d.tok_total for d in row.day_split] == [1200, 1200]
    series = ex.day_series(Scope(), "2026-10-07", "2026-10-07", db=xstore)
    (bar,) = series.days
    by_project = {p.project: p for p in bar.by_project}
    assert by_project[BIZ].cost_usd == pytest.approx(5.00)  # s2day's share on that day only
    assert bar.cost_usd == pytest.approx(5.00 + 1.00 + 0.50)
    summary = ex.period_summary(Scope("2026-10-07", "2026-10-07"), db=xstore)
    assert summary.totals.cost_usd == pytest.approx(8.00 + 1.00 + 0.50)  # EXP-2: the full 8.00
    assert row.ends_after_days == 1  # 22:29 IST on 6 Oct to 00:31 IST on 7 Oct crosses one local midnight


def test_session_row_fields(xstore):
    page = ex.list_sessions(Scope(), db=xstore, limit=1000)
    row = next(r for r in page.rows if r.session_id == "sslow1")
    assert row.session == "claude_code/sslow1/session/-"
    assert (row.name, row.title_source) == ("benchmark the slow ingest path and report", "first_ask")
    assert (row.project, row.project_name, row.color_slot) == (APP, "app", 3)
    assert (row.wall_ms, row.provider, row.is_automated, row.git_branch) == (
        3600000,
        "claude_code",
        False,
        None,
    )
    assert (
        row.start_ms < row.end_ms
        and row.activity.bin_ms > 0
        and sum(row.activity.tok) == row.tok_total == 240
    )
    agents = {r.session_id: r.n_agents for r in page.rows}
    # n_agents = distinct agent_id over the session's engagements (interfaces.md SES-24): sagents has 6
    # engagements of 4 agents (A twice, B twice), sstretch one, the rest none
    assert (agents["sagents"], agents["sstretch"], agents["sslow1"]) == (4, 1, 0)


# ── TST-3 invariants ─────────────────────────────────────────────────────────


def test_sum_rows_all_pages_equals_totals(xstore):
    for scope in (Scope(), Scope("2026-10-07", "2026-10-09"), Scope(show_automated=True), Scope(q="report")):
        rows: list[wire.SessionRow] = []
        total = None
        while total is None or len(rows) < total:
            page = ex.list_sessions(scope, db=xstore, offset=len(rows), limit=5)
            assert page.rows, "a page past the end must not happen before total rows"
            rows += page.rows
            total = page.total
        summary = ex.period_summary(scope, db=xstore)
        assert summary.totals.n_sessions == total == len(rows)
        assert summary.totals.cost_usd == pytest.approx(sum(r.cost_usd for r in rows))
        assert summary.totals.tok_total == sum(r.tok_total for r in rows)
    assert ex.period_summary(Scope(), db=xstore).totals.cost_usd == pytest.approx(
        expected()["store_total_owned_cost_usd"]["value"] - COSTS["sauto"]
    )


def test_sum_projects_equals_totals(xstore):
    for scope in (Scope(), Scope("2026-10-06", "2026-10-08"), Scope(show_automated=True)):
        s = ex.period_summary(scope, db=xstore)
        assert s.totals.cost_usd == pytest.approx(sum(p.cost_usd for p in s.projects))
        assert s.totals.tok_total == sum(p.tok_total for p in s.projects)
        assert s.totals.n_sessions == sum(p.n_sessions for p in s.projects)
        assert s.totals.n_unpriced == sum(p.n_unpriced for p in s.projects)
    s = ex.period_summary(Scope(), db=xstore)
    assert [(p.project, round(p.cost_usd, 2)) for p in s.projects][:6] == [
        (BIZ, 12.10), (AGENTS, 4.70), (APP, 1.50), (IDLE, 1.40), (SKILL, 1.30), (HARNESS, 0.60),
    ]  # fmt: skip
    assert [p.color_slot for p in s.projects] == [1, 2, 3, 4, 5, 6, None, None]


def test_project_panel_ignores_own_filter(xstore):
    s = ex.period_summary(Scope(projects=(APP,)), db=xstore)
    assert s.totals.n_sessions == 2 and s.totals.cost_usd == pytest.approx(1.50)
    assert len(s.projects) == 8  # QRY-8: the panel is computed without the projects filter
    assert sum(p.cost_usd for p in s.projects) > s.totals.cost_usd


def test_null_cost_marks_session_project_day_totals(xstore):
    page = ex.list_sessions(Scope("2026-10-06", "2026-10-06"), db=xstore)
    row = next(r for r in page.rows if r.session_id == "sunpriced")
    assert (row.cost_usd, row.cost_lower_bound, row.n_unpriced, row.tok_total) == (3.0, True, 1, 360)
    s2day = next(r for r in page.rows if r.session_id == "s2day")
    assert (s2day.cost_lower_bound, s2day.n_unpriced) == (False, 0)
    s = ex.period_summary(Scope("2026-10-06", "2026-10-06"), db=xstore)
    assert (s.totals.cost_usd, s.totals.cost_lower_bound, s.totals.n_unpriced) == (
        pytest.approx(11.0),
        True,
        1,
    )
    biz = next(p for p in s.projects if p.project == BIZ)
    assert (biz.cost_lower_bound, biz.n_unpriced) == (True, 1)
    (bar,) = ex.day_series(Scope(), "2026-10-06", "2026-10-06", db=xstore).days
    assert (bar.cost_usd, bar.cost_lower_bound, bar.n_unpriced) == (pytest.approx(6.0), True, 1)
    other = ex.period_summary(Scope("2026-10-07", "2026-10-07"), db=xstore).totals
    assert (other.cost_lower_bound, other.n_unpriced) == (False, 0)


def test_day_cost_lower_bound(xstore):
    bars = {b.day: b for b in ex.day_series(Scope(), "2026-10-06", "2026-10-10", db=xstore).days}
    assert [(d, b.cost_lower_bound, b.n_unpriced) for d, b in bars.items()] == [
        ("2026-10-06", True, 1), ("2026-10-07", False, 0), ("2026-10-08", False, 0),
        ("2026-10-09", False, 0), ("2026-10-10", False, 0),
    ]  # fmt: skip
    assert [round(b.cost_usd, 2) for b in bars.values()] == [6.0, 6.5, 5.3, 2.4, 2.05]


def test_duplicates_do_not_add(xstore):
    row = next(r for r in ex.list_sessions(Scope(), db=xstore, limit=1000).rows if r.session_id == "sfork")
    assert row.cost_usd == pytest.approx(COSTS["sfork"])  # 0.20, not 0.20 x 4 (the fork's copies)
    (bar,) = ex.day_series(Scope(), "2026-10-09", "2026-10-09", db=xstore).days
    assert bar.cost_usd == pytest.approx(0.90 + 0.20 + 1.30)
    assert bar.n_sessions == 3


# ── automated rule and filters (QRY-7) ───────────────────────────────────────


def test_automated_hidden_and_counted(xstore):
    day = Scope("2026-10-06", "2026-10-06")
    hidden = ex.period_summary(day, db=xstore)
    assert (hidden.totals.n_sessions, hidden.totals.n_sessions_period) == (2, 2)
    assert hidden.totals.n_automated_hidden == 1
    shown = ex.period_summary(Scope("2026-10-06", "2026-10-06", show_automated=True), db=xstore)
    assert (shown.totals.n_sessions, shown.totals.n_automated_hidden) == (3, 0)
    assert shown.totals.cost_usd == pytest.approx(hidden.totals.cost_usd + 0.50)
    assert HELPER in {p.project for p in shown.projects} and HELPER not in {
        p.project for p in hidden.projects
    }
    page = ex.list_sessions(Scope(show_automated=True), db=xstore, limit=1000)
    assert next(r for r in page.rows if r.session_id == "sauto").is_automated is True
    # the count follows the other filters; n_sessions_period follows only the automated rule
    narrowed = ex.period_summary(Scope(providers=("codex",)), db=xstore)
    assert (narrowed.totals.n_automated_hidden, narrowed.totals.n_sessions) == (0, 1)
    assert narrowed.totals.n_sessions_period == 12
    assert ex.period_summary(Scope(), db=xstore).totals.n_automated_hidden == 1


def test_filter_and_or(xstore):
    assert listed(xstore, Scope(providers=("omp",))) == ["somp"]
    assert listed(xstore, Scope(providers=("omp", "codex"))) == ["scodex", "somp"]  # values OR
    assert listed(xstore, Scope(providers=("omp", "codex"), projects=(CODEX,))) == ["scodex"]  # kinds AND
    assert listed(xstore, Scope(projects=(APP,))) == ["sslow1", "sslow2"]
    assert listed(xstore, Scope(models=("synth-model-1",))) == IN_SCOPE
    assert listed(xstore, Scope(models=("nope",))) == []
    assert listed(xstore, Scope(branches=("main",))) == []
    assert listed(xstore, Scope("2026-10-07", "2026-10-07", providers=("claude_code",))) == [
        "s2day", "sslow1", "sslow2"
    ]  # fmt: skip


def test_q_every_term_any_field(xstore):
    assert listed(xstore, Scope(q="report totals")) == ["sslow2"]  # both terms in its name
    assert listed(xstore, Scope(q="app totals")) == [
        "sslow2"
    ]  # one term in the project name, one in the name
    assert listed(xstore, Scope(q="verdict")) == ["sagents"]  # a later human ask of the session
    assert listed(xstore, Scope(q="give")) == []  # a message turn label is not a search field
    assert listed(xstore, Scope(q="staffing")) == ["sstretch"]
    assert listed(xstore, Scope(q="stools")) == ["stools"]  # session_id
    assert listed(xstore, Scope(q="CODEXPROJ")) == ["scodex"]  # case-insensitive, project name
    assert listed(xstore, Scope(q="Claude Code")) == sorted(
        set(IN_SCOPE) - {"scodex", "somp"}
    )  # provider name
    assert listed(xstore, Scope(q="  report   TOTALS ")) == ["sslow2"]


def test_q_percent_is_literal(xstore):
    assert listed(xstore, Scope(q="%")) == []
    # "_" occurs in the provider key claude_code only; a LIKE wildcard would also match codex and omp
    assert listed(xstore, Scope(q="_")) == sorted(set(IN_SCOPE) - {"scodex", "somp"})
    assert listed(xstore, Scope(q="cod_x")) == []


def test_model_not_a_q_field(xstore):
    assert listed(xstore, Scope(q="synth-model")) == []
    assert listed(xstore, Scope(q="synth")) == []


# ── facets (QRY-8, QRY-27) ───────────────────────────────────────────────────


def test_facets_except_own(xstore):
    f = ex.facets(Scope(providers=("omp",)), db=xstore)
    assert [(v.value, v.n) for v in f.providers] == [("claude_code", 10), ("codex", 1), ("omp", 1)]
    assert [(v.value, v.n) for v in f.models] == [("synth-model-1", 1)]  # other facets obey the filter
    only_model = ex.facets(Scope(models=("nope",)), db=xstore)
    assert [(v.value, v.n) for v in only_model.models] == [("synth-model-1", 12)]  # own filter ignored
    assert [v.n for v in only_model.providers] == [0, 0, 0]
    assert ex.facets(Scope(projects=(APP,)), db=xstore).providers[0].n == 2


def test_facet_zero_values_included(xstore):
    f = ex.facets(Scope("2026-10-07", "2026-10-07"), db=xstore)
    assert [(v.value, v.n) for v in f.providers] == [("claude_code", 3), ("codex", 0), ("omp", 0)]
    assert [(v.value, v.n) for v in f.models] == [("synth-model-1", 3)]
    assert f.automated.n == 0
    assert ex.facets(Scope("2026-10-06", "2026-10-06"), db=xstore).automated.n == 1
    shown = ex.facets(Scope("2026-10-06", "2026-10-06", show_automated=True), db=xstore)
    assert shown.automated.n == 1  # EXP-24: automated sessions in the period, whatever the toggle


def test_branches_omit_null(xstore):
    assert ex.facets(Scope(), db=xstore).branches == []  # no fixture session has a git_branch


# ── sort and paging (QRY-15, QRY-16, TST-4) ──────────────────────────────────


def test_sort_keys_nulls_last(xstore, xstore_null_tok):
    scope = Scope(show_automated=True)
    by = {k: ids(ex.list_sessions(scope, db=xstore, sort=k, limit=1000)) for k in ("start", "cost", "wall")}
    assert by["start"][0] == "scodex" and by["start"][-1] == "sauto"
    assert by["cost"][:3] == ["s2day", "sagents", "sunpriced"]  # 8.00, 4.70, 3.00
    assert by["wall"][:2] == ["sstretch", "s2day"]  # 10,085 s, 7,265 s
    # equal keys (sauto and scodex are both 25 s): first_ms descending decides, in both directions
    asc = ids(ex.list_sessions(scope, db=xstore, sort="wall", dir="asc", limit=1000))
    assert asc[:2] == ["scodex", "sauto"]
    desc = ids(ex.list_sessions(scope, db=xstore, sort="wall", limit=1000))
    assert desc[-2:] == ["scodex", "sauto"]
    for direction in ("asc", "desc"):  # a null key sorts last either way
        page = ex.list_sessions(Scope(), db=xstore_null_tok, sort="tokens", dir=direction, limit=1000)
        assert ids(page)[-1] == "scodex" and page.rows[-1].tok_total is None
    first = ex.list_sessions(Scope(), db=xstore, sort="tokens", limit=1000).rows[0]
    assert first.session_id == "s2day" and first.tok_total == 2400  # two calls of 1,200 tokens


def test_pages_disjoint_and_complete(xstore):
    for sort in ("start", "cost", "wall", "active", "tokens"):
        for direction in ("asc", "desc"):
            whole = ids(ex.list_sessions(Scope(), db=xstore, sort=sort, dir=direction, limit=1000))
            paged: list[str] = []
            for offset in range(0, 12, 5):
                page = ex.list_sessions(Scope(), db=xstore, sort=sort, dir=direction, offset=offset, limit=5)
                assert page.total == 12 and (page.offset, page.limit) == (offset, 5)
                paged += ids(page)
            assert paged == whole and len(set(paged)) == 12
    assert ex.list_sessions(Scope(), db=xstore, offset=12).rows == []
    assert ex.list_sessions(Scope(), db=xstore, offset=100).total == 12


# ── groups and stats (QRY-26) ────────────────────────────────────────────────


def test_group_by_project_and_day(xstore):
    page = ex.list_sessions(Scope(), db=xstore, group="project", group_limit=1)
    assert page.rows == [] and page.total == 12
    assert [g.key for g in page.groups] == [
        CODEX,
        OMP,
        IDLE,
        SKILL,
        BIZ,
        HARNESS,
        AGENTS,
        APP,
    ]  # latest start
    biz = next(g for g in page.groups if g.key == BIZ)
    assert (biz.name, biz.n_sessions, biz.color_slot) == ("bizdemo/src", 4, 1)
    assert biz.cost_usd == pytest.approx(12.10) and biz.cost_lower_bound and biz.n_unpriced == 1
    assert biz.wall_ms == 7265000 + 155000 + 136000 + 60000 and biz.tok_total == 2400 + 360 + 1850 + 120
    assert [r.session_id for r in biz.rows] == ["sfork"]  # first group_limit rows under the sort
    by_cost = ex.list_sessions(Scope(), db=xstore, group="project", sort="cost")
    assert [g.key for g in by_cost.groups][:3] == [BIZ, AGENTS, APP]
    by_cost_asc = ex.list_sessions(Scope(), db=xstore, group="project", sort="cost", dir="asc")
    assert by_cost_asc.groups[0].key == CODEX and by_cost_asc.groups[-1].key == BIZ
    days = ex.list_sessions(Scope(), db=xstore, group="day")
    assert [(g.key, g.name, g.n_sessions, g.color_slot) for g in days.groups] == [
        ("2026-10-10", "2026-10-10", 3, None), ("2026-10-09", "2026-10-09", 3, None),
        ("2026-10-08", "2026-10-08", 2, None), ("2026-10-07", "2026-10-07", 2, None),
        ("2026-10-06", "2026-10-06", 2, None),
    ]  # fmt: skip
    # s2day starts on 6 Oct (local), so it is in that day's group with its full 8.00 (EXP-56)
    assert days.groups[4].cost_usd == pytest.approx(8.00 + 3.00)
    assert days.groups[3].cost_usd == pytest.approx(1.00 + 0.50)


def test_group_period_activity_strip(xstore):
    short = ex.list_sessions(Scope("2026-10-06", "2026-10-08"), db=xstore, group="day")
    g = next(g for g in short.groups if g.key == "2026-10-06")
    assert g.period_activity.bin_ms == 3_600_000 and len(g.period_activity.tok) == 3 * 24
    assert g.period_activity.start_ms == 1791225000000  # 2026-10-06 00:00 IST = 2026-10-05T18:30Z
    # hour rows over the period: s2day's calls are at 22:30 IST on 6 Oct (hour 22) and 00:30 IST on 7 Oct
    # (hour 24); sunpriced's three calls are at 10:30 IST (hour 10)
    assert (g.period_activity.tok[22], g.period_activity.tok[24], g.period_activity.tok[10]) == (
        1200,
        1200,
        360,
    )
    assert sum(g.period_activity.tok) == 2400 + 360
    long = ex.list_sessions(Scope("2026-09-01", "2026-10-31"), db=xstore, group="project")
    assert (
        long.groups[0].period_activity.bin_ms == 86_400_000 and len(long.groups[0].period_activity.tok) == 61
    )
    all_time = ex.list_sessions(Scope(), db=xstore, group="project")  # extent 6-10 Oct: five days, hour rows
    assert len(all_time.groups[0].period_activity.tok) == 5 * 24
    assert sum(sum(g.period_activity.tok) for g in all_time.groups) == 9900  # all in-scope tokens


def test_group_key_pages_one_group(xstore):
    page = ex.list_sessions(Scope(), db=xstore, group="project", group_key=BIZ, offset=2, limit=2)
    assert page.groups is None and page.total == 4 and ids(page) == ["s2day", "sunpriced"]
    day = ex.list_sessions(Scope(), db=xstore, group="day", group_key="2026-10-09", limit=2)
    assert day.total == 3 and ids(day) == ["sskills", "sfork"]
    assert ex.list_sessions(Scope(), db=xstore, group="day", group_key="1999-01-01").rows == []


def test_stats_p90_and_max_activity(xstore):
    page = ex.list_sessions(Scope(), db=xstore, limit=1000)
    s = page.stats
    assert s.max_activity_tok == max(max(r.activity.tok) for r in page.rows) > 0
    assert s.max_cost_usd == 8.0  # D45: the maximum over all rows, not the visible page
    assert s.cost_p90 == 4.7  # nearest rank: 12 costs, ceil(0.9 x 12) = 11th of 0.15 .. 8.00
    small = ex.list_sessions(Scope(), db=xstore, limit=2)
    assert small.stats == s
    none = ex.list_sessions(Scope("2026-10-11", "2026-10-12"), db=xstore).stats
    assert (none.max_activity_tok, none.max_cost_usd, none.cost_p90) == (0, 0.0, None)
    one = ex.list_sessions(Scope(providers=("codex",)), db=xstore).stats
    assert (one.max_cost_usd, one.cost_p90) == (0.15, 0.15)


# ── summary and empty state (QRY-23, QRY-24) ─────────────────────────────────


def test_first_last_day(xstore):
    s = ex.period_summary(Scope("2026-10-08", "2026-10-08"), db=xstore)
    assert (s.first_day, s.last_day) == ("2026-10-06", "2026-10-10")  # the data extent, not the period
    assert s.empty is None


def test_empty_state_prev_next_and_all_time_matches(xstore):
    late = ex.period_summary(Scope("2026-10-11", "2026-10-12"), db=xstore)
    assert late.totals.n_sessions == 0 and late.totals.cost_usd is None and late.projects == []
    assert late.empty == wire.PeriodEmpty(None, wire.DayCount("2026-10-10", 3), None)
    early = ex.period_summary(Scope("2026-10-01", "2026-10-05"), db=xstore)
    assert early.empty == wire.PeriodEmpty(None, None, wire.DayCount("2026-10-06", 2))  # sauto hidden
    shown = ex.period_summary(Scope("2026-10-01", "2026-10-05", show_automated=True), db=xstore)
    assert shown.empty.next_day == wire.DayCount("2026-10-06", 3)
    gap = ex.period_summary(Scope("2026-10-11", "2026-10-12", q="migration"), db=xstore)
    assert gap.empty.all_time_matches == 2  # sagents and sharness, all time (EXP-79)
    assert gap.empty.prev_day == wire.DayCount(
        "2026-10-08", 2
    )  # the neighbour day follows the filters, q too
    narrowed = ex.period_summary(
        Scope("2026-10-11", "2026-10-12", q="migration", providers=("omp",)), db=xstore
    )
    assert narrowed.empty == wire.PeriodEmpty(0, None, None)
    assert ex.period_summary(Scope(q="nothing matches this"), db=xstore).empty == wire.PeriodEmpty(
        0, None, None
    )  # all time: no neighbour days


# ── day_series (QRY-25) ──────────────────────────────────────────────────────


def test_day_series_by_project_order(xstore):
    bars = {b.day: b for b in ex.day_series(Scope(), "2026-10-06", "2026-10-10", db=xstore).days}
    assert [(p.project, p.color_slot) for p in bars["2026-10-10"].by_project] == [
        (IDLE, 4),
        (OMP, None),
        (CODEX, None),
    ]  # slot ascending, nulls last, then cost descending
    assert [(p.project, p.color_slot) for p in bars["2026-10-08"].by_project] == [(AGENTS, 2), (HARNESS, 6)]
    assert [p.project_name for p in bars["2026-10-07"].by_project] == ["bizdemo/src", "app"]
    assert [b.n_sessions for b in bars.values()] == [2, 3, 2, 3, 3]
    assert bars["2026-10-06"].tok_total == 1200 + 360  # the automated session is hidden
    shown = ex.day_series(Scope(show_automated=True), "2026-10-06", "2026-10-06", db=xstore).days[0]
    assert (shown.n_sessions, shown.cost_usd) == (3, pytest.approx(6.5))
    assert HELPER in {p.project for p in shown.by_project}
    only = ex.day_series(Scope(providers=("codex",)), "2026-10-09", "2026-10-10", db=xstore)
    assert [(b.day, b.n_sessions) for b in only.days] == [("2026-10-09", 0), ("2026-10-10", 1)]
    ignored = ex.day_series(Scope("2026-10-01", "2026-10-02"), "2026-10-10", "2026-10-10", db=xstore)
    assert ignored.days[0].n_sessions == 3  # the scope's period does not apply (EXP-39)


def test_week_bucket_clips_partial_edge_weeks(xstore):
    # Mon 5 Oct to Sun 18 Oct: two whole weeks
    whole = ex.day_series(Scope(), "2026-10-05", "2026-10-18", bucket="week", db=xstore).days
    assert [(b.day, b.from_day, b.to_day) for b in whole] == [
        ("2026-10-05", "2026-10-05", "2026-10-11"), ("2026-10-12", "2026-10-12", "2026-10-18")
    ]  # fmt: skip
    assert whole[0].cost_usd == pytest.approx(22.25) and whole[0].n_sessions == 12
    assert whole[1].cost_usd == 0 and whole[1].by_project == []
    # Wed 7 Oct to Tue 20 Oct: both edge weeks are clipped to the window
    clipped = ex.day_series(Scope(), "2026-10-07", "2026-10-20", bucket="week", db=xstore).days
    assert [(b.day, b.from_day, b.to_day) for b in clipped] == [
        ("2026-10-05", "2026-10-07", "2026-10-11"), ("2026-10-12", "2026-10-12", "2026-10-18"),
        ("2026-10-19", "2026-10-19", "2026-10-20"),
    ]  # fmt: skip
    assert clipped[0].cost_usd == pytest.approx(22.25 - 6.0)  # the 6 Oct share (3.00 + 3.00) is outside
    assert clipped[0].n_sessions == 11 and clipped[0].cost_lower_bound is False  # sunpriced is outside
    assert clipped[0].tok_total == 9900 - 1560
    # a window inside one week
    inner = ex.day_series(Scope(), "2026-10-06", "2026-10-08", bucket="week", db=xstore).days
    assert [(b.from_day, b.to_day) for b in inner] == [("2026-10-06", "2026-10-08")]
    assert inner[0].cost_lower_bound is True and inner[0].n_unpriced == 1


# ── names and colour slots (QRY-13, QRY-14) ──────────────────────────────────


def test_colour_slot_tie_breaks_by_project_key(xstore_tie):
    # harnessproj and ompproj both cost 0.60 over the window: ranks 6 and 7 tie, the lower key wins slot 6
    page = ex.list_sessions(Scope(), db=xstore_tie, limit=1000)
    slot = {r.project: r.color_slot for r in page.rows}
    assert slot[HARNESS] == 6 and slot[OMP] is None


def test_unnamed_session_uses_project_and_id(xstore):
    row = next(
        r for r in ex.list_sessions(Scope(show_automated=True), db=xstore).rows if r.session_id == "sauto"
    )
    assert (row.name, row.title_source) == ("helper · sauto", "fallback")


# ── palette (QRY-28, API-14) ─────────────────────────────────────────────────


def test_palette_empty_text(xstore):
    for text in ("", "   "):
        p = ex.palette(text, Scope(), db=xstore)
        assert (p.filter_values, p.projects, p.sessions) == ([], [], [])


def test_palette_filter_values(xstore):
    p = ex.palette("model:SYN", Scope(), db=xstore)
    assert p.filter_values == [wire.FilterValue("model", "synth-model-1", "synth-model-1", 12)]
    assert p.projects == [] and p.sessions == []
    prov = ex.palette("provider:co", Scope(), db=xstore).filter_values
    assert [(f.value, f.label, f.n) for f in prov] == [
        ("claude_code", "Claude Code", 10),
        ("codex", "Codex", 1),
    ]
    proj = ex.palette("project:slow", Scope(), db=xstore).filter_values
    assert [(f.kind, f.value, f.label, f.n) for f in proj] == [("project", APP, "app", 2)]
    assert ex.palette("project: omp", Scope(), db=xstore).filter_values[0].value == OMP
    assert ex.palette("branch:main", Scope(), db=xstore).filter_values == []
    assert ex.palette("model", Scope(), db=xstore).filter_values == []  # no prefix, no filter values
    zero = ex.palette("provider:omp", Scope("2026-10-07", "2026-10-07"), db=xstore).filter_values
    assert [(f.value, f.n) for f in zero] == [("omp", 0)]  # counts follow the scope (QRY-28)


def test_palette_all_time_ranking_and_flags(xstore):
    p = ex.palette("src", Scope("2026-10-10", "2026-10-10"), db=xstore)
    assert [(x.project_name, x.n_sessions, x.n_automated) for x in p.projects] == [
        ("bizdemo/src", 4, 0),
        ("demolib/src", 0, 0),
    ]  # all-time cost descending; the scope's period does not apply
    assert p.projects[0].cost_usd == pytest.approx(12.10) and p.projects[0].cost_lower_bound
    assert p.projects[0].n_unpriced == 1 and p.projects[0].color_slot == 1
    assert p.projects[1].cost_usd is None  # no owned llm_call at all (QRY-11)
    helper = ex.palette("helper", Scope(), db=xstore).projects[0]
    assert (helper.n_sessions, helper.n_automated, helper.cost_usd) == (1, 1, 0.5)
    assert len(ex.palette("e", Scope(), db=xstore, limit_projects=4).projects) == 4
    # sessions: rank by name hits, then an exact project name, then the latest start
    s = ex.palette("app", Scope(), db=xstore).sessions
    assert [x.session_id for x in s] == ["sskills", "sslow2", "sslow1"]
    assert [x.session_id for x in ex.palette("migration", Scope(), db=xstore).sessions] == [
        "sharness",
        "sagents",
    ]
    assert len(ex.palette("e", Scope(), db=xstore, limit_sessions=3).sessions) == 3
    flags = {
        x.session_id: (x.in_period, x.in_filters)
        for x in ex.palette(
            "migration", Scope("2026-10-08", "2026-10-08", providers=("omp",)), db=xstore
        ).sessions
    }
    assert flags == {"sharness": (True, False), "sagents": (True, False)}
    flags = {
        x.session_id: (x.in_period, x.in_filters)
        for x in ex.palette("migration", Scope("2026-10-07", "2026-10-07"), db=xstore).sessions
    }
    assert flags == {"sharness": (False, True), "sagents": (False, True)}
    auto = ex.palette("sauto", Scope(), db=xstore).sessions  # all time, filters and automated rule ignored
    assert [(x.session_id, x.is_automated, x.in_filters) for x in auto] == [("sauto", True, False)]
    assert ex.palette("sauto", Scope(show_automated=True), db=xstore).sessions[0].in_filters is True
    assert ex.palette("unpriced", Scope(), db=xstore).sessions[0].cost_lower_bound is True


def test_palette_first_day(xstore):
    (hit,) = ex.palette("s2day", Scope(), db=xstore).sessions
    assert (hit.first_day, hit.in_period) == ("2026-10-06", True)
    (late,) = ex.palette("s2day", Scope("2026-10-07", "2026-10-07"), db=xstore).sessions
    assert (late.first_day, late.in_period) == ("2026-10-06", True)  # first_day is not the period's day
    assert hit.cost_usd == pytest.approx(8.0) and hit.name == "refactor the cost rollup for the weekly report"
    assert (hit.project, hit.project_name, hit.color_slot, hit.start_ms) == (
        BIZ,
        "bizdemo/src",
        1,
        hit.start_ms,
    )


# ── scope helper for Q3b ─────────────────────────────────────────────────────


def test_in_scope_roots(xstore):
    import sqlite3

    conn = sqlite3.connect(f"{xstore.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    roots = ex.in_scope_roots(conn, Scope("2026-10-07", "2026-10-07"))
    sids = {
        conn.execute("SELECT session_id FROM sessions WHERE op_rowid = ?", (r,)).fetchone()[0] for r in roots
    }
    conn.close()
    assert sids == {"s2day", "sslow1", "sslow2"} and roots == sorted(roots)


# ── legacy listings (API-27, API-28) ─────────────────────────────────────────


def test_legacy_sessions_offset_total_and_fields(xstore):
    from botsworth.query import service

    conn = sqlite3.connect(f"{xstore.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    whole = service.sessions(conn, {}, "-start_ms", 100)
    assert whole["total"] == 14 == len(whole["sessions"])  # every session, with or without an llm_call
    page = service.sessions(conn, {}, "-start_ms", 5, offset=10)
    conn.close()
    assert page["total"] == 14 and [s["session_id"] for s in page["sessions"]] == [
        s["session_id"] for s in whole["sessions"][10:15]
    ]
    row = next(s for s in whole["sessions"] if s["session_id"] == "sslow1")
    assert (row["name"], row["title_source"], row["project_name"], row["is_automated"]) == (
        "benchmark the slow ingest path and report", "first_ask", "app", False,
    )  # fmt: skip
    auto = next(s for s in whole["sessions"] if s["session_id"] == "sauto")
    assert (auto["name"], auto["title_source"], auto["is_automated"]) == ("helper · sauto", "fallback", True)
    with pytest.raises(QueryError):
        service.sessions(sqlite3.connect(xstore), {}, "-start_ms", 5, offset=-1)


def test_legacy_projects_fields(xstore):
    from botsworth.query import service

    conn = sqlite3.connect(f"{xstore.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    listing = service.projects(conn, "Asia/Kolkata")
    conn.close()
    assert set(listing) == {"freshness", "projects"}  # API-7
    by = {p["project"]: p for p in listing["projects"]}
    biz = by[BIZ]
    assert (biz["project_name"], biz["n_sessions"], biz["n_automated"], biz["color_slot"]) == (
        "bizdemo/src",
        4,
        0,
        1,
    )
    assert biz["providers"] == ["claude_code"] and biz["incl_cost_usd"] == pytest.approx(12.10)
    assert (by[HELPER]["n_automated"], by[HELPER]["color_slot"]) == (1, None)
    assert by["/u/b/demolib/src"]["project_name"] == "demolib/src"  # DM-60: both `src` get their parent


def test_projects_count_only_sessions_with_calls(xstore):
    from botsworth.query import service

    conn = sqlite3.connect(f"{xstore.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    by = {p["project"]: p for p in service.projects(conn, "Asia/Kolkata")["projects"]}
    conn.close()
    # szero is the only session of demolib/src and has no llm_call: the project is listed with 0 (D41 N2)
    assert (by["/u/b/demolib/src"]["n_sessions"], by["/u/b/demolib/src"]["n_automated"]) == (0, 0)
    assert by[APP]["n_sessions"] == 2 and by[CODEX]["n_sessions"] == 1
