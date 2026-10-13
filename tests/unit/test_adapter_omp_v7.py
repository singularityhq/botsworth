"""omp adapter capture for schema v7 (B2c): labels, turn kind, titles, skill step ids, refs, ts_list."""

import json
from pathlib import Path

from test_adapter_omp import ADAPTER, T, assistant, build, iso, kind, rec, result, start, user, write

from botsworth import pointer, stream


def text(s):
    return [{"type": "text", "text": s}]


def turns(b):
    return kind(b, "turn")


def slash(ms, name="team-lead", args="delegate"):
    return rec(
        "custom_message",
        ms,
        customType="skill-prompt",
        details={"name": name, "path": "/s/SKILL.md", "args": args},
    )


def write_raw(path, header_extra, rows, sid="S"):
    """A session file with exactly the given header fields and rows (no implicit `title` record)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    hdr = {"type": "session", "version": 3, "id": sid, "timestamp": iso(T), "cwd": "/proj", **header_extra}
    path.write_text("\n".join(json.dumps(r) for r in [hdr, *rows]) + "\n")


def build_raw(tmp_path, header_extra, rows):
    root = tmp_path / "sessions"
    write_raw(root / "-proj/s.jsonl", header_extra, rows)
    (ref,) = list(ADAPTER.discover(root))
    b = ADAPTER.build(ref, None)
    stream.validate(b)
    return b


# -- DM-22 labels ---------------------------------------------------------------------------


def test_label_from_text_blocks(tmp_path):
    b = build(tmp_path, [user(T, content=[*text("approved"), {"type": "image"}, *text("now")])])
    assert [t.label for t in turns(b)] == ["approved now"]


def test_label_from_string_content(tmp_path):
    b = build(tmp_path, [user(T, content="  go\n  ahead ")])
    assert [t.label for t in turns(b)] == ["go ahead"]


def test_label_is_clipped_to_120(tmp_path):
    b = build(tmp_path, [user(T, content="x" * 300)])
    assert turns(b)[0].label == "x" * 120


def test_skill_prompt_label_name_args(tmp_path):
    b = build(tmp_path, [slash(T), slash(T + 1, name="plain", args=None)])
    assert [t.label for t in turns(b)] == ["/team-lead delegate", "/plain"]


def test_no_user_prompt_labels(tmp_path):
    rows = [
        user(T, content=text("a")),
        assistant(T + 1, T + 2, id="a"),
        slash(T + 3),
        user(T + 4, content=[]),
    ]
    b = build(tmp_path, rows)
    assert not [t for t in turns(b) if t.label in ("user prompt", "skill prompt")]


def test_agent_message_label_is_envelope_body(tmp_path):
    body = text('<agent-message from="x">ship it</agent-message>')
    b = build(tmp_path, [user(T, content=body, attribution="agent")])
    assert turns(b)[0].label == "ship it"


# -- DM-20 kind and origin ------------------------------------------------------------------


def kinds(b):
    return [(t.turn_kind, t.turn_origin) for t in turns(b)]


def test_attribution_agent_message(tmp_path):
    b = build(tmp_path, [user(T, content=text("hi"), attribution="agent")])
    assert kinds(b) == [("message", "agent")]


def test_attribution_user_human(tmp_path):
    b = build(tmp_path, [user(T, content=text("hi"), attribution="user")])
    assert kinds(b) == [("human", "typed")]


def test_attribution_absent_human(tmp_path):
    b = build(tmp_path, [user(T, content=text("hi"))])
    assert kinds(b) == [("human", "typed")]


def test_skill_prompt_human_slash(tmp_path):
    b = build(tmp_path, [slash(T)])
    assert kinds(b) == [("human", "slash")]


def test_derived_initial_turn_harness_empty(tmp_path):
    b = build(tmp_path, [assistant(T, T + 1, id="a")])
    (turn,) = turns(b)
    assert (turn.label, turn.turn_kind, turn.turn_origin) == ("initial", "harness", "empty")


def test_child_transcript_turns_message_agent(tmp_path):
    root = tmp_path / "sessions"
    top = root / "-proj/s.jsonl"
    write(
        top,
        [user(T, content=text("go")), assistant(T, T + 100, calls=[("t", "task", {})], id="a")]
        + [result(T + 110, "t", "task", {"results": [{"id": "Kid"}]})],
        "S",
    )
    kid = top.with_suffix("")
    write(
        kid / "Kid.jsonl",
        [
            user(T + 200, content=text("do it"), attribution="user"),
            slash(T + 210),
            assistant(T + 220, T + 230, id="k"),
        ],
        "K",
        header_ms=T + 120,
    )
    b = build(tmp_path)
    by_agent = {t.agent_id: [] for t in turns(b)}
    for t in turns(b):
        by_agent[t.agent_id].append((t.turn_kind, t.turn_origin))
    assert by_agent == {None: [("human", "typed")], "K": [("message", "agent")] * 2}


def test_interior_records_do_not_change_turn_boundaries(tmp_path):
    steering = user(T + 2, content=text("steer"), steering=True)
    b = build(tmp_path, [user(T, content=text("a")), assistant(T + 1, T + 2, id="a"), steering])
    assert len(turns(b)) == 1


# -- DM-10 title ----------------------------------------------------------------------------


def session(b):
    return kind(b, "session")[0]


def test_title_record_last_wins_generated(tmp_path):
    rows = [rec("title", T + 1, title="first"), rec("title", T + 2, title="second"), user(T + 3, content=[])]
    s = session(build_raw(tmp_path, {"title": "header"}, rows))
    assert (s.title, s.title_source) == ("second", "generated")


def test_header_title_generated(tmp_path):
    s = session(build_raw(tmp_path, {"title": "header"}, [user(T + 3, content=[])]))
    assert (s.title, s.title_source) == ("header", "generated")


def test_no_title_null_source(tmp_path):
    s = session(build_raw(tmp_path, {}, [user(T + 3, content=[])]))
    assert (s.title, s.title_source) == (None, None)


# -- DM-30 slash step_id --------------------------------------------------------------------


def test_skill_prompt_event_step_id(tmp_path):
    thinking = assistant(T + 2, T + 5, id="a", content=[{"type": "thinking", "thinking": "hm"}])
    saying = assistant(T + 21, T + 25, id="b", content=text("ok"))
    rows = [user(T, content=text("go")), slash(T + 1), thinking, slash(T + 20, name="later"), saying]
    b = build(tmp_path, rows)
    slash_turns = [t for t in turns(b) if t.turn_origin == "slash"]
    events = [e for e in b.events if e.kind == "skill_invocation"]
    assert [e.op_id for e in events] == [t.op_id for t in slash_turns]
    first = [next(s for s in b.steps if s.turn_id == t.op_id) for t in slash_turns]
    assert [e.fields["step_id"] for e in events] == [f"{s.turn_id}#0" for s in first]
    assert [s.verb for s in first] == ["think", "answer"]


def test_skill_prompt_without_step_null(tmp_path):
    b = build(tmp_path, [slash(T), user(T + 1, content=text("next"))])
    (e,) = [e for e in b.events if e.kind == "skill_invocation"]
    assert e.fields["step_id"] is None and e.op_id == turns(b)[0].op_id


# -- DM-40 ----------------------------------------------------------------------------------


def test_ask_waits_on_user(tmp_path):
    calls = [("q", "ask", {"questions": []}), ("r", "read", {"path": "/proj/a"})]
    rows = [user(T), assistant(T + 1, T + 5, calls=calls, id="a")]
    rows += [result(T + 9, "q", "ask"), result(T + 10, "r", "read")]
    b = build(tmp_path, rows)
    assert {o.tool_name: o.waits_on_user for o in kind(b, "tool_call")} == {"ask": True, "read": None}


def test_ts_list_sorted(tmp_path):
    rows = [user(T + 5000, content=[]), user(T + 1000, content=[]), rec("session_note", T - 7)]
    b = build(tmp_path, rows)
    (f,) = b.source_files
    # the header and the three rows, ascending; the `title` record written by `write` has no timestamp
    assert f.ts_list == [T - 7, T, T + 1000, T + 5000]


# -- DM-7 references ------------------------------------------------------------------------


def tmp_root(b):
    return Path(b.source_files[0].path).parent.parent


def test_refs_resolve_each_part(tmp_path):
    cmd = "echo " + "x" * 300
    thinking = {"type": "thinking", "thinking": "deep thought"}
    said = {"type": "text", "text": "plain words"}
    calls = [
        ("b", "bash", {"command": cmd}),
        ("t", "task", {"tasks": [{"name": "A", "assignment": "do the A work"}]}),
    ]
    asst = assistant(T + 1, T + 5, calls=calls, id="a", content=[thinking, said])
    out = result(T + 9, "b", "bash")
    out["message"]["content"] = text("command output")
    rows = [user(T, content=text("the full ask")), asst, start(T + 6, "b"), out, result(T + 10, "t", "task")]
    b = build(tmp_path, rows)
    roots = {"omp": tmp_root(b)}

    def get(uri, part=None):
        return pointer.resolve(uri, roots, part)

    steps = {s.verb: s for s in b.steps}
    assert get(steps["think"].src, "thinking")["text"] == "deep thought"
    assert get(steps["answer"].src, "text")["text"] == "plain words"
    (turn,) = turns(b)
    assert get(turn.src, "message.content.0.text")["text"] == "the full ask"
    bash = next(o for o in kind(b, "tool_call") if o.tool_name == "bash")
    p = pointer.parse(bash.src)
    assert p.locator.last > p.locator.first and p.json_path == "message.content.2"
    assert p.h is None
    args = json.loads(get(bash.src, "arguments")["text"])
    assert args["command"] == cmd and len(cmd) > 160
    last = pointer.format(p.provider, p.relpath, pointer.Lines(p.locator.last, p.locator.last))
    assert json.loads(get(last, "message.content")["text"]) == text("command output")
    assert not get(bash.src, "arguments")["drift"]
    task = next(o for o in kind(b, "tool_call") if o.tool_name == "task")
    assert json.loads(get(task.src, "arguments")["text"])["tasks"][0]["assignment"] == "do the A work"
