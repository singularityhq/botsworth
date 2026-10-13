# ruff: noqa: F811
"""Q1 explorer endpoints over HTTP (API-10..API-14, API-30): key sets equal the wire types (TST-2 Shape)."""

from __future__ import annotations

import dataclasses
import json
import threading
import types
import typing
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from explorer_store import xstore  # noqa: F401  (pytest fixture)

from botsworth.http.server import make_server
from botsworth.query import wire


@pytest.fixture
def get(xstore: Path) -> Iterator[Callable[[str], tuple[int, Any]]]:
    srv = make_server(port=0, db=xstore, ui_dir=None)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def fetch(path: str) -> tuple[int, Any]:
        try:
            with urllib.request.urlopen(base + path) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    yield fetch
    srv.shutdown()
    srv.server_close()


def assert_shape(obj: Any, cls: type, where: str = "$") -> None:
    """`obj` (decoded JSON) has exactly the keys of dataclass `cls`, nested types included."""
    hints = typing.get_type_hints(cls)
    assert isinstance(obj, dict), f"{where}: expected an object, got {obj!r}"
    assert list(obj) == [f.name for f in dataclasses.fields(cls)], f"{where}: keys of {cls.__name__}"
    for name, hint in hints.items():
        _check_value(obj[name], hint, f"{where}.{name}")


def _check_value(value: Any, hint: Any, where: str) -> None:
    origin = typing.get_origin(hint)
    if origin in (typing.Union, types.UnionType):
        options = [a for a in typing.get_args(hint) if a is not type(None)]
        if value is not None and len(options) == 1:
            _check_value(value, options[0], where)
    elif origin is list:
        (item,) = typing.get_args(hint)
        for i, v in enumerate(value):
            _check_value(v, item, f"{where}[{i}]")
    elif dataclasses.is_dataclass(hint):
        assert_shape(value, hint, where)


SCOPE = "from=2026-10-06&to=2026-10-10"


def test_summary_shape(get):
    status, body = get(f"/api/explorer/summary?{SCOPE}")
    assert status == 200
    assert_shape(body, wire.PeriodSummary)
    assert body["totals"]["n_sessions"] == 12 and body["projects"]
    status, body = get("/api/explorer/summary?from=2026-10-11&to=2026-10-12&q=migration")
    assert_shape(body, wire.PeriodSummary) and body["empty"]["all_time_matches"] == 2


def test_days_shape(get):
    status, body = get(
        "/api/explorer/days?window_from=2026-10-06&window_to=2026-10-12&project=/u/a/bizdemo/src"
    )
    assert status == 200
    assert_shape(body, wire.DaySeries)
    assert [d["day"] for d in body["days"]] == [f"2026-10-{d:02d}" for d in range(6, 13)]
    assert body["days"][1]["by_project"][0]["project"] == "/u/a/bizdemo/src"
    status, body = get("/api/explorer/days?window_from=2026-10-05&window_to=2026-10-18&bucket=week")
    assert_shape(body, wire.DaySeries) and len(body["days"]) == 2


def test_sessions_shape(get):
    status, body = get(f"/api/explorer/sessions?{SCOPE}&sort=cost&limit=3&offset=1")
    assert status == 200
    assert_shape(body, wire.SessionPage)
    assert (body["total"], body["offset"], body["limit"], body["groups"]) == (12, 1, 3, None)
    assert [r["session_id"] for r in body["rows"]] == [
        "sagents",
        "sunpriced",
        "sstretch",
    ]  # 4.70, 3.00, 1.40 after s2day 8.00
    status, body = get(f"/api/explorer/sessions?{SCOPE}&group=project&group_limit=1")
    assert_shape(body, wire.SessionPage)
    assert (
        body["rows"] == [] and len(body["groups"]) == 8 and all(len(g["rows"]) == 1 for g in body["groups"])
    )
    status, body = get(f"/api/explorer/sessions?{SCOPE}&group=day&group_key=2026-10-09")
    assert_shape(body, wire.SessionPage) and body["total"] == 3 and body["groups"] is None


def test_facets_shape(get):
    status, body = get(f"/api/explorer/facets?{SCOPE}&provider=omp")
    assert status == 200
    assert_shape(body, wire.Facets)
    assert [v["value"] for v in body["providers"]] == ["claude_code", "codex", "omp"]


def test_palette_shape(get):
    status, body = get("/api/palette?text=migration&from=2026-10-08&to=2026-10-08")
    assert status == 200
    assert_shape(body, wire.PaletteResult)
    assert [s["session_id"] for s in body["sessions"]] == ["sharness", "sagents"]
    status, body = get("/api/palette?text=model:synth")
    assert_shape(body, wire.PaletteResult) and body["filter_values"][0]["value"] == "synth-model-1"
    status, body = get("/api/palette?text=src")
    assert_shape(body, wire.PaletteResult) and len(body["projects"]) == 2
    status, body = get("/api/palette")
    assert status == 200 and (body["filter_values"], body["projects"], body["sessions"]) == ([], [], [])


def test_ingest_status_shape(get):
    status, body = get("/api/ingest/status")
    assert status == 200
    assert_shape(body, wire.IngestStatus)
    assert body["store_state"] == "ready" and body["ingest"] == {
        "running": False,
        "started_ms": None,
        "pid": None,
    }


@pytest.mark.parametrize(
    "path, param",
    [
        ("/api/explorer/summary?from=2026-13-01&to=2026-13-02", "from"),
        ("/api/explorer/summary?from=2026-10-01", "to"),  # one null day (API-3)
        ("/api/explorer/summary?to=2026-10-01", "from"),
        ("/api/explorer/summary?from=2026-10-09&to=2026-10-01", "after"),
        ("/api/explorer/days?window_from=2026-10-01", "window_to"),
        ("/api/explorer/days?window_from=2025-01-01&window_to=2026-10-01", "400"),  # a window over 400 days
        ("/api/explorer/days?window_from=2026-10-01&window_to=2026-10-02&bucket=month", "bucket"),
        ("/api/explorer/sessions?limit=1001", "limit"),
        ("/api/explorer/sessions?limit=0", "limit"),
        ("/api/explorer/sessions?sort=model", "sort"),
        ("/api/explorer/sessions?dir=up", "dir"),
        ("/api/explorer/sessions?group=week", "group"),
        ("/api/explorer/sessions?group_limit=51", "group_limit"),
        ("/api/explorer/sessions?offset=-1", "offset"),
        ("/api/explorer/sessions?group_key=x", "group_key"),  # a key without a grouping
        ("/api/explorer/facets?auto=hide", "auto"),
        ("/api/palette?text=x&from=2026-10-01", "to"),
    ],
)
def test_bad_params_400(get, path, param):
    status, body = get(path)
    assert status == 400 and body["code"] == "bad_request", body
    assert param in body["error"], body
