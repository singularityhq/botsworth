"""A second fixture store for Q2c, built with the QF generator's session builder.

The generator is `tests/fixtures/qstore/make.py`. The QF store lacks four things: a Bash call whose source
holds a command over 160 characters, a tool outcome `exit:n` with no `exit_code`, a thinking block in a call,
and an agent with more than five calls. The session `sq2c` here has them. `make.py` is loaded by path and
pointed at `tmp_path`; nothing under tests/fixtures is written. The store is ingested by
`store.ingest.ingest`, as QF's is.

`q2cstore` is `(db path, provider roots)`. Ids are named in this module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from stream_load import load_streams

MAKE = Path(__file__).resolve().parents[1] / "fixtures" / "qstore" / "make.py"
SID = "sq2c"
SESSION_KEY = f"claude_code/{SID}/session/-"
_HEAD = "echo " + "x" * 1000 + " ENDMARK-D42 "
LONG_COMMAND = _HEAD + "y" * (2048 - len(_HEAD))  # 2,048 characters; the store keeps 160
END_MARK = "ENDMARK-D42"  # after the first 160 characters: only the source file holds it
SPAWN_PROMPT = "audit every migration and report the risky ones"


def op(kind: str, key: str) -> str:
    return f"claude_code/{SID}/{kind}/{key}"


TURN = op("turn", "main#0")
TURN_2 = op("turn", "main#1")
TURN_HARNESS = op("turn", "main#2")  # an interrupt turn that still has a call and a failed tool
HARNESS_CALL = op("llm_call", "req_hx")
HARNESS_TOOL = op("tool_call", "toolu_hx")
CALL_1 = op("llm_call", "req_q1")
TOOL_LONG = op("tool_call", "toolu_long")
TOOL_EXIT = op("tool_call", "toolu_exit")  # step outcome exit:2, exit_code NULL
TOOL_OK = op("tool_call", "toolu_ok")
SPAWN = op("tool_call", "toolu_qspawn")
ENGAGEMENT = op("engagement", "rz#0")
AGENT_TOOL_EXIT = op("tool_call", "toolu_zexit")  # inside the agent: outcome exit:3, exit_code NULL
AGENT_TOOL_ERR = op("tool_call", "toolu_zerr")
AGENT_COSTS = [0.50, 0.40, 0.30, 0.20, 0.10, 0.05, None]  # seven calls, the last one unpriced
AGENT_CALLS = [op("llm_call", f"req_z{i}") for i in range(len(AGENT_COSTS))]
THINK_TEXT = "weigh the options before running anything"


def _load_make():
    spec = importlib.util.spec_from_file_location("qf_make_q2c", MAKE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def build(base: Path) -> tuple[Path, dict[str, Path]]:
    """Write `sq2c` to `base/streams` and `base/src`, ingest it into `base/botsworth.sqlite`."""
    m = _load_make()
    m.STREAMS, m.SRC = base / "streams", base / "src"
    m.STREAMS.mkdir(parents=True)
    at = m.at
    s = m.Sess(SID, "claude_code", "/u/q/q2cproj", at(11, 3))
    t1 = s.turn("run the checks and audit the migrations", at(11, 3), at(11, 3, 5), "human", "typed")
    c1 = s.llm(t1, "req_q1", at(11, 3, 0, 5), at(11, 3, 0, 25), 0.40, text=None, stop="tool_use")
    f, line, content, _, _ = s.llm_info[c1]
    content.insert(0, {"type": "thinking", "thinking": THINK_TEXT})
    s.step(
        t1,
        c1,
        "think",
        m.Ref(f, line, "message.content.0"),
        summary="Thinking",
        snippet=THINK_TEXT,
        start_ms=at(11, 3, 0, 5),
        dur_ms=0,
    )
    s.tool(
        c1, "toolu_long", "Bash", at(11, 3, 0, 25), at(11, 3, 0, 40), "run", target="echo",
        command=LONG_COMMAND, command_key="echo", exit_code=0, result="done",
        input_={"command": LONG_COMMAND},
    )  # fmt: skip
    s.tool(
        c1, "toolu_exit", "Bash", at(11, 3, 0, 25), at(11, 3, 0, 30), "run", target="false",
        command="false", command_key="false", exit_code=None, outcome="exit:2", result="boom",
        input_={"command": "false"},
    )  # fmt: skip
    s.tool(
        c1, "toolu_ok", "Read", at(11, 3, 0, 25), at(11, 3, 0, 26), "read", target="notes.md",
        file_path="/u/q/q2cproj/notes.md", result="fine", input_={"file_path": "/u/q/q2cproj/notes.md"},
    )  # fmt: skip
    spawn = s.tool(
        c1, "toolu_qspawn", "Agent", at(11, 3, 0, 25), at(11, 3, 0, 27), "spawn", target="auditor",
        is_async=True, input_={"prompt": SPAWN_PROMPT, "description": "audit"},
    )  # fmt: skip
    eng, agent_turn = s.engagement(
        "rz", 0, at(11, 3, 1), at(11, 3, 4), parent=spawn, caused_by=spawn, basis="id",
        agent_type="auditor", arrival=SPAWN_PROMPT,
    )  # fmt: skip
    last = None
    for i, cost in enumerate(AGENT_COSTS):
        last = s.llm(
            agent_turn, f"req_z{i}", at(11, 3, 1, 8 * i), at(11, 3, 1, 8 * i + 6), cost,
            text=None if i == 0 else "synthetic reply", stop="tool_use" if i == 0 else "end_turn",
        )  # fmt: skip
        if i == 0:
            first = last
    s.tool(
        first, "toolu_zexit", "Bash", at(11, 3, 1, 6), at(11, 3, 1, 7), "run", target="make check",
        command="make check", command_key="make check", exit_code=None, outcome="exit:3", result="failed",
        input_={"command": "make check"},
    )  # fmt: skip
    s.tool(
        first, "toolu_zerr", "Edit", at(11, 3, 1, 6), at(11, 3, 1, 7), "edit", target="a.py",
        is_error=True, outcome="error", result="no match", input_={"target": "a.py"},
    )  # fmt: skip
    s.llm(t1, "req_q2", at(11, 3, 4, 10), at(11, 3, 4, 20), 0.05, text="all checked")
    t2 = s.turn("thanks", at(11, 3, 5), at(11, 3, 5, 30), "human", "typed")
    s.llm(t2, "req_q3", at(11, 3, 5, 5), at(11, 3, 5, 25), 0.01, text="you are welcome")
    hx = s.turn("[Request interrupted by user]", at(11, 3, 6), at(11, 3, 6, 30), "harness", "interrupt")
    hc = s.llm(hx, "req_hx", at(11, 3, 6, 2), at(11, 3, 6, 10), 0.02, text=None, stop="tool_use")
    s.tool(
        hc, "toolu_hx", "Bash", at(11, 3, 6, 10), at(11, 3, 6, 12), "run", target="harness side task",
        command="harness side task", command_key="harness side task", exit_code=9, outcome="exit:9",
        result="bad", input_={"command": "harness side task"},
    )  # fmt: skip
    s.finish(at(11, 3, 6, 30))
    assert (eng, agent_turn) == (ENGAGEMENT, agent_turn)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("BOTSWORTH_TZ", "Asia/Kolkata")
        result = load_streams(streams=[m.STREAMS], db=base / "botsworth.sqlite")
    assert result.rejected == [], result.rejected
    return base / "botsworth.sqlite", {"claude_code": m.SRC / "claude_code"}


@pytest.fixture(scope="session")
def q2cstore(tmp_path_factory) -> tuple[Path, dict[str, Path]]:
    return build(tmp_path_factory.mktemp("q2cstore"))
