"""CONTRACT: the `botsworth` CLI (interfaces.md §5, design §6.2, D3, D5).

GUARANTEES:
- Every read subcommand is argument parsing plus one `botsworth.query` call; `ingest` is one
  `pipeline.run` call; `serve` is `cli.serve.serve`. The v4 read commands (status,
  period, days, sessions, facets, search, session, turns, turn, detail, find) build their arguments by
  running the matching HTTP route's `parse` on a query-string mapping, so a command and its route reject
  the same values and call the view with the same arguments (CLI-3).
- Every read prints JSON by default: one line, UTF-8, equal to the HTTP body (or `asdict` of the view
  result). The v4 commands add readable time to it (`cli.timefmt`, D50): `tz`, an ISO sibling `<x>` of each
  `<x>_ms` timestamp and `<x>_ms_h` of each duration, in `--tz` (default the machine zone).
  `--table` prints a table of at most 2 KB (`cli.render`); `--json` is accepted and changes nothing.
- Every call gets `db=` = `store.location.resolve_db(--db)` (D20).
- Exit codes (CLI-4): 0 success; 1 `QueryError`/`NotFound`, `SourceMissing`, `IngestLocked`, a view not
  built yet; 2 argparse and flag-combination errors, no store, schema mismatch; 3 `serve` cannot bind;
  130 Ctrl-C. stderr is one line `error: <message>`.
- `ingest --rebuild` builds a fresh store from the provider logs into `<db>.rebuild` and swaps it over the
  store (`pipeline.run(rebuild=True)`; D52); on failure the old store stays and the temp file is removed.
  It cannot be combined with `--provider` (a partial rebuild would drop the other providers; exit 2).
  `--force` re-reads every bundle into the existing store. Ingest writes nothing but the store file.
- `paths` prints `{data_dir, store}` (the resolved store file and its folder) and runs without a store; it
  creates nothing.
- `status` and `serve` run without a store: `status` reports `store_state` (like its HTTP route), `serve`
  answers 503 until a store exists.
DOES NOT: contain query logic or SQL, open the store, or print source text other than `source`.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import re
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from botsworth.cli import serve as serve_cmd
from botsworth.cli import timefmt
from botsworth.cli.render import V4_COMMANDS, render_table
from botsworth.cli.scope import (
    UsageError,
    cli_zone,
    day_arg,
    resolve_projects,
    scope_args,
    scope_query,
    tz_arg,
)
from botsworth.http import server as http_server
from botsworth.query import NoStore, QueryError, SchemaMismatch, SourceMissing, wire
from botsworth.store import location

# `import botsworth.query as m` yields the function: botsworth/__init__ rebinds the name.
query_api = importlib.import_module("botsworth.query")

_SINCE = re.compile(r"(\d+)([hdw])")
_UNIT_MS = {"h": 3_600_000, "d": 86_400_000, "w": 7 * 86_400_000}


def since_ms(text: str, now_ms: int | None = None) -> int:
    m = _SINCE.fullmatch(text)
    if not m:
        raise argparse.ArgumentTypeError(f"--since wants <n>h|d|w, got {text!r}")
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    return now - int(m.group(1)) * _UNIT_MS[m.group(2)]


def _since_arg(text: str) -> str:
    since_ms(text)
    return text


def _where(pairs: list[str]) -> list[dict[str, Any]]:
    out = []
    for pair in pairs:
        field, sep, value = pair.partition("=")
        if not sep or not field:
            raise QueryError(f"--where wants k=v, got {pair!r}")
        out.append({"field": field, "eq": value})
    return out


def _scope(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "scope", None) and args.scope != "all":
        return {"op": args.scope}
    sessions = {k: getattr(args, k) for k in ("provider", "project", "account") if getattr(args, k, None)}
    if getattr(args, "since", None):
        sessions["from"] = since_ms(args.since)
    return {"sessions": sessions} if sessions else {"all": True}


# ── query builders: one per subcommand, pure (args -> query dict) ─────────────


def q_top(a: argparse.Namespace) -> dict[str, Any]:
    return {
        "scope": _scope(a),
        "select": "descendants",
        "where": [{"field": "kind", "in": [a.kind]}, *_where(a.where)],
        "measure": a.measure,
        "metrics": [a.metric],
        "order_by": {"metric": a.metric, "dir": "desc"},
        "limit": a.n,
    }


def q_children(a: argparse.Namespace) -> dict[str, Any]:
    return {
        "scope": {"op": a.op_id},
        "select": "children",
        "measure": "incl",
        "metrics": ["cost_usd", "tokens.total", "wall_ms", "active_ms"],
        "order_by": {"metric": "start_ms", "dir": "asc"},
        "limit": a.n,
    }


_STATS_METRICS = ["cost_usd", "tokens.total", "wall_ms", "llm_ms", "tool_ms"]
_KINDS = ["session", "engagement", "turn", "llm_call", "tool_call"]
_BY = {"agent_type": "agent_type", "model": "model", "tool": "tool_name"}


def q_stats(a: argparse.Namespace) -> dict[str, Any]:
    q: dict[str, Any] = {
        "scope": _scope(a),
        "select": "sessions",
        "measure": "incl",
        "metrics": _STATS_METRICS,
        "agg": "distribution",
        "limit": a.n,
    }
    if a.by:
        q["group_by"] = [_BY[a.by]]
    return q


def q_outliers(a: argparse.Namespace) -> dict[str, Any]:
    return {
        "scope": _scope(a),
        "select": "sessions",
        "measure": "incl",
        "metrics": [a.metric],
        "outliers": {"over": "p90", "with_top_child": True},
        "order_by": {"metric": a.metric, "dir": "desc"},
        "limit": a.n,
    }


def q_trend(a: argparse.Namespace) -> dict[str, Any]:
    return {
        "scope": _scope(a),
        "select": "descendants",
        "where": [{"field": "kind", "in": ["llm_call"]}, *_where(a.where)],
        "measure": "own",
        "metrics": a.metric or ["tokens.total", "cost_usd"],
        "group_by": [a.by],
        "bucket": {"time": a.bucket},
        "agg": a.agg,
        "limit": a.n,
    }


def q_limits(a: argparse.Namespace) -> dict[str, Any]:
    where: list[dict[str, Any]] = [{"field": "event_kind", "in": ["limit_state", "limit_hit"]}]
    if a.account:
        where.append({"field": "account", "eq": a.account})
    return {
        "scope": {"all": True},
        "select": "events",
        "where": where,
        "order_by": {"metric": "start_ms", "dir": "desc"},
        "limit": a.n,
    }


def q_skills(a: argparse.Namespace) -> dict[str, Any]:
    where: list[dict[str, Any]] = [{"field": "event_kind", "eq": "skill_invocation"}]
    if a.skill:
        where.append({"field": "skill", "eq": a.skill})
    return {
        "scope": _scope(a),
        "select": "events",
        "where": where,
        "group_by": ["skill", "session"],
        "agg": "count",
        "order_by": {"metric": "n", "dir": "desc"},
        "limit": a.n,
    }


QUERY_BUILDERS: dict[str, Callable[[argparse.Namespace], dict[str, Any]]] = {
    "top": q_top,
    "children": q_children,
    "stats": q_stats,
    "outliers": q_outliers,
    "trend": q_trend,
    "limits": q_limits,
    "skills": q_skills,
}


# ── parser ──────────────────────────────────────────────────────────────────


def _filters(p: argparse.ArgumentParser, since: str | None = None) -> None:
    p.add_argument("--project")
    p.add_argument("--provider", choices=["claude_code", "codex", "omp"])
    p.add_argument("--account")
    p.add_argument("--since", type=_since_arg, default=since, help="window back from now: <n>h|d|w")


_DURATION = re.compile(r"(\d+)(s|m|h)")
_DURATION_MS = {"s": 1000, "m": 60_000, "h": 3_600_000}


def duration_ms(text: str) -> int:
    m = _DURATION.fullmatch(text)
    if not m:
        raise argparse.ArgumentTypeError(f"duration wants <n>s|m|h, got {text!r}")
    return int(m.group(1)) * _DURATION_MS[m.group(2)]


def _add_v4_commands(cmd: Callable[[str, str], argparse.ArgumentParser]) -> None:
    tz_arg(cmd("status", "ingest state: store state, running ingest, sources, last run"))
    scope_args(cmd("period", "totals for the scope"))
    p = cmd("days", "day series for a window")
    scope_args(p)
    p.add_argument("--window-from", metavar="DAY", help="first day of the window, YYYY-MM-DD")
    p.add_argument("--window-to", metavar="DAY", help="last day of the window, YYYY-MM-DD")
    p = cmd("sessions", "sessions in the scope, one page")
    scope_args(p)
    p.add_argument("--sort", help="start, cost, wall, active or tokens")
    p.add_argument("--dir", help="asc or desc")
    p.add_argument("--offset", type=int)
    p.add_argument("-n", "--limit", type=int, default=20)
    p.add_argument("--group", help="project or day")
    scope_args(cmd("facets", "filter values in the scope"))
    p = cmd("search", "palette search over sessions and projects")
    p.add_argument("text", metavar="TEXT")
    scope_args(p)
    p = cmd("session", "session header")
    p.add_argument("session", metavar="SESSION")
    tz_arg(p)
    p = cmd("turns", "turns of a session")
    p.add_argument("session", metavar="SESSION")
    p.add_argument("--min-gap", type=duration_ms, default=duration_ms("2m"), metavar="DURATION")
    tz_arg(p)
    p = cmd("turn", "rows of a turn")
    p.add_argument("turn", metavar="TURN", help="op id or <session>:T12")
    p.add_argument("--from-ms", type=int)
    p.add_argument("--to-ms", type=int)
    tz_arg(p)
    p = cmd("detail", "one call, tool, agent or message")
    p.add_argument("id", metavar="ID")
    tz_arg(p)
    p = cmd("find", "finder over one session")
    p.add_argument("session", metavar="SESSION")
    p.add_argument("--kind", help="biggest, waits, failures, skills, agents or search")
    p.add_argument("--measure", help="cost, time or tokens")
    p.add_argument("--text")
    p.add_argument("-n", "--limit", type=int)
    tz_arg(p)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="botsworth", description="Botsworth (design §6.2)")
    ap.add_argument("--db", help="store path (D20: --db > configure() > BOTSWORTH_DB > default)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def cmd(name: str, help: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help)
        out = p.add_mutually_exclusive_group()
        out.add_argument("--json", dest="fmt", action="store_const", const="json", help="the default")
        out.add_argument("--table", dest="fmt", action="store_const", const="table")
        p.set_defaults(fmt="json")
        return p

    p = cmd("outline", "one line per step (default read)")
    p.add_argument("id")
    p.add_argument("--depth", type=int, default=1)

    cmd("query", "design §5 query JSON").add_argument("request", help="query JSON, or - for stdin")

    p = cmd("top", "top ops in a scope by one metric")
    p.add_argument("--scope", default="all", help="op id / display id, or 'all'")
    p.add_argument("--kind", required=True, choices=_KINDS)
    p.add_argument("--metric", default="cost_usd")
    m = p.add_mutually_exclusive_group()
    m.add_argument("--own", dest="measure", action="store_const", const="own")
    m.add_argument("--incl", dest="measure", action="store_const", const="incl")
    p.set_defaults(measure="incl")
    p.add_argument("--where", action="append", default=[], metavar="K=V")
    p.add_argument("-n", type=int, default=10)

    p = cmd("children", "children of an op")
    p.add_argument("op_id")
    p.add_argument("-n", type=int, default=50)

    for name in ("show", "steps", "timeline"):
        p = cmd(name, f"{name} of an op")
        p.add_argument("id")
    sub.choices["timeline"].add_argument("--depth", type=int, default=3)
    sub.choices["steps"].add_argument("--limit", type=int, default=200)
    sub.choices["steps"].add_argument("--cursor")

    p = cmd("source", "page source text through a pointer")
    p.add_argument("id", help="op id, step id, display id or src:// pointer")
    p.add_argument("--part", choices=["input", "output", "thinking", "text"])
    p.add_argument("--max-bytes", type=int, default=65536)
    p.add_argument("--offset", type=int, default=0)

    p = cmd("stats", "per-session distributions")
    _filters(p, since="30d")
    p.add_argument("--by", choices=sorted(_BY))
    p.add_argument("-n", type=int, default=20)

    p = cmd("outliers", "sessions above p90, with their top child")
    _filters(p, since="30d")
    p.add_argument("--metric", default="cost_usd")
    p.add_argument("-n", type=int, default=10)

    p = cmd("trend", "llm_call metrics per time bucket")
    _filters(p, since="30d")
    p.add_argument("--by", default="model")
    p.add_argument("--bucket", choices=["hour", "day", "week"], default="day")
    p.add_argument("--metric", action="append", help="repeatable; default tokens.total, cost_usd")
    p.add_argument("--agg", default="sum", choices=["sum", "count", "max", "p50", "p90", "p95"])
    p.add_argument("--where", action="append", default=[], metavar="K=V")
    p.add_argument("-n", type=int, default=100)

    p = cmd("limits", "raw limit_state / limit_hit events")
    p.add_argument("--account")
    p.add_argument("-n", type=int, default=20)

    p = cmd("skills", "skill invocations per session")
    _filters(p, since=None)
    p.add_argument("--skill")
    p.add_argument("-n", type=int, default=50)

    _add_v4_commands(cmd)

    p = cmd("serve", "serve the UI and the API (never ingests)")
    serve_cmd.add_arguments(p)

    if importlib.util.find_spec("botsworth.cli.usecases"):  # Q3b's module; absent until it lands
        importlib.import_module("botsworth.cli.usecases").register(sub)

    cmd("paths", "print the data folder and store file")

    p = cmd("ingest", "read provider logs into the store (explicit, D5)")
    p.add_argument("--provider", action="append", choices=["claude_code", "codex", "omp"])
    p.add_argument("--force", action="store_true", help="re-read every bundle into the existing store")
    p.add_argument("--rebuild", action="store_true", help="build a fresh store from the logs and swap it in")
    p.add_argument("--reprice", action="store_true")
    return ap


# ── output ──────────────────────────────────────────────────────────────────


# ── dispatch ────────────────────────────────────────────────────────────────


class CliError(Exception):
    """A failure the CLI reports as one line with exit 1 (e.g. a concurrent ingest)."""


def resolve_db(cli_db: str | None) -> Any:
    return location.resolve_db(cli_db)


# command -> (route pattern, the view's positional path id, flag -> query-string name)
V4_ROUTES: dict[str, tuple[str, str | None, dict[str, str]]] = {
    "status": ("/api/ingest/status", None, {}),
    "period": ("/api/explorer/summary", None, {}),
    "days": ("/api/explorer/days", None, {"window_from": "window_from", "window_to": "window_to"}),
    "sessions": (
        "/api/explorer/sessions",
        None,
        {"sort": "sort", "dir": "dir", "offset": "offset", "limit": "limit", "group": "group"},
    ),
    "facets": ("/api/explorer/facets", None, {}),
    "search": ("/api/palette", None, {"text": "text"}),
    "session": ("/api/sessions/{session}", "session", {}),
    "turns": ("/api/sessions/{session}/turns", "session", {"min_gap": "min_gap_ms"}),
    "turn": ("/api/turns/{turn}/rows", "turn", {"from_ms": "from_ms", "to_ms": "to_ms"}),
    "detail": ("/api/ops/{op}/detail", "id", {}),
    "find": (
        "/api/sessions/{session}/find",
        "session",
        {"kind": "kind", "measure": "measure", "text": "text", "limit": "limit"},
    ),
}


def run_v4(args: argparse.Namespace, api: Any, db: Any) -> Any:
    """The view behind `args.cmd`, called with the arguments its HTTP route would build (CLI-3)."""
    pattern, id_attr, flags = V4_ROUTES[args.cmd]
    route = next(r for r in http_server.ROUTES if r.method == "GET" and r.pattern == pattern)
    has_scope = hasattr(args, "automated")
    query: dict[str, list[str]] = {}
    if has_scope:
        resolve_projects(args, api, db)
        query = scope_query(args, cli_zone(args), period=args.cmd != "days")
    elif args.cmd == "session":
        query["tz"] = [cli_zone(args)]
    for attr, name in flags.items():
        value = getattr(args, attr)
        if value is not None:
            if attr in ("window_from", "window_to"):
                value = day_arg(value, cli_zone(args))
            query[name] = [str(value)]
    kwargs = route.parse([getattr(args, id_attr)] if id_attr else [], query)
    return getattr(api, route.view_name)(**kwargs, db=db)


def run(args: argparse.Namespace, api: Any, db: Any) -> Any:
    """One API call per subcommand; returns the payload the API returned."""
    c = args.cmd
    if c in QUERY_BUILDERS:
        return api.query(QUERY_BUILDERS[c](args), db=db)
    if c == "query":
        text = sys.stdin.read() if args.request == "-" else args.request
        try:
            q = json.loads(text)
        except json.JSONDecodeError as e:
            raise QueryError(f"query is not valid JSON: {e.msg}") from None
        return api.query(q, db=db)
    if c == "paths":
        return {
            "data_dir": str(Path(db).parent),
            "store": str(db),
        }
    if c == "outline":
        return api.outline(args.id, depth=args.depth, db=db)
    if c == "show":
        return api.show(args.id, db=db)
    if c == "steps":
        return api.steps(args.id, limit=args.limit, cursor=args.cursor, db=db)
    if c == "timeline":
        return api.timeline(args.id, depth=args.depth, db=db)
    if c == "source":
        return api.source(args.id, part=args.part, offset=args.offset, max_bytes=args.max_bytes, db=db)
    if c in V4_ROUTES:
        return run_v4(args, api, db)
    if getattr(args, "handler", None) is not None:  # a command registered by `cli.usecases` (Q3b)
        return args.handler(args, api, db)
    if c == "ingest":
        from botsworth import pipeline
        from botsworth.store import ingest

        if args.rebuild and args.provider:
            raise UsageError("--rebuild rebuilds every provider; drop --provider")
        try:
            return pipeline.run(
                args.provider, force=args.force, reprice=args.reprice, rebuild=args.rebuild, db=db
            )
        except ingest.IngestLocked as e:
            raise CliError(str(e)) from None
    raise AssertionError(f"unhandled subcommand {c}")


def emit(data: Any, fmt: str, cmd: str, tz: str | None = None) -> None:
    """`tz` is the zone of the readable time of a v4 command (D50); the legacy commands pass none."""
    if cmd in V4_COMMANDS:
        assert tz is not None, "a v4 command needs a zone"
        data = timefmt.render(asdict(data) if is_dataclass(data) else data, tz)
    if fmt == "table":
        print(render_table(cmd, data))
        return
    line = wire.to_json(data) if is_dataclass(data) else _json_line(data)
    sys.stdout.buffer.write(line + b"\n")
    sys.stdout.buffer.flush()


def _json_line(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str).encode()


def _fail(msg: object) -> None:
    text = str(msg).splitlines()[0] if str(msg) else type(msg).__name__
    print(f"error: {text}", file=sys.stderr)


def main(argv: list[str] | None = None, api: Any = query_api) -> int:
    args = build_parser().parse_args(argv)
    db = resolve_db(args.db)
    if args.cmd not in ("ingest", "serve", "status", "paths") and not Path(db).is_file():
        print(f"error: no store at {db} — run 'botsworth ingest'", file=sys.stderr)
        return 2
    try:
        if args.cmd == "serve":
            return serve_cmd.serve(args, db, api)
        data = run(args, api, db)
    except KeyboardInterrupt:
        return 130
    except (UsageError, NoStore, SchemaMismatch) as e:
        _fail(e)
        return 2
    except (QueryError, SourceMissing, CliError) as e:  # query.NotFound subclasses QueryError
        _fail(e)
        return 1
    except NotImplementedError as e:  # a view the query layer has not built yet (HTTP 501)
        _fail(e)
        return 1
    emit(data, args.fmt, args.cmd, cli_zone(args) if args.cmd in V4_COMMANDS else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
