"""T1080 REPL frames (interfaces.md REPL-5): partition, columns against the spec, rollups, price join,
count-once against `query`, the thread index, scope and failures. Stores are synthetic; no real store."""

from __future__ import annotations

import importlib
import re
import shutil
import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from repl_store import SESSION, SOLO

from botsworth.query import NoStore, QueryError, SchemaMismatch, frames

SPEC = Path(__file__).parents[2] / "docs" / "specs" / "interfaces.md"
TZ = "Asia/Kolkata"


def raw(db, sql, *args):
    con = sqlite3.connect(f"{Path(db).as_uri()}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


@pytest.fixture(scope="module")
def fr(repl_db):
    return frames.load(repl_db, TZ)


# ---- acceptance 1: partitions --------------------------------------------------------------------------


@pytest.mark.parametrize("by", ["provider", "caused_by_kind", "effect", "thread_id", "model"])
def test_partition_of_calls_by_any_column_equals_the_ungrouped_total_nulls_included(fr, by):
    c = fr.calls
    g = c.groupby(by, dropna=False)
    assert g.size().sum() == len(c)
    assert g.cost_usd.sum().sum() == pytest.approx(c.cost_usd.sum())
    assert g.in_tok.sum().sum() == c.in_tok.sum()


def test_partition_has_all_three_providers_a_null_effect_and_each_cause_kind(fr):
    c = fr.calls
    assert set(c.provider) == {"claude_code", "codex", "omp"}
    assert (
        c.effect.isna().any()
    )  # the calls that issued no tool call: counted in the partition as a null group
    kinds = set(c.caused_by_kind.dropna())
    assert {"session", "engagement", "llm_call"} <= kinds <= {"session", "engagement", "turn", "llm_call"}


def test_partition_by_provider_matches_the_stored_rows(fr, repl_db):
    stored = dict(
        raw(
            repl_db,
            "SELECT s.provider, count(*) FROM ops l JOIN sessions s ON s.rowid = l.root_rowid"
            " WHERE l.kind = 'llm_call' AND l.duplicate_of IS NULL GROUP BY 1",
        )
    )
    assert fr.calls.groupby("provider").size().to_dict() == stored


def test_nesting_comes_from_the_parent_thread_and_the_nested_engagement_is_depth_two(fr, repl_db):
    t = fr.threads.set_index("thread_id")
    nest = t[t.session_id == SESSION]
    assert sorted(nest.depth) == [0, 1, 2]
    a1 = nest[nest.index.str.endswith("engagement/a1#0")].iloc[0]
    assert nest.loc[a1.parent_thread_id].depth == 1
    assert nest.loc[nest.loc[a1.parent_thread_id].parent_thread_id].is_main
    # every call is in exactly one thread; sub-agent calls of the nested session are on depth >= 1 threads
    calls = fr.calls[fr.calls.session_id == SESSION]
    assert calls.thread_id.isin(nest.index).all()
    assert "spawn_depth" not in fr.calls.columns and "spawn_depth" not in fr.threads.columns


# ---- acceptance 2: columns against interfaces.md, rollups against the store ---------------------------


def spec_tables() -> dict[str, list[tuple[str, str]]]:
    text = SPEC.read_text()
    out = {}
    for m in re.finditer(r"#### Frame `(\w+)`\n(.*?)(?=\n#### |\nREPL-6)", text, re.S):
        rows = re.findall(r"^\| `(\w+)` \| (\w+) \|", m.group(2), re.M)
        out[m.group(1)] = rows
    return out


def test_every_frame_has_a_spec_table_and_the_columns_are_the_code_columns_in_order(fr):
    spec = spec_tables()
    assert list(spec) == list(frames.NAMES)
    for name in frames.NAMES:
        assert [c for c, _ in spec[name]] == list(getattr(fr, name).columns), name


def test_column_types_follow_the_spec(fr):
    kind = {"int": "Int64", "float": "float64", "bool": "boolean"}
    for name, rows in spec_tables().items():
        df = getattr(fr, name)
        for col, typ in rows:
            if typ in kind:
                assert str(df[col].dtype) == kind[typ], (name, col, df[col].dtype)
            elif typ == "string":
                assert pd.api.types.is_string_dtype(df[col].dtype) or df[col].isna().all(), (name, col)


def test_frame_rollups_equal_the_stored_values(fr, repl_db):
    stored = dict(raw(repl_db, "SELECT session_id, incl_cost_usd FROM sessions"))
    s = fr.sessions.set_index("session_id")
    assert {k: round(v, 12) for k, v in s.cost_usd.items()} == {k: round(v, 12) for k, v in stored.items()}
    top = dict(
        raw(repl_db, "SELECT op_id, incl_cost_usd FROM ops WHERE kind = 'engagement' OR parent_rowid IS NULL")
    )
    t = fr.threads.set_index("thread_id")
    assert {k: v for k, v in t.incl_cost_usd.items() if not pd.isna(v)} == {
        k: v for k, v in top.items() if v is not None
    }
    split = {}
    for sid, state, ms in raw(
        repl_db,
        "SELECT s.session_id, t.state, sum(t.ms) FROM time_split t JOIN sessions s ON s.op_rowid = t.op_rowid"
        " WHERE t.scope = 'session' GROUP BY 1, 2",
    ):
        split[(sid, state)] = ms
    for (sid, state), ms in split.items():
        assert s.loc[sid, f"{state}_ms"] == ms
    for state in frames.STATES:
        assert f"{state}_ms" in fr.sessions.columns  # a state missing from the data is still a column


def test_count_once_calls_cost_of_a_session_equals_its_stored_rollup(fr):
    per = fr.calls.groupby("session_id").cost_usd.sum()
    s = fr.sessions.set_index("session_id").cost_usd
    for sid, cost in per.items():
        assert cost == pytest.approx(s[sid])
    own = fr.threads.groupby("session_id").cost_usd.sum()  # own cost per thread sums to the session
    for sid, cost in own.items():
        assert cost == pytest.approx(s[sid])


def test_tool_call_cost_share_sums_back_to_the_issuing_calls(fr):
    tc = fr.tool_calls
    per_call = tc.groupby("call_id").call_cost_share_usd.sum()
    cost = fr.calls.set_index("call_id").cost_usd
    for cid, share in per_call.items():
        assert share == pytest.approx(cost[cid])


# ---- acceptance 7: what-if at the call's own model equals the stored cost; one prices row -------------


def test_every_computed_call_finds_exactly_one_prices_row(fr):
    c = fr.calls[fr.calls.cost_basis == "computed"]
    assert len(c) >= 18 and set(c.model) >= {"claude-opus-4-5", "claude-opus-4-5-20251101", "haiku", "gpt-5"}
    joined = c.merge(fr.prices, on=["provider", "model"], how="left", validate="many_to_one")
    assert len(joined) == len(c) and joined.price_id.notna().all()
    assert not fr.prices.duplicated(["provider", "model"]).any()


def test_prices_hold_catalog_ids_aliases_and_one_m_names_and_no_omp(fr):
    p = fr.prices
    assert set(p.provider) == {"claude_code", "codex"}
    row = p[(p.provider == "claude_code") & (p.model == "haiku")].iloc[0]
    assert row.price_id == "claude-haiku-4-5"
    assert ((p.model == "claude-opus-5[1m]") & (p.price_id == "claude-opus-5")).any()  # no_premium_1m base
    assert not (p.model == "claude-opus-4-5[1m]").any()  # a base that is not in no_premium_1m has no 1M price


# ---- acceptance 8: the frames total equals the `query` total on a store with duplicate rows -------------


def test_frames_total_equals_the_query_total_on_a_duplicate_row_store(qstore):
    q = importlib.import_module("botsworth.query")
    assert raw(qstore, "SELECT count(*) FROM ops WHERE duplicate_of IS NOT NULL")[0][0] >= 1
    total = q.query(
        {
            "scope": {"all": True},
            "select": "descendants",
            "where": [{"field": "kind", "in": ["llm_call"]}],
            "measure": "own",
            "metrics": ["cost_usd", "tokens.total"],
            "agg": "sum",
        },
        db=qstore,
    )
    row = total["rows"][0]
    f = frames.load(qstore, TZ)
    assert len(f.calls) == row["counts"]["n"]
    assert f.calls.cost_usd.sum() == pytest.approx(row["metrics"]["cost_usd"])
    tokens = f.calls[["in_tok", "out_tok", "cache_read_tok", "cache_write_tok"]].sum().sum()
    assert tokens == row["metrics"]["tokens.total"]
    owned = raw(qstore, "SELECT count(*) FROM ops WHERE kind = 'tool_call' AND duplicate_of IS NULL")[0][0]
    assert len(f.tool_calls) == owned


# ---- acceptance 9: the thread index --------------------------------------------------------------------


def test_the_ops_thread_rowid_index_exists(repl_db):
    indexes = [r[1] for r in raw(repl_db, "PRAGMA index_list(ops)")]
    firsts = {n: raw(repl_db, f"PRAGMA index_info({n})")[0][2] for n in indexes}
    assert "thread_rowid" in firsts.values()


# ---- scope, local time, failures -----------------------------------------------------------------------


def test_scope_by_sessions_keeps_whole_sessions(repl_db):
    f = frames.load(repl_db, TZ, sessions=[SESSION])
    assert set(f.calls.session_id) == set(f.threads.session_id) == set(f.sessions.session_id) == {SESSION}
    assert len(f.calls) == 6 and len(f.threads) == 3
    assert f.sessions.cost_usd.iloc[0] == pytest.approx(f.calls.cost_usd.sum())


def test_scope_by_local_days_uses_the_zone(fr, repl_db):
    day = fr.sessions.set_index("session_id").loc[SESSION, "local_day"]
    hit = frames.load(repl_db, TZ, since=day, until=day)
    assert SESSION in set(hit.sessions.session_id)
    after = frames.load(repl_db, TZ, since="2099-01-01")
    assert (
        len(after.sessions) == 0
        and len(after.calls) == 0
        and list(after.calls.columns) == list(fr.calls.columns)
    )


def test_local_day_and_hour_follow_the_zone(repl_db):
    utc = frames.load(repl_db, "UTC").calls.set_index("call_id")
    kol = frames.load(repl_db, "Asia/Kolkata").calls.set_index("call_id")
    ms = kol.start_ms.astype("int64")
    expect = pd.to_datetime(ms + 19_800_000, unit="ms")  # +05:30
    assert (kol.local_hour == expect.dt.hour).all()
    assert (kol.local_day == expect.dt.strftime("%Y-%m-%d")).all()
    assert (utc.local_hour == pd.to_datetime(utc.start_ms.astype("int64"), unit="ms").dt.hour).all()


def test_an_unknown_session_id_is_a_query_error(repl_db):
    with pytest.raises(QueryError, match="no such session: nope"):
        frames.load(repl_db, TZ, sessions=["nope"])


def test_an_unknown_zone_is_a_query_error(repl_db):
    with pytest.raises(QueryError):
        frames.load(repl_db, "Mars/Olympus")


def test_a_missing_store_is_no_store(tmp_path):
    with pytest.raises(NoStore):
        frames.load(tmp_path / "none.sqlite", TZ)


def test_a_store_of_another_schema_version_is_a_schema_mismatch(repl_db, tmp_path):
    old = tmp_path / "old.sqlite"
    shutil.copy(repl_db, old)
    con = sqlite3.connect(old)
    con.execute("UPDATE meta SET value = '12' WHERE key = 'schema_version'")
    con.commit()
    con.close()
    with pytest.raises(SchemaMismatch, match="store schema 12, botsworth needs 13"):
        frames.load(old, TZ)


def test_load_does_not_write_the_store(repl_db):
    path = Path(repl_db)
    before = (path.stat().st_mtime_ns, path.stat().st_size)
    f = frames.load(path, TZ)
    for name in frames.NAMES:
        getattr(f, name)
    assert (path.stat().st_mtime_ns, path.stat().st_size) == before


def test_events_frame_has_rows_and_its_integer_columns_are_int64(fr):
    ev = fr.events
    comp = ev[ev.kind == "compaction"]
    assert len(comp) == 1
    row = comp.iloc[0]
    assert (row.pre_tokens, row.post_tokens, row.duration_ms, row.trigger) == (10000, 3000, 900, "auto")
    for col in ("pre_tokens", "post_tokens", "duration_ms", "ts_ms"):
        assert str(ev[col].dtype) == "Int64", col
    assert str(ev.is_main.dtype) == "boolean"
    assert ev.event_id.is_unique


@pytest.mark.parametrize(
    ("zone", "before", "after"),
    [
        # America/New_York: clocks go 02:00 EST -> 03:00 EDT on 2026-03-08 (07:00 UTC)
        ("America/New_York", "2026-03-08T06:59:00Z", "2026-03-08T07:01:00Z"),
        # Australia/Lord_Howe: a 30-minute shift, 02:00 LHDT -> 01:30 LHST on 2026-04-05 (15:00 UTC)
        ("Australia/Lord_Howe", "2026-04-04T14:59:00Z", "2026-04-04T15:01:00Z"),
        ("Australia/Lord_Howe", "2026-04-04T15:29:00Z", "2026-04-04T15:31:00Z"),
        ("Australia/Lord_Howe", "2026-10-03T15:29:00Z", "2026-10-03T15:31:00Z"),
    ],
)
def test_local_day_and_hour_across_a_dst_change(zone, before, after):
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    def ms(iso):
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC).timestamp() * 1000)

    df = pd.DataFrame({"start_ms": pd.array([ms(before), ms(after)], dtype="Int64")})
    out = frames._local(df, "start_ms", zone)
    for i, iso in enumerate((before, after)):
        local = datetime.fromtimestamp(ms(iso) / 1000, ZoneInfo(zone))
        assert (out.local_day[i], out.local_hour[i]) == (local.strftime("%Y-%m-%d"), local.hour), (zone, iso)


def test_a_session_with_no_time_split_rows_has_zero_state_times(repl_db, fr):
    assert raw(
        repl_db,
        "SELECT count(*) FROM time_split t JOIN sessions s ON s.op_rowid = t.op_rowid"
        " WHERE s.session_id = ? AND t.scope = 'session'",
        SOLO,
    ) == [(0,)]  # the premise: the pipeline stores no split for a one-record session
    s = fr.sessions.set_index("session_id")
    for state in frames.STATES:
        assert s.loc[SOLO, f"{state}_ms"] == 0, state
        assert str(s[f"{state}_ms"].dtype) == "Int64"
