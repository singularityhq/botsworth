# ruff: noqa: F811
"""Q0 shared view helpers (views/common.py): session lookup, names, colour slots, money, local day."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from qstore import STREAMS, qstore  # noqa: F401 (the QF fixture; F811 below is its use as an argument)
from stream_load import load_streams

from botsworth.query import NotFound, QueryError, _conn, wire, zone
from botsworth.query.views.common import (
    color_slots,
    lookup_session,
    money,
    project_ref,
    session_name,
)

TZ = "Asia/Kolkata"


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp() * 1000)


@pytest.fixture(scope="module")
def ambiguous(tmp_path_factory) -> Path:
    """Seven sessions whose ids share the prefix `shared-prefix-`, plus one unique id.

    Built by the pipeline from the qstore `szero` stream with the session id replaced.
    """
    base = tmp_path_factory.mktemp("ambiguous")
    streams = base / "streams"
    streams.mkdir()
    text = (STREAMS / "szero.jsonl").read_text()
    for sid in [*(f"shared-prefix-{i}" for i in range(1, 8)), "zzunique-session"]:
        (streams / f"{sid}.jsonl").write_text(text.replace("szero", sid))
    db = base / "p.sqlite"
    result = load_streams(streams=[streams], db=db)
    assert result.rejected == [], result.rejected
    return db


def lookup(db: Path, key: str) -> sqlite3.Row:
    with _conn(db) as conn:
        return lookup_session(conn, key)


# ── QRY-6 ────────────────────────────────────────────────────────────────────


def test_lookup_by_op_id_session_id_and_prefix(qstore, ambiguous):
    by_op = lookup(qstore, "claude_code/s2day/session/-")
    by_id = lookup(qstore, "s2day")
    assert by_op["op_id"] == by_id["op_id"] == "claude_code/s2day/session/-"
    assert by_op["session_id"] == "s2day" and by_op["provider"] == "claude_code"
    assert {"first_ms", "incl_cost_usd", "project", "project_name", "display_title"} <= set(by_op.keys())
    # a prefix of at least 8 characters
    assert lookup(ambiguous, "zzunique")["session_id"] == "zzunique-session"
    assert lookup(ambiguous, "shared-prefix-3")["session_id"] == "shared-prefix-3"


def test_prefix_under_8_is_400(qstore, ambiguous):
    for db, key in ((qstore, "s2d"), (ambiguous, "shared")):
        with pytest.raises(QueryError, match="at least 8") as exc:
            lookup(db, key)
        assert not isinstance(exc.value, NotFound)


def test_ambiguous_prefix_names_up_to_5(ambiguous):
    with pytest.raises(QueryError) as exc:
        lookup(ambiguous, "shared-p")
    message = str(exc.value)
    assert not isinstance(exc.value, NotFound)
    named = [i for i in range(1, 8) if f"codex/shared-prefix-{i}/session/-" in message]
    assert len(named) == 5 and "and 2 more" in message


def test_unknown_is_404(qstore):
    for key in ("nonexistent-session", "claude_code/none/session/-"):
        with pytest.raises(NotFound):
            lookup(qstore, key)


# ── QRY-13 ───────────────────────────────────────────────────────────────────


def named_row(display_title, title_source, project_name, session_id="abcdef0123456789") -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT ? AS display_title, ? AS title_source, ? AS project_name, ? AS session_id",
        (display_title, title_source, project_name, session_id),
    ).fetchone()


def test_display_title_wins_and_fallback_is_project_and_id8():
    assert session_name(named_row("Fix rbac sync", "generated", "app")) == ("Fix rbac sync", "generated")
    assert session_name(named_row(None, "fallback", "bizdemo/src")) == ("bizdemo/src · abcdef01", "fallback")


def test_name_without_project_row_asserts():
    with pytest.raises(AssertionError, match="projects row"):
        session_name(named_row(None, "fallback", None))


def test_fallback_name_is_project_and_id8_from_the_store(qstore):
    # sauto has no title record and no qualifying first ask (DM-10), so it is the fallback case
    row = lookup(qstore, "sauto")
    assert row["project_name"] == "helper"
    assert session_name(row) == ("helper · sauto", "fallback")
    # s2day has a qualifying first ask, which wins over the fallback
    title = "refactor the cost rollup for the weekly report"
    assert session_name(lookup(qstore, "s2day")) == (title, "first_ask")


# ── QRY-14 (the project colour ranking needs the B3 local day rows and `projects`) ──

# Σ day-row cost per project over the whole fixture (expected.json sessions): bizdemo/src 12.1,
# agentsdemo 4.7, app 1.5, idleproj 1.4, skillproj 1.3, harnessproj 0.6, helper 0.5, ompproj 0.5.
SLOTS = {
    "/u/a/bizdemo/src": 1,
    "/u/e/agentsdemo": 2,
    "/u/h/slowproj/app": 3,
    "/u/i/idleproj": 4,
    "/u/g/skillproj": 5,
    "/u/f/harnessproj": 6,
}


def store_as_of(qstore: Path, tmp_path: Path, last_ingest: str, name: str = "asof") -> sqlite3.Connection:
    """A copy of the fixture store with `meta.last_ingest_ms` set (the fixture itself ingests now)."""
    src = sqlite3.connect(qstore)
    dst = sqlite3.connect(tmp_path / f"{name}.sqlite")
    src.backup(dst)
    src.close()
    dst.execute("UPDATE meta SET value = ? WHERE key = 'last_ingest_ms'", (str(ms(last_ingest)),))
    dst.commit()
    dst.row_factory = sqlite3.Row
    return dst


def test_color_slots_rank_30_days_ties_by_key(qstore, tmp_path):
    conn = store_as_of(qstore, tmp_path, "2026-10-12T06:00:00")
    assert color_slots(conn, TZ) == SLOTS
    # the 30-day window ends at the local day of last_ingest_ms: 2026-11-10 is 35 days after the data
    assert color_slots(store_as_of(qstore, tmp_path, "2026-11-10T06:00:00", "late"), TZ) == {}


def test_color_slots_equal_across_zones_when_no_project_crosses_a_rank(qstore, tmp_path):
    """TST-8 (e): one store, three zones; the window edge moves by under a day and no rank changes."""
    conn = store_as_of(qstore, tmp_path, "2026-10-12T06:00:00")
    assert color_slots(conn, "UTC") == color_slots(conn, TZ) == color_slots(conn, "America/New_York") == SLOTS


def test_color_slots_window_edge_follows_the_request_zone(qstore, tmp_path):
    """TST-8 (e): the window is the 30 local days ending at the local day of last_ingest_ms. At 2026-11-04T12Z
    that day is 11-04 in UTC (window from 2026-10-06T00:00Z) and 11-05 in Pacific/Kiritimati (+14; window from
    2026-10-06T10:00Z). `helper` has one bucket at 2026-10-06T03:00Z, given a big cost: only UTC sees it."""
    conn = store_as_of(qstore, tmp_path, "2026-11-04T12:00:00", "edge")
    conn.execute(
        "UPDATE rollup_time SET cost_usd = 100.0 WHERE bucket_start_ms = ? AND root_rowid IN "
        "(SELECT op_rowid FROM sessions WHERE project = '/u/d/helper')",
        (ms("2026-10-06T03:00:00"),),
    )
    assert zone.local_day(ms("2026-11-04T12:00:00"), "Pacific/Kiritimati") == "2026-11-05"
    assert color_slots(conn, "UTC")["/u/d/helper"] == 1
    assert "/u/d/helper" not in color_slots(conn, "Pacific/Kiritimati")


def test_color_slot_independent_of_request(qstore, tmp_path):
    conn = store_as_of(qstore, tmp_path, "2026-10-12T06:00:00")
    refs = {p: project_ref(conn, p, TZ) for p in (*SLOTS, "/u/d/helper")}
    assert {p: r.color_slot for p, r in refs.items()} == {**SLOTS, "/u/d/helper": None}
    assert refs["/u/h/slowproj/app"] == wire.ProjectRef("/u/h/slowproj/app", "app", 3)
    assert project_ref(conn, "/u/a/bizdemo/src", TZ) == refs["/u/a/bizdemo/src"]  # same store, same slot


def test_no_ingest_means_no_colour_slots(tmp_path):
    db = tmp_path / "empty.sqlite"
    conn = sqlite3.connect(db)
    from botsworth.store import schema

    schema.ensure(conn)
    conn.row_factory = sqlite3.Row
    assert color_slots(conn, TZ) == {}


# ── QRY-4, QRY-11 ────────────────────────────────────────────────────────────


def test_local_day_ist_half_hour():
    assert zone.local_day(ms("2026-10-06T19:00:00"), TZ) == "2026-10-07"
    assert zone.local_day(ms("2026-10-06T18:29:59"), TZ) == "2026-10-06"
    assert zone.local_day(ms("2026-10-06T18:30:00"), TZ) == "2026-10-07"
    assert zone.local_day(ms("2026-10-06T19:00:00"), "UTC") == "2026-10-06"


def test_money_keeps_null_zero_and_lower_bound_apart():
    assert money(3.0, True, 1) == wire.Money(3.0, True, 1)
    assert money(0.0, False, 0) == wire.Money(0.0, False, 0)
    assert money(None, False, 0).cost_usd is None
    assert money(1.0, 1, 2) == wire.Money(1.0, True, 2)  # sessions.cost_lower_bound is 0/1
