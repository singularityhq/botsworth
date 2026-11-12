"""CONTRACT: the `botsworth repl` subcommands (interfaces.md REPL-1, REPL-2, D58).

GUARANTEES:
- `add_arguments(parser)` adds `start`, `run`, `status`, `interrupt`, `stop`; `run(args, db)` calls the
  matching
  function of `botsworth.repl.client` and returns its exit code (REPL-2: 0 ok, 1 the cell raised, 2 no session
  running, 3 the cell is still running or the session is busy, 4 parameters differ or no unique session, 5
    `start`
  could not create the session). A reason is one line on stderr.
- `start` needs a store: with none at `db` it exits 5 with the `botsworth ingest` hint. The other commands do
  not
  look at the store.
DOES NOT: import pandas, read the store, or contain query logic.
"""

from __future__ import annotations

import argparse
import sys

from botsworth import repl
from botsworth.cli.scope import cli_zone, tz_arg
from botsworth.repl import client


def add_arguments(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="repl_cmd", required=True)

    s = sub.add_parser("start", help="load the frames once and keep them in a background session")
    tz_arg(s)
    s.add_argument("--name", help="session name (default: a new short name, printed)")
    s.add_argument(
        "--since", metavar="DAY", help="keep sessions that started on or after this local day (YYYY-MM-DD)"
    )
    s.add_argument("--until", metavar="DAY", help="keep sessions that started on or before this local day")
    s.add_argument("--sessions", nargs="+", metavar="ID", help="keep only these session ids")
    s.add_argument(
        "--idle", type=float, default=20, metavar="MIN", help="stop after this many minutes without a run"
    )
    s.add_argument(
        "--max-rss", type=float, default=2000, metavar="MB", help="warn after a cell above this memory"
    )

    r = sub.add_parser("run", help="run Python in the session; print its output, then the last expression")
    r.add_argument("code", help="Python source, or - to read it from stdin")
    r.add_argument("--name", help="session name (default: the only running session)")
    r.add_argument(
        "--wait", type=float, default=50, metavar="S", help="seconds to wait before exit 3 (cell goes on)"
    )

    st = sub.add_parser("status", help="list the sessions, or show one")
    st.add_argument("--name")
    st.add_argument("--last", action="store_true", help="print the output of the last cell")

    for name, text in (("interrupt", "stop the running cell"), ("stop", "end the session")):
        c = sub.add_parser(name, help=text)
        c.add_argument("--name")


def run(args: argparse.Namespace, db: str) -> int:
    try:
        c = args.repl_cmd
        if c == "start":
            from pathlib import Path

            if not Path(db).is_file():
                raise client.Exit(repl.EXIT_START_FAILED, f"no store at {db}: run 'botsworth ingest' first")
            return client.start(
                db, cli_zone(args), args.name, args.since, args.until, args.sessions, args.idle, args.max_rss
            )
        if c == "run":
            code = sys.stdin.read() if args.code == "-" else args.code
            return client.run(code, args.name, args.wait)
        if c == "status":
            return client.status(args.name, args.last)
        if c == "interrupt":
            return client.interrupt(args.name)
        return client.stop(args.name)
    except client.Exit as e:
        if e.message:
            print(e.message, file=sys.stderr)
        return e.code
