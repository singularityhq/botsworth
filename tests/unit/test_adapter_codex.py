import json
from pathlib import Path

import pytest

from botsworth import pointer, stream
from botsworth.adapters import BundleRejected, get
from botsworth.adapters.codex import discover as codex_discover
from botsworth.adapters.common import taxonomy
from botsworth.pricing import Pricer

FIXTURES = Path(__file__).parents[1] / "fixtures"
ADAPTER = get("codex")
T = "2026-01-01T00:00:%02d.000Z"
SID = "019f0000-0000-7000-8000-000000000001"
CHILD = "019f0000-0000-7000-8000-000000000002"


@pytest.fixture(scope="module")
def pricer():
    return Pricer(FIXTURES / "F8" / "prices.json")


def L(sec, payload, typ="event_msg"):
    return json.dumps({"timestamp": T % sec, "type": typ, "payload": payload})


def meta(sid, sec=0, cwd="/p", **extra):
    return L(sec, {"id": sid, "timestamp": T % sec, "cwd": cwd, **extra}, "session_meta")


def u(i, o, c=0, r=0):
    return {
        "input_tokens": i,
        "cached_input_tokens": c,
        "output_tokens": o,
        "reasoning_output_tokens": r,
        "total_tokens": i + o,
    }


def tc(sec, tot, last, rl=None):
    info = {"total_token_usage": tot, "last_token_usage": last}
    return L(sec, {"type": "token_count", "info": info, "rate_limits": rl})


def call(sec, cid, cmd, name="exec_command"):
    return L(
        sec,
        {"type": "function_call", "name": name, "call_id": cid, "arguments": json.dumps(cmd)},
        "response_item",
    )


def write(root, sid, lines):
    d = root / "sessions" / "2026" / "01" / "01"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"rollout-2026-01-01T00-00-00-{sid}.jsonl"
    f.write_text("\n".join(lines) + "\n")
    return f


def build(root, pricer):
    refs = list(ADAPTER.discover(root))
    out = {r.bundle_id: ADAPTER.build(r, pricer) for r in refs}
    for b in out.values():
        assert stream.defects(b) == []
    return out


def one(tmp_path, pricer, lines):
    write(tmp_path, SID, lines)
    return build(tmp_path, pricer)[SID]


def kind(b, k):
    return [o for o in b.ops if o.kind == k]


def by_id(b):
    return {o.op_id: o for o in b.ops}


# ---- F8 ---------------------------------------------------------------------------------------
def test_f8_old_format_token_sequence_exact():
    exp = json.loads((FIXTURES / "F8" / "expected.json").read_text())
    b = build(FIXTURES / "F8", Pricer(FIXTURES / "F8" / "prices.json"))
    (bundle,) = b.values()
    calls = kind(bundle, "llm_call")
    got = [
        {k: getattr(o, k) for k in ("op_id", "in_tok", "out_tok", "cost_usd", "start_ms", "end_ms")}
        for o in calls
    ]
    assert got == pytest.approx(exp["llm_calls"])
    assert sum(o.in_tok + o.out_tok for o in calls) == exp["total_tokens"]
    assert {o.cost_basis for o in calls} == {"computed"}


# ---- steps own an op (the 809 null op_id defect) ---------------------------------------------
def test_old_format_items_after_token_count_belong_to_that_call(tmp_path, pricer):
    """Older rollouts write a call's response_items just after its token_count."""
    lines = [
        meta(SID),
        L(0, {"model": "gpt-5"}, "turn_context"),
        L(1, {"type": "task_started", "turn_id": "t"}),
        L(2, {"type": "agent_message", "message": "looking"}),
        tc(3, u(100, 10), u(100, 10)),
        L(3, {"type": "reasoning", "summary": [{"type": "summary_text", "text": "first"}]}, "response_item"),
        call(3, "c1", {"cmd": "ls"}),
        L(3, {"type": "function_call_output", "call_id": "c1", "output": "x"}, "response_item"),
        tc(9, u(300, 20), u(200, 10)),
        L(9, {"type": "reasoning", "summary": [{"type": "summary_text", "text": "last"}]}, "response_item"),
        L(10, {"type": "task_complete", "turn_id": "t"}),
    ]
    b = one(tmp_path, pricer, lines)
    first, second = kind(b, "llm_call")
    ops = by_id(b)
    assert ops[f"codex/{SID}/tool_call/c1"].parent == first.op_id
    owner = {s.snippet: s.op_id for s in b.steps if s.verb == "think"}
    assert owner == {"first": first.op_id, "last": second.op_id}
    assert all(s.op_id for s in b.steps)


def test_items_of_an_aborted_response_get_an_open_call_with_usage_missing(tmp_path, pricer):
    lines = [
        meta(SID),
        L(0, {"model": "gpt-5"}, "turn_context"),
        L(1, {"type": "task_started", "turn_id": "t"}),
        tc(2, u(100, 10), u(100, 10)),
        L(5, {"type": "reasoning", "summary": [{"type": "summary_text", "text": "half"}]}, "response_item"),
        call(6, "c9", {"cmd": "sleep 100"}),
        L(7, {"type": "turn_aborted", "reason": "interrupted"}),
    ]
    b = one(tmp_path, pricer, lines)
    ops = by_id(b)
    open_ = ops[f"codex/{SID}/llm_call/open@L5"]
    assert (open_.tok_prov, open_.in_tok, open_.cost_usd, open_.cost_basis) == (
        "missing",
        None,
        None,
        "missing",
    )
    assert open_.occ_key == f"codex:open:{SID}:c9"
    assert ops[f"codex/{SID}/tool_call/c9"].parent == open_.op_id
    assert [(s.verb, s.op_id, s.outcome) for s in b.steps] == [
        ("think", open_.op_id, None),
        ("run", f"codex/{SID}/tool_call/c9", "interrupted"),
    ]
    turn = kind(b, "turn")[0]
    assert turn.interrupted is True and open_.parent == turn.op_id


def test_turn_steps_parsed_cmd_patch_and_parent_llm_call(tmp_path, pricer):
    patch = "*** Begin Patch\n*** Update File: /p/b.py\n@@\n-x\n+y\n*** End Patch"
    lines = [
        meta(SID),
        L(0, {"model": "gpt-5"}, "turn_context"),
        L(1, {"type": "task_started", "turn_id": "t1"}),
        L(
            2,
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "plan it"}]},
            "response_item",
        ),
        call(2, "c1", {"cmd": "sed -n 1,5p /p/a.py"}),
        L(
            2,
            {"type": "custom_tool_call", "name": "apply_patch", "call_id": "c2", "input": patch},
            "response_item",
        ),
        tc(3, u(100, 10), u(100, 10)),
        L(
            4,
            {
                "type": "exec_command_end",
                "call_id": "c1",
                "exit_code": 0,
                "duration": {"secs": 1, "nanos": 0},
                "parsed_cmd": [{"type": "read", "cmd": "sed", "path": "/p/a.py"}],
            },
        ),
        L(4, {"type": "function_call_output", "call_id": "c1", "output": "x"}, "response_item"),
        L(5, {"type": "patch_apply_end", "call_id": "c2", "success": True, "changes": {"/p/b.py": {}}}),
        L(5, {"type": "custom_tool_call_output", "call_id": "c2", "output": "ok"}, "response_item"),
        L(6, {"type": "agent_message", "message": "done"}),
        tc(7, u(300, 20), u(200, 10)),
        L(8, {"type": "task_complete", "turn_id": "t1", "time_to_first_token_ms": 420}),
    ]
    b = one(tmp_path, pricer, lines)
    ops = by_id(b)
    turn = kind(b, "turn")[0]
    assert (
        turn.op_id == f"codex/{SID}/turn/main#0" and turn.ttft_ms == 420 and turn.segments == [[T0(1), T0(8)]]
    )
    c1 = ops[f"codex/{SID}/tool_call/c1"]
    first = kind(b, "llm_call")[0]
    assert (c1.parent, c1.dur_ms, c1.time_prov, c1.exit_code_prov) == (
        first.op_id,
        1000,
        "present",
        "present",
    )
    assert [(s.verb, s.target, s.outcome) for s in b.steps] == [
        ("think", None, None),
        ("read", "a.py", "ok"),
        ("edit", "b.py", "ok"),
        ("answer", None, None),
    ]
    assert [s.ordinal for s in b.steps] == [0, 1, 2, 3]
    assert b.steps[0].op_id == first.op_id and b.steps[0].snippet == "plan it"
    assert b.steps[1].tool_name == "exec_command" and b.steps[1].op_id == c1.op_id


def T0(sec):
    return 1767225600000 + sec * 1000


# ---- runs: command fields (D17), labels, scripts -----------------------------------------------
def test_run_label_from_parsed_cmd_turn_ask_label_answer_and_command_key(tmp_path, pricer):
    lines = [
        meta(SID),
        L(0, {"model": "gpt-5"}, "turn_context"),
        L(1, {"type": "task_started", "turn_id": "t"}),
        L(1, {"type": "user_message", "message": "Please   fix the tests"}),
        call(2, "c1", {"cmd": "rg -n foo src"}),
        call(3, "c2", {"cmd": "cd /p && uv run pytest /p/tests -q"}),
        tc(3, u(10, 1), u(10, 1)),
        L(
            4,
            {
                "type": "exec_command_end",
                "call_id": "c1",
                "exit_code": 0,
                "command": ["/bin/zsh", "-lc", "rg -n foo src"],
                "parsed_cmd": [{"type": "search", "cmd": "rg -n foo src", "query": "foo", "path": "src"}],
            },
        ),
        L(
            4,
            {"type": "function_call_output", "call_id": "c2", "output": "Process exited with code 2"},
            "response_item",
        ),
        L(5, {"type": "agent_message", "message": "working"}),
        L(6, {"type": "agent_message", "message": "Fixed it"}),
        tc(7, u(30, 2), u(20, 1)),
        L(8, {"type": "task_complete", "turn_id": "t"}),
    ]
    b = one(tmp_path, pricer, lines)
    assert kind(b, "turn")[0].label == "Please fix the tests"
    assert kind(b, "session")[0].label == "Please fix the tests"
    st = b.steps
    assert (st[0].verb, st[0].summary) == ("search", "search foo in src")
    assert (st[1].verb, st[1].summary, st[1].outcome) == ("run", "uv run pytest tests -q", "exit:2")
    assert [s.verb for s in st[2:]] == ["say", "answer"] and st[3].snippet == "Fixed it"
    c2 = by_id(b)[f"codex/{SID}/tool_call/c2"]
    assert (c2.command_norm, c2.command_key) == ("uv run pytest tests -q", "uv run pytest")
    assert (c2.exit_code, c2.exit_code_prov, c2.is_error) == (2, "derived", True)
    assert all(o.command_key for o in kind(b, "tool_call"))


def test_exec_script_is_one_run_step_with_sub_items_and_skill_read(tmp_path, pricer):
    js = (
        'text(await tools.exec_command({cmd:"sed -n 1,40p /h/.codex/skills/debugging/SKILL.md"}));\n'
        "text(await tools.exec_command({cmd:`pytest -q /p/t`}));\n"
        'tools.apply_patch("*** Begin Patch\\n*** Update File: /p/x.py\\n")'
    )
    out = [
        {"type": "input_text", "text": "Script completed\nOutput:\n"},
        {"type": "input_text", "text": json.dumps({"exit_code": 0})},
        {"type": "input_text", "text": json.dumps({"exit_code": 1})},
    ]
    lines = [
        meta(SID),
        L(1, {"type": "task_started", "turn_id": "t"}),
        L(2, {"type": "custom_tool_call", "name": "exec", "call_id": "c", "input": js}, "response_item"),
        L(3, {"type": "custom_tool_call_output", "call_id": "c", "output": out}, "response_item"),
        tc(4, u(10, 1), u(10, 1)),
        L(5, {"type": "task_complete", "turn_id": "t"}),
    ]
    b = one(tmp_path, pricer, lines)
    runs = [s for s in b.steps if s.verb == "run"]
    assert len(runs) == 1 and runs[0].outcome == "ok"
    assert [(x.tool, x.outcome) for x in runs[0].sub_items] == [
        ("exec_command", "ok"),
        ("exec_command", "exit:1"),
        ("apply_patch", "unknown"),
    ]
    assert runs[0].sub_items[1].target == "pytest -q t" and runs[0].sub_items[2].target == "x.py"
    tool = by_id(b)[f"codex/{SID}/tool_call/c"]
    assert tool.command_key == "sed" and tool.command_norm.startswith("sed -n 1,40p")
    sk = [e for e in b.events if e.kind == "skill_invocation"]
    assert [(e.fields["skill"], e.fields["via"], e.op_id) for e in sk] == [("debugging", "read", tool.op_id)]
    assert sk[0].fields["step_id"] == f"{runs[0].turn_id}#{runs[0].ordinal}"


def test_apply_patch_one_edit_step_per_path_and_skill_block(tmp_path, pricer):
    patch = "*** Begin Patch\n*** Update File: /p/a.py\n*** Add File: /p/b.py\n*** End Patch"
    sk = "<skill>\n<name>grill-me</name>\n<path>/h/skills/grill-me/SKILL.md</path>\nbody"
    user = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": sk}]}
    lines = [
        meta(SID),
        L(1, {"type": "task_started", "turn_id": "t"}),
        L(1, user, "response_item"),
        L(
            2,
            {"type": "custom_tool_call", "name": "apply_patch", "call_id": "p", "input": patch},
            "response_item",
        ),
        L(
            3,
            {
                "type": "patch_apply_end",
                "call_id": "p",
                "success": False,
                "changes": {"/p/a.py": {}, "/p/b.py": {}},
            },
        ),
        tc(4, u(10, 1), u(10, 1)),
        L(5, {"type": "task_complete", "turn_id": "t"}),
    ]
    b = one(tmp_path, pricer, lines)
    assert [(s.verb, s.target, s.outcome) for s in b.steps] == [
        ("skill", "grill-me", None),
        ("edit", "a.py", "error"),
        ("edit", "b.py", "error"),
    ]
    ev = next(e for e in b.events if e.kind == "skill_invocation")
    assert (ev.fields["skill"], ev.fields["via"], ev.fields["skill_md_src"]) == (
        "grill-me",
        "slash",
        "/h/skills/grill-me/SKILL.md",
    )
    assert ev.fields["step_id"] == f"{b.steps[0].turn_id}#0" and ev.op_id == kind(b, "llm_call")[0].op_id


# ---- events -----------------------------------------------------------------------------------
def test_limit_state_dedup_account_from_plan_type_nested_fields(tmp_path, pricer):
    def rl(pct):
        return {
            "limit_id": "codex",
            "primary": {"used_percent": pct, "window_minutes": 300, "resets_at": 1000},
            "secondary": {"used_percent": 5.0, "window_minutes": 10080, "resets_at": 2000},
            "plan_type": "plus",
            "rate_limit_reached_type": None,
        }

    lines = [
        meta(SID),
        L(1, {"type": "token_count", "info": None, "rate_limits": rl(1.0)}),
        tc(2, u(10, 1), u(10, 1), rl(1.0)),
        tc(3, u(20, 2), u(10, 1), rl(2.0)),
    ]
    b = one(tmp_path, pricer, lines)
    ls = [(e.fields["window"], e.fields["used_percent"]) for e in b.events if e.kind == "limit_state"]
    assert ls == [("primary", 1.0), ("secondary", 5.0), ("primary", 2.0)]
    first = b.events[0]
    assert (first.account, first.fields["resets_at_ms"], first.op_id, first.basis) == (
        "chatgpt-plus",
        1_000_000,
        None,
        "present",
    )
    assert first.event_id == f"codex/{SID}/event/main:L2#0" and b.events[1].event_id.endswith("L2#1")
    assert {o.account for o in b.ops} == {"chatgpt-plus"}


def test_default_account_compaction_and_api_error(tmp_path, pricer):
    lines = [
        meta(SID),
        L(0, {"model": "gpt-5"}, "turn_context"),
        L(1, {"type": "task_started", "turn_id": "t"}),
        tc(2, u(5000, 10, 4000), u(5000, 10, 4000)),
        L(3, {"message": "summary"}, "compacted"),
        L(3, {"type": "context_compacted"}),
        tc(4, u(5800, 20, 4000), u(800, 10)),
        L(
            5,
            {
                "type": "error",
                "message": "You've hit your usage limit.",
                "codex_error_info": "usage_limit_exceeded",
            },
        ),
    ]
    b = one(tmp_path, pricer, lines)
    (comp,) = [e for e in b.events if e.kind == "compaction"]
    assert (comp.fields["pre_tokens"], comp.fields["post_tokens"], comp.basis) == (5000, 800, "derived")
    err = next(e for e in b.events if e.kind == "api_error")
    assert (err.fields["status"], err.fields["kind"]) == ("usage_limit_exceeded", "rate_limit")
    assert any(e.kind == "limit_hit" for e in b.events)
    assert {o.account for o in b.ops} == {"chatgpt-default"}


def test_token_decrease_writes_rollback_event_and_thread_rolled_back_merges(tmp_path, pricer):
    lines = [
        meta(SID),
        L(1, {"type": "task_started", "turn_id": "t"}),
        tc(2, u(1000, 10), u(1000, 10)),
        L(3, {"type": "thread_rolled_back", "num_turns": 1}),
        tc(4, u(300, 5), u(300, 5)),
    ]
    b = one(tmp_path, pricer, lines)
    (rb,) = [e for e in b.events if e.kind == "rollback"]
    assert (rb.fields["num_turns"], rb.fields["dropped_tokens"], rb.basis) == (1, 1010, "present")
    assert len(kind(b, "llm_call")) == 2


def test_repeated_session_meta_is_a_resume_that_splits_segments(tmp_path, pricer):
    lines = [
        meta(SID, 0),
        L(1, {"type": "task_started", "turn_id": "t"}),
        tc(2, u(10, 1), u(10, 1)),
        L(3, {"type": "task_complete", "turn_id": "t"}),
        meta(SID, 40),
        L(41, {"type": "task_started", "turn_id": "t2"}),
        tc(42, u(30, 2), u(20, 1)),
        L(43, {"type": "task_complete", "turn_id": "t2"}),
    ]
    b = one(tmp_path, pricer, lines)
    assert kind(b, "session")[0].segments == [[T0(0), T0(3)], [T0(40), T0(43)]]
    assert [e.kind for e in b.events] == ["resume"]


def test_token_usage_record_gives_one_call_per_response_id(tmp_path, pricer):
    usage = {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 7, "total_tokens": 107}
    rec = {"thread_id": SID, "turn_id": "t", "response_id": "resp_1", "usage": usage}
    lines = [
        meta(SID),
        L(0, {"model": "gpt-5"}, "turn_context"),
        L(1, {"type": "task_started", "turn_id": "t"}),
        call(2, "c1", {"cmd": "ls"}),
        tc(3, u(100, 7, 60), u(100, 7, 60)),
        L(3, rec, "token_usage_record"),
        L(4, {"type": "task_complete", "turn_id": "t"}),
    ]
    b = one(tmp_path, pricer, lines)
    (c,) = kind(b, "llm_call")
    assert (c.op_id, c.occ_key, c.id_prov) == (
        f"codex/{SID}/llm_call/resp_1",
        "codex:response:resp_1",
        "natural",
    )
    assert (c.in_tok, c.cache_read_tok, c.out_tok, c.context_tokens) == (40, 60, 7, 100)
    assert by_id(b)[f"codex/{SID}/tool_call/c1"].parent == c.op_id


# ---- pricing ----------------------------------------------------------------------------------
def test_pricing_alias_and_unpriced(tmp_path):
    snap = tmp_path / "s.json"
    snap.write_text(json.dumps({"openai": {"models": {"gpt-5": {"cost": {"input": 1.25, "output": 10}}}}}))
    pr = Pricer(snap)
    lines = [
        meta(SID),
        L(0, {"model": "gpt-5-codex"}, "turn_context"),
        tc(1, u(1_000_000, 1_000_000), u(1_000_000, 1_000_000)),
    ]
    lines += [
        L(2, {"model": "gpt-5.1-codex-max"}, "turn_context"),
        tc(3, u(2_000_000, 2_000_000), u(1_000_000, 1_000_000)),
    ]
    write(tmp_path, SID, lines)
    (b,) = build(tmp_path, pr).values()
    a, m = kind(b, "llm_call")
    assert (a.cost_usd, a.cost_basis, a.price_alias, a.price_fingerprint) == (
        11.25,
        "computed",
        "gpt-5",
        pr.fingerprint,
    )
    assert (m.cost_usd, m.cost_basis, m.price_alias) == (None, "missing", None)


# ---- tree: spawns, forks, ownership hints ------------------------------------------------------
def spawn_parent(spawn_end=True, output=None, name="spawn_agent", namespace=None):
    item = {"type": "function_call", "name": name, "call_id": "sp", "arguments": "{}"}
    if namespace:
        item["namespace"] = namespace
    lines = [
        meta(SID, 0),
        L(0, {"model": "gpt-5"}, "turn_context"),
        L(1, {"type": "task_started", "turn_id": "t1"}),
        L(1, item, "response_item"),
        tc(2, u(100, 10), u(100, 10)),
    ]
    if spawn_end:
        lines.append(
            L(
                3,
                {
                    "type": "collab_agent_spawn_end",
                    "call_id": "sp",
                    "sender_thread_id": SID,
                    "new_thread_id": CHILD,
                    "new_agent_role": "worker",
                    "new_agent_nickname": "Ada",
                },
            )
        )
    if output is not None:
        lines.append(
            L(3, {"type": "function_call_output", "call_id": "sp", "output": output}, "response_item")
        )
    return lines + [L(4, {"type": "task_complete", "turn_id": "t1"})]


def fork_child(agent_path=None):
    spawn = {"parent_thread_id": SID, "depth": 1, "agent_path": agent_path, "agent_role": "worker"}
    return [
        meta(
            CHILD, 5, forked_from_id=SID, source={"subagent": {"thread_spawn": spawn}}, agent_nickname="Ada"
        ),
        meta(SID, 5),
        L(5, {"type": "task_started", "turn_id": "t1"}),
        tc(5, u(100, 10), u(100, 10)),
        L(5, {"type": "task_complete", "turn_id": "t1"}),
        L(6, {"type": "task_started", "turn_id": "t2"}),
        tc(7, u(150, 20), u(50, 10)),
        L(8, {"type": "task_complete", "turn_id": "t2"}),
    ]


def test_fork_copy_repeats_occ_key_marked_duplicate_and_child_linked_to_spawn(tmp_path, pricer):
    write(tmp_path, CHILD, fork_child())
    write(tmp_path, SID, spawn_parent())
    bundles = build(tmp_path, pricer)
    assert list(bundles) == [SID]
    b = bundles[SID]
    eng = kind(b, "engagement")[0]
    assert eng.caused_by == f"codex/{SID}/tool_call/sp" and eng.parent == eng.caused_by
    assert (eng.caused_by_basis, eng.agent_id, eng.agent_type, eng.agent_name) == (
        "id",
        CHILD,
        "worker",
        "Ada",
    )
    assert (eng.project, eng.spawn_depth, eng.is_fork, eng.depth) == ("/p", 1, True, 4)
    parent_call = next(o for o in kind(b, "llm_call") if o.agent_id is None)
    child_calls = [o for o in kind(b, "llm_call") if o.agent_id == CHILD]
    assert child_calls[0].occ_key == parent_call.occ_key and parent_call.occ_key.startswith("codex:fp:")
    assert [o.duplicate_of for o in child_calls] == [parent_call.op_id, None]
    assert child_calls[0].op_id == f"codex/{SID}/llm_call/{CHILD}:seg@0"
    assert {o.spawn_depth for o in child_calls} == {1} and {o.is_fork for o in child_calls} == {True}
    assert not [e for e in b.events if e.kind == "rollback"]  # a fork's copied prefix is not a rollback
    assert [p.split("/")[-1] for p in (s.path for s in b.source_files)] == [
        f"rollout-2026-01-01T00-00-00-{SID}.jsonl",
        f"rollout-2026-01-01T00-00-00-{CHILD}.jsonl",
    ]


@pytest.mark.parametrize(
    ("spawn_end", "output", "namespace", "basis"),
    [
        (False, json.dumps({"agent_id": CHILD, "nickname": "Ada"}), "multi_agent_v1", "id"),
        (False, json.dumps({"agent_id": None, "task_name": "/root/w1", "nickname": "Ada"}), None, "name"),
        (False, "collab spawn failed: agent thread limit reached (max 6)", None, "none"),
    ],
)
def test_spawn_link_from_spawn_agent_output(tmp_path, pricer, spawn_end, output, namespace, basis):
    write(tmp_path, CHILD, fork_child(agent_path="/root/w1"))
    write(tmp_path, SID, spawn_parent(spawn_end, output, namespace=namespace))
    eng = kind(build(tmp_path, pricer)[SID], "engagement")[0]
    assert eng.caused_by_basis == basis
    assert eng.parent == (f"codex/{SID}/session/-" if basis == "none" else f"codex/{SID}/tool_call/sp")


def test_discover_groups_threads_into_root_bundles_in_ownership_order(tmp_path):
    grand = "019f0000-0000-7000-8000-000000000003"
    spawn = {"parent_thread_id": CHILD, "depth": 2}
    write(tmp_path, grand, [meta(grand, 9, source={"subagent": {"thread_spawn": spawn}})])
    write(tmp_path, CHILD, fork_child())
    write(tmp_path, SID, [meta(SID)])
    (tmp_path / "sessions" / "2026" / "01" / "01" / "rollout-v0.jsonl").write_text('{"id": "x"}\n')
    found = codex_discover.discover(tmp_path)
    (ref,) = found.refs
    assert ref.bundle_id == SID and [f.name[-41:-6] for f in ref.files] == [SID[1:], CHILD[1:], grand[1:]]
    assert [p.name for p in found.skipped] == ["rollout-v0.jsonl"]


def test_thread_cycle_rejects_the_bundle(tmp_path, pricer):
    a, c = SID, CHILD
    write(tmp_path, a, [meta(a, 0, source={"subagent": {"thread_spawn": {"parent_thread_id": c}}})])
    write(tmp_path, c, [meta(c, 1, source={"subagent": {"thread_spawn": {"parent_thread_id": a}}})])
    (ref,) = ADAPTER.discover(tmp_path)
    with pytest.raises(BundleRejected) as exc:
        ADAPTER.build(ref, pricer)
    assert "spawn cycle" in str(exc.value) and exc.value.op_ids


# ---- canonical stream ---------------------------------------------------------------------------
def test_round_trip_through_stream_and_pointers_resolve(tmp_path, pricer):
    write(tmp_path, SID, spawn_parent())
    b = build(tmp_path, pricer)[SID]
    out = stream.write_bundle(b, tmp_path / "out")
    back = stream.read_bundle(out)
    assert back.dropped_fields == {} and stream.defects(back) == []
    assert [o.op_id for o in back.ops] == [o.op_id for o in b.ops]
    leaf = kind(b, "llm_call")[0]
    got = pointer.resolve(leaf.src, {"codex": tmp_path})
    assert got["drift"] is False and '"token_count"' in got["text"]
    assert kind(b, "session")[0].segments == [[T0(0), T0(4)]] and kind(b, "session")[0].in_tok is None


def test_taxonomy_verbs_and_shared_command_functions():
    assert taxonomy.verb_of("codex", "apply_patch") == "edit"
    assert taxonomy.verb_of("codex", "update_plan") == "other"
    assert taxonomy.verb_of("omp", "task") == "spawn" and taxonomy.verb_of("claude_code", "Grep") == "search"
    assert taxonomy.command_key is not None and taxonomy.clip("  a \n b ", 3) == "a b"
    assert taxonomy.rel("/p/x/y.py", "/p") == "x/y.py" and taxonomy.rel("/q/y.py", "/p") == "/q/y.py"


def test_exec_script_inner_patch_apply_end_sets_the_sub_item_outcome(tmp_path, pricer):
    js = 'tools.apply_patch("*** Begin Patch\\n*** Update File: /p/x.py\\n")'
    lines = [
        meta(SID),
        L(1, {"type": "task_started", "turn_id": "t"}),
        L(2, {"type": "custom_tool_call", "name": "exec", "call_id": "c", "input": js}, "response_item"),
        L(3, {"type": "patch_apply_end", "call_id": "exec-1", "success": False, "changes": {"/p/x.py": {}}}),
        tc(4, u(10, 1), u(10, 1)),
        L(5, {"type": "task_complete", "turn_id": "t"}),
    ]
    b = one(tmp_path, pricer, lines)
    (run,) = b.steps
    assert [(x.tool, x.target, x.outcome) for x in run.sub_items] == [("apply_patch", "x.py", "error")]


def test_session_metadata_from_meta_and_state_db(tmp_path, pricer):
    import sqlite3

    lines = [
        meta(SID, git={"branch": "feat/a", "commit_hash": "x"}, cli_version="0.160.1"),
        L(1, {"type": "task_started", "turn_id": "t"}),
        L(1, {"type": "user_message", "message": "Please   fix the tests"}),
        L(2, {"type": "task_complete", "turn_id": "t"}),
    ]
    s = kind(one(tmp_path, pricer, lines), "session")[0]
    # DM-10: the first user message is no longer the adapter title (the store derives first_ask)
    assert (s.title, s.title_source, s.label) == (None, None, "Please fix the tests")
    assert (s.git_branch, s.harness_version) == ("feat/a", "0.160.1")
    con = sqlite3.connect(tmp_path / "state_5.sqlite")
    con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
    con.execute("INSERT INTO threads VALUES (?, ?)", (SID, "Db title"))
    con.commit()
    con.close()
    s = kind(build(tmp_path, pricer)[SID], "session")[0]
    assert (s.title, s.title_source) == ("Db title", "thread_title")  # DM-10
    assert s.label == "Db title"
    assert all(o.title is None for o in build(tmp_path, pricer)[SID].ops if o.kind != "session")


def test_first_line_ms_is_the_first_envelope_timestamp(tmp_path, pricer):
    lines = [
        meta(SID, sec=9),
        L(1, {"type": "task_started", "turn_id": "t"}),
        L(2, {"type": "task_complete", "turn_id": "t"}),
    ]
    s = kind(one(tmp_path, pricer, lines), "session")[0]
    assert s.first_line_ms == 1767225609000  # the session_meta line (00:00:09), not the min


def test_session_metadata_null_when_absent(tmp_path, pricer):
    s = kind(one(tmp_path, pricer, [meta(SID)]), "session")[0]
    assert (s.title, s.git_branch, s.harness_version) == (None, None, None)
