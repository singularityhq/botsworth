"""DM-28 effect: the verb table, the shell rules, the call-level set, and the per-provider counts on E1."""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

import pytest
from test_adapter_claude import assistant, build, ops, result, tool, user
from test_d56_timesplit import _row

from botsworth import pipeline
from botsworth.adapters.common import taxonomy
from botsworth.adapters.common.commands import shell_effect
from botsworth.model import Effect, Verb, join_effects
from botsworth.pricing import Pricer
from botsworth.store import timesplit

FIXTURES = Path(__file__).parents[1] / "fixtures"
E1 = FIXTURES / "E1"
PROVIDERS = {"claude_code": "claude", "codex": "codex", "omp": "omp"}


# ---- the verb -> effect mapping -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verb, effect",
    [
        (Verb.READ, "look"),
        (Verb.SEARCH, "look"),
        (Verb.FETCH, "look"),
        (Verb.SKILL, "look"),
        (Verb.WRITE, "change"),
        (Verb.EDIT, "change"),
        (Verb.SPAWN, "run"),
        (Verb.ASK_USER, "message"),
        (Verb.ANSWER, "message"),
        (Verb.MESSAGE, "message"),
        (Verb.WAIT, "wait"),
        (Verb.OTHER, "unknown"),
    ],
)
def test_effect_of_maps_each_non_shell_verb(verb, effect):
    assert taxonomy.effect_of(verb, None) == effect


@pytest.mark.parametrize("verb", [Verb.THINK, Verb.SAY])
def test_effect_of_asserts_on_a_verb_that_is_no_tool_call(verb):
    with pytest.raises(AssertionError):
        taxonomy.effect_of(verb, None)


@pytest.mark.parametrize(
    "provider, tool_name, verb",
    [
        ("claude_code", "SendMessage", Verb.MESSAGE),
        ("claude_code", "ScheduleWakeup", Verb.WAIT),
        ("claude_code", "Monitor", Verb.WAIT),
        ("codex", "send_input", Verb.MESSAGE),
        ("codex", "wait_agent", Verb.WAIT),
        ("omp", "wait", Verb.WAIT),
    ],
)
def test_provider_native_message_and_wait_tools_have_their_own_verbs(provider, tool_name, verb):
    assert taxonomy.verb_of(provider, tool_name) == verb


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_a_tool_the_table_does_not_name_is_unknown(provider):
    verb = taxonomy.verb_of(provider, "mcp__some_server__lookup")
    assert verb == Verb.OTHER
    assert taxonomy.effect_of(verb, None) == "unknown"


# ---- shell commands -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command, expected",
    [
        ("cat a.md", "look"),
        ("cd /x/y && ls -la | wc -l", "look"),
        ("sed -n 5,30p file.py", "look"),
        ("sed -i s/a/b/ file.py", "change"),
        ("grep -rn foo src/ 2>/dev/null", "look"),
        ("git log --oneline | head -5", "look"),
        ("git status --short", "look"),
        ("git commit -m x", "change"),
        ("git bisect start", "run"),
        ("sqlite3 db.sqlite 'select count(*) from t'", "look"),
        ("sqlite3 db.sqlite 'delete from t'", "change"),
        ("sqlite3 db.sqlite", "run"),
        ("find . -name '*.py' -delete", "run"),
        ("find . -name '*.py'", "look"),
        ("echo hi > out.txt", "change"),
        ("echo hi >> out.txt", "change"),
        ("echo hi > /dev/null", "look"),
        ("echo hi 2>&1", "look"),
        ("cat > f.py <<'E'\nx=1\nE", "change"),
        ("FOO=1 uv run script.py", "run"),
        ("cd /tmp; ls; make build", "run"),
        ("rm -rf build && ls", "change"),
        ("ls && grep x f && make build", "run"),
        ("cd /a && export X=1 && set -e", "look"),
        ("cat <<E\nrm -rf /\nE", "look"),
        ("bash -lc 'cd /r && rm x'", "change"),
    ],
)
def test_shell_effect_rules(command, expected):
    assert shell_effect(command) == expected


def test_shell_effect_of_an_argv_list_unwraps_the_shell():
    assert shell_effect(["bash", "-lc", "cd /r && git commit -m x"]) == "change"


@pytest.mark.parametrize(
    "command, expected",
    [
        ("pytest -q && rm -rf build", "test"),
        ("rm x; uv run pytest", "test"),
        ("sleep 5; rm x", "wait"),
        ("until ls f; do sleep 1; done; rm f", "wait"),
        ("ls | wc -l", "look"),
    ],
)
def test_a_test_or_wait_command_decides_before_the_segments(command, expected):
    assert taxonomy.effect_of(Verb.RUN, command) == expected


def test_a_shell_run_with_no_command_text_is_run():
    assert taxonomy.effect_of(Verb.RUN, None) == "run"


# ---- the call-level set ---------------------------------------------------------------------------------


def test_join_effects_sorts_and_dedups():
    assert join_effects(["look"]) == "look"
    assert join_effects(["run", "look", "look", "test"]) == "look+run+test"


def test_join_effects_of_nothing_is_none():
    assert join_effects([]) is None


def test_join_effects_asserts_on_an_unknown_effect():
    with pytest.raises(AssertionError):
        join_effects(["look", "lint"])


# ---- adapters -------------------------------------------------------------------------------------------


def test_claude_adapter_sets_an_effect_on_every_tool_call(tmp_path):
    uses = [
        tool("a", "Read", file_path="/repo/a"),
        tool("b", "Edit", file_path="/repo/a"),
        tool("c", "Bash", command="make build"),
        tool("d", "SendMessage", to="x"),
        tool("e", "Monitor"),
        tool("f", "mcp__srv__lookup"),
    ]
    rows = [user("u"), assistant("r1", 1, stop="tool_use", content=uses)]
    rows += [result(t["id"], 2, content="ok") for t in uses]
    got = {t.tool_name: t.effect for t in ops(build(tmp_path, rows), "tool_call")}
    assert got == {
        "Read": "look",
        "Edit": "change",
        "Bash": "run",
        "SendMessage": "message",
        "Monitor": "wait",
        "mcp__srv__lookup": "unknown",
    }


# ---- E1: the synthetic fixtures through the real pipeline ----------------------------------------------


@pytest.fixture(scope="module")
def e1_db(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("e1") / "s.db"
    roots = {name: E1 / sub for name, sub in PROVIDERS.items()}
    report = pipeline.run(list(PROVIDERS), roots, db=db, pricer=Pricer(E1 / "prices.json"))
    assert all(p["rejected"] == [] and p["rebuilt"] == 1 for p in report["providers"].values())
    return db


def _effects(db: Path, provider: str, kind: str) -> Counter:
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT o.effect FROM ops o JOIN sessions s ON s.op_rowid = o.root_rowid WHERE s.provider = ?"
            " AND o.kind = ?",
            (provider, kind),
        )
        return Counter(e for (e,) in rows)
    finally:
        conn.close()


TOOL_COUNTS = {"look": 4, "change": 2, "run": 1, "test": 1, "wait": 2, "message": 1, "unknown": 1}
CALL_COUNTS = {"look": 1, "change+look": 2, "run+test": 1, "wait": 1, "message+unknown": 1, None: 1}


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_effect_counts_per_provider_on_synthetic_fixtures(e1_db, provider):
    assert _effects(e1_db, provider, "tool_call") == TOOL_COUNTS
    assert _effects(e1_db, provider, "llm_call") == CALL_COUNTS


def test_a_call_is_the_set_of_its_tool_calls_effects(e1_db):
    conn = sqlite3.connect(e1_db)
    try:
        for provider in PROVIDERS:
            rows = conn.execute(
                "SELECT c.effect, group_concat(DISTINCT t.effect) FROM ops c JOIN sessions s ON"
                " s.op_rowid = c.root_rowid LEFT JOIN ops t ON t.parent_rowid = c.rowid AND t.kind ="
                " 'tool_call' WHERE s.provider = ? AND c.kind = 'llm_call' GROUP BY c.rowid",
                (provider,),
            ).fetchall()
            assert len(rows) == 7
            for call, kids in rows:
                assert call == ("+".join(sorted(kids.split(","))) if kids else None)
    finally:
        conn.close()


def test_a_duplicate_call_carries_the_effect_of_its_owner(tmp_path):
    root = tmp_path / "claude"
    shutil.copytree(E1 / "claude", root)
    folder = root / "project"
    lines = [json.loads(line) for line in (folder / "s1.jsonl").read_text().splitlines()]
    # a copy that lost the second call's Bash use: its own tool calls would give `change`; the owner's give
    # `change+look`
    (second,) = [r for r in lines if r.get("requestId") == "r2"]
    uses = second["message"]["content"]
    assert [u["name"] for u in uses] == ["Edit", "Bash"]
    second["message"]["content"] = uses[:1]
    (folder / "s2.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))
    db = tmp_path / "s.db"
    pipeline.run(["claude_code"], {"claude_code": root}, db=db, pricer=Pricer(E1 / "prices.json"))
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT d.effect, o.effect, o.root_rowid = (SELECT op_rowid FROM sessions"
            " WHERE session_id = 's1') FROM ops d JOIN ops o ON o.op_id = d.duplicate_of"
            " WHERE d.kind = 'llm_call'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 7
    assert all(dup == owner and in_s1 for dup, owner, in_s1 in rows)
    assert Counter(owner for _, owner, _ in rows) == Counter(CALL_COUNTS)


# ---- timesplit ------------------------------------------------------------------------------------------


def test_timesplit_reads_the_effect_of_tool_calls_only():
    m = 60_000
    rows = [
        _row("session", rowid=1, start_ms=0, end_ms=10 * m),
        _row("llm_call", rowid=2, start_ms=0, end_ms=m, effect="look+test"),
        _row("tool_call", rowid=3, start_ms=m, end_ms=2 * m, effect="test"),
        _row("tool_call", rowid=4, start_ms=2 * m, end_ms=3 * m, effect="change"),
    ]
    c = timesplit.collect(rows).classes
    assert c.main_test == [(m, 2 * m)]
    assert c.main_wait == []
    assert timesplit.collect(rows).main_tool == [(m, 3 * m)]


def test_effect_is_a_closed_set():
    assert {e.value for e in Effect} == {"look", "change", "run", "test", "wait", "message", "unknown"}
