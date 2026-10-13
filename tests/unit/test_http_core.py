"""Q0 HTTP core: route table, parameter parsing, error codes, store states, caching, gzip, assets."""

from __future__ import annotations

import gzip
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fake_query import FakeQuery

from botsworth.http import params as P
from botsworth.http.server import ROUTES, make_server, match_routes
from botsworth.query import NotFound, QueryError, SourceMissing
from botsworth.query.scope import Scope
from botsworth.store import ingest

SPEC = Path(__file__).resolve().parents[2] / "docs" / "specs" / "interfaces.md"
TZ = "Asia/Kolkata"


# ── helpers ──────────────────────────────────────────────────────────────────


def build_store(path: Path, parser_version: str = "q0-1") -> Path:
    """An empty current-schema store written by the ingest's own meta step (no hand-written rows)."""
    ingest.ingest(db=path, parser_version=parser_version)
    return path


class Resp:
    def __init__(self, status: int, headers: Any, raw: bytes):
        self.status, self.headers, self.raw = status, headers, raw

    @property
    def body(self) -> Any:
        raw = gzip.decompress(self.raw) if self.headers.get("Content-Encoding") == "gzip" else self.raw
        return json.loads(raw) if raw else None


def request(
    base: str, path: str, method: str = "GET", headers: dict[str, str] | None = None, data=None
) -> Resp:
    req = urllib.request.Request(base + path, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req) as r:
            return Resp(r.status, r.headers, r.read())
    except urllib.error.HTTPError as e:
        return Resp(e.code, e.headers, e.read())


def start(db: Path | None, ui_dir: Path | None = None, api: Any = None) -> tuple[Any, str]:
    kw = {"api": api} if api is not None else {}
    srv = make_server(port=0, db=db, ui_dir=ui_dir, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Callable[..., str]]:
    servers = []

    def go(db: Path | None, ui_dir: Path | None = None, api: Any = None) -> str:
        srv, base = start(db, ui_dir, api)
        servers.append(srv)
        return base

    yield go
    for srv in servers:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return build_store(tmp_path / "store.sqlite")


# ── route table (API-1) ──────────────────────────────────────────────────────


def _spec_index() -> list[tuple[str, str, str]]:
    text = SPEC.read_text()
    section = text[text.index("### 3.2 Endpoint index") : text.index("### 3.3 Shared wire types")]
    rows = re.findall(r"^\| (GET|POST) \| `([^`]+)` \| `([^`]+)` \|", section, re.M)
    assert len(rows) >= 24
    return rows


def test_every_spec_route_is_in_route_table():
    table = {(r.method, r.pattern): r.view_name for r in ROUTES}
    for method, path, function in _spec_index():
        assert table.get((method, path)) == function, (method, path)
    spec = {(m, p) for m, p, _ in _spec_index()}
    assert set(table) == spec, "ROUTES has a route the spec index does not list"


def test_no_two_routes_answer_one_request():
    for method, path, _ in _spec_index():
        probe = re.sub(r"\{\w+\}", "x", path)
        hits = [r for r, _ in match_routes(probe) if r.method == method]
        assert len(hits) == 1, (method, path)


@pytest.mark.parametrize(
    "target,view,expected",
    [
        (
            "/api/sessions/claude_code%2Fabc%2Fsession%2F-/find?kind=search&text=rbac&limit=5",
            "find",
            {
                "session": "claude_code/abc/session/-",
                "kind": "search",
                "measure": "cost",
                "text": "rbac",
                "limit": 5,
            },
        ),
        (
            "/api/turns/t%231/rows?from_ms=5&to_ms=9",
            "turn_rows",
            {"turn": "t#1", "from_ms": 5, "to_ms": 9},
        ),
        (
            "/api/turns/t1/histogram?bins=50",
            "histogram",
            {"scope_op": "t1", "bins": 50, "from_ms": None, "to_ms": None},
        ),
        ("/api/ops/e1/rows", "agent_rows", {"engagement": "e1", "offset": 0, "limit": 500}),
        ("/api/ops/op%2F1/detail", "detail", {"op": "op/1"}),
        ("/api/sessions/s1/resolve?id=gap-5", "resolve", {"session": "s1", "id": "gap-5"}),
        ("/api/sessions/s1/turns", "session_turns", {"session": "s1", "min_gap_ms": 120000}),
        ("/api/palette?text=rbac&model=opus", "palette", None),
    ],
)
def test_route_parse_gives_view_kwargs(target, view, expected):
    path, _, qs = target.partition("?")
    (route, ids), *_ = [m for m in match_routes(path) if m[0].method == "GET"]
    assert route.view_name == view
    kwargs = route.parse(ids, P.parse_query(qs))
    if expected is not None:
        assert kwargs == expected
    else:
        assert kwargs["text"] == "rbac" and kwargs["scope"].models == ("opus",)


# ── parameters (API-2, API-3) ────────────────────────────────────────────────


def test_repeated_keys_and_bool01():
    q = P.parse_query("provider=codex&provider=omp&auto=show&model=a&model=b&x=1&q=rbac+opus&flag=1&off=0")
    scope = P.scope_from_query(q)
    assert scope == Scope(None, None, "rbac opus", ("codex", "omp"), ("a", "b"), (), (), True)
    assert P.bool01(q, "flag") is True and P.bool01(q, "off") is False and P.bool01(q, "absent") is False
    with pytest.raises(QueryError, match="flag"):
        P.bool01(P.parse_query("flag=yes"), "flag")
    assert P.scope_from_query(P.parse_query("")).show_automated is False


def test_unknown_param_ignored():
    scope = P.scope_from_query(P.parse_query("from=2026-10-01&to=2026-10-02&wat=1&sort=cost"))
    assert (scope.from_day, scope.to_day) == ("2026-10-01", "2026-10-02")


@pytest.mark.parametrize(
    "target,param",
    [
        ("/api/explorer/summary?from=2026-10-01", "from"),
        ("/api/explorer/summary?from=10-01-2026&to=2026-10-02", "from"),
        ("/api/explorer/summary?from=2026-10-09&to=2026-10-02", "from"),
        ("/api/explorer/summary?auto=hide", "auto"),
        ("/api/explorer/sessions?limit=1001", "limit"),
        ("/api/explorer/sessions?limit=abc", "limit"),
        ("/api/explorer/sessions?sort=bogus", "sort"),
        ("/api/explorer/sessions?dir=up", "dir"),
        ("/api/explorer/sessions?group=week", "group"),
        ("/api/explorer/days?window_from=2026-10-01", "window_to"),
        ("/api/explorer/days?window_from=2026-10-01&window_to=2026-13-01", "window_to"),
        ("/api/explorer/days?window_from=2026-10-01&window_to=2026-10-02&bucket=month", "bucket"),
        ("/api/sessions/s1/turns?min_gap_ms=1000", "min_gap_ms"),
        ("/api/sessions/s1/find", "kind"),
        ("/api/sessions/s1/find?kind=search", "text"),
        ("/api/sessions/s1/find?kind=nope", "kind"),
        ("/api/sessions/s1/find?kind=biggest&measure=bytes", "measure"),
        ("/api/sessions/s1/resolve", "id"),
        ("/api/turns/t1/rows?from_ms=5", "to_ms"),
        ("/api/ops/e1/rows?limit=2001", "limit"),
        ("/api/sessions/s1/histogram?bins=0", "bins"),
        ("/api/source", "src"),
        ("/api/source?src=x&limit=1048577", "limit"),
        ("/api/source?src=x&part=bogus", "part"),
    ],
)
def test_malformed_value_is_400_naming_param(served, store, target, param):
    r = request(served(store), target)
    assert r.status == 400 and r.body["code"] == "bad_request"
    assert param in r.body["error"], r.body


# ── error codes (API-4) ──────────────────────────────────────────────────────


class FaultApi(FakeQuery):
    def show(self, op_id, db=None):
        raise NotFound(f"no op {op_id}")

    def source(self, id_or_src, part=None, offset=0, max_bytes=65536, db=None):
        raise SourceMissing(id_or_src)

    def sessions(self, db=None, **kw):
        return 1 / 0

    def timeline(self, op_id, depth=3, db=None):
        raise QueryError("depth is wrong")


def test_error_codes(served, tmp_path, store):
    base = served(store, api=FaultApi())
    cases = [
        (request(base, "/api/timeline-nope"), 404, "not_found"),
        (request(base, "/api/ops/zz/timeline"), 400, "bad_request"),
        (request(base, "/api/ops/zz"), 404, "not_found"),
        (request(base, "/api/source?src=gone"), 404, "source_missing"),
        (request(base, "/api/ingest/status", "POST", data=b"{}"), 405, "method_not_allowed"),
        (request(base, "/api/query"), 405, "method_not_allowed"),
        (request(base, "/api/sessions", "DELETE"), 405, "method_not_allowed"),
        (request(base, "/api/sessions"), 500, "internal"),
        (request(base, "/api/query", "POST", data=b"{not json"), 400, "bad_request"),
        (request(base, "/api/query", "POST", data=b"[1]"), 400, "bad_request"),
    ]
    for resp, status, code in cases:
        assert (resp.status, resp.body["code"]) == (status, code), resp.body
        assert resp.headers["content-type"] == "application/json" and resp.body["error"]
    assert "ZeroDivisionError" in cases[7][0].body["error"]
    assert cases[4][0].headers["Allow"] == "GET"
    assert request(base, "/api/ops/zz").status == 404  # the 500 did not stop the server
    gone = served(tmp_path / "none.sqlite")
    assert request(gone, "/api/projects").body["code"] == "no_store"
    old = tmp_path / "old.sqlite"
    build_store(old)
    sqlite3.connect(old).execute("UPDATE meta SET value='6' WHERE key='schema_version'").connection.commit()
    assert request(served(old), "/api/projects").body["code"] == "schema_mismatch"


def test_stub_route_is_501_json(served, store):
    # a view marked `__stub__` answers 501; Q1 built the explorer views, so the stubs here are fakes
    def not_built(**kw):
        raise NotImplementedError

    not_built.__stub__ = True
    api = FakeQuery()
    api.period_summary = api.palette = not_built
    r = request(served(store, api=api), "/api/explorer/summary?from=2026-10-01&to=2026-10-02")
    assert (r.status, r.body) == (501, {"error": "not implemented", "code": "internal"})
    assert request(served(store, api=api), "/api/palette?text=x").status == 501


# ── store states (API-5, QRY-22) ─────────────────────────────────────────────


def test_store_states(served, tmp_path, store, monkeypatch):
    missing = served(tmp_path / "none.sqlite")
    for path in ("/api/projects", "/api/sessions", "/api/explorer/facets", "/api/ops/x/detail"):
        r = request(missing, path)
        assert (r.status, r.body["code"]) == (503, "no_store"), path
        assert "botsworth ingest" in r.body["error"]
    r = request(missing, "/api/ingest/status")
    assert r.status == 200
    assert (r.body["store_exists"], r.body["store_state"], r.body["sources"], r.body["last_run"]) == (
        False,
        "missing",
        [],
        None,
    )
    assert r.body["freshness"]["last_ingest_ms"] is None and r.body["freshness"]["schema_version"] == 9
    assert "tz" not in request(missing, "/api/ingest/status").body["freshness"]  # API-30, D50
    old = tmp_path / "old.sqlite"
    build_store(old)
    with sqlite3.connect(old) as c:
        c.execute("UPDATE meta SET value='6' WHERE key='schema_version'")
    base = served(old)
    r = request(base, "/api/projects")
    assert (r.status, r.body["code"]) == (503, "schema_mismatch")
    assert "store schema 6, botsworth needs 9" in r.body["error"]
    r = request(base, "/api/ingest/status")
    assert (r.status, r.body["store_state"], r.body["store_exists"]) == (200, "schema_mismatch", True)
    assert r.body["sources"] == [] and r.body["freshness"]["last_ingest_ms"] is None
    ready = request(served(store), "/api/ingest/status").body
    assert (ready["store_state"], ready["ingest"]) == (
        "ready",
        {"running": False, "started_ms": None, "pid": None},
    )
    assert request(served(store), "/api/projects").status == 200  # store present but empty: zero figures


# ── caching (API-6, API-7, QRY-17) ───────────────────────────────────────────


def test_etag_304(served, store):
    base = served(store)
    first = request(base, "/api/sessions")
    fresh = first.body["freshness"]
    want = f'W/"1.9.{fresh["last_ingest_ms"]}.{fresh["price_fingerprint"]}"'
    assert (first.status, first.headers["ETag"], first.headers["Cache-Control"]) == (200, want, "no-cache")
    again = request(base, "/api/sessions", headers={"If-None-Match": want})
    assert (again.status, again.raw, again.headers["ETag"]) == (304, b"", want)
    stale = request(base, "/api/sessions", headers={"If-None-Match": 'W/"1.9.0.x"'})
    assert stale.status == 200
    ingest.ingest(db=store, parser_version="q0-1")  # a new ingest moves last_ingest_ms
    moved = request(base, "/api/sessions", headers={"If-None-Match": want})
    assert moved.status == 200 and moved.headers["ETag"] != want
    other = request(base, "/api/sessions", headers={"If-None-Match": f'"x", {moved.headers["ETag"]}'})
    assert other.status == 304


def test_ingest_status_and_source_no_store(served, store):
    for base, path in (
        (served(store), "/api/ingest/status"),
        (served(store, api=FakeQuery()), "/api/source?src=src%3A%2F%2Fx"),
    ):
        r = request(base, path)
        assert r.status == 200, path
        assert r.headers["Cache-Control"] == "no-store" and r.headers["ETag"] is None, path


def test_live_lock_pid_sends_no_store(served, store):
    base = served(store)
    Path(f"{store}.lock").write_text(json.dumps({"pid": os.getpid(), "started_ms": 1234}))
    r = request(base, "/api/sessions")
    assert (r.status, r.headers["Cache-Control"], r.headers["ETag"]) == (200, "no-store", None)
    assert request(base, "/api/ingest/status").body["ingest"] == {
        "running": True,
        "started_ms": 1234,
        "pid": os.getpid(),
    }
    dead = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True
    )
    Path(f"{store}.lock").write_text(json.dumps({"pid": int(dead.stdout), "started_ms": 1}))
    assert request(base, "/api/sessions").headers["ETag"] is not None  # a dead pid is not an ingest
    Path(f"{store}.lock").write_text("")
    assert request(base, "/api/ingest/status").body["ingest"]["running"] is False


def test_freshness_on_every_200(served, store):
    base = served(store)
    bodies = [
        request(base, "/api/ingest/status").body,
        request(base, "/api/sessions").body,
        request(
            base,
            "/api/query",
            "POST",
            data=json.dumps({"scope": {"all": True}, "select": "sessions"}).encode(),
        ).body,
    ]
    for body in bodies:
        fresh = body["freshness"]
        assert list(fresh) == [
            "last_ingest_ms",
            "parser_version",
            "price_fingerprint",
            "schema_version",
            "api_version",
        ]
        assert (fresh["schema_version"], fresh["api_version"], fresh["parser_version"]) == (
            9,
            1,
            "q0-1",
        )
        assert isinstance(fresh["last_ingest_ms"], int)


# ── gzip (API-52) ────────────────────────────────────────────────────────────


class BigApi(FakeQuery):
    def sessions(self, db=None, **kw):
        n = int(kw["limit"])
        return {"freshness": {}, "pad": "x" * n}


def test_gzip_over_32k(served, store):
    base = served(store, api=BigApi())
    big = request(base, "/api/sessions?limit=40000", headers={"Accept-Encoding": "gzip, deflate"})
    assert big.headers["Content-Encoding"] == "gzip" and big.headers["Vary"] == "Accept-Encoding"
    assert len(big.raw) < 1000 and big.body["pad"] == "x" * 40000
    plain = request(base, "/api/sessions?limit=40000")
    assert plain.headers["Content-Encoding"] is None and len(plain.raw) > 40000
    small = request(base, "/api/sessions?limit=100", headers={"Accept-Encoding": "gzip"})
    assert small.headers["Content-Encoding"] is None
    assert int(big.headers["content-length"]) == len(big.raw)


# ── assets (API-41) ──────────────────────────────────────────────────────────


@pytest.fixture
def ui(tmp_path: Path) -> Path:
    d = tmp_path / "ui"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<html>app</html>")
    (d / "assets" / "app-1a2b.js").write_text("console.log(1)")
    (d / "favicon.svg").write_text("<svg/>")
    return d


def test_assets_cache_headers_and_spa_fallback(served, store, ui):
    base = served(store, ui)
    js = request(base, "/assets/app-1a2b.js")
    assert (js.raw, js.headers["content-type"]) == (b"console.log(1)", "text/javascript")
    assert js.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    svg = request(base, "/favicon.svg")
    assert (svg.raw, svg.headers["Cache-Control"], svg.headers["content-type"]) == (
        b"<svg/>",
        "no-cache",
        "image/svg+xml",
    )
    for path in (
        "/",
        "/index.html",
        "/sessions/abc",
        "/assets/missing.js",
        "/../../etc/passwd",
        "/%2e%2e/%2e%2e/etc/passwd",
    ):
        r = request(base, path)
        assert (r.status, r.raw, r.headers["Cache-Control"]) == (200, b"<html>app</html>", "no-cache"), path
    assert request(base, "/", "POST", data=b"x").status == 405


def test_no_ui_dir_404_json(served, store, tmp_path):
    for ui_dir in (None, tmp_path / "empty"):
        r = request(served(store, ui_dir), "/sessions/abc")
        assert (r.status, r.body) == (
            404,
            {"error": "UI assets not found; build the frontend (make build)", "code": "not_found"},
        )
        assert request(served(store, ui_dir), "/api/ingest/status").status == 200  # the API keeps working
