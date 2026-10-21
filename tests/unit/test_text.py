# ruff: noqa: F811
"""Q2c content references and lazy text (views/text.py, pointer.py): API-26, DM-6 to DM-9, D39, D42.

Real fixture files come from tests/fixtures/qstore/src (`qroots`). The provider shapes the fixture lacks (omp
tool results, skill-prompt turns, Codex `payload.arguments`, redacted thinking, a Bash command over 160
characters) are written to `tmp_path` as synthetic provider lines and addressed with `pointer.format`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from qstore import STREAMS, qstore  # noqa: F401 (the QF fixture)
from qstore_ids import CODEX_CALL, TOOL_BIG, TOOL_ERROR

from botsworth import pointer
from botsworth.query import QueryError, _conn
from botsworth.query.views import text
from botsworth.query.views.text import src_uri
from botsworth.store import refs

BIG = 11_264  # bytes of the toolu_big result in the fixture


def step_ref(db, op_id: str) -> str:
    """The `src_uri` of the first step of a tool_call (in the fixture it names the result line)."""
    with _conn(db) as conn:
        row = conn.execute(
            "SELECT s.* FROM steps s JOIN ops o ON o.rowid = s.op_rowid WHERE o.op_id = ? ORDER BY s.ordinal",
            (op_id,),
        ).fetchone()
        return src_uri(conn, row)


def write(tmp_path: Path, provider: str, rel: str, lines: list[dict]) -> dict[str, Path]:
    root = tmp_path / provider
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return {provider: root}


def ref(provider: str, rel: str, first: int, last: int | None = None, path: str | None = None) -> str:
    return pointer.format(provider, rel, pointer.Lines(first, last or first), path)


def read(db, src: str, roots, part=None, **kw) -> dict:
    return text.source(src, part=part, db=db, roots=roots, **kw)


# ── src_uri (D39, DM-6, DM-8) ────────────────────────────────────────────────


def test_src_uri_round_trips_pointer_format(qstore):
    """Every op, step and event of the fixture: the URI rebuilt from the stored parts is the stream's `src`
    byte for byte, `pointer.parse` accepts it, and it is the SQL form (`refs.src_sql`) of the same rule."""
    want: dict[tuple, str] = {}
    for f in sorted(STREAMS.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            r = json.loads(line)
            if r["rec"] == "op" and r.get("src"):
                want[("op", r["op_id"])] = r["src"]
            elif r["rec"] == "step":
                want[("step", r["turn_id"], r["ordinal"])] = r["src"]
            elif r["rec"] == "event":
                want[("event", r["event_id"])] = r["src"]
    got: dict[tuple, str] = {}
    with _conn(qstore) as conn:
        for r in conn.execute("SELECT o.*, o.op_id AS key FROM ops o"):
            got[("op", r["key"])] = src_uri(conn, r)
            sql = conn.execute(
                f"SELECT {refs.src_sql('o', 'sf')} FROM ops o JOIN src_files sf ON sf.rowid = o.src_file "
                "WHERE o.rowid = ?",
                (r["rowid"],),
            ).fetchone()[0]
            assert sql == got[("op", r["key"])]
        for r in conn.execute(
            "SELECT s.*, t.op_id AS turn FROM steps s JOIN ops t ON t.rowid = s.turn_rowid"
        ):
            got[("step", r["turn"], r["ordinal"])] = src_uri(conn, r)
        for r in conn.execute("SELECT * FROM events"):
            got[("event", r["event_id"])] = src_uri(conn, r)
    assert len(got) > 100
    for key, uri in got.items():
        pointer.parse(uri)
        if key in want:
            assert uri == want[key], key
    ops = [k for k in want if k[0] == "op"]
    assert ops and all(got[k] == want[k] for k in ops)
    assert any(k[0] == "step" for k in want) and any(k[0] == "event" for k in want)


# ── part mapping (DM-7) ──────────────────────────────────────────────────────


def test_source_block_tool_result_11kb(qstore, qroots):
    """API-26 scenario: an 11 KB result read with part=output returns all of it and block `tool_result`."""
    src = step_ref(qstore, TOOL_BIG)
    out = read(qstore, src, qroots, "output")
    assert out["total_bytes"] == BIG == len(out["text"].encode())
    assert out["block"] == "tool_result"
    assert out["text"].startswith("synthetic output line 0000")
    assert out["drift"] is False
    tail = read(qstore, src, qroots, "output", offset=BIG - 64, max_bytes=1000)
    assert tail["text"] == out["text"][-64:] and tail["total_bytes"] == BIG
    mid = read(qstore, src, qroots, "output", offset=100, max_bytes=50)
    assert mid["text"] == out["text"][100:150] and mid["total_bytes"] == BIG


def test_source_block_thinking_and_redacted(qstore, tmp_path):
    """DET-64: empty thinking is told from redacted thinking by `block`; text is the block's own."""
    roots = write(
        tmp_path,
        "claude_code",
        "p/s.jsonl",
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "weigh the options"},
                        {"type": "thinking", "thinking": ""},
                        {"type": "redacted_thinking", "data": "xxxx"},
                        {"type": "text", "text": "done"},
                    ]
                },
            }
        ],
    )
    full = read(qstore, ref("claude_code", "p/s.jsonl", 1, path="message.content.0"), roots, "thinking")
    assert (full["text"], full["block"]) == ("weigh the options", "thinking")
    empty = read(qstore, ref("claude_code", "p/s.jsonl", 1, path="message.content.1"), roots, "thinking")
    assert (empty["text"], empty["total_bytes"], empty["block"]) == ("", 0, "thinking")
    redacted = read(qstore, ref("claude_code", "p/s.jsonl", 1, path="message.content.2"), roots, "thinking")
    assert (redacted["text"], redacted["total_bytes"], redacted["block"]) == ("", 0, "redacted_thinking")
    said = read(qstore, ref("claude_code", "p/s.jsonl", 1, path="message.content.3"), roots, "text")
    assert (said["text"], said["block"]) == ("done", "text")
    with pytest.raises(QueryError):  # a text block has no thinking: not a redaction
        read(qstore, ref("claude_code", "p/s.jsonl", 1, path="message.content.3"), roots, "thinking")


def test_codex_reasoning_summary_only(qstore, qroots, tmp_path):
    """API-26, DET-63: a Codex reasoning item gives its plaintext summary or "", never encrypted content."""
    with _conn(qstore) as conn:
        steps = [
            src_uri(conn, s)
            for s in conn.execute(
                "SELECT s.* FROM steps s JOIN ops o ON o.rowid = s.op_rowid "
                "WHERE o.op_id = ? AND s.verb = 'think' ORDER BY s.ordinal",
                (CODEX_CALL,),
            )
        ]
    assert len(steps) == 2
    for part in ("thinking", None, "text", "output", "input"):
        with_summary = read(qstore, steps[0], qroots, part)
        assert with_summary["text"] == "Weighing cache eviction after the deploy."
        assert with_summary["block"] == "reasoning"
        assert with_summary["total_bytes"] == len(with_summary["text"].encode())
        empty = read(qstore, steps[1], qroots, part)
        assert (empty["text"], empty["total_bytes"], empty["block"]) == ("", 0, "reasoning")
    whole_line = ref("codex", "sessions/2026/10/10/rollout-scodex.jsonl", 2)  # no json path at all
    leak = read(qstore, whole_line, qroots)
    assert "enc-synthetic" not in leak["text"] and leak["block"] == "reasoning"
    paged = read(qstore, steps[0], qroots, "thinking", offset=3, max_bytes=5)
    assert paged["text"] == "Weighing cache eviction after the deploy."[3:8]
    # two summary parts join with a blank line
    roots = write(
        tmp_path,
        "codex",
        "sessions/r.jsonl",
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "reasoning",
                    "summary": [
                        {"type": "summary_text", "text": "one"},
                        {"type": "summary_text", "text": "two"},
                    ],
                    "encrypted_content": "enc-secret",
                },
            }
        ],
    )
    two = read(qstore, ref("codex", "sessions/r.jsonl", 1, path="payload"), roots, "thinking")
    assert two["text"] == "one\n\ntwo" and "enc-secret" not in json.dumps(two)


def test_source_missing_404(qstore, tmp_path):
    gone = ref("claude_code", "p/gone.jsonl", 1)
    with pytest.raises(pointer.SourceMissing):
        read(qstore, gone, {"claude_code": tmp_path})
    short = write(tmp_path, "claude_code", "p/short.jsonl", [{"a": 1}])
    with pytest.raises(pointer.SourceMissing):
        read(qstore, ref("claude_code", "p/short.jsonl", 5), short, "text")
    with pytest.raises(pointer.SourceMissing):  # the result line is past the end of the file
        read(qstore, ref("claude_code", "p/short.jsonl", 1, 3, "message.content.0"), short, "output")


def test_limit_max(qstore, qroots):
    src = step_ref(qstore, TOOL_BIG)
    ok = read(qstore, src, qroots, "output", max_bytes=1_048_576)
    assert ok["total_bytes"] == BIG and len(ok["text"].encode()) == BIG
    for bad in (1_048_577, 0, -1):
        with pytest.raises(QueryError):
            read(qstore, src, qroots, "output", max_bytes=bad)
    with pytest.raises(QueryError):
        read(qstore, src, qroots, "output", offset=-1)
    with pytest.raises(QueryError):
        read(qstore, src, qroots, "bogus")
    with pytest.raises(QueryError):
        read(qstore, "src://not a pointer", qroots)


def test_source_result_has_api_26_fields(qstore, qroots):
    out = read(qstore, step_ref(qstore, TOOL_ERROR), qroots, "output")
    assert list(out) == ["freshness", "text", "total_bytes", "drift", "file", "locator", "block"]
    assert out["text"] == "String to replace not found in file"
    assert out["freshness"]["schema_version"] == 11


# ── provider gaps (B2c, B2b findings) ────────────────────────────────────────


def test_omp_output_is_the_result_line_content(qstore, tmp_path):
    """The omp tool_call reference spans the call line to the result line with the path of the call block;
    `output` is the result line's `message.content`, `input` the call block's arguments."""
    roots = write(
        tmp_path,
        "omp",
        "p/o.jsonl",
        [
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "toolCall", "id": "c1", "name": "bash", "arguments": {"command": "ls -la"}}
                    ],
                },
            },
            {"type": "message", "message": {"role": "toolResult", "content": "total 0\nREADME.md"}},
        ],
    )
    src = ref("omp", "p/o.jsonl", 1, 2, "message.content.0")
    out = read(qstore, src, roots, "output")
    assert out["text"] == "total 0\nREADME.md" and out["locator"] == "L2-L2"
    inp = read(qstore, src, roots, "input")
    assert inp["text"] == "ls -la" and inp["block"] == "toolCall"
    # a result made of text blocks is the joined text; a non-text block is a placeholder
    blocks = write(
        tmp_path,
        "omp",
        "p/b.jsonl",
        [
            {"message": {"content": [{"type": "toolCall", "arguments": {"path": "a.py"}}]}},
            {
                "message": {
                    "role": "toolResult",
                    "content": [
                        {"type": "text", "text": "body"},
                        {"type": "image", "data": "xx"},
                        {"type": "text", "text": "tail"},
                    ],
                }
            },
        ],
    )
    listed = read(qstore, ref("omp", "p/b.jsonl", 1, 2, "message.content.0"), blocks, "output")
    assert listed["text"] == "body\n[image]\ntail" and listed["total_bytes"] == len(listed["text"])
    assert (
        read(
            qstore,
            ref("omp", "p/b.jsonl", 1, 2, "message.content.0"),
            blocks,
            "output",
            offset=5,
            max_bytes=7,
        )["text"]
        == "[image]"
    )
    args = read(qstore, ref("omp", "p/b.jsonl", 1, 2, "message.content.0"), blocks, "input")
    assert json.loads(args["text"]) == {"path": "a.py"}


def test_omp_skill_prompt_text_is_details(qstore, tmp_path):
    roots = write(
        tmp_path,
        "omp",
        "p/s.jsonl",
        [
            {
                "type": "message",
                "message": {"role": "user", "content": [{"type": "text", "text": "profile it"}]},
            },
            {
                "type": "custom_message",
                "customType": "skill-prompt",
                "details": {"name": "mad:demo", "args": "x"},
            },
        ],
    )
    skill = read(qstore, ref("omp", "p/s.jsonl", 2), roots, "text")
    assert json.loads(skill["text"]) == {"name": "mad:demo", "args": "x"}
    ask = read(qstore, ref("omp", "p/s.jsonl", 1), roots, "text")
    assert ask["text"] == "profile it"


def test_codex_arguments_part_input_does_not_append_the_path(qstore, tmp_path):
    """The legacy resolver appended `input` to `payload.arguments`; the reference IS the input."""
    roots = write(
        tmp_path,
        "codex",
        "sessions/c.jsonl",
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "shell",
                    "arguments": '{"cmd": "make check"}',
                    "call_id": "k1",
                },
            },
            {
                "type": "response_item",
                "payload": {"type": "function_call_output", "call_id": "k1", "output": "all green"},
            },
            {
                "type": "response_item",
                "payload": {"type": "custom_tool_call", "input": "*** Begin Patch", "call_id": "k2"},
            },
            {
                "type": "response_item",
                "payload": {"type": "custom_tool_call_output", "output": "patched", "call_id": "k2"},
            },
        ],
    )
    call = ref("codex", "sessions/c.jsonl", 1, 2, "payload.arguments")
    assert read(qstore, call, roots, "input")["text"] == '{"cmd": "make check"}'
    assert read(qstore, call, roots, "output")["text"] == "all green"
    patch = ref("codex", "sessions/c.jsonl", 3, 4, "payload.input")
    assert read(qstore, patch, roots, "input")["text"] == "*** Begin Patch"
    assert read(qstore, patch, roots, "output")["text"] == "patched"


def test_claude_input_prefers_command_and_prompt_and_turn_text(qstore, tmp_path):
    """DM-7: the full command (over 160 characters), the agent prompt, and the turn ask."""
    long_cmd = "echo " + "x" * 2043
    roots = write(
        tmp_path,
        "claude_code",
        "p/c.jsonl",
        [
            {"type": "user", "message": {"role": "user", "content": "fix the parser"}},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": long_cmd}},
                        {
                            "type": "tool_use",
                            "id": "t2",
                            "name": "Task",
                            "input": {"prompt": "audit the cache", "x": 1},
                        },
                        {"type": "tool_use", "id": "t3", "name": "Read", "input": {"file_path": "/a/b.py"}},
                    ]
                },
            },
            {
                "type": "user",
                "toolUseResult": {"stdout": "ok"},
                "message": {"content": [{"type": "tool_result", "content": "ok"}]},
            },
        ],
    )
    cmd = read(qstore, ref("claude_code", "p/c.jsonl", 2, 3, "message.content.0"), roots, "input")
    assert cmd["text"] == long_cmd and cmd["total_bytes"] == 2048 > 160
    assert cmd["block"] == "tool_use"
    prompt = read(qstore, ref("claude_code", "p/c.jsonl", 2, 3, "message.content.1"), roots, "input")
    assert prompt["text"] == "audit the cache"
    read_in = read(qstore, ref("claude_code", "p/c.jsonl", 2, 3, "message.content.2"), roots, "input")
    assert json.loads(read_in["text"]) == {"file_path": "/a/b.py"}
    ask = read(qstore, ref("claude_code", "p/c.jsonl", 1), roots, "text")
    assert ask["text"] == "fix the parser"
    # `toolUseResult` is first for output (DM-7); the block it reports is the tool_result
    structured = read(qstore, ref("claude_code", "p/c.jsonl", 2, 3, "message.content.0"), roots, "output")
    assert json.loads(structured["text"]) == {"stdout": "ok"} and structured["block"] == "tool_result"


def test_codex_turn_ask_and_answer_text(qstore, qroots):
    with _conn(qstore) as conn:
        turn = conn.execute("SELECT * FROM ops WHERE op_id = ?", ("codex/scodex/turn/main#0",)).fetchone()
        answer = conn.execute(
            "SELECT s.* FROM steps s JOIN ops o ON o.rowid = s.op_rowid "
            "WHERE o.op_id = ? AND s.verb = 'answer'",
            (CODEX_CALL,),
        ).fetchone()
        ask_src, answer_src = src_uri(conn, turn), src_uri(conn, answer)
    assert read(qstore, ask_src, qroots, "text")["text"] == "explain why the cache misses spike after deploy"
    assert read(qstore, answer_src, qroots, "text")["text"] == "synthetic reply"


# ── references emitted by the real adapters (DM-7), read through `source` ─────────────────────


def test_omp_adapter_references_resolve(qstore, tmp_path):
    """The omp adapter's tool_call range, turn and skill-prompt references give input, output and text."""
    import test_adapter_omp as O

    cmd = "cd /proj && uv run pytest tests/x -q " + "y" * 300
    rows = [
        O.user(O.T - 2, content=[{"type": "text", "text": "profile the daemon"}]),
        O.assistant(O.T, O.T + 100, calls=[("b", "bash", {"command": cmd})], id="a"),
        O.start(O.T + 101, "b"),
        O.result(O.T + 108, "b", "bash"),
        O.rec(
            "custom_message",
            O.T + 200,
            customType="skill-prompt",
            details={"name": "team-lead", "path": "/s/SKILL.md", "args": "go"},
        ),
    ]
    rows[3]["message"]["content"] = [{"type": "text", "text": "6 passed"}]
    b = O.build(tmp_path, rows)
    roots = {"omp": tmp_path / "sessions"}
    (tool,) = O.kind(b, "tool_call")
    assert pointer.parse(tool.src).locator.last > pointer.parse(tool.src).locator.first
    assert read(qstore, tool.src, roots, "input")["text"] == cmd
    out = read(qstore, tool.src, roots, "output")
    assert out["text"] == "6 passed"
    turns = O.kind(b, "turn")
    ask = next(t for t in turns if t.turn_origin == "typed")
    assert read(qstore, ask.src, roots, "text")["text"] == "profile the daemon"
    skill = next(t for t in turns if t.turn_origin == "slash")
    got = json.loads(read(qstore, skill.src, roots, "text")["text"])
    assert (got["name"], got["args"]) == ("team-lead", "go")
    thinking = [s for s in b.steps if s.verb in ("think", "say", "answer")]
    assert thinking == [] or all(read(qstore, s.src, roots, "text") for s in thinking)


def test_codex_adapter_references_resolve(qstore, tmp_path):
    import test_adapter_codex as C

    from botsworth.pricing import Pricer

    lines = [
        C.meta(C.SID),
        C.L(0, {"model": "gpt-5"}, "turn_context"),
        C.L(1, {"type": "task_started", "turn_id": "t"}),
        C.L(2, {"type": "user_message", "message": "run the checks"}),
        C.L(
            3,
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "plan"}],
                "encrypted_content": "enc-x",
            },
            "response_item",
        ),
        C.call(3, "c1", {"cmd": "make check"}),
        C.L(4, {"type": "function_call_output", "call_id": "c1", "output": "all green"}, "response_item"),
        C.tc(5, C.u(100, 10), C.u(100, 10)),
        C.L(6, {"type": "agent_message", "message": "done"}),
    ]
    b = C.one(tmp_path, Pricer(C.FIXTURES / "F8" / "prices.json"), lines)
    roots = {"codex": tmp_path}
    (tool,) = C.kind(b, "tool_call")
    assert read(qstore, tool.src, roots, "input")["text"] == json.dumps({"cmd": "make check"})
    assert read(qstore, tool.src, roots, "output")["text"] == "all green"
    turn = C.kind(b, "turn")[0]
    assert read(qstore, turn.src, roots, "text")["text"] == "run the checks"
    (reason,) = [s for s in b.steps if s.verb == "think"]
    got = read(qstore, reason.src, roots, "thinking")
    assert pointer.parse(reason.src).json_path == "payload.summary"  # the adapter names the summary list
    assert got["text"] == "plan" and got["block"] == "reasoning" and "enc-x" not in json.dumps(got)
    assert "enc-x" not in json.dumps(read(qstore, reason.src, roots))  # no part: still the summary


def test_claude_adapter_fixtures_every_reference_resolves(qstore):
    """F1 to F7 and F9 (Claude Code fixtures): each tool_call reads input and output, each turn its ask, each
    think step its thinking and each say or answer step its text, through the references the adapter emits."""
    from test_adapter_claude import FIXTURES

    from botsworth.adapters.claude.adapter import build_bundle
    from botsworth.adapters.claude.discover import discover
    from botsworth.pricing import Pricer

    seen = {"input": 0, "output": 0, "text": 0, "thinking": 0}
    for name in ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F9"):
        root = FIXTURES / name
        roots = {"claude_code": root}
        for ref in discover(root):
            bundle, _ = build_bundle(ref, Pricer(root / "prices.json"))
            wanted = [(o.src, p) for o in bundle.ops if o.kind == "tool_call" for p in ("input", "output")]
            wanted += [
                (o.src, "text") for o in bundle.ops if o.kind == "turn" and o.parent == bundle.ops[0].op_id
            ]
            for s in bundle.steps:
                if s.verb == "think":
                    wanted.append((s.src, "thinking"))
                elif s.verb in ("say", "answer"):
                    wanted.append((s.src, "text"))
            for src, part in wanted:
                got = read(qstore, src, roots, part)
                assert got["total_bytes"] >= 0 and got["locator"], (name, src, part)
                seen[part] += 1
    assert all(n > 0 for n in seen.values()), seen


def test_claude_block_lists_are_joined(qstore, tmp_path):
    """A tool_result whose content is a list of blocks, and a user message made of blocks (DM-7)."""
    roots = write(
        tmp_path,
        "claude_code",
        "p/l.jsonl",
        [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "first"},
                        {"type": "image", "source": {"type": "base64", "data": "xx"}},
                        {"type": "text", "text": "second"},
                    ],
                },
            },
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t", "name": "Read"}]}},
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "content": [
                                {"type": "text", "text": "line a"},
                                {"type": "text", "text": "line b"},
                            ],
                        }
                    ]
                },
            },
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "plain"}]}},
        ],
    )
    ask = read(qstore, ref("claude_code", "p/l.jsonl", 1), roots, "text")
    assert ask["text"] == "first\n[image]\nsecond"  # not just the first block
    out = read(qstore, ref("claude_code", "p/l.jsonl", 2, 3, "message.content.0"), roots, "output")
    assert (out["text"], out["block"]) == ("line a\nline b", "tool_result")
    plain = read(qstore, ref("claude_code", "p/l.jsonl", 4), roots, "output")
    assert plain["text"] == "plain"  # a string stays a string
