# ruff: noqa: F811
"""Q2b turn endpoints over HTTP (API-22, API-31, API-32): shapes, status codes (TST-2).

Key sets are written from the TypeScript interfaces of docs/specs/interfaces.md §3.7 and §3.6, not read from
`wire.py`. The CLI/HTTP parity case for `turn` is in test_cli_v4.py.
"""

from __future__ import annotations

import gzip
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from qstore import qstore  # noqa: F401 (the QF fixture)
from qstore_ids import A0, B0, SAGENTS, SSKILLS, turn

from botsworth.http.server import make_server

FRESHNESS = {"last_ingest_ms", "parser_version", "price_fingerprint", "schema_version", "api_version"}
MONEY = {"cost_usd", "cost_lower_bound", "n_unpriced"}
SPLIT = {"parts"}
STRETCH = {"start_ms", "end_ms", "dur_ms", "state", "substate", "turn", "next_turn", "running"}
CALL = MONEY | {
    "type", "id", "depth", "start_ms", "end_ms", "dur_ms", "time_prov", "model", "tok", "ttft_ms",
    "stop_reason", "error_kind", "is_final", "steps",
}  # fmt: skip
TOOL = {
    "type", "id", "depth", "step_id", "tool_name", "verb", "summary", "target", "command", "command_src",
    "file_path", "start_ms", "end_ms", "dur_ms", "time_prov", "status", "failed", "exit_code", "is_error",
    "interrupted", "skill_load", "skill_via", "user_wait", "max_silence_ms", "result_bytes", "src",
}  # fmt: skip
AGENT = MONEY | {
    "type", "id", "depth", "caused_by", "mode", "description", "agent_names", "agent_type", "tok_total",
    "n_llm_calls", "n_tool_calls", "n_errors", "models", "run_ms", "n_rows", "runs",
}  # fmt: skip
GAP = STRETCH | {"type", "id"}
TURN_ROW = MONEY | {
    "type", "turn", "display_id", "kind", "origin", "label", "start_ms", "end_ms", "wall_ms", "time_split",
    "waiting_ms", "cost_main", "tok_total", "n_llm_calls", "models", "n_agents", "n_skill_loads", "n_failed",
    "agent_runs",
}  # fmt: skip
HEADER = TURN_ROW | {"cost", "models_full", "text_src", "text_bytes", "n_turns_in_session"}
BIN = {"cost_usd", "main_cost_usd", "tok_total", "state_ms"}


def get(base: str, path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(base + path) as r:
            raw = r.read()
            return r.status, json.loads(
                gzip.decompress(raw) if r.headers.get("Content-Encoding") == "gzip" else raw
            )
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def q(op_id: str) -> str:
    return urllib.parse.quote(op_id, safe="")


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


# ── shapes ───────────────────────────────────────────────────────────────────


def test_rows_shape(base):
    status, body = get(base, f"/api/turns/{q(turn(SAGENTS, 0))}/rows")
    assert status == 200 and set(body) == {"freshness", "turn", "rows", "range"}
    assert set(body["freshness"]) == FRESHNESS and body["range"] is None
    t = body["turn"]
    assert set(t) == HEADER
    assert set(t["cost"]) == MONEY | {"main", "agents"}
    model_line = MONEY | {"model", "n_calls", "model_ms", "speed_tok_s", "speed_basis", "ttft_ms_mean"}
    assert all(set(m) == model_line for m in t["models_full"])
    kinds = {r["type"]: r for r in body["rows"]}
    assert set(kinds) == {"call", "agent", "gap"}
    assert set(kinds["call"]) == CALL and set(kinds["call"]["tok"]) == {
        "in", "cache_read", "cache_write", "out", "reasoning", "total",
    }  # fmt: skip
    assert set(kinds["agent"]) == AGENT
    assert set(kinds["agent"]["runs"][0]) == {"engagement", "agent_name", "start_ms", "end_ms", "wall_ms"}
    assert set(kinds["gap"]) == GAP and kinds["gap"]["id"] == f"gap-{kinds['gap']['start_ms']}"
    # a tool row and a call row with a step (stools, sagents)
    tools = get(base, f"/api/turns/{q(turn('claude_code/stools', 0))}/rows")[1]["rows"]
    tool = next(r for r in tools if r["type"] == "tool")
    assert set(tool) == TOOL and tool["status"] in ("exit:0", "exit:1", "ok")
    assert tool["src"].startswith("src://claude_code/") and tool["command_src"] == tool["src"]
    said = get(base, f"/api/turns/{q(turn(SAGENTS, 1))}/rows")[1]["rows"]
    step = next(r for r in said if r["type"] == "call" and r["steps"])["steps"][0]
    assert set(step) == {"step_id", "verb", "summary", "snippet"}
    # display ids reach the same turn
    same = get(base, "/api/turns/sagents%3AT1/rows")
    assert same == (200, body)


def test_rows_range_shape(base):
    status, body = get(
        base, f"/api/turns/{q(turn(SAGENTS, 0))}/rows?from_ms=1791450000000&to_ms=1791450300000"
    )
    assert status == 200
    r = body["range"]
    assert set(r) == {"from_ms", "to_ms", "cost", "cost_main", "cost_agents", "time_split", "agents"}
    assert set(r["cost"]) == MONEY and set(r["time_split"]) == SPLIT
    assert all(set(a) == {"row", "cost_usd", "tok_total", "n_calls_in_range", "n_calls"} for a in r["agents"])


def test_agent_rows_shape(base):
    status, body = get(base, f"/api/ops/{q(A0)}/rows")
    assert status == 200 and set(body) == {"freshness", "total", "offset", "limit", "rows"}
    assert (body["total"], body["offset"], body["limit"]) == (2, 0, 500)
    assert set(body["rows"][0]) == CALL and set(body["rows"][1]) == AGENT
    assert [r["depth"] for r in body["rows"]] == [1, 1]
    paged = get(base, f"/api/ops/{q(B0)}/rows?offset=1&limit=1")[1]
    assert (paged["total"], paged["offset"], paged["limit"], len(paged["rows"])) == (2, 1, 1, 1)


def test_histogram_shapes(base):
    for path, scope in (
        (f"/api/turns/{q(turn(SAGENTS, 0))}/histogram?bins=12", "turn"),
        ("/api/sessions/sagents/histogram?bins=20", "session"),
    ):
        status, body = get(base, path)
        assert status == 200, path
        assert set(body) == {"freshness", "start_ms", "bin_ms", "tail_from_ms", "bins", "marks", "stretches"}
        assert len(body["bins"]) == (12 if scope == "turn" else 20)
        want = BIN | ({"by_model"} if scope == "turn" else set())
        assert all(set(b) == want and set(b["state_ms"]) == SPLIT for b in body["bins"])
        assert set(body["marks"]) == ({"skill_loads"} if scope == "turn" else {"turn_starts", "skill_loads"})
        assert all(set(s) == STRETCH for s in body["stretches"])
        assert (body["tail_from_ms"] is not None) == (scope == "turn")
    marks = get(base, "/api/sessions/sskills/histogram")[1]["marks"]
    assert set(marks["turn_starts"][0]) == {"turn", "display_id", "start_ms"}
    pin = marks["skill_loads"][0]
    assert set(pin) == {"row", "start_ms", "skill"} and set(pin["row"]) == {"type", "id"}
    assert len(get(base, "/api/sessions/sagents/histogram")[1]["bins"]) == 600  # session default
    assert len(get(base, f"/api/turns/{q(turn(SSKILLS, 1))}/histogram")[1]["bins"]) == 120  # turn default


# ── errors ───────────────────────────────────────────────────────────────────


def test_non_main_turn_400(base):
    for path in (
        f"/api/turns/{q(A0)}/rows",  # an engagement
        f"/api/turns/{q('claude_code/sagents/turn/ra#0')}/rows",  # an agent's own turn
        f"/api/turns/{q('claude_code/sagents/llm_call/req_c1')}/rows",
        f"/api/turns/{q('claude_code/sagents/llm_call/req_c1')}/histogram",
    ):
        status, body = get(base, path)
        assert status == 400 and body["code"] == "bad_request" and "error" in body, path
    status, body = get(base, f"/api/turns/{q('claude_code/sagents/turn/main#99')}/rows")
    assert status == 404 and body["code"] == "not_found"


def test_from_without_to_400(base):
    t = q(turn(SAGENTS, 0))
    for path in (f"/api/turns/{t}/rows?from_ms=5", f"/api/turns/{t}/rows?to_ms=5"):
        status, body = get(base, path)
        assert status == 400 and "together" in body["error"], path
    assert get(base, f"/api/turns/{t}/rows?from_ms=9&to_ms=5")[0] == 400
    assert get(base, f"/api/turns/{t}/histogram?from_ms=5")[0] == 400


def test_histogram_and_agent_rows_parameter_errors(base):
    for bins in (0, 1001):
        assert get(base, f"/api/sessions/sagents/histogram?bins={bins}")[0] == 400
        assert get(base, f"/api/turns/{q(turn(SAGENTS, 0))}/histogram?bins={bins}")[0] == 400
    assert get(base, f"/api/ops/{q(A0)}/rows?limit=2001")[0] == 400
    assert get(base, f"/api/ops/{q(A0)}/rows?limit=0")[0] == 400
    assert get(base, f"/api/ops/{q(A0)}/rows?offset=-1")[0] == 400
    assert get(base, f"/api/ops/{q('claude_code/sagents/engagement/rc#0')}/rows")[0] == 404  # no causing call
    assert get(base, f"/api/ops/{q(turn(SAGENTS, 0))}/rows")[0] == 400  # a turn is not an agent row
    assert get(base, "/api/sessions/nosuchsession/histogram")[0] == 404
    assert get(base, f"/api/ops/{q('claude_code/sagents/engagement/nope#0')}/rows")[0] == 404
