# ruff: noqa: E501, B023 — a data table; the nested builders run inside their loop iteration.
"""Hand-built 51-op store for the query tests (design §11: "50-op hand-built tree").

Built directly against botsworth.store.schema (T0), not against T4's loader: the
builder fills the derived columns the store owns (incl_*, closure, rollups of
time, sessions rows) with the simple rules of design §4.1 so every expected
number in the tests can be checked by hand from the literals below.

Sessions (D0 = Monday 2026-10-05 10:00 UTC):
  sA claude_code /p/alpha  D0      incl cost 0.40 (dup A6dup excluded)
  sB codex       /p/alpha  D0+1d   incl cost 0.34, B2 cost missing -> lower bound
  sC omp         /p/beta   D0+8d   incl cost 0.05, ttft present
  sD claude_code /p/beta   D0+9d   incl cost 0.21
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from botsworth import pointer
from botsworth.store import refs, schema

DAY = 86_400_000
D0 = int(datetime(2026, 10, 5, 10, 0, tzinfo=UTC).timestamp() * 1000)
WEEK0 = int(datetime(2026, 10, 5, tzinfo=UTC).timestamp() * 1000)  # Monday 00:00 UTC
S = 1000


@dataclass
class N:
    kind: str
    key: str
    start: int | None = None
    end: int | None = None
    children: list[N] = field(default_factory=list)
    f: dict[str, Any] = field(default_factory=dict)


def llm(
    key, start, end, model, tin, cr, cw, out, cost, *children, basis="computed", time_prov="derived", **f
):
    return N(
        "llm_call",
        key,
        start,
        end,
        list(children),
        dict(
            model=model,
            in_tok=tin,
            cache_read_tok=cr,
            cache_write_tok=cw,
            out_tok=out,
            reasoning_tok=f.pop("reasoning", 0),
            cost_usd=cost,
            cost_basis=basis if cost is not None else "missing",
            time_prov=time_prov,
            **f,
        ),
    )


def tool(key, start, end, name, *children, time_prov="derived", **f):
    return N("tool_call", key, start, end, list(children), dict(tool_name=name, time_prov=time_prov, **f))


def eng(key, agent_type, *turns, basis="id"):
    return N("engagement", key, children=list(turns), f=dict(agent_type=agent_type, caused_by_basis=basis))


def turn(key, label, *children):
    return N("turn", key, children=list(children), f=dict(label=label))


def T(sec: float, base: int = D0) -> int:
    return base + int(sec * S)


B0, C0, DD = D0 + DAY, D0 + 8 * DAY, D0 + 9 * DAY

SESSIONS = [
    dict(
        provider="claude_code",
        sid="sA",
        project="/p/alpha",
        account="anthropic:default",
        title="alpha fix",
        tree=[
            turn(
                "main#0",
                "fix the rounding bug",
                llm(
                    "req_a1", T(1), T(3), "claude-sonnet-4-5", 100, 1000, 200, 50, 0.10,
                    tool(
                        "toolu_a1", T(3), T(7), "Bash", command="uv run pytest -q tests/cost",
                        command_norm="uv run pytest -q tests/cost", command_key="uv run pytest",
                        exit_code=1, is_error=1,
                    ),
                ),
                llm(
                    "req_a2", T(7), T(9), "claude-sonnet-4-5", 120, 1300, 0, 80, 0.06,
                    tool(
                        "toolu_a2", T(9), T(30), "Agent",
                        eng(
                            "x#0", "Explore",
                            turn(
                                "x#0", "explore cost module",
                                llm(
                                    "req_x1", T(10), T(12), "claude-haiku-4-5", 50, 0, 500, 40, 0.02,
                                    tool("toolu_x1", T(12), T(13), "Read", file_path="src/cost.py"),
                                ),
                                llm("req_x2", T(13), T(15), "claude-haiku-4-5", 60, 500, 0, 30, 0.01),
                            ),
                        ),
                    ),
                ),
                llm(
                    "req_a3", T(30), T(33), "claude-sonnet-4-5", 150, 1500, 0, 200, 0.08,
                    tool("toolu_a3", T(33), T(34), "Edit", file_path="src/cost.py"),
                ),
                llm("req_a4", T(34), T(36), "claude-sonnet-4-5", 80, 1600, 0, 60, 0.04),
            ),
            turn(
                "main#1",
                "run tests again",
                llm(
                    "req_a5", T(100), T(102), "claude-sonnet-4-5", 90, 1700, 0, 40, 0.05,
                    tool(
                        "toolu_a5", T(102), T(110), "Bash", command="uv run pytest",
                        command_norm="uv run pytest", command_key="uv run pytest", exit_code=0,
                    ),
                ),
                llm("req_a6", T(110), T(111), "claude-sonnet-4-5", 20, 1800, 0, 30, 0.03),
                llm("req_a6dup", T(110), T(111), "claude-sonnet-4-5", 20, 1800, 0, 30, 0.03, dup="req_a6"),
            ),
            eng("y#0", "teammate", turn("y#0", "teammate ping", llm("req_y1", T(200), T(202), "claude-haiku-4-5", 10, 0, 100, 10, 0.01)), basis="none"),
        ],
        steps={
            "main#0": [
                ("run", "toolu_a1", "uv run pytest -q tests/cost", "exit:1"),
                ("spawn", "toolu_a2", "Explore", "ok"),
                ("edit", "toolu_a3", "src/cost.py", "ok"),
                ("answer", "req_a4", "Fixed the rounding", "ok"),
            ],
            "x#0": [("read", "toolu_x1", "src/cost.py", "ok"), ("answer", "req_x2", "cost.py rounds", "ok")],
            "main#1": [
                ("skill", "req_a5", "grill-me", "ok"),
                ("run", "toolu_a5", "uv run pytest", "exit:0"),
                ("answer", "req_a6", "All green", "ok"),
            ],
            "y#0": [("answer", "req_y1", "pong", "ok")],
        },
        events=[
            ("compaction", T(50), "main#1", "turn", dict(trigger="auto", pre_tokens=150000, post_tokens=20000)),
            ("skill_invocation", T(101), "req_a5", "llm_call", dict(skill="grill-me", via="tool", step_id="claude_code/sA/turn/main#1#0")),
            ("api_error", T(111), "req_a6dup", "llm_call", dict(kind="overloaded")),  # copied: on a duplicate op
        ],
    ),
    dict(
        provider="codex",
        sid="sB",
        project="/p/alpha",
        account="openai:plus",
        title="alpha codex",
        tree=[
            turn(
                "main#0",
                "add tests",
                llm(
                    "resp_b1", T(1, B0), T(5, B0), "gpt-5", 1000, 2000, 0, 300, 0.20,
                    tool(
                        "call_b1", T(5, B0), T(17, B0), "exec_command", time_prov="present",
                        command="uv run pytest tests", command_norm="uv run pytest tests",
                        command_key="uv run pytest", exit_code=0,
                    ),
                    reasoning=100,
                ),
                llm("resp_b2", T(17, B0), T(20, B0), "gpt-5.1-codex-max", 500, 2500, 0, 100, None),
                llm("resp_b3", T(20, B0), T(22, B0), "gpt-5", 200, 2600, 0, 50, 0.05),
            ),
            turn(
                "main#1",
                "patch a.py",
                llm(
                    "resp_b4", T(300, B0), T(304, B0), "gpt-5", 300, 0, 0, 70, 0.07,
                    tool("call_b4", T(304, B0), T(304.5, B0), "apply_patch", time_prov="present", file_path="src/a.py"),
                ),
                llm("resp_b5", T(305, B0), T(306, B0), "gpt-5", 50, 300, 0, 20, 0.02),
            ),
        ],
        steps={
            "main#0": [
                ("think", "resp_b1", "", "ok"),
                ("run", "call_b1", "uv run pytest tests", "exit:0"),
                ("say", "resp_b2", "", "ok"),
                ("answer", "resp_b3", "tests added", "ok"),
            ],
            "main#1": [("edit", "call_b4", "src/a.py", "ok"), ("answer", "resp_b5", "patched", "ok")],
        },
        events=[("limit_state", T(5, B0), "resp_b1", "llm_call", dict(used_percent=42.0, window_minutes=300))],
    ),
    dict(
        provider="omp",
        sid="sC",
        project="/p/beta",
        account="openai:cred-1",
        title="beta omp",
        tree=[
            turn(
                "main#0",
                "make test",
                llm(
                    "c1", T(1, C0), T(3, C0), "gpt-5", 100, 0, 0, 100, 0.03,
                    tool(
                        "tc_c1", T(3, C0), T(6, C0), "bash", time_prov="present", command="make test",
                        command_norm="make test", command_key="make test", exit_code=2, is_error=1,
                    ),
                    basis="reported", time_prov="present", ttft_ms=400, ttft_prov="present",
                ),
                llm(
                    "c2", T(6, C0), T(7, C0), "gpt-5", 120, 100, 0, 40, 0.02,
                    basis="reported", time_prov="present", ttft_ms=300, ttft_prov="present",
                ),
            ),
        ],
        steps={"main#0": [("run", "tc_c1", "make test", "exit:2"), ("answer", "c2", "make test fails", "ok")]},
        events=[("api_error", T(5, C0), "c1", "llm_call", dict(kind="rate_limit", status=429))],
    ),
    dict(
        provider="claude_code",
        sid="sD",
        project="/p/beta",
        account="anthropic:default",
        title="beta search",
        tree=[
            turn(
                "main#0",
                "find slow tests",
                *[
                    llm(
                        f"req_d{i}", T(10 * i, DD), T(10 * i + 2, DD), "claude-sonnet-4-5", 10, 100, 0, 10,
                        round(0.01 * i, 2),
                        tool(
                            f"toolu_d{i}", T(10 * i + 2, DD), T(10 * i + 2 + dur, DD), name, **extra,
                        ),
                    )
                    for i, (name, dur, extra) in enumerate(
                        [
                            ("Grep", 0.1, {}),
                            ("Grep", 0.2, {}),
                            ("Grep", 0.3, {}),
                            ("Bash", 1, dict(command="make test", command_norm="make test", command_key="make test")),
                            ("Bash", 2, dict(command="make test", command_norm="make test", command_key="make test")),
                            ("Bash", 9, dict(command="make test", command_norm="make test", command_key="make test")),
                        ],
                        start=1,
                    )
                ],
            ),
        ],
        steps={
            "main#0": [
                *[("search" if i <= 3 else "run", f"toolu_d{i}", "tests" if i <= 3 else "make test", "ok") for i in range(1, 7)],
                ("answer", "req_d6", "slow: make test", "ok"),
            ]
        },
        events=[("skill_invocation", T(1, DD), "req_d1", "llm_call", dict(skill="grill-me", via="slash"))],
    ),
]  # fmt: skip

WEAK = {"present": 3, "derived": 2, "estimated": 1, "missing": 0}


def op_id(provider: str, sid: str, kind: str, key: str) -> str:
    return f"{provider}/{sid}/{kind}/{key}"


def _union(intervals: list[tuple[int, int]]) -> int:
    total, cur = 0, None
    for s, e in sorted(intervals):
        if cur is None or s > cur[1]:
            if cur is not None:
                total += cur[1] - cur[0]
            cur = [s, e]
        else:
            cur[1] = max(cur[1], e)
    return total + (cur[1] - cur[0] if cur else 0)


def _max_parallel(intervals: list[tuple[int, int]]) -> int:
    ev = sorted([(s, 1) for s, _ in intervals] + [(e, -1) for _, e in intervals], key=lambda x: (x[0], x[1]))
    best = cur = 0
    for _, d in ev:
        cur += d
        best = max(best, cur)
    return best


def _src_cols(conn: sqlite3.Connection, uri: str) -> dict[str, Any]:
    provider, relpath, prefix, loc, h = refs.to_parts(uri)
    return dict(src_file=refs.intern_file(conn, provider, relpath, prefix), src_loc=loc, src_h=h)


def _v7_cols(conn: sqlite3.Connection, row: dict[str, Any], rowids: dict[str, int]) -> dict[str, Any]:
    """The v7 columns (DM-8, DM-14) of a v6-shaped fixture row."""
    return dict(
        **_src_cols(conn, row["src"]),
        caused_by_rowid=rowids[row["caused_by"]] if row.get("caused_by") else None,
        cwd_id=refs.intern_name(conn, row.get("cwd")),
        account_id=refs.intern_name(conn, row.get("account")),
        path_sig_id=refs.intern_name(conn, row.get("path_sig")),
        occ_hash=refs.occ_hash(row["occ_key"]) if row.get("occ_key") else None,
    )


def build(db: Path, src_root: Path | None = None) -> dict[str, int]:
    """Write the store; returns {op_id: rowid}. With src_root, writes a provider file for source() tests."""
    conn = sqlite3.connect(db)
    schema.ensure(conn)
    rowids: dict[str, int] = {}
    ops: list[dict[str, Any]] = []
    all_steps: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []

    for sess in SESSIONS:
        prov, sid = sess["provider"], sess["sid"]
        base = dict(
            provider=prov, session_id=sid, project=sess["project"], account=sess["account"], bundle=sid
        )
        src = pointer.format(prov, f"proj/{sid}.jsonl", pointer.Lines(1, 1))
        root = N("session", "-", children=sess["tree"], f=dict(label=sess["title"]))
        session_rows: list[dict[str, Any]] = []
        steps_by_turn: dict[str, int] = {}
        pre = [0]  # DM-15 pre-order counter of this session
        main_turns = [0]  # DM-24 display_ix counter (these fixture turns are all human turns)

        def visit(n: N, parent: dict | None, ancestors: list[int], agent: str | None, depth: int, seq: int):
            rid = len(ops) + 1
            oid = op_id(prov, sid, n.kind, n.key)
            pre_ix, pre[0] = pre[0], pre[0] + 1
            if n.kind == "engagement":
                agent = n.key.split("#")[0]
            row: dict[str, Any] = dict(
                base, rowid=rid, op_id=oid, kind=n.kind, parent=parent["op_id"] if parent else None,
                parent_rowid=parent["rowid"] if parent else None, root_rowid=ancestors[0] if ancestors else rid,
                seq=seq, depth=depth, agent_id=agent, src=src, label=n.f.get("label", n.key), pre=pre_ix,
                occ_key=n.key if n.kind in ("llm_call", "tool_call") else None,
            )  # fmt: skip
            f = dict(n.f)
            dup = f.pop("dup", None)
            if dup:
                row["duplicate_of"] = op_id(prov, sid, n.kind, dup)
                row["occ_key"] = None
            if n.kind == "engagement":
                row["caused_by"] = parent["op_id"] if parent["kind"] == "tool_call" else None
            row.update(f)
            if n.kind == "turn" and parent is not None and parent["kind"] == "session":
                main_turns[0] += 1
                row.setdefault("turn_kind", "human")
                row.setdefault("turn_origin", "typed")
                row["display_ix"] = main_turns[0]  # QRY-2: display ids read display_ix
            ops.append(row)
            rowids[oid] = rid
            kids = [visit(c, row, [*ancestors, rid], agent, depth + 1, i) for i, c in enumerate(n.children)]
            row["post"] = pre[0] - 1
            rollup(row, n, kids)
            return row

        def rollup(row: dict, n: N, kids: list[dict]) -> None:
            live = [k for k in kids if not k.get("duplicate_of")]
            leaf = n.kind in ("llm_call", "tool_call")
            if leaf:
                row["start_ms"], row["end_ms"] = n.start, n.end
                row["dur_ms"] = n.end - n.start
                if n.kind == "llm_call":
                    row["tok_prov"] = "present"
                    row["context_tokens"] = row["in_tok"] + row["cache_read_tok"] + row["cache_write_tok"]
                    row["tok_total"] = row["context_tokens"] + row["out_tok"]
                    row.setdefault("ttft_prov", "missing")
                else:
                    for k in ("in_tok", "cache_read_tok", "cache_write_tok", "out_tok", "reasoning_tok"):
                        row[k] = 0
                    row["tok_total"], row["tok_prov"] = 0, "present"
                row.setdefault("is_error", 0)
                intervals = [(row["start_ms"], row["end_ms"])] + [iv for k in live for iv in k["_iv"]]
            else:
                row["start_ms"] = min(k["start_ms"] for k in live)
                row["end_ms"] = max(k["end_ms"] for k in live)
                row["time_prov"] = None
                intervals = [iv for k in live for iv in k["_iv"]]
            row["_iv"] = [] if row.get("duplicate_of") else intervals
            for col in (
                "in_tok",
                "cache_read_tok",
                "cache_write_tok",
                "out_tok",
                "reasoning_tok",
                "tok_total",
            ):
                row[f"incl_{col}"] = (row.get(col) or 0) + sum(k[f"incl_{col}"] for k in live)
            costs = ([row.get("cost_usd")] if leaf else []) + [k["incl_cost_usd"] for k in live]
            known = [c for c in costs if c is not None]
            row["incl_cost_usd"] = round(sum(known), 10) if known else None
            row["cost_lower_bound"] = int(
                (leaf and row.get("cost_basis") == "missing") or any(k["cost_lower_bound"] for k in live)
            )
            own_llm = row["dur_ms"] if n.kind == "llm_call" else 0
            own_tool = row["dur_ms"] if n.kind == "tool_call" else 0
            row["incl_llm_ms"] = own_llm + sum(k["incl_llm_ms"] for k in live)
            row["incl_tool_ms"] = own_tool + sum(k["incl_tool_ms"] for k in live)
            row["busy_sum_ms"] = row["incl_llm_ms"] + row["incl_tool_ms"]
            if not leaf:
                row["segments"] = json.dumps([[row["start_ms"], row["end_ms"]]])
            row["wall_ms"] = row["end_ms"] - row["start_ms"]
            row["active_ms"] = _union(intervals)
            row["idle_ms"] = row["wall_ms"] - row["active_ms"]
            row["blocked_ms"] = 0
            row["max_parallel"] = _max_parallel(intervals)
            row["n_llm_calls"] = (n.kind == "llm_call") + sum(k["n_llm_calls"] for k in live)
            row["n_tool_calls"] = (n.kind == "tool_call") + sum(k["n_tool_calls"] for k in live)
            row["n_engagements"] = (n.kind == "engagement") + sum(k["n_engagements"] for k in live)
            row["n_errors"] = (row.get("is_error") or 0) + sum(k["n_errors"] for k in live)
            row["n_steps"] = len(sess["steps"].get(n.key, [])) if n.kind == "turn" else 0
            row["n_steps"] += sum(k["n_steps"] for k in live)
            provs = ([row["tok_prov"]] if leaf else []) + [k["incl_tok_prov"] for k in live]
            row["incl_tok_prov"] = min(provs, key=WEAK.__getitem__)
            tprovs = ([row["time_prov"]] if leaf else []) + [k["incl_time_prov"] for k in live]
            row["incl_time_prov"] = min(tprovs, key=WEAK.__getitem__)

        srow = visit(root, None, [], None, 0, 0)
        session_rows.append(srow)
        for turn_key, items in sess["steps"].items():
            tid = op_id(prov, sid, "turn", turn_key)
            for ordinal, (verb, op_key, target, outcome) in enumerate(items):
                okind = "tool_call" if op_key.startswith(("toolu", "call", "tc_")) else "llm_call"
                oid = op_id(prov, sid, okind, op_key)
                o = next(x for x in ops if x["op_id"] == oid)
                all_steps.append(
                    dict(
                        root_rowid=srow["rowid"], turn_rowid=rowids[tid], ordinal=ordinal, op_rowid=rowids[oid], verb=verb,
                        tool_name=o.get("tool_name"), target=target, outcome=outcome, summary=target,
                        snippet=target, start_ms=o["start_ms"], dur_ms=o["dur_ms"], skill=None,
                        sub_items=None, **_src_cols(conn, src),
                    )
                )  # fmt: skip
            steps_by_turn[tid] = len(items)
        for n_ev, (kind, ts, okey, okind, fields) in enumerate(sess["events"]):
            all_events.append(
                dict(
                    root_rowid=srow["rowid"], event_id=f"{prov}/{sid}/event/{kind}#{n_ev}",
                    kind=kind, ts_ms=ts, session_id=sid, op_id=op_id(prov, sid, okind, okey), provider=prov,
                    account=sess["account"], project=sess["project"], basis="present",
                    fields=json.dumps(fields), copied=int(okey.endswith("dup")), **_src_cols(conn, src),
                )
            )  # fmt: skip
        conn.execute(
            "INSERT INTO sessions(rowid, op_rowid, bundle, provider, session_id, project, account, title, git_branch, "
            "harness_version, status, first_ms, last_ms, incl_cost_usd, incl_tok_total) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                srow["rowid"], srow["rowid"], sid, prov, sid, sess["project"], sess["account"], sess["title"], "main", "1.0",
                "settled", srow["start_ms"], srow["end_ms"], srow["incl_cost_usd"], srow["incl_tok_total"],
            ),
        )  # fmt: skip

    # Q1 (API-27, API-28): the listings read the project name (DM-60); one row per project
    for project, n in conn.execute("SELECT project, COUNT(*) FROM sessions GROUP BY project").fetchall():
        conn.execute(
            "INSERT INTO projects(project, name, n_sessions, n_automated) VALUES (?, ?, ?, 0)",
            (project, project.rsplit("/", 1)[-1], n),
        )

    cols = [c[1] for c in conn.execute("PRAGMA table_info(ops)")]
    for row in ops:
        row.update(_v7_cols(conn, row, rowids))
        values = [row.get(c) for c in cols]
        conn.execute(f"INSERT INTO ops({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", values)
    for table, rows in (("steps", all_steps), ("events", all_events)):
        for r in rows:
            conn.execute(
                f"INSERT INTO {table}({', '.join(r)}) VALUES ({', '.join('?' for _ in r)})", list(r.values())
            )
    _rollups(conn, ops)
    conn.executemany(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        [
            ("last_ingest_ms", str(D0 + 30 * DAY)),
            ("parser_version", "fixture-1"),
            ("price_fingerprint", "fp-1"),
        ],
    )
    conn.commit()
    conn.close()
    if src_root is not None:
        _write_source(src_root)
    return rowids


SOURCE_LINES = [
    {"message": {"content": [{"type": "text", "text": "hello world"}]}},
    {"message": {"content": [{"type": "tool_use", "input": {"command": "uv run pytest"}}]}},
    {"toolUseResult": "3 failed"},
]


def _write_source(root: Path) -> None:
    (root / "proj").mkdir(parents=True, exist_ok=True)
    (root / "proj" / "sA.jsonl").write_text("".join(json.dumps(x) + "\n" for x in SOURCE_LINES))


def _rollups(conn: sqlite3.Connection, ops: list[dict[str, Any]]) -> None:
    """rollup_time as store.load builds it: owned leaves per (root, bucket, provider, account, model, project)."""
    groups: dict[tuple, dict[str, Any]] = {}
    for r in ops:
        if r["kind"] not in ("llm_call", "tool_call") or r.get("duplicate_of"):
            continue
        start = r["start_ms"] // 900_000 * 900_000  # DM-41: one 15-minute epoch bucket
        llm = r["kind"] == "llm_call"
        key = (r["root_rowid"], start, r["provider"], r["account"], r.get("model"), r["project"])
        g = groups.setdefault(
            key,
            dict.fromkeys(
                ("n_llm_calls", "n_tool_calls", "n_unpriced", "n_tok_missing")
                + ("n_time_derived", "n_time_estimated", "n_time_missing"),
                0,
            ),
        )
        if llm:
            g["n_tok_missing"] += r.get("tok_prov") == "missing"
            g[f"n_time_{r['time_prov']}"] = g.get(f"n_time_{r['time_prov']}", 0) + 1
        g["n_llm_calls"] += llm
        g["n_tool_calls"] += not llm
        g["n_unpriced"] += llm and r.get("cost_usd") is None
        for c in ("in_tok", "out_tok", "cache_read_tok", "cache_write_tok", "reasoning_tok", "cost_usd"):
            if r.get(c) is not None and (llm or c != "cost_usd"):
                g[c] = (g.get(c) or 0) + r[c]
        if llm:
            g["tok_total"] = (g.get("tok_total") or 0) + r["tok_total"]
        ms = "llm_ms" if llm else "tool_ms"
        g[ms] = (g.get(ms) or 0) + r["dur_ms"]
    for key, g in groups.items():
        g.pop("n_time_present", None)
        cols = ["root_rowid", "bucket_start_ms", "provider", "account", "model", "project", *g]
        conn.execute(
            f"INSERT INTO rollup_time({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [*key, *g.values()],
        )
