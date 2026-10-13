"""Request-zone arithmetic (DM-41 [D50], QRY-3, QRY-4): resolve_tz, day, week and hour edges, local_day."""

import logging
from datetime import UTC, datetime

import pytest

from botsworth.query import QueryError
from botsworth.query import zone as tz


def link(tmp_path, target):
    path = tmp_path / "localtime"
    path.symlink_to(target)
    return path


def test_order_botsworth_tz_then_tz_then_localtime_then_utc(tmp_path, caplog):
    lt = link(tmp_path, "/var/db/timezone/zoneinfo/Asia/Kolkata")
    both = {"BOTSWORTH_TZ": "Europe/Paris", "TZ": "America/New_York"}
    assert tz.resolve_tz(both, lt) == "Europe/Paris"
    assert tz.resolve_tz({"TZ": "America/New_York"}, lt) == "America/New_York"
    assert tz.resolve_tz({"BOTSWORTH_TZ": "", "TZ": ""}, lt) == "Asia/Kolkata"  # empty counts as unset
    plain = tmp_path / "plain"
    plain.write_bytes(b"TZif")  # a regular file, not a symlink: no zone name
    assert tz.resolve_tz({}, plain) == "UTC"
    with caplog.at_level(logging.WARNING):
        assert tz.resolve_tz({}, tmp_path / "absent") == "UTC"
    assert "using UTC" in caplog.text


def test_invalid_zone_raises(tmp_path):
    with pytest.raises(QueryError, match="Not/AZone"):
        tz.resolve_tz({"BOTSWORTH_TZ": "Not/AZone"}, tmp_path / "absent")
    with pytest.raises(QueryError, match="Nowhere"):
        tz.resolve_tz({}, link(tmp_path, "/x/zoneinfo/Nowhere/City"))


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp()) * 1000


def test_kathmandu_day_starts_at_1815_utc():
    # +5:45: local midnight of 2026-10-07 is 2026-10-06T18:15Z
    assert tz.day_bounds("2026-10-07", "Asia/Kathmandu") == (
        ms("2026-10-06T18:15:00"),
        ms("2026-10-07T18:15:00"),
    )
    assert tz.local_day(ms("2026-10-06T18:14:00"), "Asia/Kathmandu") == "2026-10-06"
    assert tz.local_day(ms("2026-10-06T18:15:00"), "Asia/Kathmandu") == "2026-10-07"


def test_kolkata_day_and_utc_day_differ():
    assert tz.day_bounds("2026-10-07", "Asia/Kolkata")[0] == ms("2026-10-06T18:30:00")
    assert tz.day_bounds("2026-10-07", "UTC")[0] == ms("2026-10-07T00:00:00")


def test_new_york_dst_days_are_25_and_23_hours():
    fall = tz.day_bounds("2026-11-01", "America/New_York")
    spring = tz.day_bounds("2026-03-08", "America/New_York")
    assert (fall[1] - fall[0]) // 3_600_000 == 25
    assert (spring[1] - spring[0]) // 3_600_000 == 23
    assert (
        tz.day_bounds("2026-10-07", "America/New_York")[1]
        - tz.day_bounds("2026-10-07", "America/New_York")[0]
    ) == 86_400_000


def test_every_edge_is_a_15_minute_bucket_start():
    for name in ("Asia/Kathmandu", "Asia/Kolkata", "America/New_York", "Pacific/Chatham", "UTC"):
        edges = tz.day_edges("2026-03-01", "2026-11-30", name)
        edges += tz.week_edges("2026-03-01", "2026-11-30", name)
        edges += tz.hour_edges(ms("2026-03-07T00:00:00"), ms("2026-03-10T00:00:00"), name)
        assert all(e % 900_000 == 0 for e in edges), name


def test_day_edges_are_n_plus_one_and_contiguous():
    edges = tz.day_edges("2026-10-30", "2026-11-02", "America/New_York")
    assert len(edges) == 5
    assert [b - a for a, b in zip(edges, edges[1:], strict=False)] == [
        86_400_000,
        86_400_000,
        90_000_000,
        86_400_000,
    ]
    assert tz.day_edges("2026-10-07", "2026-10-07", "UTC") == list(tz.day_bounds("2026-10-07", "UTC"))


def test_week_edges_start_on_local_monday():
    # 2026-10-07 is a Wednesday; its Monday is 2026-10-05
    edges = tz.week_edges("2026-10-07", "2026-10-14", "Asia/Kolkata")
    assert edges[0] == tz.day_bounds("2026-10-05", "Asia/Kolkata")[0]
    assert edges[-1] == tz.day_bounds("2026-10-19", "Asia/Kolkata")[0]
    assert len(edges) == 3


def test_hour_edges_new_york_repeat_hour_is_two_buckets():
    start = tz.day_bounds("2026-11-01", "America/New_York")[0]  # 04:00Z
    edges = tz.hour_edges(start, start + 25 * 3_600_000, "America/New_York")
    assert len(edges) == 26  # 25 hourly buckets across the 25 h day
    assert edges[1] - edges[0] == 3_600_000
    assert edges[0] == ms("2026-11-01T04:00:00") and edges[-1] == ms("2026-11-02T05:00:00")


def test_hour_edges_in_half_hour_zone_fall_on_the_half_hour():
    edges = tz.hour_edges(ms("2026-10-06T18:40:00"), ms("2026-10-06T20:00:00"), "Asia/Kolkata")
    assert edges == [ms("2026-10-06T18:30:00"), ms("2026-10-06T19:30:00"), ms("2026-10-06T20:30:00")]


@pytest.mark.parametrize(
    "call",
    [
        lambda: tz.day_bounds("2026-10-07", "Not/AZone"),
        lambda: tz.day_edges("2026-10-07", "2026-10-08", "Not/AZone"),
        lambda: tz.week_edges("2026-10-07", "2026-10-08", "Not/AZone"),
        lambda: tz.hour_edges(0, 1, "Not/AZone"),
        lambda: tz.local_day(0, "Not/AZone"),
        lambda: tz.day_bounds("2026-13-01", "UTC"),
    ],
)
def test_unknown_zone_or_bad_day_is_query_error(call):
    with pytest.raises(QueryError):
        call()
