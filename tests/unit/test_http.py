"""T6 http.server: routes of design §6.3 against a fake query layer; wire keys from types.ts."""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
import test_query_contract as contract
from fake_query import AVAIL, PROJECT, FakeQuery, ts_keys
from query_fixture import build

import botsworth
from botsworth import pointer
from botsworth.http.server import make_server
from botsworth.store import schema


def empty_store(path: Path) -> Path:
    """A current-schema store with no rows: the state checks (API-5) pass, the fake api answers."""
    conn = sqlite3.connect(path)
    schema.ensure(conn)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "a.js").write_text("console.log(1)")
    return tmp_path


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return empty_store(tmp_path / "store.sqlite")


def serve(db: Path, dist: Path) -> Iterator[tuple[str, FakeQuery]]:
    fake = FakeQuery()
    srv = make_server(port=0, db=db, api=fake, ui_dir=dist)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", fake
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def server(db: Path, dist: Path) -> Iterator[tuple[str, FakeQuery]]:
    yield from serve(db, dist)


def call(url: str, body: object | None = None, raw: bytes | None = None) -> tuple[int, str, object]:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers["content-type"], json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.headers["content-type"], json.loads(e.read())


def test_post_query_passes_body_through(server):
    base, fake = server
    q = {"scope": {"all": True}, "select": "sessions"}
    status, ctype, body = call(f"{base}/api/query", q)
    assert status == 200 and ctype == "application/json"
    assert set(body) == ts_keys("QueryResponse")
    assert fake.calls == [("query", (q,), {})]


@pytest.mark.parametrize(
    "path,iface,expected",
    [
        ("/api/ops/s%2F1", "ShowResponse", ("show", ("s/1",), {})),
        ("/api/ops/s1/timeline?depth=2", "TimelineResponse", ("timeline", ("s1",), {"depth": 2})),
        ("/api/ops/s1/timeline", "TimelineResponse", ("timeline", ("s1",), {"depth": 3})),
        (
            "/api/turns/t1/steps?limit=5&cursor=c",
            "StepsResponse",
            ("steps", ("t1",), {"limit": 5, "cursor": "c"}),
        ),
        (
            "/api/source?src=src%3A%2F%2Fomp%2Fa&part=output&offset=2&limit=3",
            "SourceResponse",
            ("source", ("src://omp/a",), {"part": "output", "offset": 2, "max_bytes": 3}),
        ),
    ],
)
def test_get_routes_map_to_one_query_call(server, path, iface, expected):
    base, fake = server
    status, _, body = call(base + path)
    assert status == 200
    assert set(body) == ts_keys(iface)
    assert fake.calls == [expected]


def test_outline_route(server):
    base, fake = server
    status, _, body = call(f"{base}/api/ops/T12/outline?depth=2")
    assert status == 200 and body["rows"][0]["display_id"] == "T12.2"
    assert fake.calls == [("outline", ("T12",), {"depth": 2})]


def test_source_pages_through_offset(server):
    base, _ = server
    pages = [call(f"{base}/api/source?src=src://x&offset={o}&limit=4")[2] for o in (0, 4, 8)]
    assert "".join(p["text"] for p in pages) == "0123456789"
    assert {p["total_bytes"] for p in pages} == {10}


def test_sessions_passes_filters_to_query_sessions(server):
    base, fake = server
    status, _, body = call(f"{base}/api/sessions?provider=omp&project=p&from=5&to=9&order=cost_usd&limit=7")
    assert status == 200
    assert set(body) == ts_keys("SessionsResponse")
    assert {"title", "git_branch", "harness_version", "status"} <= set(body["sessions"][0])
    assert fake.calls == [
        (
            "sessions",
            (),
            {"provider": "omp", "project": "p", "from_ms": 5, "to_ms": 9, "order": "cost_usd", "limit": 7},
        )
    ]


def test_sessions_defaults(server):
    base, fake = server
    call(f"{base}/api/sessions")
    assert fake.calls == [
        ("sessions", (), {"from_ms": None, "to_ms": None, "order": "-start_ms", "limit": 200})
    ]


def test_projects_and_availability_pass_through(server):
    base, fake = server
    status, _, body = call(f"{base}/api/projects")
    assert status == 200 and body == [PROJECT]
    assert set(body[0]) == ts_keys("ProjectRow")
    assert call(f"{base}/api/availability")[2] == AVAIL
    assert [c[0] for c in fake.calls] == ["projects", "availability"]


@pytest.mark.parametrize(
    "path,body,raw,code",
    [
        ("/api/query", {"scope": {"all": True}, "select": "bad"}, None, 400),
        ("/api/query", None, b"{not json", 400),
        ("/api/query", None, b"[1]", 400),
        ("/api/ops/missing-1", None, None, 404),
        ("/api/turns/missing-1/steps", None, None, 404),
        ("/api/ops/missing-1/outline", None, None, 404),
        ("/api/source?src=gone-1", None, None, 404),
        ("/api/ops/s1/timeline?depth=x", None, None, 400),
        ("/api/source", None, None, 400),
        ("/api/nope", None, None, 404),
        ("/api/query", None, None, 405),
        ("/api/sessions", {}, None, 405),
    ],
)
def test_errors_are_json(server, path, body, raw, code):
    base, _ = server
    status, ctype, payload = call(base + path, body, raw)
    assert (status, ctype) == (code, "application/json")
    assert set(payload) == {"error", "code"} and payload["error"]


def test_unexpected_exception_is_json_500(server):
    base, fake = server
    fake.show = lambda op_id, db=None: 1 / 0  # type: ignore[method-assign]
    status, ctype, payload = call(f"{base}/api/ops/s1")
    assert (status, ctype) == (500, "application/json")
    assert "ZeroDivisionError" in payload["error"]


def test_no_ingest_endpoint(server):
    base, _ = server
    assert call(f"{base}/api/ingest", {})[0] == 404


def test_static_files_and_spa_fallback(server):
    base, _ = server
    with urllib.request.urlopen(f"{base}/assets/a.js") as r:
        assert r.read() == b"console.log(1)" and r.headers["content-type"] == "text/javascript"
    for path in ("/", "/sessions/abc", "/../../etc/passwd"):
        with urllib.request.urlopen(base + path) as r:
            assert r.read() == b"<html>app</html>"


def test_resolved_db_is_passed_to_every_query_call(server, db):
    base, fake = server
    for path in ("/api/ops/s1", "/api/ops/s1/outline", "/api/sessions", "/api/projects"):
        call(base + path)
    assert fake.dbs == {db}


def test_missing_store_d22(db, dist):
    db.unlink()
    gen = serve(db, dist)
    base, fake = next(gen)
    try:
        for path in (
            "/api/ops/s1",
            "/api/sessions",
            "/api/projects",
            "/api/availability",
            "/api/source?src=x",
        ):
            status, ctype, body = call(base + path)
            assert (status, ctype, body["code"]) == (503, "application/json", "no_store"), path
            assert "botsworth ingest" in body["error"]
        assert call(f"{base}/api/query", {"scope": {"all": True}})[0] == 503
        assert fake.calls == []
        empty_store(db)  # an ingest creates the store; the server picks it up without restart
        assert call(f"{base}/api/ops/s1")[0] == 200
    finally:
        next(gen, None)


# ── gate: the real botsworth.query on T5's 51-op fixture store (no fake) ─────────

SA = "claude_code/sA/session/-"
A_T1 = "claude_code/sA/turn/main#0"
ALPHA = "/p/alpha"
GONE_SRC = pointer.format("claude_code", "proj/gone.jsonl", pointer.Lines(1, 1))


def Q(op_id: str) -> str:
    """Path segment as the client sends it (encodeURIComponent: '/' and '#' escaped)."""
    return urllib.parse.quote(op_id, safe="")


@pytest.fixture(scope="module")
def real(tmp_path_factory) -> Iterator[tuple[str, Path]]:
    d = tmp_path_factory.mktemp("store")
    store = d / "store.db"
    build(store, d / "src")
    dist = d / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("app")
    mp = pytest.MonkeyPatch()
    mp.setitem(pointer.DEFAULT_ROOTS, "claude_code", d / "src")  # source() resolves against the fixture file
    srv = make_server(port=0, db=store, ui_dir=dist)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", store
    srv.shutdown()
    srv.server_close()
    mp.undo()


def same(a: object, b: object) -> bool:
    return json.loads(json.dumps(a, default=str)) == json.loads(json.dumps(b, default=str))


def test_gate_every_route_equals_the_api_and_types(real):
    base, db = real
    src = botsworth.show(A_T1, db=db)["op"]["src"]
    cases = [
        ("/api/ops/" + Q(SA), "ShowResponse", lambda: botsworth.show(SA, db=db)),
        ("/api/ops/sA:T1", "ShowResponse", lambda: botsworth.show("sA:T1", db=db)),
        (f"/api/ops/{Q(SA)}/timeline?depth=2", "TimelineResponse", lambda: botsworth.timeline(SA, 2, db=db)),
        (f"/api/ops/{Q(SA)}/outline?depth=2", None, lambda: botsworth.outline(SA, 2, db=db)),
        (f"/api/turns/{Q(A_T1)}/steps?limit=3", "StepsResponse", lambda: botsworth.steps(A_T1, 3, db=db)),
        (
            "/api/sessions?project=/p/alpha",
            "SessionsResponse",
            lambda: botsworth.sessions(project="/p/alpha", db=db),
        ),
        ("/api/projects", None, lambda: botsworth.projects(db=db)),
        ("/api/availability", "Availability", botsworth.availability),
        (
            "/api/source?src=" + urllib.parse.quote(src) + "&part=text&limit=5",
            "SourceResponse",
            lambda: botsworth.source(src, part="text", max_bytes=5, db=db),
        ),
    ]
    for path, iface, direct in cases:
        status, ctype, body = call(base + path)
        assert (status, ctype) == (200, "application/json"), (path, body)
        assert same(body, direct()), path
        if iface:
            contract.check(iface, body)
    for op in botsworth.timeline(SA, 2, db=db)["ops"]:
        contract.check("Op", op)
    projects = call(base + "/api/projects")[2]["projects"]  # API-7, API-28
    assert projects
    for row in projects:
        contract.exact("ProjectRow", row)
    for s in call(base + "/api/sessions")[2]["sessions"]:
        contract.check("Op", s, contract.SESSION_EXTRA | contract.WIRE_EXTRA["ListingRow"])  # API-27


def test_gate_post_query_equals_the_api(real):
    base, db = real
    for q in contract.QUERIES:
        status, _, body = call(base + "/api/query", q)
        assert status == 200, (q, body)
        assert same(body, botsworth.query(q, db=db))
        contract.check("QueryResponse", body)


def test_gate_source_pages_through_a_pointer(real):
    base, db = real
    src = botsworth.show(A_T1, db=db)["op"]["src"]
    whole = call(f"{base}/api/source?src={urllib.parse.quote(src)}")[2]
    pages, offset = [], 0
    while offset < whole["total_bytes"]:
        page = call(f"{base}/api/source?src={urllib.parse.quote(src)}&offset={offset}&limit=7")[2]
        assert len(page["text"].encode()) <= 7 and page["drift"] is False
        pages.append(page["text"])
        offset += 7
    assert "".join(pages) == whole["text"] and whole["total_bytes"] > 7


@pytest.mark.parametrize(
    "path,body,code",
    [
        ("/api/ops/" + Q("claude_code/nope"), None, 404),
        ("/api/ops/nope-op", None, 404),
        ("/api/ops/nope-op/timeline", None, 404),
        ("/api/ops/nope-op/outline", None, 404),
        ("/api/turns/nope-turn/steps", None, 404),
        (f"/api/turns/{Q(SA)}/steps", None, 400),  # a session is not a turn
        (
            "/api/source?src=" + Q(pointer.format("claude_code", "proj/gone.jsonl", pointer.Lines(1, 1))),
            None,
            404,
        ),
        ("/api/query", {"scope": {"all": True}, "select": "bogus"}, 400),
        ("/api/sessions?from=abc", None, 400),
    ],
)
def test_gate_errors_on_the_real_store(real, path, body, code):
    base, _ = real
    status, ctype, payload = call(base + path, body)
    assert (status, ctype) == (code, "application/json"), payload
    assert set(payload) == {"error", "code"} and payload["error"]


def test_gate_ingest_status_on_real_store_and_missing_store(real, tmp_path, dist):
    base, _ = real
    status, _, body = call(base + "/api/ingest/status")
    assert status == 200 and body["store_exists"] is True and body["store_state"] == "ready"
    assert list(body["freshness"]) == contract.FRESHNESS_KEYS
    srv = make_server(port=0, db=tmp_path / "none.sqlite", ui_dir=dist)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        b2 = f"http://127.0.0.1:{srv.server_address[1]}"
        status, _, body = call(b2 + "/api/ingest/status")
        assert (status, body["store_exists"], body["store_state"]) == (200, False, "missing")
        assert call(b2 + "/api/ops/" + Q(SA))[0] == 503
        assert call(b2 + "/api/query", {"scope": {"all": True}, "select": "sessions"})[0] == 503
    finally:
        srv.shutdown()
        srv.server_close()
