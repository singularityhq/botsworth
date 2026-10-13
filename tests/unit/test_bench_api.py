# ruff: noqa: F811
"""Q4: the API latency bench (TST-5) runs on the QF store and opens stores read-only."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

from qstore import qroots, qstore  # noqa: F401 (the QF fixtures)

BENCH = Path(__file__).resolve().parents[1] / "bench" / "bench_api.py"


def load_bench():
    spec = importlib.util.spec_from_file_location("bench_api", BENCH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bench_api"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_self_test_runs_on_fixture(qstore, qroots, capsys):
    """TST-5: every API-50 and CLI-24 case answers once on the fixture store, without targets."""
    bench = load_bench()
    code = bench.main(
        ["--self-test", "--db", str(qstore), "--roots", str(next(iter(qroots.values())).parent)]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "0 failed" in out
    assert out.count("p95") == 30  # 27 HTTP cases and 3 CLI cases print a line each


def test_bench_opens_read_only(qstore, qroots, monkeypatch):
    """TST-5, Q4: every connection the bench or the query layer opens is a `mode=ro` URI."""
    bench = load_bench()
    opened: list[tuple[tuple, dict]] = []
    real = sqlite3.connect

    def spy(*args, **kwargs):
        opened.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    bench.main(["--self-test", "--db", str(qstore), "--roots", str(next(iter(qroots.values())).parent)])
    assert opened
    for args, kwargs in opened:
        assert "mode=ro" in str(args[0]), args
        assert kwargs.get("uri") is True, kwargs


def test_bench_skips_when_store_missing(tmp_path, capsys):
    """TST-5: a missing store skips with a message and exit 0."""
    bench = load_bench()
    assert bench.main(["--db", str(tmp_path / "none.sqlite")]) == 0
    assert "skipped" in capsys.readouterr().out
