"""CONTRACT: the shared scope flags of the v4 read commands (CLI-5).

GUARANTEES:
- `scope_args(parser)` adds `--from DAY --to DAY`, `--since <n>d|w`, `--tz ZONE`, `--q TEXT`, `--provider`,
  `--model`, `--branch`, `--project` (each repeatable) and `--automated`. `--since` accepts only `d` and `w`;
  any other form is an argparse error (exit 2), as is a `--tz` that is not an IANA zone (CLI-5, D50).
- `--from`, `--to` (and `days --window-from/--window-to`) accept a `YYYY-MM-DD` day, an ISO-8601 time with an
  offset, or epoch ms; `day_arg(text, tz)` turns the last two into their local day in `tz`. Any other form is
  a `UsageError` (exit 2).
- `cli_zone(ns)` is the CLI zone: `--tz`, else the machine zone (`zone.resolve_tz()`). The store has no zone.
- `scope_query(ns, tz)` returns the HTTP query-string mapping of those flags
  (`{"from": ["2026-10-06"], "tz": ["Asia/Kolkata"], ...}`), the form `botsworth.http.params.scope_from_query`
  reads; it always carries `tz`. `to_scope(ns, tz)` is `Scope` built from it, so a command and its HTTP route
  read one scope.
- `--since <n>d` is the `n` local days ending today in the zone `tz`, today included
  (`1d` = today); `<n>w` is `7n` days. `--since` together with `--from` or `--to`, or `--from` without
  `--to` (or the reverse), raises `UsageError` (exit 2).
EXPECTS: `tz` is a valid zone name (`cli_zone` gives one).
FAILURE BEHAVIOR: `UsageError`; a malformed or impossible day raises `QueryError` (exit 1) from `Scope`.
- `resolve_projects(ns, api, db)` replaces each `--project` value in `ns` with a project key through
  `api.resolve_project` (D49), before the scope is built; an ambiguous or unknown value raises `QueryError`.
DOES NOT: read the store itself (only `resolve_projects` does, through the query layer),
  or accept the `<n>h` form (that stays on the legacy commands).
"""

from __future__ import annotations

import argparse
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from botsworth.http import params as P
from botsworth.query import QueryError
from botsworth.query.scope import Scope
from botsworth.query.zone import resolve_tz, zone_of

_SINCE_DAYS = re.compile(r"([1-9]\d*)([dw])")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_EPOCH_MS = re.compile(r"\d{9,}")


class UsageError(Exception):
    """A flag combination argparse cannot see; the CLI reports it like an argparse error (exit 2)."""


def day_arg(text: str, tz: str) -> str:
    """The local day of a `--from`/`--to` value: `YYYY-MM-DD`, an ISO time with offset, or epoch ms (D50)."""
    if _DAY.fullmatch(text):
        return text  # an impossible day such as 2026-02-30 is `Scope`'s QueryError
    zone = ZoneInfo(tz)
    if _EPOCH_MS.fullmatch(text):
        return datetime.fromtimestamp(int(text) // 1000, zone).date().isoformat()
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        when = None
    if when is None or when.tzinfo is None:
        raise UsageError(f"day wants YYYY-MM-DD, an ISO-8601 time with an offset, or epoch ms, got {text!r}")
    return when.astimezone(zone).date().isoformat()


def since_days(text: str) -> str:
    if not _SINCE_DAYS.fullmatch(text):
        raise argparse.ArgumentTypeError(f"--since wants <n>d or <n>w, got {text!r}")
    return text


def zone_arg(text: str) -> str:
    try:
        zone_of(text, "--tz")
    except QueryError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return text


def tz_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--tz", type=zone_arg, metavar="ZONE", help="IANA zone for days and --since (default: this machine's)"
    )


def cli_zone(ns: argparse.Namespace) -> str:
    return ns.tz or resolve_tz()


def scope_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--from",
        dest="from_day",
        metavar="DAY",
        help="first day: YYYY-MM-DD, ISO time with offset, or epoch ms (with --to)",
    )
    p.add_argument("--to", dest="to_day", metavar="DAY", help="last day, same forms as --from (with --from)")
    p.add_argument(
        "--since", type=since_days, metavar="<n>d|w", help="n days back, today included; not with --from/--to"
    )
    tz_arg(p)
    p.add_argument("--q", help="text filter")
    for name in ("provider", "model", "branch", "project"):
        p.add_argument(f"--{name}", action="append", default=[], help="repeatable")
    p.add_argument("--automated", action="store_true", help="include automated sessions")


def today(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


def scope_query(ns: argparse.Namespace, tz: str, period: bool = True) -> dict[str, list[str]]:
    """`period=False` leaves the period out, for a command that ignores it (`days`, QRY-25)."""
    out: dict[str, list[str]] = {"tz": [tz]}
    if period:
        if ns.since and (ns.from_day or ns.to_day):
            raise UsageError("--since cannot be used with --from or --to")
        if (ns.from_day is None) != (ns.to_day is None):
            raise UsageError("--from and --to must be given together")
        if ns.since:
            n = int(ns.since[:-1]) * (7 if ns.since[-1] == "w" else 1)
            end = today(tz)
            out["from"] = [(end - timedelta(days=n - 1)).isoformat()]
            out["to"] = [end.isoformat()]
        elif ns.from_day is not None:
            out["from"], out["to"] = [day_arg(ns.from_day, tz)], [day_arg(ns.to_day, tz)]
    if ns.q:
        out["q"] = [ns.q]
    for name in ("provider", "model", "branch", "project"):
        if getattr(ns, name):
            out[name] = list(getattr(ns, name))
    if ns.automated:
        out["auto"] = ["show"]
    return out


def to_scope(ns: argparse.Namespace, tz: str) -> Scope:
    return P.scope_from_query(scope_query(ns, tz))


def resolve_projects(ns: argparse.Namespace, api: Any, db: Any) -> None:
    ns.project = [api.resolve_project(v, db=db) for v in ns.project]
