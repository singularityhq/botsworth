# ruff: noqa: F811
"""Q3b: the `slow`, `breakdown` and `tasks` commands (CLI-20 to CLI-23, TST-7) on the QF store."""

from __future__ import annotations

import importlib
import json
from dataclasses import asdict

import pytest
from cli_plain import plain_top
from qstore import qstore  # noqa: F401 (the QF fixture)
from qstore_ids import SLOW_PROJECT

from botsworth.cli import main as cli
from botsworth.cli.render import TABLE_BYTES
from botsworth.query.scope import Scope
from botsworth.query.views import usecases as U

# `import botsworth.query` yields the function: botsworth/__init__ rebinds the name.
Q = importlib.import_module("botsworth.query")
SESSION = "claude_code/sagents/session/-"


def run(capsys, db, *argv: str) -> tuple[int, str, str]:
    code = cli.main(["--db", str(db), *argv], api=Q)
    out = capsys.readouterr()
    return code, out.out, out.err


def as_json(result) -> object:
    return json.loads(json.dumps(asdict(result)))


def test_json_equals_asdict_slow(qstore, capsys):
    code, out, err = run(
        capsys, qstore, "slow", "--project", SLOW_PROJECT, "--from", "2026-10-07", "--to", "2026-10-07"
    )
    assert (code, err) == (0, "")
    assert out.count("\n") == 1  # one line (CLI-2)
    scope = Scope(projects=(SLOW_PROJECT,), from_day="2026-10-07", to_day="2026-10-07")
    assert plain_top(json.loads(out)) == as_json(U.project_slow(scope, True, 10, db=qstore))
    code, out, _ = run(
        capsys,
        qstore,
        "slow",
        "--project",
        SLOW_PROJECT,
        "--from",
        "2026-10-07",
        "--to",
        "2026-10-07",
        "--no-compare",
        "-n",
        "1",
    )
    assert code == 0
    body = plain_top(json.loads(out))
    assert body["previous"] is None and len(body["tools"]) == 1
    assert body == as_json(U.project_slow(scope, False, 1, db=qstore))


def test_json_equals_asdict_breakdown(qstore, capsys):
    code, out, err = run(capsys, qstore, "breakdown", SESSION, "-n", "3")
    assert (code, err) == (0, "")
    assert plain_top(json.loads(out)) == as_json(U.session_breakdown(SESSION, 3, db=qstore))


def test_json_equals_asdict_tasks(qstore, capsys):
    code, out, err = run(capsys, qstore, "tasks", "--by", "command_key", "--measure", "count", "-n", "5")
    assert (code, err) == (0, "")
    assert plain_top(json.loads(out)) == as_json(U.top_tasks(Scope(), "command_key", "count", 5, db=qstore))
    code, out, _ = run(capsys, qstore, "tasks", "--by", "tool", "--json")  # --json changes nothing
    assert plain_top(json.loads(out)) == as_json(U.top_tasks(Scope(), "tool", "tokens", 20, db=qstore))


def test_tasks_since_uses_the_cli_zone(qstore, capsys):
    code, out, err = run(capsys, qstore, "tasks", "--by", "skill", "--since", "1d")
    assert (code, err) == (0, "")  # the fixture is old: today has no sessions
    body = json.loads(out)
    assert body["groups"] == [] and body["total_groups"] == 0 and body["basis"] == "turn_split"


@pytest.mark.parametrize(
    "argv",
    [
        ["tasks", "--by", "bogus"],
        ["tasks"],  # --by is required
        ["tasks", "--by", "tool", "--measure", "words"],
    ],
)
def test_tasks_bad_by_exit_2(qstore, capsys, argv):
    with pytest.raises(SystemExit) as e:  # argparse exits 2 itself
        cli.main(["--db", str(qstore), *argv], api=Q)
    assert e.value.code == 2
    assert "error" in capsys.readouterr().err


def test_flag_combination_is_exit_2(qstore, capsys):
    for argv in (
        ["slow", "--from", "2026-10-07"],  # --from needs --to (CLI-5)
        ["tasks", "--by", "tool", "--since", "2d", "--from", "2026-10-07", "--to", "2026-10-07"],
    ):
        code, out, err = run(capsys, qstore, *argv)
        assert (code, out) == (2, "")
        assert err.startswith("error:")


def test_breakdown_unknown_session_exit_1(qstore, capsys):
    code, out, err = run(capsys, qstore, "breakdown", "claude_code/nosuch/session/-")
    assert (code, out) == (1, "")
    assert err.startswith("error:") and err.count("\n") == 1


def test_n_out_of_range_exit_1(qstore, capsys):
    for argv in (
        ["slow", "-n", "101"],
        ["breakdown", SESSION, "-n", "0"],
        ["tasks", "--by", "tool", "-n", "501"],
    ):
        code, out, err = run(capsys, qstore, *argv)
        assert (code, out) == (1, "") and err.startswith("error:")


def test_no_store_is_exit_2(tmp_path, capsys):
    code, _, err = run(capsys, tmp_path / "none.sqlite", "tasks", "--by", "tool")
    assert code == 2 and "no store" in err


def test_output_under_64kb_n10(qstore, capsys):
    """CLI-23 on the fixture: every command at -n 10 prints under 64 KB."""
    for argv in (
        ["slow", "-n", "10"],
        ["slow", "--automated", "-n", "10"],
        ["breakdown", SESSION, "-n", "10"],
        ["tasks", "--by", "agent_type", "-n", "10"],
    ):
        code, out, _ = run(capsys, qstore, *argv)
        assert code == 0
        assert len(out.encode()) < 64 * 1024, argv


@pytest.mark.parametrize(
    "argv", [["slow"], ["breakdown", SESSION], ["tasks", "--by", "command_key"]], ids=lambda a: a[0]
)
def test_table_output(qstore, capsys, argv):
    code, out, _ = run(capsys, qstore, *argv, "--table")
    assert code == 0
    assert len(out.encode()) <= TABLE_BYTES
    assert not out.startswith("{") and ":" in out


# ── D49: --project resolution and --help epilogs ──────────────────────────────

BIZ_KEY, BIZ_NAME = "/u/a/bizdemo/src", "bizdemo/src"
PERIOD = ["--from", "2026-10-01", "--to", "2026-10-31"]


@pytest.mark.parametrize(
    "value", [BIZ_KEY, BIZ_NAME, "BizDemo/SRC", "a/bizdemo/src", "slowproj/app", "HELPER"], ids=str
)
def test_project_value_resolves_to_its_key(qstore, capsys, value):
    key = Q.resolve_project(value, db=qstore)
    code, out, err = run(capsys, qstore, "slow", "--project", value, *PERIOD)
    assert (code, err) == (0, "")
    code2, out2, _ = run(capsys, qstore, "slow", "--project", key, *PERIOD)
    assert code2 == 0 and out == out2


def test_resolution_order_and_keys(qstore):
    assert Q.resolve_project(BIZ_KEY, db=qstore) == BIZ_KEY
    assert Q.resolve_project("BIZDEMO/Src", db=qstore) == BIZ_KEY  # name, case-insensitive
    assert Q.resolve_project("bizdemo/src", db=qstore) == BIZ_KEY
    assert Q.resolve_project("slowproj/app", db=qstore) == "/u/h/slowproj/app"  # key suffix
    assert Q.resolve_project("app", db=qstore) == "/u/h/slowproj/app"  # name wins over suffix


def test_project_ambiguous_exits_1_with_candidates(qstore, capsys):
    code, out, err = run(capsys, qstore, "slow", "--project", "src", *PERIOD)
    assert (code, out) == (1, "")
    assert "ambiguous" in err
    assert f"{BIZ_KEY} ({BIZ_NAME})" in err and "/u/b/demolib/src (demolib/src)" in err


def test_project_unknown_exits_1_with_substring_candidates(qstore, capsys):
    code, out, err = run(capsys, qstore, "tasks", "--by", "tool", "--project", "bizdemo", *PERIOD)
    assert (code, out) == (1, "")
    assert "unknown" in err and f"{BIZ_KEY} ({BIZ_NAME})" in err
    code, _, err = run(capsys, qstore, "slow", "--project", "no-such-thing")
    assert code == 1 and "no project key or name contains it" in err


def test_project_candidates_are_capped_at_five(qstore, capsys):
    code, _, err = run(capsys, qstore, "slow", "--project", "/u/")  # unknown; all ten keys contain it
    assert code == 1
    assert err.count(" (") == 5 and "and 5 more" in err


def test_project_resolved_in_other_scope_commands(qstore, capsys):
    code, out, _ = run(capsys, qstore, "period", "--project", BIZ_NAME, *PERIOD)
    code2, out2, _ = run(capsys, qstore, "period", "--project", BIZ_KEY, *PERIOD)
    assert code == code2 == 0 and out == out2
    code, _, err = run(capsys, qstore, "period", "--project", "nope")
    assert code == 1 and "unknown" in err


def _fields(cls) -> list[str]:
    import dataclasses

    return [f.name for f in dataclasses.fields(cls)]


@pytest.mark.parametrize(
    ("cmd", "argv", "wire_cls"),
    [
        ("slow", [], "SlowReport"),
        ("breakdown", ["S"], "SessionBreakdown"),
        ("tasks", ["--by", "tool"], "TaskGroups"),
    ],
)
def test_help_epilog_names_every_output_field(capsys, cmd, argv, wire_cls):
    import re

    from botsworth.query import wire

    with pytest.raises(SystemExit) as e:
        cli.main([cmd, "--help"], api=Q)
    assert e.value.code == 0
    text = capsys.readouterr().out
    epilog = text[text.index("output fields") :]
    assert len(epilog.splitlines()) <= 25
    for name in _fields(getattr(wire, wire_cls)):
        assert re.search(rf"\b{name}\b", epilog), f"{cmd} --help epilog omits {name}"


def test_help_epilogs_define_the_unclear_terms(capsys):
    texts = {}
    for cmd in ("slow", "tasks"):
        with pytest.raises(SystemExit):
            cli.main([cmd, "--help"], api=Q)
        texts[cmd] = capsys.readouterr().out
    for term in ("model_ms", "tool_ms", "agents_ms", "none_ms", "you", "agentmsg", "limit", "before_first"):
        assert term in texts["slow"]
    assert "derived" in texts["slow"] and "waits_on_user" in texts["slow"]
    for basis in ("issuing_call_split", "turn_split", "nearest_engagement", "turn_attr"):
        assert basis in texts["tasks"]
    assert "unassigned" in texts["tasks"] and "fractional" in texts["tasks"]
