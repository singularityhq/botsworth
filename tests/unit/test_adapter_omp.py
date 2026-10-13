import json
from datetime import UTC, datetime

import pytest

from botsworth import stream
from botsworth.adapters import BundleRejected, get
from botsworth.adapters.omp import records
from botsworth.adapters.omp.tree import check_depth
from botsworth.model import Op

T = 1790263073972  # epoch ms
H = 3_600_000
ADAPTER = get("omp")


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat().replace("+00:00", "Z")


def rec(type_, ms, **kw):
    return {"type": type_, "id": kw.pop("id", f"r{ms}"), "parentId": None, "timestamp": iso(ms), **kw}


def user(ms, **m):
    return rec("message", ms, message={"role": "user", "content": [], **m})


def assistant(ms, end, calls=(), usage=True, id=None, content=(), **m):
    parts = [*content, *({"type": "toolCall", "id": c, "name": n, "arguments": a} for c, n, a in calls)]
    msg = {
        "role": "assistant",
        "content": parts,
        "provider": "anthropic",
        "model": "m1",
        "timestamp": ms,
        "completedAt": end,
        "duration": end - ms,
        "ttft": 5,
        **m,
    }
    if usage:
        msg["usage"] = {
            "input": 10,
            "output": 2,
            "cacheRead": 100,
            "cacheWrite": 7,
            "reasoningTokens": 1,
            "cost": {"input": 0.1, "output": 0.2, "cacheRead": 0.01, "cacheWrite": 0.02, "total": 0.33},
        }
    return rec("message", end, message=msg, **({"id": id} if id else {}))


def start(ms, call):
    return rec(
        "custom", ms, customType="tool_execution_start", data={"toolCallId": call, "startedAt": iso(ms)}
    )


def result(ms, call, name, details=None, is_error=False):
    msg = {
        "role": "toolResult",
        "toolCallId": call,
        "toolName": name,
        "content": [],
        "details": details or {},
        "isError": is_error,
        "timestamp": ms,
    }
    return rec("message", ms, message=msg)


def write(path, rows, sid, header_ms=T, cwd="/proj"):
    path.parent.mkdir(parents=True, exist_ok=True)
    hdr = {"type": "session", "version": 3, "id": sid, "timestamp": iso(header_ms), "cwd": cwd}
    path.write_text("\n".join(json.dumps(r) for r in [{"type": "title", "title": "t"}, hdr, *rows]) + "\n")


def build(tmp_path, rows=None, sid="S"):
    root = tmp_path / "sessions"
    if rows is not None:
        write(root / "-proj/s.jsonl", rows, sid)
    refs = list(ADAPTER.discover(root))
    bundles = [ADAPTER.build(ref, None) for ref in refs]
    for b in bundles:
        stream.validate(b)
    return bundles[0] if len(bundles) == 1 else bundles


def kind(b, k):
    return [o for o in b.ops if o.kind == k]


def test_tools_steps_events_and_bounds(tmp_path):
    calls = [
        ("r", "read", {"path": "skill://code-quality"}),
        ("w", "write", {"path": "/proj/a"}),
        ("e", "edit", {"path": "/proj/a"}),
        ("b", "bash", {"command": "cd /proj && FOO=1 uv run pytest tests/x -q"}),
        ("g", "grep", {"pattern": "needle"}),
        ("t", "task", {"tasks": [{"name": "A"}, {"name": "B"}]}),
        ("v", "eval", {"code": "1+1"}),
        ("h", "hub", {"url": "https://example.org/a"}),
    ]
    a = assistant(
        T,
        T + 100,
        calls=calls,
        id="a",
        content=[{"type": "thinking", "thinking": "x " * 200}, {"type": "text", "text": "said"}],
        stopReason="error",
        errorStatus=429,
        errorMessage="rate limit",
    )
    a["message"]["content"][5]["intent"] = "Running the tests"
    rows = [user(T - 1), a]
    for cid, name, _ in calls:
        rows += [
            start(T + 101, cid),
            result(T + 108, cid, name, {"async": {"jobId": "j"}} if cid == "b" else {}),
        ]
    rows += [
        rec("compaction", T + 1100, tokensBefore=100, tokensAfter=40),
        assistant(T + 1200, T + 1300, id="x", stopReason="aborted"),
    ]
    b = build(tmp_path, rows)
    steps = b.steps
    assert [s.verb for s in steps] == [
        "think",
        "answer",
        "skill",
        "write",
        "edit",
        "run",
        "search",
        "spawn",
        "spawn",
        "run",
        "other",
    ]
    assert len(steps[0].snippet) == 160 and steps[3].target == "a" and steps[3].tool_name == "write"
    assert [s.target for s in steps[7:9]] == ["A", "B"] and steps[7].op_id == steps[8].op_id
    assert steps[5].summary == "Running the tests" and steps[9].summary == "eval 1+1"
    bash = next(o for o in b.ops if o.tool_name == "bash")
    assert (bash.dur_ms, bash.time_prov, bash.is_async) == (7, "present", True)
    assert (bash.command_norm, bash.command_key) == ("FOO=1 uv run pytest tests/x -q", "uv run pytest")
    assert bash.occ_key == "omp:tool:b" and bash.op_id == "omp/S/tool_call/b"
    llm = kind(b, "llm_call")[0]
    assert llm.op_id == "omp/S/llm_call/main@L4" and llm.id_prov == "derived"  # no responseId
    assert (llm.cache_write_tok, llm.cache_write_5m_tok, llm.context_tokens, llm.ttft_ms) == (7, None, 117, 5)
    assert (llm.cost_usd, llm.cost_basis, llm.error_kind) == (0.33, "reported", "rate_limit")
    by_kind = {e.kind: e for e in b.events}
    assert by_kind["compaction"].fields == {"trigger": "unknown", "pre_tokens": 100, "post_tokens": 40}
    errs = [e.fields["kind"] for e in b.events if e.kind == "api_error"]
    assert errs == ["rate_limit", "aborted"]
    skill = by_kind["skill_invocation"].fields
    assert (skill["skill"], skill["via"], skill["step_id"]) == (
        "code-quality",
        "read",
        f"{steps[2].turn_id}#2",
    )
    assert all(s.skill is None for s in steps)
    assert all(o.src.startswith("src://omp/-proj/s.jsonl#L") for o in b.ops)


def test_last_text_in_turn_is_answer_earlier_is_say(tmp_path):
    text = [{"type": "text", "text": "x"}]
    b = build(
        tmp_path,
        [
            user(T),
            assistant(T + 1, T + 2, content=text, id="a"),
            assistant(T + 3, T + 4, content=text, id="b"),
            user(T + 5),
            assistant(T + 6, T + 7, content=text, id="c"),
        ],
    )
    assert [s.verb for s in b.steps] == ["say", "answer", "answer"]


def test_child_links_by_id_name_time_and_none(tmp_path):
    root = tmp_path / "sessions"
    top = root / "-proj/s.jsonl"
    write(
        top,
        [
            user(T),
            assistant(
                T,
                T + 100,
                calls=[
                    ("t", "task", {}),
                    ("n", "eval", {"code": "agent('Named')"}),
                    ("q", "eval", {"code": "agent(x)"}),
                ],
                responseId="R1",
            ),
            start(T + 101, "n"),
            start(T + 102, "q"),
            result(T + 110, "t", "task", {"results": [{"id": "Kid"}]}),
            result(T + 120, "n", "eval"),
            result(T + 130, "q", "eval"),
        ],
        "S",
    )
    kid = top.with_suffix("")
    write(
        kid / "Kid.jsonl",
        [
            user(T + 200),
            assistant(T + 200, T + 300, calls=[("k", "read", {"path": "/c/a"})]),
            result(T + 310, "k", "read"),
        ],
        "K",
        header_ms=T + 120,
        cwd="/child",
    )
    write(kid / "Named.jsonl", [user(T + 200)], "N", header_ms=T + 130)
    write(kid / "Other.jsonl", [user(T + 200)], "O", header_ms=T + 140)
    write(kid / "Early.jsonl", [user(T - 50)], "E", header_ms=T - 100)
    b = build(tmp_path)
    eng = {o.agent_name: o for o in kind(b, "engagement")}
    assert {k: (o.caused_by_basis, o.caused_by) for k, o in eng.items()} == {
        "Kid": ("id", "omp/S/tool_call/t"),
        "Named": ("name", "omp/S/tool_call/n"),
        "Other": ("time-heuristic", "omp/S/tool_call/q"),
        "Early": ("none", None),
    }
    assert eng["Early"].parent == "omp/S/session/-" and eng["Kid"].parent == "omp/S/tool_call/t"
    leaf = next(o for o in b.ops if o.tool_name == "read")
    assert (leaf.project, leaf.cwd, leaf.spawn_depth, leaf.depth, leaf.agent_id) == (
        "/proj",
        "/child",
        1,
        7,
        "K",
    )
    assert next(s for s in b.steps if s.op_id == leaf.op_id).target == "/c/a"
    assert len(b.source_files) == 5 and b.source_files[0].path == str(top.absolute())
    assert all(f.path.startswith("/") for f in b.source_files)
    assert leaf.src.startswith("src://omp/-proj/")  # pointers stay root-relative


def test_response_id_is_key_and_repeat_falls_back_to_line(tmp_path):
    b = build(
        tmp_path, [user(T), assistant(T, T + 1, responseId="R"), assistant(T + 2, T + 3, responseId="R")]
    )
    llm = kind(b, "llm_call")
    assert [o.op_id for o in llm] == ["omp/S/llm_call/R", "omp/S/llm_call/main@L5"]
    assert [o.occ_key for o in llm] == ["omp:response:R", "omp:response:R"]
    assert [o.id_prov for o in llm] == ["natural", "derived"]


def test_steering_is_interior_aux_call_and_missing_cost(tmp_path):
    aux = rec(
        "model_usage",
        T + 20,
        provider="deepseek",
        model="aux",
        usage={"input": 3, "output": 1, "cost": {"total": 0.004}},
    )
    nocost = assistant(T + 30, T + 40, id="z")
    del nocost["message"]["usage"]["cost"]
    b = build(
        tmp_path,
        [
            user(T),
            assistant(T + 1, T + 10, id="a"),
            user(T + 11, steering=True),
            aux,
            nocost,
            assistant(T + 50, T + 60, usage=False, id="u"),
        ],
    )
    assert len(kind(b, "turn")) == 1
    a = next(o for o in b.ops if o.role == "aux")
    assert (a.account, a.cost_usd, a.cost_basis, a.dur_ms, a.time_prov) == (
        "deepseek:unknown",
        0.004,
        "reported",
        None,
        "missing",
    )
    missing = [o for o in kind(b, "llm_call") if o.cost_basis == "missing"]
    assert [(o.cost_usd, o.tok_prov) for o in missing] == [(None, "present"), (None, "missing")]
    assert all(o.cost_usd is None and o.in_tok is None for o in b.ops if o.kind in ("session", "turn"))


def test_derived_initial_turn_when_no_user_boundary(tmp_path):
    b = build(tmp_path, [assistant(T, T + 1, id="a")])
    (turn,) = kind(b, "turn")
    assert (turn.op_id, turn.label) == ("omp/S/turn/main#0", "initial")


def test_slash_skill_prompt_starts_turn_and_emits_invocation(tmp_path):
    prompt = rec(
        "custom_message",
        T + 20,
        customType="skill-prompt",
        details={"name": "team-lead", "path": "/s/team-lead/SKILL.md", "args": "delegate"},
    )
    b = build(
        tmp_path, [user(T), assistant(T + 1, T + 10, id="a"), prompt, assistant(T + 21, T + 30, id="b")]
    )
    assert len(kind(b, "turn")) == 2
    (inv,) = [e for e in b.events if e.kind == "skill_invocation"]
    assert inv.fields == {
        "skill": "team-lead",
        "args_head": "delegate",
        "via": "slash",
        "skill_md_src": "/s/team-lead/SKILL.md",
        "step_id": None,
    }


def test_session_exit_splits_segments_and_never_extends_span(tmp_path):
    def exit_(ms):
        return rec("custom", ms, customType="session_exit", data={"reason": "dispose"})

    b = build(
        tmp_path,
        [
            user(T),
            assistant(T + 1, T + 10, id="a"),
            exit_(T + 2 * H),
            user(T + 3 * H),
            assistant(T + 3 * H + 1, T + 3 * H + 10, id="b"),
            exit_(T + 5000 * H),
        ],
    )
    (session,) = kind(b, "session")
    assert session.segments == [[T, T + 10], [T + 3 * H, T + 3 * H + 10]]
    assert (session.start_ms, session.end_ms) == (T, T + 3 * H + 10)
    turns = kind(b, "turn")
    assert [t.segments for t in turns] == [[[T, T + 10]], [[T + 3 * H, T + 3 * H + 10]]]
    (resume,) = [e for e in b.events if e.kind == "resume"]
    assert (resume.ts_ms, resume.fields) == (T + 3 * H, {"from_session": "S", "copied_ops": 0})


def test_account_table_matches_codex_plan_keys():
    before, after = records.PLUS_UNTIL_529, records.PLUS_UNTIL_529 + 1
    assert records.account("openai-codex", 2, after) == "chatgpt-plus"
    assert records.account("openai-codex", 5, before) == records.account("openai-codex-2", None, before)
    assert records.account("openai-codex", 5, after) == "chatgpt-default"
    assert records.account("openai-codex", None, before) == "chatgpt-plus"
    assert records.account("openai-codex", 9, before) == "chatgpt-default"
    assert records.account("deepseek", 2) == "deepseek:2"


def test_account_flows_from_call_to_tools_and_containers(tmp_path):
    a = assistant(
        T, T + 10, calls=[("x", "read", {"path": "/proj/f"})], id="a", provider="openai-codex", credentialId=2
    )
    b = build(tmp_path, [user(T), a, result(T + 20, "x", "read")])
    assert {o.kind: o.account for o in b.ops} == {
        k: "chatgpt-plus" for k in ("session", "turn", "llm_call", "tool_call")
    }


def test_partial_and_malformed_lines_are_counted(tmp_path):
    p = tmp_path / "sessions/-proj/s.jsonl"
    write(p, [user(T), assistant(T, T + 1, id="a")], "S")
    p.write_text(p.read_text() + "{not json\n" + '{"type":"message"')
    b = build(tmp_path)
    assert (b.n_malformed, b.n_partial, len(kind(b, "llm_call"))) == (1, 1, 1)


def test_unpaired_tool_has_unknown_outcome_and_missing_time(tmp_path):
    b = build(tmp_path, [user(T), assistant(T, T + 1, calls=[("x", "bash", {"command": "ls"})])])
    tool = kind(b, "tool_call")[0]
    assert (tool.time_prov, tool.end_ms, b.steps[0].outcome) == ("missing", None, "unknown")


def test_exit_code_outcome(tmp_path):
    b = build(
        tmp_path,
        [
            user(T),
            assistant(T, T + 1, calls=[("x", "bash", {"command": "false"})]),
            start(T + 2, "x"),
            result(T + 3, "x", "bash", {"exitCode": 1}),
        ],
    )
    assert (kind(b, "tool_call")[0].exit_code, b.steps[0].outcome) == (1, "exit:1")


def test_depth_over_32_and_cycles_are_rejected():
    def op(i, parent):
        return Op(op_id=str(i), kind="turn", provider="omp", session_id="S", src="s", parent=parent)

    chain = [op(0, None)] + [op(i, str(i - 1)) for i in range(1, 34)]
    with pytest.raises(BundleRejected):
        check_depth(chain)
    assert check_depth(chain[:33])["32"] == 32
    with pytest.raises(BundleRejected):
        check_depth([op(0, "1"), op(1, "0")])


def test_stream_round_trip(tmp_path):
    b = build(
        tmp_path,
        [
            user(T),
            assistant(T, T + 1, calls=[("x", "read", {"path": "/proj/f"})], id="a"),
            result(T + 2, "x", "read"),
        ],
    )
    back = stream.read_bundle(stream.write_bundle(b, tmp_path / "out"))
    assert (
        back.dropped_fields == {} and back.ops == b.ops and back.steps == b.steps and back.events == b.events
    )


def test_session_title_from_title_record_branch_and_version_null(tmp_path):
    b = build(tmp_path, [user(T + 1, content=[{"type": "text", "text": "hi"}])])
    s = kind(b, "session")[0]
    assert (s.title, s.git_branch, s.harness_version) == ("t", None, None)


def test_first_line_ms_is_the_first_record_in_file_order(tmp_path):
    b = build(tmp_path, [user(T + 5000, content=[]), user(T - H, content=[])])
    s = kind(b, "session")[0]
    assert s.first_line_ms == T  # the header line, not the minimum (T - H)
