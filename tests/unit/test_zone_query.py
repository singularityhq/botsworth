# ruff: noqa: F811
"""D50 zone tests, query and HTTP layers (TST-8 (a)-(e), (h); QRY-3, QRY-4, API-3, API-6).

One fixture store (the QF store); no test re-ingests to change a zone. Tests that need a call at a chosen
instant move the `rollup_time` buckets of `s2day` (two llm_calls costing 3.0 and 5.0) in a copy of the store.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest
from explorer_store import _copy, xstore  # noqa: F401  (pytest fixture)

from botsworth.http.server import make_server
from botsworth.query import QueryError, wire
from botsworth.query.scope import Scope, parse_scope
from botsworth.query.views import explorer as ex


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp() * 1000)


def costs(series: wire.DaySeries) -> dict[str, float]:
    return {b.day: b.cost_usd for b in series.days}


@pytest.fixture
def moved(xstore: Path, tmp_path: Path):
    """A copy of the store whose `s2day` llm buckets can be moved to chosen instants."""
    db = _copy(xstore, tmp_path / "moved.sqlite")

    def retime(first: str, second: str) -> Path:
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT rowid FROM rollup_time WHERE n_llm_calls > 0 AND root_rowid = "
            "(SELECT op_rowid FROM sessions WHERE session_id = 's2day') ORDER BY bucket_start_ms"
        ).fetchall()
        assert len(rows) == 2
        for (rowid,), at in zip(rows, (first, second), strict=True):
            conn.execute("UPDATE rollup_time SET bucket_start_ms = ? WHERE rowid = ?", (ms(at), rowid))
        conn.commit()
        conn.close()
        return db

    return retime


# ── (a) the zone comes from the request ─────────────────────────────────────


def test_a_one_store_three_zones_three_day_splits(xstore):
    window = ("2026-10-05", "2026-10-12")
    by_zone = {
        tz: costs(ex.day_series(Scope(tz=tz, show_automated=True), *window, db=xstore))
        for tz in ("Asia/Kolkata", "UTC", "America/New_York")
    }
    assert by_zone["Asia/Kolkata"] != by_zone["UTC"] != by_zone["America/New_York"]
    assert by_zone["Asia/Kolkata"] != by_zone["America/New_York"]
    totals = {tz: pytest.approx(sum(c.values())) for tz, c in by_zone.items()}
    assert len({round(sum(c.values()), 6) for c in by_zone.values()}) == 1, totals
    # s2day: calls at 17:00Z and 19:00Z on 2026-10-06 are one UTC day and two IST days (22:30 and 00:30)
    assert by_zone["UTC"]["2026-10-06"] - by_zone["Asia/Kolkata"]["2026-10-06"] == pytest.approx(5.0)


def test_a_session_row_day_splits_follow_the_zone(xstore):
    def splits(tz: str) -> list[tuple[str, float]]:
        page = ex.list_sessions(Scope(tz=tz, from_day="2026-10-06", to_day="2026-10-07"), db=xstore)
        (row,) = [r for r in page.rows if r.session_id == "s2day"]
        return [(d.day, d.cost_usd) for d in row.day_split]

    assert splits("Asia/Kolkata") == [("2026-10-06", 3.0), ("2026-10-07", 5.0)]
    assert splits("UTC") == [("2026-10-06", 8.0)]


def test_a_membership_period_is_the_local_day_range(xstore):
    def n(tz: str, day: str) -> int:
        return ex.period_summary(Scope(tz=tz, from_day=day, to_day=day), db=xstore).totals.n_sessions

    # s2day has no call on 2026-10-07 in UTC and one in IST (00:30)
    assert n("Asia/Kolkata", "2026-10-07") == n("UTC", "2026-10-07") + 1


# ── (b) a +5:45 zone ────────────────────────────────────────────────────────


def test_b_kathmandu_day_edge_at_1815z(moved):
    db = moved("2026-10-06T18:00:00", "2026-10-06T18:15:00")  # buckets of a call at 18:14Z and one at 18:15Z
    window = ("2026-10-06", "2026-10-07")

    def split(tz: str) -> dict[str, float]:
        return costs(ex.day_series(Scope(tz=tz, q="s2day", show_automated=True), *window, db=db))

    assert split("UTC") == {"2026-10-06": 8.0, "2026-10-07": 0}  # one UTC date
    assert split("Asia/Kathmandu") == {"2026-10-06": 3.0, "2026-10-07": 5.0}  # 18:15Z is local midnight
    # 18:15Z is 23:45 in IST: the IST edge is 18:30Z, so both calls are still on 10-06 there
    assert split("Asia/Kolkata") == {"2026-10-06": 8.0, "2026-10-07": 0}


# ── (c) DST days ────────────────────────────────────────────────────────────


def ny_costs(db: Path, first: str, last: str) -> dict[str, float]:
    return costs(ex.day_series(Scope(tz="America/New_York", show_automated=True), first, last, db=db))


def test_c_25_hour_day_counts_each_call_once(moved):
    # 2026-11-01 runs 04:00Z to 05:00Z the next day; 06:30Z is in the repeated hour, 04:45Z the last quarter
    db = moved("2026-11-01T06:30:00", "2026-11-02T04:45:00")
    got = ny_costs(db, "2026-10-31", "2026-11-02")
    assert got["2026-11-01"] == 8.0 and got["2026-10-31"] == 0 and got["2026-11-02"] == 0


def test_c_25_hour_day_edge_is_local_midnight(moved):
    db = moved("2026-11-02T04:45:00", "2026-11-02T05:00:00")
    got = ny_costs(db, "2026-11-01", "2026-11-02")
    assert (got["2026-11-01"], got["2026-11-02"]) == (3.0, 5.0)


def test_c_23_hour_day_edge_is_local_midnight(moved):
    # 2026-03-08 runs 05:00Z to 04:00Z the next day
    db = moved("2026-03-09T03:45:00", "2026-03-09T04:00:00")
    got = ny_costs(db, "2026-03-08", "2026-03-09")
    assert (got["2026-03-08"], got["2026-03-09"]) == (3.0, 5.0)


# ── (d) unknown zone ────────────────────────────────────────────────────────


def test_d_unknown_zone_is_a_query_error():
    with pytest.raises(QueryError, match="unknown time zone"):
        Scope(tz="Not/AZone")
    with pytest.raises(QueryError, match="unknown time zone"):
        parse_scope({"tz": "Not/AZone"})
    with pytest.raises(QueryError, match="tz"):
        parse_scope({"tz": 5})


def test_scope_without_tz_takes_the_machine_zone(monkeypatch):
    monkeypatch.setenv("BOTSWORTH_TZ", "Asia/Kathmandu")
    assert Scope().tz == parse_scope({}).tz == "Asia/Kathmandu"
    assert parse_scope({"tz": "UTC"}).tz == "UTC"


def test_d_session_detail_unknown_zone(xstore):
    from botsworth.query.views.session import session_detail

    with pytest.raises(QueryError, match="unknown time zone"):
        session_detail("s2day", db=xstore, tz="Not/AZone")


# ── HTTP: (d) 400 JSON, (h) ETag ────────────────────────────────────────────


@pytest.fixture
def get(xstore: Path):
    srv = make_server(port=0, db=xstore, ui_dir=None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def fetch(path: str):
        try:
            with urllib.request.urlopen(base + path) as r:
                return r.status, json.loads(r.read()), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), e.headers

    yield fetch
    srv.shutdown()
    srv.server_close()


@pytest.mark.parametrize(
    "path",
    [
        "/api/explorer/summary?tz=Not/AZone",
        "/api/explorer/days?window_from=2026-10-05&window_to=2026-10-12&tz=Not/AZone",
        "/api/explorer/sessions?tz=Not/AZone",
        "/api/explorer/facets?tz=Not/AZone",
        "/api/palette?text=a&tz=Not/AZone",
        "/api/sessions/s2day?tz=Not/AZone",
        "/api/projects?tz=Not/AZone",
    ],
)
def test_d_http_unknown_tz_is_400_json(get, path):
    status, body, headers = get(path)
    assert status == 400 and headers["Content-Type"].startswith("application/json")
    assert body["code"] == "bad_request" and "unknown time zone" in body["error"]


def test_http_tz_param_sets_the_day_split(get):
    q = "/api/explorer/days?window_from=2026-10-06&window_to=2026-10-07&auto=show"
    kolkata = {d["day"]: d["cost_usd"] for d in get(q + "&tz=Asia/Kolkata")[1]["days"]}
    utc = {d["day"]: d["cost_usd"] for d in get(q + "&tz=UTC")[1]["days"]}
    assert kolkata != utc and sum(kolkata.values()) == pytest.approx(sum(utc.values()))


def test_http_post_query_takes_tz_from_the_url(get, xstore):
    srv = make_server(port=0, db=xstore, ui_dir=None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        body = {
            "scope": {"all": True},
            "select": "descendants",
            "metrics": ["cost_usd"],
            "bucket": {"time": "day"},
        }

        def post(qs: str, payload: dict) -> list[int]:
            req = urllib.request.Request(
                base + "/api/query" + qs, json.dumps(payload).encode(), method="POST"
            )
            with urllib.request.urlopen(req) as r:
                return sorted({row["bucket"] for row in json.loads(r.read())["rows"]})

        assert (
            post("?tz=Asia/Kolkata", body)
            == post("", {**body, "tz": "Asia/Kolkata"})
            != post("?tz=UTC", body)
        )
    finally:
        srv.shutdown()
        srv.server_close()


def test_h_etag_ignores_tz_and_freshness_has_none(get):
    s1, b1, h1 = get("/api/explorer/summary?tz=Asia/Kolkata")
    s2, b2, h2 = get("/api/explorer/summary?tz=UTC")
    assert (s1, s2) == (200, 200)
    assert h1["ETag"] == h2["ETag"] and "tz" not in h1["ETag"].lower()
    assert "tz" not in b1["freshness"] and list(b1["freshness"]) == list(b2["freshness"])
    assert get("/api/ingest/status")[1]["freshness"].get("tz") is None
