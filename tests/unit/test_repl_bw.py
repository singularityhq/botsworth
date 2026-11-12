"""T1080 REPL helpers (`bw`, interfaces.md REPL-6): one test per helper, each against a value computed without
  it
(raw SQL on the store, the bundled price file, or a hand-built frame)."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from repl_store import SESSION

from botsworth import pointer
from botsworth.pricing import snapshot
from botsworth.query import frames
from botsworth.repl.bw import BW, TRAPS

TZ = "Asia/Kolkata"


def raw(db, sql, *args):
    con = sqlite3.connect(f"{Path(db).as_uri()}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def make_bw(db, **kw) -> BW:
    f = frames.load(db, TZ, **kw)
    fr = {n: getattr(f, n) for n in frames.NAMES}
    return BW(fr, lambda: {n: getattr(frames.load(db, TZ, **kw), n) for n in frames.NAMES})


@pytest.fixture(scope="module")
def bw(repl_db):
    return make_bw(repl_db)


@pytest.fixture(scope="module")
def qbw(qstore):
    return make_bw(qstore)


def test_help_lists_every_public_helper_the_frames_and_the_traps(bw, capsys):
    bw.help()
    out = capsys.readouterr().out
    for name in (
        "help",
        "schema",
        "session",
        "by",
        "issued",
        "prev",
        "first_after",
        "cache_writes",
        "what_if",
        "text",
        "reload",
    ):
        assert re.search(rf"^bw\.{name}: \S", out, re.M), name
    assert all(n in out for n in frames.NAMES)
    assert TRAPS in out and "incl_cost_usd" in out and "bw.issued" in out


def test_schema_without_argument_gives_the_row_counts_of_the_store(bw, repl_db):
    counts = bw.schema().rows.to_dict()
    assert (
        counts["calls"]
        == raw(repl_db, "SELECT count(*) FROM ops WHERE kind='llm_call' AND duplicate_of IS NULL")[0][0]
    )
    assert counts["sessions"] == raw(repl_db, "SELECT count(*) FROM sessions")[0][0]
    assert (
        counts["threads"]
        == raw(repl_db, "SELECT count(*) FROM ops WHERE kind='engagement'")[0][0] + counts["sessions"]
    )
    assert set(counts) == set(frames.NAMES)


def test_schema_of_a_frame_name_or_dataframe_gives_dtype_and_non_null(bw, repl_db):
    s = bw.schema("calls")
    n_effect = raw(repl_db, "SELECT count(effect) FROM ops WHERE kind='llm_call' AND duplicate_of IS NULL")[
        0
    ][0]
    assert s.loc["effect", "non_null"] == n_effect
    assert s.loc["cost_usd", "dtype"] == "float64"
    assert bw.schema(bw._frames["turns"]).index.tolist()[:2] == ["turn_id", "session_id"]
    with pytest.raises(KeyError):
        bw.schema("nope")


def test_session_splits_main_against_subagents_at_any_depth(bw, repl_db):
    s = bw.session("nes")
    rows = raw(
        repl_db,
        "SELECT l.thread_rowid = so.rowid, count(*), sum(l.cost_usd) FROM ops l"
        " JOIN sessions x ON x.rowid = l.root_rowid JOIN ops so ON so.rowid = x.op_rowid"
        " WHERE x.session_id = ? AND l.kind='llm_call' AND l.duplicate_of IS NULL"
        " GROUP BY 1",
        SESSION,
    )
    main, sub = (next(r for r in rows if r[0] == k) for k in (1, 0))
    assert (s.split.loc["main", "n_calls"], s.split.loc["subagents", "n_calls"]) == (main[1], sub[1])
    assert s.sub_calls == 4 and s.sub_cost == pytest.approx(sub[2])
    assert s.cost == pytest.approx(main[2] + sub[2]) and s.n_calls == 6
    assert len(s.nested) == 1 and s.nested.iloc[0].thread_id.endswith("engagement/a1#0")
    assert "nested deeper than 1: 1" in repr(s)


def test_session_prefix_must_match_exactly_one_session(bw):
    with pytest.raises(ValueError, match="0 sessions match 'zzz'"):
        bw.session("zzz")
    with pytest.raises(ValueError, match="sessions match ''"):
        bw.session("")


def test_by_groups_counts_and_cost_columns_and_never_incl_cost(bw, repl_db):
    out = bw.by(bw._frames["calls"], "provider")
    stored = {
        p: (n, c)
        for p, n, c in raw(
            repl_db,
            "SELECT s.provider, count(*), sum(l.cost_usd) FROM ops l"
            " JOIN sessions s ON s.rowid = l.root_rowid"
            " WHERE l.kind='llm_call' AND l.duplicate_of IS NULL GROUP BY 1",
        )
    }
    assert {p: (r.n, r.cost_usd) for p, r in out.iterrows()} == pytest.approx(stored)
    assert list(out.cost_usd) == sorted(out.cost_usd, reverse=True)
    t = bw.by(bw._frames["threads"], "depth")
    assert "incl_cost_usd" not in t.columns and t.n.to_dict() == {0: 5, 1: 1, 2: 1}
    assert t.cost_usd.sum() == pytest.approx(bw._frames["calls"].cost_usd.sum())
    by_tool = bw.by(bw._frames["tool_calls"], "tool_name")
    assert by_tool.call_cost_share_usd.sum() == pytest.approx(
        bw._frames["calls"].loc[bw._frames["calls"].n_tools > 0, "cost_usd"].sum()
    )
    with pytest.raises(AssertionError):
        bw.by(bw._frames["calls"])


def test_by_keeps_null_keys_in_the_groups(bw):
    out = bw.by(bw._frames["calls"], "effect")
    assert out.n.sum() == len(bw._frames["calls"]) and out.index.isna().any()


def test_issued_returns_each_call_once_whatever_the_number_of_tool_calls(bw, repl_db):
    tc = bw._frames["tool_calls"]
    test_tools = tc[tc.effect == "test"]
    expected = {
        r[0]
        for r in raw(
            repl_db,
            "SELECT DISTINCT pc.op_id FROM ops tc JOIN ops pc ON pc.rowid = tc.parent_rowid"
            " WHERE tc.kind='tool_call' AND tc.duplicate_of IS NULL AND tc.effect = 'test'",
        )
    }
    got = bw.issued(test_tools)
    assert set(got.call_id) == expected and got.call_id.is_unique and len(expected) >= 1
    multi = tc.groupby("call_id").size()
    assert (multi > 1).any()  # a plain merge would repeat those calls
    assert len(bw.issued()) == (bw._frames["calls"].n_tools > 0).sum()
    assert bw.issued(tc.iloc[0:0]).empty


def test_prev_adds_the_previous_call_of_the_same_thread(bw, repl_db):
    rows = raw(
        repl_db,
        "SELECT l.op_id, th.op_id, l.thread_pos, l.model, l.effect, l.gap_ms FROM ops l"
        " JOIN ops th ON th.rowid = l.thread_rowid"
        " WHERE l.kind='llm_call' AND l.duplicate_of IS NULL",
    )
    by_thread: dict[str, list] = {}
    for r in rows:
        by_thread.setdefault(r[1], []).append(r)
    expect = {}
    for calls in by_thread.values():
        calls.sort(key=lambda r: r[2])
        expect[calls[0][0]] = (None, None, None, None)
        for p, c in zip(calls, calls[1:], strict=False):
            expect[c[0]] = (p[0], p[3], p[4], p[5])
    out = bw.prev().set_index("call_id")
    for cid, (pid, pmodel, peffect, pgap) in expect.items():
        r = out.loc[cid]
        assert (r.prev_call_id if isinstance(r.prev_call_id, str) else None) == pid
        assert (r.prev_model if isinstance(r.prev_model, str) else None) == pmodel
        assert (r.prev_effect if isinstance(r.prev_effect, str) else None) == peffect
        assert (None if pd.isna(r.prev_gap_ms) else int(r.prev_gap_ms)) == pgap
    assert len(out) == len(bw._frames["calls"])


def test_prev_of_a_subset_still_looks_at_the_whole_thread(bw):
    calls = bw._frames["calls"]
    subset = calls[
        calls.call_id.str.endswith("nest/llm_call/m2")
    ]  # m1, the call before it, is not in the subset
    out = bw.prev(subset)
    assert len(out) == 1
    assert out.iloc[0].prev_call_id == "claude_code/nest/llm_call/m1"
    assert out.iloc[0].prev_model == "claude-opus-4-5"


def test_first_after_is_the_first_call_that_starts_after_the_event_in_the_same_thread(qbw, qstore):
    ev = raw(
        qstore,
        "SELECT ev.event_id, th.op_id, ev.ts_ms FROM events ev JOIN ops eo ON eo.op_id = ev.op_id"
        " JOIN ops th ON th.rowid = eo.thread_rowid WHERE ev.kind = 'skill_invocation' AND ev.copied = 0",
    )
    calls = raw(
        qstore,
        "SELECT l.op_id, th.op_id, l.start_ms FROM ops l JOIN ops th ON th.rowid = l.thread_rowid"
        " WHERE l.kind='llm_call' AND l.duplicate_of IS NULL",
    )
    expected: dict[str, str] = {}
    for eid, thread, ts in sorted(ev, key=lambda e: e[2]):
        later = sorted((c for c in calls if c[1] == thread and c[2] > ts), key=lambda c: c[2])
        if later:
            expected.setdefault(later[0][0], eid)
    assert expected
    out = qbw.first_after("skill_invocation")
    assert dict(zip(out.call_id, out.event_id, strict=True)) == expected
    assert out.call_id.is_unique
    narrowed = qbw._frames["calls"][lambda d: ~d.call_id.isin(list(expected))]
    assert not set(qbw.first_after("skill_invocation", narrowed).call_id) & set(expected)
    assert qbw.first_after("no_such_kind").empty


def test_cache_writes_are_the_calls_that_wrote_and_read_nothing(bw):
    frame = pd.DataFrame(
        {
            "call_id": list("abcdef"),
            "thread_pos": [1, 2, 3, 1, 2, 4],
            "cache_read_tok": [0, 10, None, 0, 0, 5],
            "cache_write_tok": [500, 500, 700, None, 0, 10],
            "cache_write_5m_tok": [None, None, None, 100, None, None],
            "cache_write_1h_tok": [None, None, None, None, 200, None],
        }
    )
    num = [c for c in frame.columns if c != "call_id"]
    frame[num] = frame[num].astype("Int64")
    out = bw.cache_writes(frame)
    assert list(out.call_id) == ["a", "c", "d", "e"]  # b read, f read
    assert out.set_index("call_id").first_in_thread.to_dict() == {
        "a": True,
        "c": False,
        "d": True,
        "e": False,
    }


def test_cache_writes_default_is_the_full_frame(bw, repl_db):
    n = raw(
        repl_db,
        "SELECT count(*) FROM ops WHERE kind='llm_call' AND duplicate_of IS NULL"
        " AND COALESCE(cache_read_tok,0) = 0 AND COALESCE(cache_write_tok,0)"
        " + COALESCE(cache_write_5m_tok,0) + COALESCE(cache_write_1h_tok,0) > 0",
    )[0][0]
    assert len(bw.cache_writes()) == n


def rates(provider_catalog: str, model: str) -> dict:
    """The bundled price file read directly, not through the Pricer."""
    data = json.loads(snapshot.latest().read_text())
    return data[provider_catalog]["models"][model]["cost"]


def test_what_if_prices_the_tokens_at_the_other_models_rates(bw):
    r = rates("anthropic", "claude-haiku-4-5")
    frame = pd.DataFrame(
        {
            "provider": ["claude_code", "claude_code", "omp", "codex"],
            "model": ["claude-opus-4-5", "claude-opus-4-5", "m1", "gpt-5"],
            "in_tok": [1_000_000, 10, 1000, 1000],
            "out_tok": [100_000, 10, 1000, 1000],
            "cache_read_tok": [2_000_000, 0, 0, 0],
            "cache_write_tok": [1_500_000, 0, 0, 0],
            "cache_write_5m_tok": [1_000_000, None, None, None],
            "cache_write_1h_tok": [500_000, None, None, None],
            "cost_usd": [99.0, 5.0, 7.0, 8.0],
        }
    )
    out = bw.what_if(frame, "claude-haiku-4-5")
    expect = (
        1.0 * r["input"]
        + 0.1 * r["output"]
        + 2.0 * r["cache_read"]
        + 1.0 * r["cache_write"]
        + 0.5 * 2 * r["input"]
    )
    assert out.repriced.tolist() == [True, True, False, False]
    assert out.new_cost_usd[0] == pytest.approx(expect)
    assert out.delta_usd[0] == pytest.approx(expect - 99.0)
    assert out.new_cost_usd[2] == 7.0 and out.new_cost_usd[3] == 8.0 and out.delta_usd[2] == 0
    assert frame.cost_usd[0] == 99.0  # the argument is not changed
    only_first = bw.what_if(frame, "claude-haiku-4-5", where=frame.index == 0)
    assert only_first.repriced.tolist() == [True, False, False, False]
    assert only_first.new_cost_usd[1] == 5.0
    with pytest.raises(ValueError, match="claude-sonet-4-5"):  # a misspelt model is an error, not delta 0
        bw.what_if(frame, "claude-sonet-4-5")


def test_what_if_at_each_calls_own_model_returns_the_stored_computed_cost(bw):
    calls = bw._frames["calls"]
    computed = calls[calls.cost_basis == "computed"]
    assert computed.model.nunique() >= 4
    for model, sub in computed.groupby("model"):
        out = bw.what_if(sub, model)
        assert out.repriced.all(), model
        assert np.allclose(out.new_cost_usd, sub.cost_usd, rtol=0, atol=1e-12), model


def test_text_reads_the_source_line_behind_a_src_pointer(qbw, qroots, monkeypatch):
    monkeypatch.setattr(pointer, "DEFAULT_ROOTS", qroots)
    t = qbw._frames["threads"]
    row = t[t.thread_id.str.endswith("engagement/ra#0")].iloc[0]
    m = re.fullmatch(r"src://(\w+)/(.+)#L(\d+)-L(\d+)(\?h=\w+)?", row.src)
    provider, rel, first = m.group(1), m.group(2), int(m.group(3))
    line = (qroots[provider] / rel).read_text().splitlines()[first - 1]
    assert qbw.text(row.src, max_bytes=100_000) == line + "\n"
    assert qbw.text(row.src, max_bytes=10) == line[:10]


def test_reload_replaces_the_frames_the_helpers_use_and_a_rebound_name_does_not(repl_db):
    f = frames.load(repl_db, TZ)
    fr = {n: getattr(f, n) for n in frames.NAMES}
    shrunk = dict(fr, calls=fr["calls"].iloc[:3])
    bw = BW(dict(shrunk), lambda: fr)
    ns_calls = bw._frames["calls"]
    ns_calls = ns_calls.iloc[:0]  # a cell rebinding its own name changes nothing in bw
    assert len(bw.issued()) <= 3 and len(ns_calls) == 0
    assert bw.schema().rows["calls"] == 3
    bw.reload()
    assert bw.schema().rows["calls"] == len(fr["calls"])
    assert bw.schema().rows["calls"] > 3


def _synthetic_calls(n: int, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    def toks(hi, p_null=0.0):
        v = pd.array(rng.integers(0, hi, n), dtype="Int64")
        v[rng.random(n) < p_null] = pd.NA
        return v

    w5, w1 = toks(5000, 0.5), toks(5000, 0.5)
    flat = rng.random(n) < 0.3
    w5[flat], w1[flat] = pd.NA, pd.NA
    return pd.DataFrame(
        {
            "provider": rng.choice(["claude_code", "codex", "omp"], n),
            "in_tok": toks(100_000, 0.05),
            "out_tok": toks(10_000, 0.05),
            "cache_read_tok": toks(200_000, 0.3),
            "cache_write_tok": toks(5000, 0.3),
            "cache_write_5m_tok": w5,
            "cache_write_1h_tok": w1,
            "cost_usd": rng.random(n),
        }
    )


@pytest.mark.parametrize("model", ["claude-haiku-4-5", "gpt-5", "claude-opus-4-5-20251101"])
def test_what_if_vectorised_equals_the_pricer_row_by_row(bw, model):
    from botsworth.pricing import Pricer
    from botsworth.repl.bw import TOKEN_COLS

    frame = _synthetic_calls(3000)
    out = bw.what_if(frame, model)
    pricer = Pricer()
    for i, row in frame.iterrows():
        rates = pricer.resolve(model, row.provider)
        tokens = {c: (None if pd.isna(row[c]) else int(row[c])) for c in TOKEN_COLS}
        cost = pricer.cost(tokens, rates) if rates else None
        assert bool(out.repriced[i]) == (cost is not None), (i, dict(row))
        want = row.cost_usd if cost is None else cost.cost_usd
        assert out.new_cost_usd[i] == pytest.approx(want, rel=1e-12, abs=1e-12), i
    assert out.repriced.any() and not out.repriced.all()


def test_what_if_on_300k_rows_takes_seconds_not_minutes(bw):
    import time

    frame = _synthetic_calls(300_000)
    t0 = time.perf_counter()
    out = bw.what_if(frame, "claude-haiku-4-5")
    assert time.perf_counter() - t0 < 5
    assert out.repriced.any()


def test_what_if_names_a_model_no_provider_prices(bw):
    with pytest.raises(ValueError, match="claude-sonet-4-5"):
        bw.what_if(bw._frames["calls"], "claude-sonet-4-5")


def test_help_carries_the_column_notes_and_the_spec_says_the_same(bw, capsys):
    bw.help()
    out = capsys.readouterr().out
    for note in (
        "gap_ms = call start minus the previous call's end",
        "negative when calls overlap",
        "in_tok is uncached input only",
        "shares sum to less than total call cost",
        "NULL for a session with no LLM calls",
        "agent_type is NULL for Claude",
        "works on any calls frame you pass",
    ):
        assert note in out, note
    spec = (Path(__file__).parents[2] / "docs" / "specs" / "interfaces.md").read_text()
    for text in (
        "negative when calls overlap",
        "uncached input tokens",
        "sum to less than the total call cost",
        "NULL for a session with no LLM calls",
        "NULL for the main thread and for Claude engagements (adapter gap)",
    ):
        assert text in spec, text
