# ruff: noqa: F811
"""Q2c endpoints over HTTP (API-24, API-25, API-26): shapes, status codes, headers (TST-2).

Key sets are written from the TypeScript in docs/specs/interfaces.md §3.8, §3.9 and API-25, not read from
`wire.py`. The CLI/HTTP parity cases for `detail` and `find` are in test_cli_v4.py.
"""

from __future__ import annotations

import gzip
import importlib
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
import q2c_store as Q
from q2c_store import q2cstore  # noqa: F401 (fixture)
from qstore import qstore  # noqa: F401 (the QF fixture)
from qstore_ids import A0, SAGENTS, STOOLS, TOOL_BIG, turn

from botsworth import pointer
from botsworth.http.server import make_server
from botsworth.query.views import text

api = importlib.import_module("botsworth.query")  # the module; `botsworth.query` also names a function
FRESHNESS = {"last_ingest_ms", "parser_version", "price_fingerprint", "schema_version", "api_version"}
MONEY = {"cost_usd", "cost_lower_bound", "n_unpriced"}
POSITION = {"session", "turn", "display_id", "row", "depth"}
CALL_ROW = MONEY | {
    "id", "depth", "start_ms", "end_ms", "dur_ms", "time_prov", "model", "tok", "ttft_ms", "stop_reason",
    "error_kind", "is_final", "steps",
}  # fmt: skip
TOOL_ROW = {
    "id", "depth", "step_id", "tool_name", "verb", "summary", "target", "command", "command_src", "file_path",
    "start_ms", "end_ms", "dur_ms", "time_prov", "status", "failed", "exit_code", "is_error", "interrupted",
    "skill_load", "skill_via", "waits_on_user", "max_silence_ms", "result_bytes", "src",
}  # fmt: skip
AGENT_ROW = MONEY | {
    "id", "depth", "caused_by", "mode", "description", "agent_names", "agent_type", "tok_total",
    "n_llm_calls", "n_tool_calls", "n_errors", "models", "run_ms", "n_rows",
}  # fmt: skip
CALL_DETAIL = (
    CALL_ROW
    | {"freshness", "kind", "position", "turn_cost_usd", "cost_in_usd", "cost_out_usd", "cost_cache_read_usd"}
    | {"cost_cache_write_usd", "cost_basis", "context_tokens", "speed_tok_s", "speed_basis", "sections"}
)
TOOL_DETAIL = TOOL_ROW | {"freshness", "kind", "position", "input_src", "output_src", "neighbours"}
AGENT_DETAIL = (AGENT_ROW - {"runs"}) | {
    "freshness", "kind", "position", "turn_cost_usd", "focus_engagement", "runs", "top_calls", "other_calls",
}  # fmt: skip
MESSAGE_DETAIL = {
    "freshness", "kind", "position", "turn", "display_id", "turn_kind", "turn_origin", "label", "text_src",
    "text_bytes",
}  # fmt: skip
HIT = {"row", "turn", "display_id", "depth", "label", "value", "start_ms"}
SOURCE = {"freshness", "text", "total_bytes", "drift", "file", "locator", "block"}


def fetch(base: str, path: str) -> tuple[int, dict, dict]:
    try:
        with urllib.request.urlopen(base + path) as r:
            raw = r.read()
            body = gzip.decompress(raw) if r.headers.get("Content-Encoding") == "gzip" else raw
            return r.status, json.loads(body), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), dict(e.headers)


def get(base: str, path: str) -> tuple[int, dict]:
    status, body, _ = fetch(base, path)
    return status, body


def q(text_: str) -> str:
    return urllib.parse.quote(text_, safe="")


@pytest.fixture(scope="module")
def serve() -> Iterator:
    servers = []

    def go(db: Path) -> str:
        srv = make_server(port=0, db=db, ui_dir=None)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield go
    for srv in servers:
        srv.shutdown()
        srv.server_close()


@pytest.fixture(scope="module")
def base(qstore, serve) -> str:
    return serve(qstore)


@pytest.fixture(scope="module")
def base2(q2cstore, serve) -> str:
    return serve(q2cstore[0])


# ── detail ───────────────────────────────────────────────────────────────────


def test_detail_shapes_per_kind(base, base2):
    status, call = get(base2, f"/api/ops/{q(Q.CALL_1)}/detail")
    assert status == 200 and set(call) == CALL_DETAIL and call["kind"] == "call"
    assert set(call["freshness"]) == FRESHNESS and set(call["position"]) == POSITION
    assert set(call["position"]["row"]) == {"type", "id"}
    assert [set(s) for s in call["sections"]] == [{"part", "src", "snippet", "bytes", "row"}] * 5
    assert set(call["tok"]) == {"in", "cache_read", "cache_write", "out", "reasoning", "total"}

    status, tool = get(base, f"/api/ops/{q(TOOL_BIG)}/detail")
    assert status == 200 and set(tool) == TOOL_DETAIL and tool["kind"] == "tool"
    assert set(tool["neighbours"]) == {"before", "after"}
    assert set(tool["neighbours"]["before"][0]) == CALL_ROW | {"type"}
    assert tool["input_src"] == tool["src"] and tool["output_src"].startswith("src://claude_code/")

    status, agent = get(base, f"/api/ops/{q(A0)}/detail")
    assert status == 200 and set(agent) == AGENT_DETAIL and agent["kind"] == "agent"
    assert set(agent["runs"][0]) == {"engagement", "agent_name", "start_ms", "end_ms", "wall_ms", "ask_src"}
    assert set(agent["other_calls"]) == {"n", "cost_usd"}
    assert all(set(c) == CALL_ROW | {"type"} for c in agent["top_calls"])
    assert agent["focus_engagement"] == A0 and agent["position"]["row"] == {"type": "agent", "id": A0}

    status, message = get(base, f"/api/ops/{q(turn(SAGENTS, 0))}/detail")
    assert status == 200 and set(message) == MESSAGE_DETAIL and message["kind"] == "message"
    assert message["position"]["row"]["type"] == "turn"
    # the id forms of QRY-2 reach the same op
    assert get(base, "/api/ops/sagents%3AT1/detail") == (200, message)


def test_detail_errors(base):
    status, body = get(base, f"/api/ops/{q('claude_code/stools/session/-')}/detail")
    assert status == 400 and body["code"] == "bad_request" and "error" in body
    status, body = get(base, "/api/ops/nope/detail")
    assert status == 404 and body["code"] == "not_found"


# ── find ─────────────────────────────────────────────────────────────────────


def test_find_shape(base):
    status, body = get(base, f"/api/sessions/{q('claude_code/stools/session/-')}/find?kind=failures")
    assert status == 200 and set(body) == {"freshness", "kind", "total", "hits", "per_turn"}
    assert body["kind"] == "failures" and body["total"] == 3 and len(body["hits"]) == 3
    assert set(body["hits"][0]) == HIT and set(body["hits"][0]["row"]) == {"type", "id"}
    assert sum(body["per_turn"].values()) == 3
    biggest = get(base, f"/api/sessions/{STOOLS.split('/')[1]}/find?kind=biggest&measure=time&limit=2")[1]
    assert biggest["total"] > 2 and len(biggest["hits"]) == 2
    assert biggest["hits"][0]["value"] >= biggest["hits"][1]["value"]
    searched = get(
        base, f"/api/sessions/{q('claude_code/stools/session/-')}/find?kind=search&text=PYTEST%20uv"
    )[1]
    assert searched["total"] == 2


def test_search_requires_text_400(base):
    for path in ("kind=search", "kind=search&text="):
        status, body = get(base, f"/api/sessions/stools/find?{path}")
        assert status == 400 and body["code"] == "bad_request" and "text" in body["error"]
    status, body = get(base, "/api/sessions/stools/find")
    assert status == 400 and "kind" in body["error"]
    assert get(base, "/api/sessions/stools/find?kind=nope")[0] == 400
    assert get(base, "/api/sessions/stools/find?kind=biggest&measure=weight")[0] == 400


def test_find_limit_max_10000(base):
    assert get(base, "/api/sessions/stools/find?kind=biggest&limit=10000")[0] == 200
    for bad in ("10001", "0", "-1", "x"):
        status, body = get(base, f"/api/sessions/stools/find?kind=biggest&limit={bad}")
        assert status == 400 and "limit" in body["error"], bad
    assert get(base, "/api/sessions/nosuchsess/find?kind=biggest")[0] == 404


# ── source ───────────────────────────────────────────────────────────────────


def test_source_part_and_no_store_header(qstore, qroots, serve, monkeypatch):
    """API-26: `part`, `limit` and `block` over HTTP; the response is never cached."""
    monkeypatch.setattr(api, "source", text.source)  # the facade swap (see the gate report)
    monkeypatch.setitem(pointer.DEFAULT_ROOTS, "claude_code", qroots["claude_code"])
    base = serve(qstore)
    with api._conn(qstore) as conn:
        step = conn.execute(
            "SELECT s.* FROM steps s JOIN ops o ON o.rowid = s.op_rowid WHERE o.op_id = ?", (TOOL_BIG,)
        ).fetchone()
        src = text.src_uri(conn, step)
    status, body, headers = fetch(base, f"/api/source?src={q(src)}&part=output")
    assert status == 200 and set(body) == SOURCE and set(body["freshness"]) == FRESHNESS
    assert body["total_bytes"] == 11264 == len(body["text"]) and body["block"] == "tool_result"
    assert headers["Cache-Control"] == "no-store"
    paged = get(base, f"/api/source?src={q(src)}&part=output&offset=16&limit=8")[1]
    assert paged["text"] == body["text"][16:24] and paged["total_bytes"] == 11264
    ask = get(base, f"/api/source?src={q(turn(STOOLS, 0))}&part=text")[1]  # an op id works as `src`
    assert ask["text"] == "run the tests and fix any failures in the parser module"
    assert get(base, f"/api/source?src={q(src)}&part=output&limit=1048576")[0] == 200
    status, body = get(base, f"/api/source?src={q(src)}&part=output&limit=1048577")
    assert status == 400 and "limit" in body["error"]
    assert get(base, f"/api/source?src={q(src)}&part=bogus")[0] == 400
    gone = "src://claude_code/-u-a-bizdemo-src/gone.jsonl#L1-L1"
    status, body = get(base, f"/api/source?src={q(gone)}&part=text")
    assert status == 404 and body["code"] == "source_missing"
