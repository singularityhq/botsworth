"""D50-T4: readable time in the v4 CLI output (cli.timefmt, Q31 option A)."""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import json
import re

import pytest
from qstore import qstore  # noqa: F401 (the QF fixture)
from qstore_ids import SLOW_PROJECT

from botsworth.cli import main as cli
from botsworth.cli import timefmt
from botsworth.cli.render import render_table
from botsworth.cli.scope import UsageError, day_arg
from botsworth.query import wire

Q = importlib.import_module("botsworth.query")
MS = re.compile(r"_ms(_|$)")
WIRE_TYPES = [c for _, c in inspect.getmembers(wire, inspect.isclass) if dataclasses.is_dataclass(c)]
MS_FIELDS = sorted({f.name for c in WIRE_TYPES for f in dataclasses.fields(c) if MS.search(f.name)})

SLOW_DAY = ("slow", "--project", SLOW_PROJECT, "--from", "2026-10-07", "--to", "2026-10-07")


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv), api=Q)
    out = capsys.readouterr()
    return code, out.out, out.err


# ── the allow-lists cover the wire types ─────────────────────────────────────


@pytest.mark.parametrize("name", MS_FIELDS)
def test_every_ms_field_is_classified(name):
    listed = [s for s in (timefmt.ISO_FIELDS, timefmt.DURATION_FIELDS, timefmt.NESTED_FIELDS) if name in s]
    assert len(listed) == 1, f"{name} must be in exactly one of ISO_FIELDS, DURATION_FIELDS, NESTED_FIELDS"


def test_lists_have_no_dead_entry():
    used = set(MS_FIELDS) | {"ms"}  # `ms` is the duration of a `TimePart` (D54); it has no `_ms` suffix
    assert (timefmt.ISO_FIELDS | timefmt.DURATION_FIELDS | timefmt.NESTED_FIELDS) <= used


def test_no_wire_type_has_a_sibling_name_or_tz():
    for c in WIRE_TYPES:
        names = {f.name for f in dataclasses.fields(c)}
        assert "tz" not in names, c.__name__
        for n in names:
            if n in timefmt.ISO_FIELDS:
                assert n[: -len("_ms")] not in names, (c.__name__, n)
            assert f"{n}_h" not in names, (c.__name__, n)


def test_time_part_duration_is_classified():
    assert [f.name for f in dataclasses.fields(wire.TimePart)] == ["state", "substate", "ms"]
    assert "ms" in timefmt.DURATION_FIELDS
    assert not any(hasattr(wire, n) for n in ("NoneSplit",))  # D54: no separate none split


# ── ISO ──────────────────────────────────────────────────────────────────────

T = 1_791_315_149_000  # 2026-10-06T19:32:29Z


@pytest.mark.parametrize(
    "tz,want",
    [
        ("UTC", "2026-10-06T19:32:29+00:00"),
        ("Asia/Kolkata", "2026-10-07T01:02:29+05:30"),
        ("Asia/Kathmandu", "2026-10-07T01:17:29+05:45"),
        ("America/New_York", "2026-10-06T15:32:29-04:00"),
    ],
)
def test_iso_offsets(tz, want):
    assert timefmt.iso(T, tz) == want
    assert timefmt.render({"start_ms": T}, tz) == {"tz": tz, "start_ms": T, "start": want}


def test_iso_on_a_dst_day_changes_offset():
    # America/New_York 2026-11-01: the clocks go back at 06:00Z; the day has 25 hours.
    before, after = 1_793_512_800_000 - 1000, 1_793_512_800_000
    assert timefmt.iso(before, "America/New_York") == "2026-11-01T01:59:59-04:00"
    assert timefmt.iso(after, "America/New_York") == "2026-11-01T01:00:00-05:00"


def test_milliseconds_are_cut_not_rounded():
    assert timefmt.iso(T + 999, "UTC") == "2026-10-06T19:32:29+00:00"


# ── durations ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ms,want",
    [
        (0, "0s"),
        (999, "0s"),
        (1000, "1s"),
        (45_000, "45s"),
        (59_999, "59s"),
        (60_000, "1m 00s"),
        (725_000, "12m 05s"),
        (3_599_000, "59m 59s"),
        (3_600_000, "1h 0m"),
        (8_100_000, "2h 15m"),
        (86_399_000, "23h 59m"),
        (86_400_000, "1d 0h"),
        (4 * 3_600_000 + 3 * 86_400_000, "3d 4h"),
        (1500.7, "1s"),
    ],
)
def test_human_unit_edges(ms, want):
    assert timefmt.human(ms) == want


# ── render ───────────────────────────────────────────────────────────────────


def test_render_walks_depth_keeps_raw_values_and_nulls():
    data = {
        "wall_ms": 725_000,
        "freshness": {"last_ingest_ms": None},
        "rows": [{"end_ms": T, "dur_ms": None, "name": "x"}],
        "time_split": {
            "parts": [
                {"state": "you", "substate": "ask", "ms": 3_600_000},
                {"state": "none", "substate": "none", "ms": 61_000},
            ]
        },
        "state_ms": {"parts": [{"state": "model", "substate": None, "ms": 2000}]},
        "n": 5,
    }
    out = timefmt.render(data, "UTC")
    assert list(out)[0] == "tz" and out["tz"] == "UTC"
    assert out["wall_ms"] == 725_000 and out["wall_ms_h"] == "12m 05s"
    assert out["freshness"] == {"last_ingest_ms": None, "last_ingest": None}
    assert out["rows"][0] == {
        "end_ms": T,
        "end": "2026-10-06T19:32:29+00:00",
        "dur_ms": None,
        "dur_ms_h": None,
        "name": "x",
    }
    assert [p["ms_h"] for p in out["time_split"]["parts"]] == ["1h 0m", "1m 01s"]
    assert out["time_split"]["parts"][0] == {
        "state": "you",
        "substate": "ask",
        "ms": 3_600_000,
        "ms_h": "1h 0m",
    }
    assert out["state_ms"]["parts"] == [{"state": "model", "substate": None, "ms": 2000, "ms_h": "2s"}]
    assert out["n"] == 5 and "n_h" not in out
    assert data["wall_ms"] == 725_000 and "wall_ms_h" not in data  # the input is not changed


def test_render_leaves_unlisted_fields_alone():
    out = timefmt.render({"elapsed_ms": 5000, "tok_total": 9}, "UTC")
    assert out == {"tz": "UTC", "elapsed_ms": 5000, "tok_total": 9}


@pytest.mark.parametrize(
    "data",
    [
        {"tz": "x"},
        {"start_ms": 1, "start": "x"},
        {"start": "x", "start_ms": 1},
        {"wall_ms": 1, "wall_ms_h": "x"},
    ],
)
def test_render_refuses_a_sibling_name_already_in_use(data):
    with pytest.raises(ValueError, match="already has the key"):
        timefmt.render(data, "UTC")


# ── input forms ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,tz,want",
    [
        ("2026-10-07", "Asia/Kolkata", "2026-10-07"),
        ("2026-10-07T01:02:29+05:30", "Asia/Kolkata", "2026-10-07"),
        ("2026-10-06T19:32:29Z", "Asia/Kolkata", "2026-10-07"),  # UTC evening is the next day in Kolkata
        ("2026-10-06T19:32:29Z", "America/New_York", "2026-10-06"),
        ("2026-10-07T01:02:29+05:30", "UTC", "2026-10-06"),
        (str(T), "Asia/Kolkata", "2026-10-07"),
        (str(T), "UTC", "2026-10-06"),
        ("2026-11-02T04:30:00Z", "America/New_York", "2026-11-01"),  # after the DST change: UTC-5
    ],
)
def test_day_arg_forms(text, tz, want):
    assert day_arg(text, tz) == want


@pytest.mark.parametrize("text", ["tomorrow", "2026-10-07T10:00:00", "10/07/2026", "123", ""])
def test_day_arg_invalid(text):
    with pytest.raises(UsageError):
        day_arg(text, "UTC")


def test_cli_from_to_accept_iso_and_epoch(qstore, capsys):  # noqa: F811
    base = ["--db", str(qstore), "period", "--tz", "UTC"]
    code, plain_out, _ = run(capsys, *base, "--from", "2026-10-07", "--to", "2026-10-07")
    assert code == 0
    code, out, err = run(capsys, *base, "--from", "2026-10-07T03:00:00+00:00", "--to", "2026-10-07T23:00:00Z")
    assert (code, err) == (0, "") and out == plain_out
    code, out, err = run(
        capsys, *base, "--from", "1791331200000", "--to", "1791331200000"
    )  # 2026-10-07T00:00Z
    assert (code, err) == (0, "") and out == plain_out


def test_cli_invalid_day_is_exit_2(qstore, capsys):  # noqa: F811
    code, out, err = run(capsys, "--db", str(qstore), "period", "--from", "soon", "--to", "2026-10-07")
    assert (code, out) == (2, "")
    assert err.startswith("error:") and "soon" in err
    code, out, err = run(
        capsys, "--db", str(qstore), "days", "--window-from", "soon", "--window-to", "2026-10-07"
    )
    assert (code, out) == (2, "")


# ── commands ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tz", ["UTC", "Asia/Kolkata"])
def test_status_and_slow_carry_tz_iso_and_readable_durations(qstore, capsys, tz):  # noqa: F811
    code, out, err = run(capsys, "--db", str(qstore), *SLOW_DAY, "--tz", tz)
    assert (code, err) == (0, "")
    body = json.loads(out)
    assert body["tz"] == tz and list(body)[0] == "tz"
    first = body["sessions"][0]
    assert first["start"] == timefmt.iso(first["start_ms"], tz)
    assert first["wall_ms_h"] == timefmt.human(first["wall_ms"])
    for part in first["time_split"]["parts"] + body["current"]["time_split"]["parts"]:
        assert part["ms_h"] == timefmt.human(part["ms"])
    assert "none_split" not in body["current"]


def test_default_zone_is_the_machine_zone(qstore, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr("botsworth.cli.scope.resolve_tz", lambda: "Asia/Kathmandu")
    code, out, _ = run(capsys, "--db", str(qstore), "status")
    assert code == 0 and json.loads(out)["tz"] == "Asia/Kathmandu"


@pytest.mark.parametrize(
    "argv",
    [
        ["status"],
        ["period"],
        ["facets"],
        ["days", "--window-from", "2026-10-06", "--window-to", "2026-10-07"],
        ["sessions", "-n", "2"],
    ],
)
def test_every_scope_command_has_tz(qstore, capsys, argv):  # noqa: F811
    code, out, err = run(capsys, "--db", str(qstore), *argv, "--tz", "Asia/Kolkata")
    assert (code, err) == (0, "")
    assert json.loads(out)["tz"] == "Asia/Kolkata"


def test_table_shows_siblings_not_raw_ms(qstore, capsys):  # noqa: F811
    code, out, err = run(capsys, "--db", str(qstore), *SLOW_DAY, "--tz", "Asia/Kolkata", "--table")
    assert (code, err) == (0, "")
    assert "tz: Asia/Kolkata" in out
    assert "wall_ms_h=" in out and "wall_ms=" not in out
    assert re.search(r"start=2026-10-0\dT\d\d:\d\d:\d\d\+05:30", out) and "start_ms=" not in out


def test_table_keeps_a_raw_field_without_a_sibling():
    data = {"wall_ms": 3000, "rows": [{"wall_ms": 5000, "wall_ms_h": "5s"}]}
    text = render_table("status", data)
    assert "wall_ms: 3.0s" in text and "wall_ms_h=5s" in text and "wall_ms=" not in text


def test_legacy_commands_are_untouched(qstore, capsys):  # noqa: F811
    code, out, err = run(capsys, "--db", str(qstore), "top", "--kind", "session", "-n", "1")
    assert (code, err) == (0, "")
    assert "tz" not in json.loads(out) and "_ms_h" not in out


@pytest.mark.parametrize("cmd", ["slow", "breakdown", "tasks"])
def test_epilog_mentions_the_siblings_in_one_line(cmd, capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([cmd, "--help"])
    text = capsys.readouterr().out
    assert len([ln for ln in text.splitlines() if "_ms_h" in ln]) == 1
    assert "tz" in text


def test_table_shows_split_parts_with_substates_and_readable_durations(qstore, capsys):  # noqa: F811
    code, out, err = run(capsys, "--db", str(qstore), *SLOW_DAY, "--table")
    assert (code, err) == (0, "")
    line = next(x for x in out.splitlines() if x.startswith("current:"))
    # D54: state/substate with the readable duration; the raw `ms` is not shown beside it
    assert "time_split=[" in line and "you/ask=15m 00s" in line and "you/reply=25m 00s" in line
    assert " none=15s" in line  # none/none is shown as `none`
    assert "ms=" not in line.split("time_split=[")[1].split("]")[0]
    assert "user_waits:" in out


def test_json_user_waits_have_readable_durations(qstore, capsys):  # noqa: F811
    code, out, _ = run(capsys, "--db", str(qstore), *SLOW_DAY)
    body = json.loads(out)
    assert body["user_waits"]["ask"] == {"n": 1, "dur_ms": 900_000, "dur_ms_h": "15m 00s"}
    assert body["user_waits"]["permission"]["n"] == 0
