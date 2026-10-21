# ruff: noqa: F811
"""Q2a session endpoints over HTTP (API-20, API-21, API-29): shapes, status codes (TST-2)."""

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
from qstore import STREAMS, qstore  # noqa: F401 (the QF fixture)
from stream_load import load_streams

from botsworth.http.server import make_server

TZ = "Asia/Kolkata"
FRESHNESS = {"last_ingest_ms", "parser_version", "price_fingerprint", "schema_version", "api_version"}
MONEY = {"cost_usd", "cost_lower_bound", "n_unpriced"}
STRETCH = {"start_ms", "end_ms", "dur_ms", "state", "substate", "turn", "next_turn", "running"}
SESSION_KEYS = {
    "project", "project_name", "color_slot", "session", "session_id", "provider", "name", "title_source",
    "cwd", "git_branch", "harness_version", "entrypoint", "originator", "start_ms", "end_ms",
    "ends_after_days",
    "is_automated", "n_turns", "n_harness_turns", "cost", "tok_total", "wall_ms", "active_ms", "time_split",
    "longest", "n_agents", "n_skill_loads", "n_failed", "n_llm_calls", "models",
}  # fmt: skip
TURN_KEYS = MONEY | {
    "type", "turn", "display_id", "kind", "origin", "label", "start_ms", "end_ms", "wall_ms", "time_split",
    "waiting_ms", "cost_main", "tok_total", "n_llm_calls", "models", "n_agents", "n_skill_loads", "n_failed",
    "agent_runs",
}  # fmt: skip


def get(base: str, path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(base + path) as r:
            raw = r.read()
            return r.status, json.loads(
                gzip.decompress(raw) if r.headers.get("Content-Encoding") == "gzip" else raw
            )
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


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
def ambiguous_base(tmp_path_factory, serve) -> str:
    """Two sessions whose ids share an 8-character prefix, built by the pipeline from the szero stream."""
    root = tmp_path_factory.mktemp("ambiguous")
    streams = root / "streams"
    streams.mkdir()
    text = (STREAMS / "szero.jsonl").read_text()
    for sid in ("shared-prefix-1", "shared-prefix-2"):
        (streams / f"{sid}.jsonl").write_text(text.replace("szero", sid))
    db = root / "p.sqlite"
    assert load_streams(streams=[streams], db=db).rejected == []
    return serve(db)


# ── shapes ───────────────────────────────────────────────────────────────────


def test_session_shape(base):
    status, body = get(base, "/api/sessions/sagents")
    assert status == 200 and set(body) == {"freshness", "session"}
    assert set(body["freshness"]) == FRESHNESS
    s = body["session"]
    assert set(s) == SESSION_KEYS
    assert set(s["cost"]) == MONEY | {"main", "agents", "unattributed"}
    assert set(s["time_split"]) == {"parts"}
    assert all(set(p) == {"state", "substate", "ms"} for p in s["time_split"]["parts"])
    assert "none_split" not in s
    assert set(s["longest"]) == {"model", "tool", "agents", "you", "agentmsg", "limit", "none"}
    assert set(s["longest"]["you"]) == {"row", "turn", "dur_ms"} and set(s["longest"]["you"]["row"]) == {
        "type",
        "id",
    }
    assert all(
        set(m) == MONEY | {"model", "n_calls", "model_ms", "speed_tok_s", "speed_basis", "ttft_ms_mean"}
        for m in s["models"]
    )
    assert (s["session"], s["session_id"], s["n_turns"], s["n_agents"]) == (
        "claude_code/sagents/session/-",
        "sagents",
        2,
        4,
    )
    # the op_id form of the key (percent-encoded) reaches the same session
    same = get(base, "/api/sessions/" + urllib.parse.quote("claude_code/sagents/session/-", safe=""))
    assert same == (200, body)


def test_turns_shape(base):
    status, body = get(base, "/api/sessions/sstretch/turns")
    assert status == 200 and set(body) == {"freshness", "items"}
    # T1, you, T2, limit, T3, agentmsg, T4, T5 (case 8); the 22 min wait and the agents stretch are no rows
    kinds = [i["type"] for i in body["items"]]
    assert kinds == ["turn", "gap", "turn", "gap", "turn", "gap", "turn", "turn"]
    for item in body["items"]:
        if item["type"] == "turn":
            assert set(item) == TURN_KEYS
            assert set(item["time_split"]) == {"parts"}
        else:
            assert set(item) == STRETCH | {"type", "id"} and item["id"] == f"gap-{item['start_ms']}"
            assert item["state"] in ("you", "limit", "agentmsg") and item["running"] == []
    limit = next(i for i in body["items"] if i["type"] == "gap" and i["state"] == "limit")
    assert set(limit["next_turn"]) == {"turn", "kind", "origin", "label", "display_id"}
    # min_gap_ms is applied
    status, wide = get(base, "/api/sessions/sstretch/turns?min_gap_ms=3000000")
    assert [i["state"] for i in wide["items"] if i["type"] == "gap"] == ["limit"]
    harness = get(base, "/api/sessions/sharness/turns")[1]["items"]
    assert [i["display_id"] for i in harness if i["type"] == "turn"] == ["T1", None, "T2", None, None, "T3"]


def test_resolve_shape(base):
    status, body = get(base, "/api/sessions/sagents/resolve?id=toolu_sendA")
    assert status == 200 and set(body) == {"freshness", "row", "turn", "depth"}
    assert body["row"] == {"type": "agent", "id": "claude_code/sagents/engagement/ra#1"}
    assert (body["turn"], body["depth"]) == ("claude_code/sagents/turn/main#1", 0)
    assert get(base, "/api/sessions/sagents/resolve?id=req_nope")[0] == 404


# ── errors ───────────────────────────────────────────────────────────────────


def test_min_gap_below_60000_400(base):
    status, body = get(base, "/api/sessions/sagents/turns?min_gap_ms=59999")
    assert status == 400 and "error" in body
    assert get(base, "/api/sessions/sagents/turns?min_gap_ms=60000")[0] != 400  # the floor itself is valid


def test_unknown_session_404(base):
    for path in (
        "/api/sessions/nosuchsession",
        "/api/sessions/nosuchsession/turns",
        "/api/sessions/nosuchsession/resolve?id=x",
    ):
        status, body = get(base, path)
        assert status == 404 and "error" in body and "nosuchsession" in body["error"], path


def test_ambiguous_prefix_400(ambiguous_base):
    for path in (
        "/api/sessions/shared-prefix",
        "/api/sessions/shared-prefix/turns",
        "/api/sessions/shared-prefix/resolve?id=x",
    ):
        status, body = get(ambiguous_base, path)
        assert status == 400 and "ambiguous" in body["error"], path
    assert (
        get(ambiguous_base, "/api/sessions/shared-prefix-1/resolve?id=x")[0] == 404
    )  # the full id is not ambiguous


def test_resolve_requires_id(base):
    status, body = get(base, "/api/sessions/sagents/resolve")
    assert status == 400 and "error" in body
