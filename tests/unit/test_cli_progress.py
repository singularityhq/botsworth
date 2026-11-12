"""cli.progress and render.ingest_lines: the live ingest line and the readable run report (D57)."""

from __future__ import annotations

import io

import pytest

from botsworth.cli.progress import IngestProgress
from botsworth.cli.render import render_table

REPORT = {
    "mode": "incremental",
    "providers": {
        "claude_code": {
            "root": "/home/u/.claude",
            "discovered": 1204,
            "rebuilt": 24,
            "unchanged": 1180,
            "rejected": [],
            "unreadable": [],
        },
        "codex": {
            "root": "/home/u/.codex",
            "discovered": 312,
            "rebuilt": 0,
            "unchanged": 312,
            "rejected": [],
            "unreadable": [],
        },
        "omp": {
            "root": "/home/u/.omp",
            "discovered": 0,
            "rebuilt": 0,
            "unchanged": 0,
            "rejected": [],
            "unreadable": [],
        },
    },
    "store": {"bundles_changed": 24, "loaded": 24, "rejected": []},
    "ms": {"discover": 120.0, "build": 900.0, "store": 4100.0, "total": 12600.0},
}


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def paint(stream: io.StringIO) -> list[str]:
    """Each in-place frame, without the carriage-return protocol."""
    return [seg for seg in stream.getvalue().split("\r") if seg]


def test_draws_phases_and_ends_with_the_summary():
    stream, clock = TTY(), Clock()
    p = IngestProgress(stream, min_interval=0, clock=clock, width=200)
    clock.t = 1.0
    p({"event": "start", "mode": "incremental", "store": "/tmp/s.sqlite"})
    clock.t = 2.0
    p({"event": "scanning", "provider": "claude_code"})
    clock.t = 3.0
    p({"event": "discovered", "provider": "claude_code", "bundles": 1204})
    clock.t = 4.0
    p({"event": "bundle", "done": 37, "total": 1204, "rebuilt": 24, "unchanged": 13, "rejected": 1})
    clock.t = 9.0
    p({"event": "store", "bundles": 1204})
    p.finish(REPORT)

    text = stream.getvalue()
    assert "ingest incremental · /tmp/s.sqlite" in text
    assert "scanning claude_code" in text
    assert "37/1,204 bundles · 24 rebuilt · 13 unchanged · 1 rejected · 4s" in text
    assert "writing store · 1,204 bundles" in text
    assert "ingest: incremental" in text  # the table block, once the line is cleared
    assert "claude_code  1,204 bundles · 24 rebuilt · 1,180 unchanged" in text
    assert "done         12.6s" in text


def test_throttles_repaints_but_never_a_phase_change():
    stream, clock = TTY(), Clock()
    p = IngestProgress(stream, min_interval=1.0, clock=clock, width=200)
    p({"event": "start", "mode": "incremental", "store": "s"})
    clock.t = 0.2
    p({"event": "bundle", "done": 1, "total": 2, "rebuilt": 1, "unchanged": 0, "rejected": 0})
    assert "1/2 bundles" not in stream.getvalue()
    clock.t = 1.3
    p({"event": "bundle", "done": 2, "total": 2, "rebuilt": 1, "unchanged": 1, "rejected": 0})
    assert "2/2 bundles · 1 rebuilt · 1 unchanged" in stream.getvalue()
    clock.t = 1.4
    p({"event": "store", "bundles": 2})  # a phase change paints at once
    assert "writing store" in stream.getvalue()


def test_abort_clears_the_line_without_a_summary():
    stream = TTY()
    p = IngestProgress(stream, min_interval=0, clock=Clock(), width=200)
    p({"event": "start", "mode": "rebuild", "store": "s"})
    p.abort()
    assert "ingest: rebuild" not in stream.getvalue()
    assert stream.getvalue().endswith("\r")


def test_non_tty_writes_nothing():
    stream = io.StringIO()
    p = IngestProgress(stream, min_interval=0)
    assert not p.enabled
    p({"event": "start", "mode": "incremental", "store": "s"})
    p({"event": "bundle", "done": 1, "total": 1, "rebuilt": 1, "unchanged": 0, "rejected": 0})
    p.finish(REPORT)
    p.abort()
    assert stream.getvalue() == ""


def test_a_line_never_exceeds_the_terminal_width():
    stream = TTY()
    p = IngestProgress(stream, min_interval=0, clock=Clock(), width=40)
    p({"event": "start", "mode": "incremental", "store": "/very/long/path/" + "x" * 80})
    assert all(len(frame.strip()) <= 40 for frame in paint(stream))
    assert paint(stream)[0].endswith("…")


def test_unknown_event_is_a_loud_failure():
    p = IngestProgress(TTY(), min_interval=0, clock=Clock())
    with pytest.raises(AssertionError, match="unknown progress event"):
        p({"event": "nonsense"})


def test_a_closed_stream_disables_progress_instead_of_failing_the_run():
    class Broken(TTY):
        def write(self, _: str) -> int:
            raise OSError("stderr is gone")

    p = IngestProgress(Broken(), min_interval=0, clock=Clock())
    p({"event": "start", "mode": "incremental", "store": "s"})
    p.finish(REPORT)


def test_ingest_table_lists_providers_rejections_and_total():
    data = {
        "mode": "rebuild",
        "providers": {
            "claude_code": {
                "root": "/r",
                "discovered": 5,
                "rebuilt": 2,
                "unchanged": 2,
                "rejected": [{"bundle_id": f"s{i}", "reason": "ValueError: bad line"} for i in range(4)],
                "unreadable": [],
            },
            "omp": {
                "root": "/o",
                "discovered": 0,
                "rebuilt": 0,
                "unchanged": 0,
                "rejected": [],
                "unreadable": [],
                "skipped": "root missing",
            },
        },
        "store": {
            "bundles_changed": 2,
            "loaded": 1,
            "rejected": [{"bundle": "claude_code/s9", "error": "load refused"}],
        },
        "ms": {"store": 4100.0, "total": 12600.0},
    }
    text = render_table("ingest", data)
    lines = text.splitlines()
    assert lines[0] == "ingest: rebuild"
    assert lines[1] == "  claude_code  5 bundles · 2 rebuilt · 2 unchanged · 4 rejected"
    assert lines[2] == "  omp          skipped — root missing"
    assert lines[3] == "  store        2 bundles · 1 rejected · 4.1s"
    assert lines[4] == "    rejected claude_code/s9: load refused"  # the store's refusal is named first
    assert "    rejected claude_code/s0: ValueError: bad line" in text
    assert "    ... 2 more rejected (use --json)" in text
    assert lines[-1] == "  done         12.6s"


def test_a_noop_run_has_no_empty_store_line():
    data = {
        "mode": "incremental",
        "providers": {
            "omp": {
                "root": "/o",
                "discovered": 2,
                "rebuilt": 0,
                "unchanged": 2,
                "rejected": [],
                "unreadable": [],
            }
        },
        "store": {"bundles_changed": 0, "loaded": 0, "rejected": []},
        "ms": {"store": 1.0, "total": 4.0},
    }
    lines = render_table("ingest", data).splitlines()
    assert lines[0] == "ingest: incremental"
    assert not any(line.startswith("  store") for line in lines)
    assert lines[-1].startswith("  done")
