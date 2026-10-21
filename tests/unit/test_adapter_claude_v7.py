"""Claude adapter, v7 capture (B2a): turn kind and origin (DM-20), slash and message labels (DM-21, DM-23),
titles (DM-10), entrypoint (DM-26), slash event step ids (DM-30), user_wait and ts_list (DM-40, D54),
and content references (DM-7). Every record is synthetic and written to tmp_path."""

from __future__ import annotations

import json

import pytest
from test_adapter_claude import assistant, build, ops, result, stamp, tool, user

from botsworth import pointer

# ---- helpers ---------------------------------------------------------------


def main_turns(b):
    return [o for o in ops(b, "turn") if o.agent_id is None]


def first_turn(tmp_path, **extra):
    """The first main turn of a session whose first record is `user(**extra)`."""
    text = extra.pop("text", "do the thing")
    b = build(tmp_path, [user("u", text=text, **extra)])
    return main_turns(b)[0]


def kind_origin(turn):
    return (turn.turn_kind, turn.turn_origin)


def expanded(key, second=0, prompt_id="p1", text="Base directory for this skill: /x"):
    return user(key, second, text, isMeta=True, promptId=prompt_id)


# ---- DM-20: turn kind and origin ---------------------------------------------


def test_turn_kind_plugin_message(tmp_path):
    turn = first_turn(
        tmp_path,
        text="The e2e-grid plugin sent a message\nrun the grid",
        origin={"kind": "plugin", "name": "e2e-grid"},
        promptSource="system",
    )
    assert kind_origin(turn) == ("message", "plugin:e2e-grid")


def test_turn_kind_sdk_message(tmp_path):
    turn = first_turn(tmp_path, promptSource="sdk", entrypoint="sdk-cli")
    assert kind_origin(turn) == ("message", "sdk")


def test_turn_kind_interrupt_harness(tmp_path):
    turn = first_turn(tmp_path, text="[Request interrupted by user for tool use]")
    assert kind_origin(turn) == ("harness", "interrupt")


def test_turn_kind_empty_harness(tmp_path):
    turn = first_turn(tmp_path, text="(no text)", promptSource="typed")
    assert kind_origin(turn) == ("harness", "empty")


def test_turn_kind_typed_human(tmp_path):
    turn = first_turn(tmp_path, text="fix it", promptSource="typed", origin={"kind": "human"})
    assert kind_origin(turn) == ("human", "typed")


def test_turn_kind_queued_human(tmp_path):
    turn = first_turn(tmp_path, text="and also this", promptSource="queued", origin={"kind": "human"})
    assert kind_origin(turn) == ("human", "queued")


@pytest.mark.parametrize(
    "extra",
    [
        {"text": '<mc-message from="x">ship it</mc-message>', "promptSource": "typed"},
        {"text": "Another Claude session sent a message: hello", "promptSource": "typed"},
        {"text": "hello from a peer", "origin": {"kind": "peer"}, "promptSource": "typed"},
    ],
)
def test_turn_kind_peer_mc_message(tmp_path, extra):
    turn = first_turn(tmp_path, **extra)
    assert turn.turn_kind == "message"
    assert turn.turn_origin == extra.get("origin", {}).get("kind", "peer")


def test_turn_kind_coordinator_origin(tmp_path):
    turn = first_turn(tmp_path, text="status please", origin={"kind": "coordinator"}, promptSource="system")
    assert kind_origin(turn) == ("message", "coordinator")


def test_turn_kind_system_message(tmp_path):
    turn = first_turn(tmp_path, text="scheduled tick", promptSource="system")
    assert kind_origin(turn) == ("message", "system")


def test_turn_kind_legacy_cli_human_and_sdk_cli_message(tmp_path):
    cli = first_turn(tmp_path / "a", text="older harness ask", entrypoint="cli")
    sdk = first_turn(tmp_path / "b", text="older harness sdk", entrypoint="sdk-cli")
    assert kind_origin(cli) == ("human", "typed")
    assert kind_origin(sdk) == ("message", "sdk")


def test_turn_kind_bash_input_is_harness(tmp_path):
    turn = first_turn(tmp_path, text="<bash-input>ls</bash-input>", promptSource="typed")
    assert kind_origin(turn) == ("harness", "builtin:bash")


def test_agent_turns_message_agent(tmp_path):
    main = [
        user("u"),
        assistant("r1", 1, content=[tool("spawn", "Agent", prompt="first")]),
        result("spawn", 2, toolUseResult={"agentId": "a"}),
    ]
    agent = [
        user("au", 2, "first", promptSource="typed", origin={"kind": "human"}),
        assistant("ra", 5),
        user("au2", 12, '<teammate-message teammate_id="main">again</teammate-message>'),
        assistant("rb", 14),
    ]
    b = build(tmp_path, main, [("a", agent, {})])
    agent_turns = [o for o in ops(b, "turn") if o.agent_id == "a"]
    assert len(agent_turns) == 2
    assert {kind_origin(t) for t in agent_turns} == {("message", "agent")}


# ---- DM-21: slash commands ---------------------------------------------------


def test_builtin_clear_harness_no_skill_event(tmp_path):
    rows = [
        user("u", text="<command-name>/clear</command-name><command-args></command-args>", promptId="p1"),
        {"type": "system", "uuid": "s", "timestamp": stamp(1), "subtype": "local_command", "content": "x"},
        user("v", 2, "next real ask here", promptSource="typed"),
    ]
    b = build(tmp_path, rows)
    clear = main_turns(b)[0]
    assert (kind_origin(clear), clear.label) == (("harness", "builtin:clear"), "/clear")
    assert [e for e in b.events if e.kind == "skill_invocation"] == []


def test_slash_skill_human_one_event_with_label_args(tmp_path):
    rows = [
        user(
            "u",
            text="<command-name>/mad:bootstrap</command-name><command-args>ARGS</command-args>",
            promptId="p1",
            promptSource="typed",
        ),
        expanded("u-meta", 0),
        assistant("r1", 1, content=[{"type": "text", "text": "ok"}]),
    ]
    b = build(tmp_path, rows)
    turn = main_turns(b)[0]
    assert (kind_origin(turn), turn.label) == (("human", "slash"), "/mad:bootstrap ARGS")
    (event,) = [e for e in b.events if e.kind == "skill_invocation"]
    assert (event.fields["skill"], event.fields["via"], event.fields["args_head"]) == (
        "mad:bootstrap",
        "slash",
        "ARGS",
    )


def test_slash_without_matching_prompt_id_is_builtin(tmp_path):
    rows = [
        user("u", text="<command-name>/foo</command-name>", promptId="p1"),
        expanded("u-meta", 0, prompt_id="p2"),
    ]
    turn = main_turns(build(tmp_path, rows))[0]
    assert kind_origin(turn) == ("harness", "builtin:foo")


def test_compact_boundary_builtin_compact(tmp_path):
    rows = [
        user("u", text="<command-name>/compact</command-name><command-args></command-args>", promptId="p1"),
        user("v", 1, "<local-command-stdout>Compacted</local-command-stdout>", promptId="p1"),
        {
            "type": "system",
            "uuid": "c",
            "timestamp": stamp(2),
            "subtype": "compact_boundary",
            "compactMetadata": {"trigger": "manual", "preTokens": 100, "postTokens": 10, "durationMs": 5},
        },
    ]
    b = build(tmp_path, rows)
    assert kind_origin(main_turns(b)[0]) == ("harness", "builtin:compact")
    assert [e.kind for e in b.events] == ["compaction"]


# ---- DM-23: message labels ---------------------------------------------------


def test_mc_message_label_is_body(tmp_path):
    turn = first_turn(tmp_path, text='<mc-message from="x">ship it</mc-message>', promptSource="typed")
    assert turn.label == "ship it"


def test_plugin_label_drops_prefix_line(tmp_path):
    turn = first_turn(
        tmp_path,
        text="The e2e-grid plugin sent a message\nrun the grid now",
        origin={"kind": "plugin", "name": "e2e-grid"},
        promptSource="system",
    )
    assert turn.label == "run the grid now"


# ---- DM-10: titles -----------------------------------------------------------


def titled(tmp_path, *records):
    rows = [user("u", text="fix the rounding in cost.py please", promptSource="typed"), *records]
    return build(tmp_path, rows).ops[0]


def test_title_custom_beats_ai_title(tmp_path):
    session = titled(
        tmp_path,
        {"type": "ai-title", "aiTitle": "A"},
        {"type": "custom-title", "customTitle": "B"},
        {"type": "ai-title", "aiTitle": "C"},
    )
    assert (session.title, session.title_source, session.label) == ("B", "rename", "B")


def test_title_last_custom_title_wins(tmp_path):
    session = titled(
        tmp_path, {"type": "custom-title", "customTitle": "x"}, {"type": "custom-title", "customTitle": "y"}
    )
    assert (session.title, session.title_source) == ("y", "rename")


def test_title_ai_title_last_wins_generated(tmp_path):
    session = titled(tmp_path, {"type": "ai-title", "aiTitle": "A"}, {"type": "ai-title", "aiTitle": "C"})
    assert (session.title, session.title_source) == ("C", "generated")


def test_title_summary_generated(tmp_path):
    session = titled(
        tmp_path, {"type": "summary", "summary": "first"}, {"type": "summary", "summary": "second"}
    )
    assert (session.title, session.title_source) == ("first", "generated")


def test_title_none_source_none(tmp_path):
    session = titled(tmp_path)
    assert (session.title, session.title_source) == (None, None)
    assert session.label == "fix the rounding in cost.py please"


# ---- DM-26: entrypoint -------------------------------------------------------


def test_entrypoint_first_record(tmp_path):
    rows = [
        {"type": "queue-operation", "timestamp": stamp(0), "operation": "enqueue"},
        user("u", 1, "an ask", entrypoint="cli"),
        assistant("r1", 2, entrypoint="sdk-cli"),
    ]
    b = build(tmp_path, rows)
    assert ops(b, "session")[0].entrypoint == "cli"
    assert ops(b, "session")[0].originator is None


def test_entrypoint_none_without_field(tmp_path):
    b = build(tmp_path, [user("u")])
    assert ops(b, "session")[0].entrypoint is None


def test_entrypoint_ignores_agent_transcripts(tmp_path):
    main = [user("u"), assistant("r1", 1, content=[tool("spawn", "Agent", prompt="first")])]
    agent = [user("au", 2, "first", entrypoint="sdk-cli"), assistant("ra", 3)]
    b = build(tmp_path, main, [("a", agent, {})])
    assert ops(b, "session")[0].entrypoint is None


# ---- DM-30: slash event step id ----------------------------------------------


def slash_rows(*after):
    return [
        user(
            "u", text="<command-name>/debugging</command-name><command-args>why</command-args>", promptId="p1"
        ),
        expanded("u-meta", 0),
        *after,
    ]


def test_slash_event_step_id_first_step_of_turn(tmp_path):
    rows = slash_rows(assistant("r1", 1, content=[{"type": "thinking", "thinking": "hm"}]))
    b = build(tmp_path, rows)
    (event,) = [e for e in b.events if e.kind == "skill_invocation"]
    turn = main_turns(b)[0]
    first = min((s for s in b.steps if s.turn_id == turn.op_id), key=lambda s: s.ordinal)
    assert event.fields["step_id"] == f"{turn.op_id}#{first.ordinal}" == f"{turn.op_id}#0"


def test_slash_event_without_step_has_null_step_id(tmp_path):
    b = build(tmp_path, slash_rows())
    (event,) = [e for e in b.events if e.kind == "skill_invocation"]
    assert event.fields["step_id"] is None
    assert event.op_id == main_turns(b)[0].op_id


# ---- DM-40 [D54]: user_wait and ts_list ----------------------------------------


def test_ask_user_question_is_user_wait_ask(tmp_path):
    rows = [
        user("u"),
        assistant(
            "r1",
            1,
            content=[
                tool("q", "AskUserQuestion", questions=[{"question": "which one?"}]),
                tool("b", "Bash", command="ls"),
            ],
        ),
        result("q", 30),
        result("b", 31),
    ]
    b = build(tmp_path, rows)
    by_name = {o.tool_name: o for o in ops(b, "tool_call")}
    assert by_name["AskUserQuestion"].user_wait == "ask"
    assert by_name["Bash"].user_wait is None


def _user_waits(tmp_path, result_row):
    rows = [user("u"), assistant("r1", 1, content=[tool("b", "Bash", command="ls")]), result_row]
    (op,) = ops(build(tmp_path, rows), "tool_call")
    return op.user_wait


def test_permission_decision_user_source_is_permission(tmp_path):
    decision = {"decision": "accept", "source": "user_temporary"}
    assert _user_waits(tmp_path, result("b", 30, permissionDecision=decision)) == "permission"
    decision = {"decision": "reject", "source": "user_reject"}
    assert _user_waits(tmp_path, result("b", 30, permissionDecision=decision)) == "permission"


def test_permission_decision_config_source_is_not_a_wait(tmp_path):
    decision = {"decision": "reject", "source": "config"}
    assert _user_waits(tmp_path, result("b", 30, permissionDecision=decision)) is None


def test_permission_decision_unseen_source_is_not_a_wait(tmp_path):
    # DM-40: a source that is neither `user*` nor `config` leaves user_wait NULL (known values pinned here)
    decision = {"decision": "accept", "source": "classifier"}
    assert _user_waits(tmp_path, result("b", 30, permissionDecision=decision)) is None
    assert _user_waits(tmp_path, result("b", 30, permissionDecision="odd")) is None


def test_rejection_text_without_decision_is_permission(tmp_path):
    text = "  The user doesn't want to proceed with this tool use. The tool use was rejected."
    assert _user_waits(tmp_path, result("b", 30, content=text)) == "permission"
    block = [{"type": "text", "text": text}]
    assert _user_waits(tmp_path, result("b", 30, content=block)) == "permission"


def test_ask_beats_rejection_text_and_decision(tmp_path):
    rows = [
        user("u"),
        assistant("r1", 1, content=[tool("q", "AskUserQuestion", questions=[])]),
        result("q", 30, content="The user doesn't want to proceed", permissionDecision={"source": "user"}),
    ]
    (op,) = ops(build(tmp_path, rows), "tool_call")
    assert op.user_wait == "ask"


def test_ordinary_result_text_is_not_a_wait(tmp_path):
    assert _user_waits(tmp_path, result("b", 30, content="done")) is None
    assert _user_waits(tmp_path, result("b", 30, content="note: The user doesn't want to proceed")) is None


def test_ts_list_sorted_all_timestamped_records(tmp_path):
    main = [
        user("u", 0),
        {"type": "queue-operation", "timestamp": stamp(7), "operation": "enqueue"},
        assistant("r1", 1, content=[tool("spawn", "Agent", prompt="first")]),
        {"type": "summary", "summary": "no timestamp here"},
        result("spawn", 9, toolUseResult={"agentId": "a"}),
    ]
    agent = [user("au", 2, "first"), assistant("ra", 5)]
    b = build(tmp_path, main, [("a", agent, {"agentType": "x"})])
    by_name = {sf.path.rsplit("/", 1)[1]: sf for sf in b.source_files}
    assert by_name["s1.jsonl"].ts_list == sorted(
        1790848800000 + s * 1000
        for s in (0, 1, 7, 9)  # the queue-operation record is in it
    )
    assert by_name["agent-a.jsonl"].ts_list == [1790848800000 + 2000, 1790848800000 + 5000]
    assert by_name["agent-a.meta.json"].ts_list == []


# ---- DM-7: references --------------------------------------------------------


def test_refs_resolve_each_part(tmp_path):
    long_command = "echo " + "x" * 300
    main = [
        user("u", 0, "please do the big thing now", promptSource="typed"),
        assistant(
            "r1",
            1,
            content=[
                {"type": "thinking", "thinking": "plan it carefully"},
                tool("b", "Bash", command=long_command),
                tool("spawn", "Agent", prompt="investigate the cache " * 20),
            ],
        ),
        result("b", 2, content="fallback text", toolUseResult={"stdout": "full output " * 20}),
        result("spawn", 3, toolUseResult={"agentId": "a"}),
        assistant("r2", 4, content=[{"type": "text", "text": "all done here"}]),
    ]
    agent = [user("au", 3, "investigate the cache " * 20), assistant("ra", 5)]
    b = build(tmp_path, main, [("a", agent, {})])
    roots = {"claude_code": tmp_path}
    calls = {o.tool_name: o for o in ops(b, "tool_call")}

    bash = calls["Bash"]
    assert pointer.resolve(bash.src, roots, part="input")["text"].count("x") == 300
    assert pointer.resolve(bash.src, roots, part="input.command")["text"] == long_command
    assert len(long_command) > 160

    last = pointer.parse(bash.src).locator
    out_uri = pointer.format(
        "claude_code", pointer.parse(bash.src).relpath, pointer.Lines(last.last, last.last)
    )
    assert json.loads(pointer.resolve(out_uri, roots, part="toolUseResult")["text"]) == {
        "stdout": "full output " * 20
    }

    spawn = calls["Agent"]
    assert pointer.resolve(spawn.src, roots, part="input.prompt")["text"] == "investigate the cache " * 20

    steps = {s.verb: s for s in b.steps}
    assert pointer.resolve(steps["think"].src, roots, part="thinking")["text"] == "plan it carefully"
    assert pointer.resolve(steps["answer"].src, roots, part="text")["text"] == "all done here"

    turn = main_turns(b)[0]
    assert pointer.resolve(turn.src, roots, part="message.content")["text"] == "please do the big thing now"


def test_refs_tool_call_spans_call_line_to_result_line(tmp_path):
    rows = [user("u"), assistant("r1", 1, content=[tool("b", "Bash", command="ls")]), result("b", 2)]
    b = build(tmp_path, rows)
    loc = pointer.parse(ops(b, "tool_call")[0].src).locator
    assert (loc.first, loc.last) == (2, 3)
