"""Claude Code adapter: fixture F1-F7, F9 exact against expected.json, plus the CONTRACT rules
that the fixtures do not reach (events, links, cycle rule, labels, rejection)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

from botsworth import pointer, stream
from botsworth.adapters import BundleRejected, get
from botsworth.adapters.claude.adapter import build_bundle, run
from botsworth.adapters.claude.discover import discover
from botsworth.model import Bundle, Op
from botsworth.pricing import Pricer

FIXTURES = Path(__file__).parents[1] / "fixtures"
T0 = 1790848800000  # 2026-10-01T10:00:00Z, the fixtures' time origin
RATES = {
    "anthropic": {
        "models": {"fixture-a": {"cost": {"input": 3, "output": 15, "cache_read": 0.30, "cache_write": 3.75}}}
    }
}


# ---- helpers ---------------------------------------------------------------


def fixture_bundles(name: str) -> dict[str, Bundle]:
    root = FIXTURES / name
    pricer = Pricer(root / "prices.json")
    out = {}
    for ref in discover(root):
        bundle, _ = build_bundle(ref, pricer)
        stream.validate(bundle)
        out[ref.session_id] = bundle
    return out


def ops(b: Bundle, kind: str | None = None) -> list[Op]:
    return [o for o in b.ops if kind is None or o.kind == kind]


def tail(op_id: str | None) -> str | None:
    return op_id.rsplit("/", 1)[1] if op_id else None


def tokens(o: Op) -> dict:
    return {
        "agent_id": o.agent_id,
        "in_tok": o.in_tok,
        "out_tok": o.out_tok,
        "cache_read_tok": o.cache_read_tok,
        "cache_write_tok": o.cache_write_tok,
        "cache_write_5m_tok": o.cache_write_5m_tok,
        "cache_write_1h_tok": o.cache_write_1h_tok,
        "cost_usd": round(o.cost_usd, 9),
    }


def subtree(b: Bundle, root: Op) -> list[Op]:
    children = defaultdict(list)
    for o in b.ops:
        children[o.parent].append(o)
    out, stack = [], [root.op_id]
    while stack:
        for c in children[stack.pop()]:
            out.append(c)
            stack.append(c.op_id)
    return out


def union(intervals):
    merged = []
    for s, e in sorted(intervals):
        if e <= s:
            continue
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(e, merged[-1][1])
        else:
            merged.append([s, e])
    return merged


def interval_metrics(b: Bundle, parent: Op) -> dict:
    """Design §4.6 container metrics from the emitted leaf intervals (what the store computes)."""
    leaves = [o for o in subtree(b, parent) if o.kind in ("llm_call", "tool_call") and o.end_ms is not None]
    spans = [(o.start_ms, o.end_ms) for o in leaves]
    own = union([(o.start_ms, o.end_ms) for o in leaves if o.agent_id == parent.agent_id])
    other = union([(o.start_ms, o.end_ms) for o in leaves if o.agent_id != parent.agent_id])
    gaps = [(own[i][1], own[i + 1][0]) for i in range(len(own) - 1)]
    blocked = sum(max(0, min(e, ge) - max(s, gs)) for gs, ge in gaps for s, e in other)
    running = peak = 0
    for _, change in sorted([(s, 1) for s, e in spans if e > s] + [(e, -1) for s, e in spans if e > s]):
        running += change
        peak = max(peak, running)
    return {
        "wall_ms": max(e for _, e in spans) - min(s for s, _ in spans),
        "busy_sum_ms": sum(o.dur_ms for o in leaves),
        "active_ms": sum(e - s for s, e in union(spans)),
        "blocked_ms": blocked,
        "idle_ms": sum(e - s for s, e in gaps) - blocked,
        "max_parallel": peak,
    }


def observed(name: str) -> dict:
    """The facts each fixture's expected.json pins, computed from the adapter output."""
    bundles = fixture_bundles(name)
    b = bundles.get("s1")
    calls = ops(b, "llm_call") if b else []
    if name in ("F1", "F2", "F4"):
        out = {"llm_calls": [tokens(c) for c in calls]}
        if name == "F1":
            out["tool_calls"] = len(ops(b, "tool_call"))
            out["steps"] = [
                [tail(t), [s.verb for s in sorted(b.steps, key=lambda s: s.ordinal) if s.turn_id == t]]
                for t in sorted({s.turn_id for s in b.steps})
            ]
        if name == "F4":
            out["cost_cache_write_usd"] = round(calls[0].cost_cache_write_usd, 9)
        return out
    if name == "F3":
        turn = next(t for t in ops(b, "turn") if t.agent_id is None)
        ta = next(t for t in ops(b, "tool_call") if tail(t.op_id) == "ta")
        return {
            "llm_calls": len(calls),
            "out_tok_total": sum(c.out_tok for c in calls),
            f"turn:{tail(turn.op_id)}": interval_metrics(b, turn),
            "tool_call:ta": {
                "dur_ms": ta.dur_ms,
                "is_async": ta.is_async,
                "subtree_wall_ms": interval_metrics(b, ta)["wall_ms"],
            },
        }
    if name == "F5":
        per = {sid: ops(x, "llm_call") for sid, x in bundles.items()}
        shared = sorted(set.intersection(*({c.occ_key for c in cs} for cs in per.values())))
        unique = {c.occ_key: c.cost_usd for cs in per.values() for c in cs}
        return {
            "bundles": {
                sid: {"llm_calls": len(cs), "duplicate_of": [c.duplicate_of for c in cs]}
                for sid, cs in sorted(per.items())
            },
            "shared_occ_keys": shared,
            "unique_cost_usd": round(sum(unique.values()), 9),
            "copy_out_tok": sum(c.out_tok for c in per["s2"]),
        }
    if name == "F6":
        call = calls[0]
        by_id = {o.op_id: o for o in b.ops}
        ancestors, node = [], by_id[call.parent]
        while node is not None:
            ancestors.append(node.kind)
            node = by_id.get(node.parent)
        return {
            "llm_calls": [
                {
                    "agent_id": call.agent_id,
                    "cost_basis": call.cost_basis,
                    "cost_usd": call.cost_usd,
                    "out_tok": call.out_tok,
                }
            ],
            "lower_bound_ancestors": sorted(ancestors),
        }
    if name == "F7":
        eg = ops(b, "engagement")[0]
        session = ops(b, "session")[0]
        return {
            "engagement": {
                "parent": "session" if eg.parent == session.op_id else eg.parent,
                "caused_by_basis": eg.caused_by_basis,
                "caused_by": eg.caused_by,
            },
            "out_tok_total": sum(c.out_tok for c in subtree(b, session) if c.kind == "llm_call"),
        }
    if name == "F9":
        return {
            "owners": sorted([c.agent_id, c.out_tok] for c in calls if c.duplicate_of is None),
            "copies": [
                {"agent_id": c.agent_id, "occ_key": c.occ_key, "duplicate_of": c.duplicate_of}
                for c in calls
                if c.duplicate_of
            ],
        }
    raise KeyError(name)


# ---- fixtures F1-F7, F9: expected.json exact ---------------------------------


@pytest.mark.parametrize("name", ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F9"])
def test_fixture_matches_expected_json(name):
    expected = json.loads((FIXTURES / name / "expected.json").read_text())
    expected.pop("note")
    assert observed(name) == expected


def test_F9_copy_has_its_own_op_id_and_the_owner_occ_key():
    b = fixture_bundles("F9")["s1"]
    ids = [o.op_id for o in b.ops]
    assert len(ids) == len(set(ids))
    copy = next(o for o in ops(b, "llm_call") if o.duplicate_of)
    assert tail(copy.op_id) == "rp@copy:afork" and copy.is_fork and copy.spawn_depth == 2


def test_F3_containers_carry_own_file_segments_and_no_metrics():
    b = fixture_bundles("F3")["s1"]
    session = ops(b, "session")[0]
    assert session.segments == [[T0 - 5000, T0 + 45000]]  # main file only, not the agents' spans
    for o in b.ops:
        if o.kind in ("session", "engagement", "turn"):
            assert o.segments and all(len(s) == 2 and s[0] <= s[1] for s in o.segments)
            assert o.cost_usd is None and o.out_tok is None and o.dur_ms is None
        else:
            assert o.segments is None
    eg_a = next(o for o in ops(b, "engagement") if o.agent_id == "a")
    assert eg_a.segments == [[T0, T0 + 40000]]  # agent a's own file only (D19)


# ---- built transcripts (tmp_path) ------------------------------------------


def stamp(second: int) -> str:
    return f"2026-10-01T10:{second // 60:02d}:{second % 60:02d}.000Z"


def user(key, second=0, text="hi", **extra):
    return dict(
        type="user", uuid=key, timestamp=stamp(second), cwd="/repo", message={"content": text}, **extra
    )


def assistant(rid, second=1, out=200, stop="end_turn", content=None, uuid=None, model="fixture-a", **extra):
    usage = {
        "input_tokens": 10,
        "output_tokens": out,
        "cache_read_input_tokens": 5000,
        "cache_creation_input_tokens": 1000,
        "cache_creation": {"ephemeral_5m_input_tokens": 1000, "ephemeral_1h_input_tokens": 0},
    }
    msg = {"model": model, "stop_reason": stop, "content": content or [], "usage": usage}
    return dict(
        type="assistant",
        uuid=uuid or f"{rid}-{second}-{out}",
        requestId=rid,
        timestamp=stamp(second),
        cwd="/repo",
        message=msg,
        **extra,
    )


def tool(key, name, **inp):
    return {"type": "tool_use", "id": key, "name": name, "input": inp}


def result(key, second=2, content="", **extra):
    block = {"type": "tool_result", "tool_use_id": key, "content": content}
    return user(f"result-{key}", second, [block], **extra)


def write(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def build(tmp_path, rows, agents=(), sid="s1", folder="-repo") -> Bundle:
    main = write(tmp_path / folder / f"{sid}.jsonl", rows)
    for aid, records, meta in agents:
        p = write(main.with_suffix("") / "subagents" / f"agent-{aid}.jsonl", records)
        p.with_suffix(".meta.json").write_text(json.dumps(meta))
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps(RATES))
    (ref,) = [r for r in discover(tmp_path) if r.session_id == sid]
    bundle, _ = build_bundle(ref, Pricer(prices))
    stream.validate(bundle)
    return bundle


def test_events_have_nested_fields_and_point_at_their_step(tmp_path):
    records = [
        user(
            "u", text="<command-name>/debugging</command-name><command-args>why</command-args>", promptId="p1"
        ),
        user(
            "u-meta", text="Base directory for this skill: /x", isMeta=True, promptId="p1"
        ),  # DM-21 expansion
        assistant(
            "r1",
            1,
            content=[
                tool("ts", "Skill", skill="debugging", args="bug"),
                tool("tr", "Read", file_path="/home/me/skills/debugging/SKILL.md"),
            ],
        ),
        result("ts"),
        result("tr"),
        {
            "type": "system",
            "uuid": "c",
            "timestamp": stamp(5),
            "subtype": "compact_boundary",
            "compactMetadata": {"trigger": "auto", "preTokens": 10000, "postTokens": 3000, "durationMs": 900},
        },
        {
            "type": "assistant",
            "uuid": "e",
            "timestamp": stamp(6),
            "isApiErrorMessage": True,
            "error": "rate_limit",
            "message": {
                "model": "<synthetic>",
                "content": [
                    {"type": "text", "text": "You've hit your session limit · resets 2:20am (Asia/Calcutta)"}
                ],
            },
        },
    ]
    b = build(tmp_path, records)
    ev = {(e.kind, e.fields.get("via")): e for e in b.events}
    assert sorted(ev) == [
        ("api_error", None),
        ("compaction", None),
        ("limit_hit", None),
        ("skill_invocation", "read"),
        ("skill_invocation", "slash"),
        ("skill_invocation", "tool"),
    ]
    comp = ev[("compaction", None)].fields
    assert comp == {"trigger": "auto", "pre_tokens": 10000, "post_tokens": 3000, "duration_ms": 900}
    assert ev[("api_error", None)].fields["kind"] == "rate_limit"
    hit = ev[("limit_hit", None)]
    assert hit.fields["window"] == "session" and hit.fields["tz"] == "Asia/Calcutta"
    assert hit.fields["resets_at_ms"] == T0 + 6000 + (10 * 3600 + 50 * 60 - 6) * 1000  # 02:20 IST next day
    assert hit.basis == "derived"
    steps = {f"{s.turn_id}#{s.ordinal}": s for s in b.steps}
    for via in ("tool", "read"):
        e = ev[("skill_invocation", via)]
        step = steps[e.fields["step_id"]]
        assert (step.verb, step.target, step.op_id) == ("skill", "debugging", e.op_id)
    assert ev[("skill_invocation", "read")].fields["skill_md_src"] == ev[("skill_invocation", "read")].src
    slash_step = steps[ev[("skill_invocation", "slash")].fields["step_id"]]  # DM-30: first step of the turn
    assert slash_step.ordinal == 0 and slash_step.turn_id == ev[("skill_invocation", "slash")].op_id
    assert len({e.event_id for e in b.events}) == len(b.events)


def test_limit_hit_reset_time_is_next_local_occurrence():
    from botsworth.adapters.claude.events import reset_ms

    at, tz = reset_ms("resets 2:20am (Asia/Calcutta)", T0, None)  # 10:00Z = 15:30 IST
    assert tz == "Asia/Calcutta" and at == T0 + (10 * 3600 + 50 * 60) * 1000  # 02:20 IST next day = 20:50Z
    assert reset_ms("resets 4pm (Asia/Calcutta)", T0, None)[0] == T0 + 30 * 60 * 1000  # later today
    assert reset_ms("resets 2:20am (Not/AZone)", T0, None) == (None, None)
    assert reset_ms("whatever", T0, 1791200000) == (1791200000000, None)


def test_turn_boundaries_and_answer_rule(tmp_path):
    records = [
        user("u", text="first question"),
        assistant(
            "r1",
            1,
            stop="tool_use",
            content=[{"type": "text", "text": "looking"}, tool("tb", "Bash", command="ls")],
        ),
        result("tb"),
        user("n", 3, "<task-notification>done</task-notification>"),
        user("m", 4, "Base directory for this skill: /x", isMeta=True),
        assistant(
            "r2", 5, content=[{"type": "thinking", "thinking": "hm"}, {"type": "text", "text": "done"}]
        ),
        user("u2", 8, "second question"),
        assistant("r3", 9, content=[{"type": "text", "text": "ok"}]),
    ]
    b = build(tmp_path, records)
    turns = ops(b, "turn")
    assert [t.label for t in turns] == ["first question", "second question"]
    by_turn = defaultdict(list)
    for s in sorted(b.steps, key=lambda s: s.ordinal):
        by_turn[tail(s.turn_id)].append((s.verb, s.snippet))
    assert by_turn["main#0"] == [("say", "looking"), ("run", None), ("think", "hm"), ("answer", "done")]
    assert by_turn["main#1"] == [("answer", "ok")]
    assert ops(b, "session")[0].label == "first question"


def test_usage_rule_zero_stub_synthetic_and_missing_usage(tmp_path):
    real = assistant("r", 1, out=80)
    stub = assistant("r", 2, out=0)
    stub["message"]["usage"] = {"input_tokens": 0, "output_tokens": 0}
    missing = assistant("missing", 3)
    del missing["message"]["usage"]
    synthetic = assistant("synth", 4, model="<synthetic>")
    b = build(tmp_path, [user("u"), real, stub, missing, synthetic])
    calls = {tail(c.op_id): c for c in ops(b, "llm_call")}
    assert sorted(calls) == ["missing", "r"]
    assert calls["r"].out_tok == 80 and calls["r"].tok_prov == "present"
    m = calls["missing"]
    assert (m.out_tok, m.cost_usd, m.cost_basis, m.tok_prov) == (None, None, "missing", "missing")


def test_flat_cache_write_without_tiers_is_priced_at_5m(tmp_path):
    rec = assistant("r", 1)
    rec["message"]["usage"]["cache_creation"] = {
        "ephemeral_5m_input_tokens": 0,
        "ephemeral_1h_input_tokens": 0,
    }
    (call,) = ops(build(tmp_path, [user("u"), rec]), "llm_call")
    assert (call.cache_write_tok, call.cache_write_5m_tok, call.cache_write_1h_tok) == (1000, None, None)
    assert round(call.cost_cache_write_usd, 9) == 0.00375


def test_llm_timing_is_gap_from_previous_non_assistant_record(tmp_path):
    b = build(tmp_path, [user("u", 0), assistant("r", 7, stop=None), assistant("r", 9)])
    (call,) = ops(b, "llm_call")
    assert (call.dur_ms, call.time_prov, call.cut_off) == (9000, "derived", False)


def test_remessaged_agent_links_each_engagement_to_its_send(tmp_path):
    main = [
        user("u"),
        assistant("r1", 1, content=[tool("spawn", "Agent", prompt="first")]),
        result("spawn", 2, toolUseResult={"agentId": "a"}),
        user("u2", 10, "followup"),
        assistant("r2", 11, content=[tool("send", "SendMessage", message="again")]),
        result("send", 12),
    ]
    agent = [
        user("au", 2, "first"),
        assistant("ra", 5),
        user("au2", 12, '<teammate-message teammate_id="main">again</teammate-message>'),
        assistant("rb", 14),
    ]
    b = build(tmp_path, main, [("a", agent, {})])
    egs = sorted(ops(b, "engagement"), key=lambda e: e.engagement_ix)
    assert [(tail(e.parent), e.caused_by_basis, e.engagement_ix) for e in egs] == [
        ("spawn", "id", 0),
        ("send", "content_hash", 1),
    ]


def test_same_body_sent_twice_links_to_latest_send_before_arrival(tmp_path):
    main = [
        user("u"),
        assistant("r1", 1, content=[tool("s1", "SendMessage", message="ping")]),
        result("s1", 2),
        user("u2", 20, "again"),
        assistant("r2", 21, content=[tool("s2", "SendMessage", message="ping")]),
        result("s2", 22),
    ]
    agent = [
        user("a0", 0, "spawned"),
        assistant("ra", 1),
        user("a1", 5, "<teammate-message>ping</teammate-message>"),
        assistant("rb", 6),
        user("a2", 25, "<teammate-message>ping</teammate-message>"),
        assistant("rc", 26),
    ]
    b = build(tmp_path, main, [("a", agent, {})])
    assert [tail(e.parent) for e in sorted(ops(b, "engagement"), key=lambda e: e.engagement_ix)] == [
        "-",
        "s1",
        "s2",
    ]


def test_cycle_rule_reparents_ping_pong_replies_under_the_spawn(tmp_path):
    def tm(text):
        return f'<teammate-message teammate_id="x">{text}</teammate-message>'

    main = [
        user("u"),
        assistant(
            "m1",
            1,
            content=[tool("spawn_r", "Agent", prompt="go"), tool("spawn_v", "Agent", prompt="review")],
        ),
        result("spawn_r", 2, toolUseResult={"agentId": "r"}),
        result("spawn_v", 2, toolUseResult={"agentId": "v"}),
    ]
    runner = [
        user("r0", 2, "go"),
        assistant("r1", 3, content=[tool("s1", "SendMessage", message="m1")]),
        result("s1", 4),
        user("r2", 6, tm("m2")),
        assistant("r3", 7, content=[tool("s3", "SendMessage", message="m3")]),
        result("s3", 8),
        user("r4", 10, tm("m4")),
        assistant("r5", 11),
    ]
    reviewer = [
        user("v0", 2, "review"),
        assistant("v1", 3),
        user("v2", 4, tm("m1")),
        assistant("v3", 5, content=[tool("s2", "SendMessage", message="m2")]),
        result("s2", 6),
        user("v4", 8, tm("m3")),
        assistant("v5", 9, content=[tool("s4", "SendMessage", message="m4")]),
        result("s4", 10),
    ]
    b = build(tmp_path, main, [("r", runner, {}), ("v", reviewer, {})])
    eg = {tail(e.op_id): e for e in ops(b, "engagement")}
    assert {k: (tail(e.parent), e.caused_by_basis) for k, e in eg.items()} == {
        "r#0": ("spawn_r", "id"),
        "v#0": ("spawn_v", "id"),
        "v#1": ("s1", "content_hash"),
        "r#1": ("spawn_r", "content_hash+cycle"),
        "v#2": ("s3", "content_hash"),
        "r#2": ("spawn_r", "content_hash+cycle"),
    }
    assert tail(eg["r#1"].caused_by) == "s2"  # cause kept as recorded
    assert max(o.depth for o in b.ops) <= 4 * 1 + 3 + 4  # bounded, does not grow per exchange


def test_depth_over_32_rejects_the_bundle_with_op_ids(tmp_path):
    # 8 agents, each spawned by the previous one: 4 levels per agent -> depth 4k+3 = 35 > 32 (D18)
    main = [
        user("u"),
        assistant("m", 1, content=[tool("sp0", "Agent", prompt="p0")]),
        result("sp0", 2, toolUseResult={"agentId": "a0"}),
    ]
    agents = []
    for k in range(8):
        rows = [user(f"a{k}u", 2, f"p{k}")]
        if k < 7:
            rows += [
                assistant(f"a{k}r", 3, content=[tool(f"sp{k + 1}", "Agent", prompt=f"p{k + 1}")]),
                result(f"sp{k + 1}", 4, toolUseResult={"agentId": f"a{k + 1}"}),
            ]
        else:
            rows.append(assistant(f"a{k}r", 3))
        agents.append((f"a{k}", rows, {"spawnDepth": k + 1}))
    with pytest.raises(BundleRejected) as exc:
        build(tmp_path, main, agents)
    assert exc.value.op_ids and all("/a7" in o or "/a6" in o for o in exc.value.op_ids)


def test_run_steps_labels_command_fields_occ_keys_account(tmp_path):
    b = build(
        tmp_path,
        [
            user("u"),
            assistant(
                "r1",
                1,
                content=[
                    tool(
                        "b1",
                        "Bash",
                        command="cd /repo && FOO=1 uv run pytest tests/x.py -q",
                        description="Run tests",
                    ),
                    tool("b2", "Bash", command="git status --short"),
                    tool("m1", "mcp__srv__do", x=1),
                ],
            ),
            result("b1", toolUseResult={"exitCode": 7}),
            result("b2", content="fatal\nexit code 128"),
            result("m1"),
        ],
    )
    calls = {tail(o.op_id): o for o in ops(b, "tool_call")}
    steps = {tail(s.op_id): s for s in b.steps if s.tool_name}
    assert [
        (steps[k].verb, steps[k].target, steps[k].summary, steps[k].outcome) for k in ("b1", "b2", "m1")
    ] == [
        ("run", "uv run pytest", "Run tests", "exit:7"),
        ("run", "git status", "git status --short", "exit:128"),
        ("other", "mcp__srv__do", "other mcp__srv__do", "ok"),
    ]
    assert (calls["b1"].command_norm, calls["b1"].command_key) == (
        "FOO=1 uv run pytest tests/x.py -q",
        "uv run pytest",
    )
    assert (calls["b1"].exit_code_prov, calls["b2"].exit_code_prov) == ("present", "derived")
    leaves = ops(b, "llm_call") + ops(b, "tool_call")
    assert sorted(o.occ_key for o in leaves) == [
        "claude_code:request:r1",
        "claude_code:tool_use:b1",
        "claude_code:tool_use:b2",
        "claude_code:tool_use:m1",
    ]
    assert all(o.spawn_depth == 0 and o.is_fork is False for o in leaves)
    assert all(s.skill is None for s in b.steps)
    assert {o.account for o in b.ops} == {"anthropic:default"}
    assert all(e.account == "anthropic:default" for e in b.events)


def test_project_is_first_cwd_else_decoded_folder(tmp_path):
    b = build(tmp_path, [{k: v for k, v in user("u").items() if k != "cwd"}], folder="-nonexistent-x")
    s = ops(b, "session")[0]
    assert (s.project, s.cwd) == ("/nonexistent/x", None)
    b2 = build(tmp_path, [user("u")], sid="s2")
    assert ops(b2, "session")[0].project == "/repo" == ops(b2, "session")[0].cwd


def test_pointers_resolve_without_drift_and_source_files_listed(tmp_path):
    b = build(
        tmp_path,
        [
            user("u"),
            assistant(
                "r1", 1, content=[{"type": "text", "text": "hello"}, tool("t", "Read", file_path="/repo/a")]
            ),
            result("t", 2, content="body"),
        ],
    )
    roots = {"claude_code": tmp_path}
    text_step = next(s for s in b.steps if s.verb == "answer" or s.verb == "say")
    got = pointer.resolve(text_step.src, roots, part="text")
    assert (got["text"], got["drift"]) == ("hello", False)
    tool_op = ops(b, "tool_call")[0]
    assert pointer.resolve(tool_op.src, roots, part="input.file_path")["text"] == "/repo/a"
    (sf,) = b.source_files
    assert sf.size == (tmp_path / "-repo" / "s1.jsonl").stat().st_size and len(sf.sha_first_4k) == 64


def test_malformed_lines_counted_and_in_file_repeated_uuid_dropped(tmp_path):
    main = write(tmp_path / "-repo" / "s1.jsonl", [user("u"), assistant("r", 1), assistant("r", 1)])
    with main.open("a") as f:
        f.write("{not json\n")
    (tmp_path / "prices.json").write_text(json.dumps(RATES))
    (ref,) = discover(tmp_path)
    bundle, stats = build_bundle(ref, Pricer(tmp_path / "prices.json"))
    assert bundle.n_malformed == 1 and stats["dup_uuid_lines"] == 1
    (call,) = ops(bundle, "llm_call")
    assert call.src.split("#")[1].startswith("L2-L2")


def test_registry_and_run_write_bundles_and_summary(tmp_path):
    assert get("claude_code").provider == "claude_code"
    src = tmp_path / "src"
    write(src / "-repo" / "s1.jsonl", [user("u"), assistant("r", 1)])
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps(RATES))
    summary = run(src, tmp_path / "out", Pricer(prices))
    assert summary["bundles"] == 1 and summary["rejected"] == [] and summary["defects"] == {}
    assert json.loads((tmp_path / "out" / "_summary.json").read_text())["own_cost_usd"] == 0.00828
    (back,) = stream.read_bundles(tmp_path / "out")
    assert back.dropped_fields == {} and len(back.ops) == 3


def test_session_metadata_title_branch_version_and_nulls(tmp_path):
    rows = [
        user("u", text="first question", gitBranch="HEAD", version="2.1.1"),
        assistant("r1", 1, gitBranch="feature/x", version="2.1.2"),
        {"type": "ai-title", "aiTitle": "Old title"},
        user("u2", 3, "again", gitBranch="main"),
        {"type": "ai-title", "aiTitle": "New title"},
    ]
    s = ops(build(tmp_path, rows), "session")[0]
    assert (s.title, s.label, s.git_branch, s.harness_version) == (
        "New title",
        "New title",
        "feature/x",
        "2.1.1",
    )
    bare = ops(build(tmp_path, [user("u", text="q")], sid="s2"), "session")[0]
    assert (bare.title, bare.git_branch, bare.harness_version) == (None, None, None)
    assert bare.label == "q"
    summ = ops(build(tmp_path, [{"type": "summary", "summary": "Sum"}, user("u")], sid="s3"), "session")[0]
    assert summ.title == "Sum"


def test_first_line_ms_is_the_physical_first_line_not_the_minimum(tmp_path):
    # a resumed copy: first line stamped at copy time, copied records older
    rows = [{"type": "ai-title", "aiTitle": "t"}, user("u", 30, "resumed"), user("old", 5, "copied")]
    s = ops(build(tmp_path, rows), "session")[0]
    assert s.first_line_ms == T0 + 30_000
    assert s.start_ms == T0 + 5_000
    assert all(o.first_line_ms is None for o in build(tmp_path, rows).ops if o.kind != "session")
