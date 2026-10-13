"""B2b: Codex adapter capture for v7 (DM-7, DM-10, DM-12, DM-20, DM-26, DM-30, DM-31, DM-40)."""

import json
import sqlite3

import pytest
import test_adapter_codex as base
from test_adapter_codex import CHILD, SID, T0, L, build, by_id, call, kind, meta, one, tc, u, write

from botsworth import pointer
from botsworth.adapters.codex import adapter as codex_adapter
from botsworth.adapters.codex import steps

pricer = base.pricer


def turn_lines(message, **meta_extra):
    return [
        meta(SID, **meta_extra),
        L(1, {"type": "task_started", "turn_id": "t"}),
        L(1, {"type": "user_message", "message": message}),
        L(2, {"type": "task_complete", "turn_id": "t"}),
    ]


def main_turn(b):
    (t,) = [o for o in kind(b, "turn")]
    return t


def state_db(root, title):
    con = sqlite3.connect(root / "state_5.sqlite")
    con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
    con.execute("INSERT INTO threads VALUES (?, ?)", (SID, title))
    con.commit()
    con.close()


def index(root, *entries):
    (root / "session_index.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))


# ---- DM-20 --------------------------------------------------------------------------------------
def test_exec_source_message_exec(tmp_path, pricer):
    t = main_turn(one(tmp_path, pricer, turn_lines("run the job", source="exec")))
    assert (t.turn_kind, t.turn_origin) == ("message", "exec")


def test_codex_exec_originator_message_exec(tmp_path, pricer):
    t = main_turn(one(tmp_path, pricer, turn_lines("run the job", originator="codex_exec", source="cli")))
    assert (t.turn_kind, t.turn_origin) == ("message", "exec")


def test_empty_message_harness(tmp_path, pricer):
    t = main_turn(one(tmp_path, pricer, turn_lines("  \n ", source="exec")))
    assert (t.turn_kind, t.turn_origin) == ("harness", "empty")


def test_vscode_typed_human(tmp_path, pricer):
    t = main_turn(
        one(tmp_path, pricer, turn_lines("fix the rounding", originator="codex_vscode", source="vscode"))
    )
    assert (t.turn_kind, t.turn_origin) == ("human", "typed")


def test_subagent_turns_message_agent(tmp_path, pricer):
    spawn = {"parent_thread_id": SID, "depth": 1}
    write(tmp_path, SID, turn_lines("please delegate"))
    child = [
        meta(CHILD, 3, source={"subagent": {"thread_spawn": spawn}}),
        L(4, {"type": "task_started", "turn_id": "c"}),
        L(4, {"type": "user_message", "message": "typed-looking text in a child"}),
        L(5, {"type": "task_complete", "turn_id": "c"}),
    ]
    write(tmp_path, CHILD, child)
    b = build(tmp_path, pricer)[SID]
    turns = {t.op_id: t for t in kind(b, "turn")}
    child_turn = next(t for t in turns.values() if "/turn/" + CHILD in t.op_id)
    main = next(t for t in turns.values() if t is not child_turn)
    assert (child_turn.turn_kind, child_turn.turn_origin) == ("message", "agent")
    assert (main.turn_kind, main.turn_origin) == ("human", "typed")


def test_turn_src_is_the_user_message_line(tmp_path, pricer):
    root = tmp_path
    write(root, SID, turn_lines("fix the rounding in cost"))
    t = main_turn(build(root, pricer)[SID])
    got = pointer.resolve(t.src, {"codex": root})
    assert got["text"] == "fix the rounding in cost" and got["drift"] is False
    assert pointer.parse(t.src).locator.first == pointer.parse(t.src).locator.last == 3


# ---- DM-10 --------------------------------------------------------------------------------------
def test_session_index_rename_wins_over_threads_title(tmp_path, pricer):
    write(tmp_path, SID, turn_lines("first message of the thread"))
    state_db(tmp_path, "first message of the thread")
    index(tmp_path, {"id": SID, "thread_name": "Review app"})
    s = kind(build(tmp_path, pricer)[SID], "session")[0]
    assert (s.title, s.title_source) == ("Review app", "rename")


def test_thread_name_updated_used_without_index(tmp_path, pricer):
    lines = turn_lines("first message")
    lines.insert(2, L(1, {"type": "thread_name_updated", "thread_name": "Old"}))
    lines.insert(3, L(1, {"type": "thread_name_updated", "thread_name": "Newer"}))
    write(tmp_path, SID, lines)
    state_db(tmp_path, "first message")
    s = kind(build(tmp_path, pricer)[SID], "session")[0]
    assert (s.title, s.title_source) == ("Newer", "rename")


def test_threads_title_marked_thread_title(tmp_path, pricer):
    write(tmp_path, SID, turn_lines("first message"))
    state_db(tmp_path, "Db title")
    s = kind(build(tmp_path, pricer)[SID], "session")[0]
    assert (s.title, s.title_source) == ("Db title", "thread_title")


def test_no_title_source_leaves_both_none(tmp_path, pricer):
    s = kind(one(tmp_path, pricer, turn_lines("first message")), "session")[0]
    assert (s.title, s.title_source) == (None, None)


def test_build_title_equals_override(tmp_path, pricer):
    write(tmp_path, SID, turn_lines("first message"))
    index(tmp_path, {"id": SID, "thread_name": "  Spaced   name "})
    s = kind(build(tmp_path, pricer)[SID], "session")[0]
    assert s.title == codex_adapter.session_overrides(tmp_path)[SID] == "Spaced name"


def test_index_is_not_a_bundle_file(tmp_path, pricer):
    write(tmp_path, SID, turn_lines("first message"))
    index(tmp_path, {"id": SID, "thread_name": "x"})
    b = build(tmp_path, pricer)[SID]
    assert [f.path.rsplit("/", 1)[-1][:7] for f in b.source_files] == ["rollout"]


# ---- DM-12 --------------------------------------------------------------------------------------
def test_session_overrides_last_line_wins(tmp_path):
    index(
        tmp_path,
        {"id": "a", "thread_name": "one"},
        {"id": "b", "thread_name": "other"},
        {"id": "a", "thread_name": "two"},
    )
    with (tmp_path / "session_index.jsonl").open("a") as f:
        f.write("not json\n")
    assert codex_adapter.session_overrides(tmp_path) == {"a": "two", "b": "other"}


def test_session_overrides_missing_file_empty(tmp_path):
    assert codex_adapter.session_overrides(tmp_path) == {}


# ---- DM-26 --------------------------------------------------------------------------------------
def test_entrypoint_and_originator(tmp_path, pricer):
    s = kind(
        one(tmp_path, pricer, turn_lines("hi there", originator="codex_vscode", source="vscode")), "session"
    )[0]
    assert (s.entrypoint, s.originator) == ("vscode", "codex_vscode")


# ---- DM-31 / DM-30 ------------------------------------------------------------------------------
def skill_events(tmp_path, pricer, cmd, parsed=None):
    lines = [
        meta(SID),
        L(1, {"type": "task_started", "turn_id": "t"}),
        call(2, "c", {"cmd": cmd}),
    ]
    if parsed is not None:
        lines.append(
            L(
                3,
                {
                    "type": "exec_command_end",
                    "call_id": "c",
                    "exit_code": 0,
                    "command": ["bash", "-lc", cmd],
                    "parsed_cmd": parsed,
                    "duration": {"secs": 0, "nanos": 1},
                },
            )
        )
    lines += [
        L(4, {"type": "function_call_output", "call_id": "c", "output": "ok"}, "response_item"),
        tc(5, u(10, 1), u(10, 1)),
        L(6, {"type": "task_complete", "turn_id": "t"}),
    ]
    b = one(tmp_path, pricer, lines)
    return b, [e for e in b.events if e.kind == "skill_invocation"]


def test_skill_read_sed_loads(tmp_path, pricer):
    b, ev = skill_events(tmp_path, pricer, "sed -n '1,260p' /x/skills/a/SKILL.md")
    assert [e.fields["skill"] for e in ev] == ["a"]
    (st,) = b.steps
    assert ev[0].fields["step_id"] == f"{st.turn_id}#{st.ordinal}" and st.verb == "run"


def test_skill_read_rg_not_load(tmp_path, pricer):
    _, ev = skill_events(tmp_path, pricer, "rg -n foo .claude/skills/team/SKILL.md")
    assert ev == []


def test_skill_read_chain_pwd_ls_sed_loads_b(tmp_path, pricer):
    _, ev = skill_events(tmp_path, pricer, "pwd && ls dir && sed -n '1,220p' /x/skills/b/SKILL.md")
    assert [e.fields["skill"] for e in ev] == ["b"]


def test_skill_read_sed_inplace_not_load(tmp_path, pricer):
    _, ev = skill_events(tmp_path, pricer, "sed -i 's/a/b/' /x/skills/c/SKILL.md")
    assert ev == []


@pytest.mark.parametrize("prog", ["cat", "head -n 5", "tail -n 5", "nl -ba"])
def test_skill_read_cat_head_tail_nl_load(prog):
    assert steps.skill_reads(f"{prog} /x/skills/d/SKILL.md") == [("d", "skills/d/SKILL.md")]


def test_skill_read_redirect_into_file_not_load():
    assert steps.skill_reads("cat notes > /x/skills/e/SKILL.md") == []
    assert steps.skill_reads("echo hi >/x/skills/e/SKILL.md") == []
    assert steps.skill_reads("wc -l /x/skills/e/SKILL.md; git log /x/skills/e/SKILL.md") == []


def test_skill_read_semicolon_inside_quotes_does_not_split():
    assert steps.skill_reads("sed -n '1p;2p' /x/skills/f/SKILL.md") == [("f", "skills/f/SKILL.md")]


def test_skill_read_parsed_cmd_read_loads(tmp_path, pricer):
    parsed = [{"type": "read", "cmd": "python show.py", "name": "SKILL.md", "path": "/x/skills/g/SKILL.md"}]
    _, ev = skill_events(tmp_path, pricer, "python show.py", parsed)
    assert [e.fields["skill"] for e in ev] == ["g"] and ev[0].fields["step_id"]


# ---- DM-40 --------------------------------------------------------------------------------------
def test_request_user_input_waits_on_user(tmp_path, pricer):
    lines = [
        meta(SID),
        L(1, {"type": "task_started", "turn_id": "t"}),
        call(2, "q", {"questions": []}, name="request_user_input"),
        call(3, "r", {"cmd": "ls"}),
        tc(4, u(10, 1), u(10, 1)),
        L(5, {"type": "task_complete", "turn_id": "t"}),
    ]
    ops = by_id(one(tmp_path, pricer, lines))
    assert ops[f"codex/{SID}/tool_call/q"].waits_on_user is True
    assert ops[f"codex/{SID}/tool_call/r"].waits_on_user is None


def test_ts_list_sorted(tmp_path, pricer):
    lines = turn_lines("hello there")
    lines.insert(2, L(0, {"type": "task_started", "turn_id": "u"}))  # out of order, earlier than line 1
    b = one(tmp_path, pricer, lines)
    (f,) = b.source_files
    assert f.ts_list == sorted(f.ts_list) and f.ts_list[0] == T0(0) and len(f.ts_list) == 5


# ---- DM-7 ---------------------------------------------------------------------------------------
def test_refs_resolve_each_part(tmp_path, pricer):
    cmd = "echo " + "x" * 300
    spawn_args = {"agent_type": "worker", "message": "do the thing"}
    lines = [
        meta(SID),
        L(1, {"type": "task_started", "turn_id": "t"}),
        L(1, {"type": "user_message", "message": "fix the rounding in cost"}),
        L(
            2,
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "pondering"}],
                "encrypted_content": "SECRET-BLOB",
            },
            "response_item",
        ),
        L(2, {"type": "agent_message", "message": "looking now"}),
        call(3, "c1", {"cmd": cmd}),
        L(4, {"type": "function_call_output", "call_id": "c1", "output": "the output text"}, "response_item"),
        call(5, "sp", spawn_args, name="spawn_agent"),
        L(6, {"type": "function_call_output", "call_id": "sp", "output": "{}"}, "response_item"),
        tc(7, u(10, 1), u(10, 1)),
        L(8, {"type": "task_complete", "turn_id": "t"}),
    ]
    root = tmp_path
    b = one(root, pricer, lines)
    roots = {"codex": root}
    ops = by_id(b)

    c1 = ops[f"codex/{SID}/tool_call/c1"]
    got = pointer.resolve(c1.src, roots)
    assert json.loads(got["text"])["cmd"] == cmd and len(cmd) > 160 and got["drift"] is False
    last = pointer.parse(c1.src).locator
    assert (last.first, last.last) == (6, 7)
    out_uri = pointer.format("codex", pointer.parse(c1.src).relpath, pointer.Lines(last.last, last.last))
    assert pointer.resolve(out_uri, roots, "payload.output")["text"] == "the output text"

    sp = ops[f"codex/{SID}/tool_call/sp"]
    assert json.loads(pointer.resolve(sp.src, roots)["text"]) == spawn_args

    think = next(s for s in b.steps if s.verb == "think")
    text = pointer.resolve(think.src, roots)["text"]
    assert "pondering" in text and "SECRET-BLOB" not in text
    say = next(s for s in b.steps if s.verb in ("say", "answer"))
    assert pointer.resolve(say.src, roots)["text"] == "looking now"
    run_step = next(s for s in b.steps if s.tool_name == "exec_command")
    assert json.loads(pointer.resolve(run_step.src, roots)["text"])["cmd"] == cmd
