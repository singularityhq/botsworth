"""Pure store derivations: DM-11 (first ask), DM-44 (bins), DM-41 (15-minute buckets), DM-24, DM-10, DM-45."""

from datetime import UTC, datetime

import pytest

from botsworth.store import derive


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp() * 1000)


# --- DM-11 ----------------------------------------------------------------------------------------


def test_first_ask_three_words_fails():
    assert not derive.first_ask_ok("go with recommendation")


def test_first_ask_slash_angle_bracket_fail():
    assert not derive.first_ask_ok("/mad:bootstrap HANDOFF.md now please")
    assert not derive.first_ask_ok("<pasted_content id> explain this to me")
    assert not derive.first_ask_ok("[Request interrupted by user for tool use]")
    assert not derive.first_ask_ok("~/projects/x is the place to look")
    assert not derive.first_ask_ok("# heading words in the message")


def test_first_ask_uuid_fails():
    assert not derive.first_ask_ok("0123abcd-0123-4567-89ab-cdef01234567 resume this one")


def test_first_ask_four_words_passes():
    assert derive.first_ask_ok("plan out the team roster")
    assert derive.first_ask_ok("  plan   out\nthe team\troster ")
    assert not derive.first_ask_ok(None)


# --- DM-10 ----------------------------------------------------------------------------------------


def test_title_levels():
    asks = ["ok", "fix the rounding in cost.py"]
    assert derive.title("B", "rename", asks) == ("B", "rename")
    assert derive.title("G", "generated", asks) == ("G", "generated")
    assert derive.title(None, None, asks) == ("fix the rounding in cost.py", "first_ask")
    assert derive.title(None, None, ["/clear", "ok"]) == (None, "fallback")
    assert derive.title("", "rename", []) == (None, "fallback")


def test_title_thread_title_is_after_asks():
    assert derive.title("a long thread title here", "thread_title", ["fix the rounding in cost.py"]) == (
        "fix the rounding in cost.py",
        "first_ask",
    )
    assert derive.title("a long thread title here", "thread_title", ["ok"]) == (
        "a long thread title here",
        "first_ask",
    )
    assert derive.title("short title", "thread_title", []) == (None, "fallback")


# --- DM-24 ----------------------------------------------------------------------------------------


def test_display_ix_numbers_non_harness_by_start_then_seq():
    turns = [("human", 30, 2), ("harness", 20, 1), ("human", 10, 0), ("message", 30, 3)]
    assert derive.display_ix(turns) == [2, None, 1, 3]


def test_display_ix_null_start_keeps_seq_place():
    turns = [("human", 10, 0), ("human", None, 1), ("human", 50, 2)]
    assert derive.display_ix(turns) == [1, 2, 3]


# --- DM-44 ----------------------------------------------------------------------------------------


def test_bins_2h_is_60_of_120s():
    bin_ms, bins = derive.activity_bins(0, 7_200_000, [(0, 5), (7_199_999, 7)])
    assert bin_ms == 120_000 and len(bins) == 60
    assert bins[0] == 5 and bins[-1] == 7 and sum(bins) == 12


def test_bins_zero_span_one_bin():
    assert derive.activity_bins(1000, 1000, [(1000, 4), (1000, None)]) == (60_000, [4])


def test_bins_no_calls_empty():
    assert derive.activity_bins(0, 3_600_000, []) == (60_000, [])


def test_bins_call_at_span_end_lands_in_last_bin():
    bin_ms, bins = derive.activity_bins(0, 120_000, [(120_000, 9)])
    assert (bin_ms, bins) == (60_000, [0, 9])


# --- DM-41 ----------------------------------------------------------------------------------------


def test_bucket15_is_epoch_aligned():
    assert derive.bucket15(ms("2026-10-06T19:00:00")) == ms("2026-10-06T19:00:00")
    assert derive.bucket15(ms("2026-10-06T19:14:59") + 999) == ms("2026-10-06T19:00:00")
    assert derive.bucket15(ms("2026-10-06T19:15:00")) == ms("2026-10-06T19:15:00")
    assert derive.bucket15(ms("2026-10-06T19:29:59")) == ms("2026-10-06T19:15:00")


def test_bucket15_takes_no_zone_and_every_result_divides_by_900000():
    assert derive.BUCKET_MS == 900_000
    assert all(derive.bucket15(t) % 900_000 == 0 for t in (0, 1, 899_999, 900_000, 1_791_000_123_456))
    assert not hasattr(derive, "local_buckets")


# --- DM-45 walk -----------------------------------------------------------------------------------


def row(op_id, kind, parent, rowid, caused_by=None, agent_id=None, dup=None):
    return {
        "op_id": op_id,
        "kind": kind,
        "parent": parent,
        "rowid": rowid,
        "caused_by": caused_by,
        "agent_id": agent_id,
        "duplicate_of": dup,
    }


def test_walk_follows_caused_by_not_parent():
    rows = [
        row("s", "session", None, 1),
        row("t1", "turn", "s", 2),
        row("t2", "turn", "s", 3),
        row("c1", "llm_call", "t1", 4),
        row("spawn", "tool_call", "c1", 5),
        row("c2", "llm_call", "t2", 6),
        row("send", "tool_call", "c2", 7),
        row("e1", "engagement", "spawn", 8, caused_by="spawn", agent_id="a"),
        row("e2", "engagement", "spawn", 9, caused_by="send", agent_id="a"),
        row("l1", "llm_call", "e1", 10, agent_id="a"),
        row("l2", "llm_call", "e2", 11, agent_id="a"),
    ]
    got = derive.attr_walk(rows)
    assert got["l1"] == 2 and got["l2"] == 3 and got["c1"] == 2 and got["send"] == 3
    assert "e1" not in got and "t1" not in got


def test_walk_unattributed_cases_and_duplicates():
    rows = [
        row("s", "session", None, 1),
        row("t1", "turn", "s", 2),
        row("e", "engagement", "s", 3, caused_by=None, agent_id="a"),
        row("l", "llm_call", "e", 4, agent_id="a"),
        row("top", "llm_call", "s", 5),
        row("dup", "llm_call", "t1", 6, dup="x"),
    ]
    assert derive.attr_walk(rows) == {"l": None, "top": None}


def test_walk_revisit_and_bad_cause_raise():
    cyc = [
        row("s", "session", None, 1),
        row("ta", "tool_call", "ea", 2),
        row("tb", "tool_call", "eb", 3),
        row("ea", "engagement", "s", 4, caused_by="tb", agent_id="a"),
        row("eb", "engagement", "s", 5, caused_by="ta", agent_id="b"),
    ]
    with pytest.raises(derive.AttributionError, match="revisited"):
        derive.attr_walk([*cyc, row("l", "llm_call", "ea", 6, agent_id="a")])
    bad = [row("s", "session", None, 1), row("e", "engagement", "s", 2, caused_by="s", agent_id="a")]
    with pytest.raises(derive.AttributionError, match="not a tool_call"):
        derive.attr_walk([*bad, row("l", "llm_call", "e", 3, agent_id="a")])
