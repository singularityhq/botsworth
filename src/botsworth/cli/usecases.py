"""CONTRACT: the use-case commands `slow`, `breakdown` and `tasks` (interfaces.md §5.2, CLI-20 to CLI-23).

GUARANTEES:
- `register(sub)` adds the three subcommands to the `botsworth` parser. Each is argument parsing plus one
  `botsworth.query` call (`project_slow`, `session_breakdown`, `top_tasks`); the scope comes from
  `cli.scope.to_scope`, the same flags and `--since` rule as the other read commands (CLI-5).
- Output is the CLI-2 output: one line of JSON, `asdict` of the function's result plus the time siblings of
  `cli.timefmt` (`--json` changes nothing);
  `--table` prints the table of `cli.render`.
- `--help` of each command ends with an epilog (at most 25 lines) that defines the fields of its JSON output
  from interfaces.md §5.2, DM-40, DM-41, DM-51 and DM-52 (D55); it names every top-level field of the
  output (D49)
  and, in one line, the time siblings (D50).
- `--by` and `--measure` take a fixed list; a value outside it is an argparse error (exit 2, CLI-4). `-n`
  outside the range of the view is a `QueryError` (exit 1).
EXPECTS: the parser built by `cli.main.build_parser`, which calls `register(sub)` and runs the `handler`
  default each subcommand sets (`cli.main.run`).
FAILURE BEHAVIOR: as `cli.main.main` (exit codes of CLI-4).
DOES NOT: contain query logic or SQL, or open the store.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from botsworth.cli.scope import cli_zone, resolve_projects, scope_args, to_scope, tz_arg
from botsworth.query.views.usecases import BY, MEASURES

_TIME_FIELDS = """\
time_split: parts {state, substate, ms}; an instant of the wall is in the first
  part that matches: model (main model call), tool (main tool call; tool/test,
  tool/wait if it runs tests or sleeps), agents (agent call or background
  command; agents/test, agents/wait), tool/background (main background command),
  you/ask or you/permission (a call waits for you); else nothing runs:
  none/stalled (a finished task waits for an agent that is not running), else
  the next turn decides: you/reply, limit, agentmsg, none/before_first,
  none/none. Parts sum to wall_ms.
review: causes {cause, ms} sum to wall_ms: stalled (none/stalled; lost_ms),
  tests (*/test), polling (*/wait), you, message (agentmsg), limit, working
  (other model, tool, agents time), idle (none/*). findings: causes of 2 min+;
  n counts stretches (stalled, you, message) or calls (tests, polling). tests is
  wall time held by tests; test_runs.run_ms sums runs, which can overlap.
time_prov: derived = result minus call time, may include a wait; n_no_records: 5 min.
user_waits: calls that waited on you, {n, dur_ms} for ask (AskUserQuestion,
  request_user_input, ask) and permission (Claude calls you approved or rejected;
  older Claude, Codex and omp leave no record). They are not in tools.
test_runs: {n, run_ms, n_failed, n_passed, n_no_result, n_background} of calls
  with command_class test; a background run lasts until its notice.
waits: waits of 2 min+, longest first: stretches (row null) and main calls that
  waited on you. longest_wait: longest not agents or tool/background.
"""
_TIME_FORMAT = """\
time: tz is the zone used (--tz). Each <x>_ms timestamp has an ISO sibling <x>; each duration has <x>_ms_h
  (45s, 12m 05s, 2h 15m, 3d 4h)."""

SLOW_EPILOG = (
    """\
output fields: freshness, current, previous (prior period or null), sessions,
  tools, slowest_tools, user_waits, test_runs, waits, models, review
"""
    + _TIME_FIELDS
    + _TIME_FORMAT
)

BREAKDOWN_EPILOG = (
    """\
output fields: freshness, session, turns_by_time, turns_by_tokens, rows_by_time,
  rows_by_cost, rows_by_tokens, tools, agents, waits, test_runs, models, review
"""
    + _TIME_FIELDS
    + _TIME_FORMAT
)

TASKS_EPILOG = (
    """\
output fields: freshness, by, measure, basis, scope_totals, unassigned,
  total_groups (all groups), groups (the first n)
basis: how tokens and cost reach a group.
  issuing_call_split  by tool, command_key, command_class, verb: the tokens of
                      the call that issued the tool calls, divided evenly
  turn_split          by skill: the turn's tokens, divided among its skills
  nearest_engagement  by agent_type: calls under the nearest agent run;
                      "(main)" is calls under no agent, "(none)" has no type
  turn_attr           by slash: the tokens of the turn the command started
unassigned: part of scope_totals that is in no group. It is 0 for
  issuing_call_split, where a call that issued no tool is the group "no tool".
Token figures are fractional because a call's tokens are divided evenly.
share: the group's measure divided by scope_totals, or null.
--by: command_key = tool calls that have a command_key; command_class = test
  or wait shell calls; tool = tool calls by tool name; verb = steps of tool
  calls, by steps.verb; skill = skill loads; agent_type = agent runs
  (engagements); slash = slash-command and builtin turns.
"""
    + _TIME_FORMAT
)


Handler = Callable[[argparse.Namespace, Any, Any], Any]


def _scope(args: argparse.Namespace, api: Any, db: Any) -> Any:
    resolve_projects(args, api, db)
    return to_scope(args, cli_zone(args))


def _slow(args: argparse.Namespace, api: Any, db: Any) -> Any:
    return api.project_slow(_scope(args, api, db), args.compare, args.n, db=db)


def _breakdown(args: argparse.Namespace, api: Any, db: Any) -> Any:
    return api.session_breakdown(args.session, args.n, db=db)


def _tasks(args: argparse.Namespace, api: Any, db: Any) -> Any:
    return api.top_tasks(_scope(args, api, db), args.by, args.measure, args.n, db=db)


def register(sub: Any) -> None:
    epilogs = {"slow": SLOW_EPILOG, "breakdown": BREAKDOWN_EPILOG, "tasks": TASKS_EPILOG}

    def cmd(name: str, help: str, handler: Handler) -> argparse.ArgumentParser:
        p = sub.add_parser(
            name, help=help, epilog=epilogs[name], formatter_class=argparse.RawDescriptionHelpFormatter
        )
        out = p.add_mutually_exclusive_group()
        out.add_argument("--json", dest="fmt", action="store_const", const="json", help="the default")
        out.add_argument("--table", dest="fmt", action="store_const", const="table")
        p.set_defaults(fmt="json", handler=handler)
        return p

    p = cmd("slow", "where the time of the in-scope sessions went", _slow)
    scope_args(p)
    p.add_argument("--compare", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("-n", type=int, default=10, help="rows per list, 1 to 100")

    p = cmd("breakdown", "the largest turns, rows, tools and agents of one session", _breakdown)
    p.add_argument("session", metavar="SESSION")
    p.add_argument("-n", type=int, default=10, help="rows per list, 1 to 100")
    tz_arg(p)

    p = cmd("tasks", "tokens, cost and time grouped by a task key", _tasks)
    scope_args(p)
    p.add_argument("--by", required=True, choices=BY)
    p.add_argument("--measure", choices=MEASURES, default="tokens")
    p.add_argument("-n", type=int, default=20, help="groups, 1 to 500")
