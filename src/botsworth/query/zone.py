"""CONTRACT: request-zone arithmetic (DM-41 [D50], QRY-3, QRY-4): where a zone name becomes epoch edges.

GUARANTEES:
- `resolve_tz(env, localtime)` returns the IANA zone name of the machine answering a query that names none, by
  order: env `BOTSWORTH_TZ`, env `TZ`, the target of the `localtime` symlink after its last `zoneinfo/`,
  else `"UTC"` (a warning is logged). An empty env value counts as unset. A name `ZoneInfo` rejects stops
  the order.
- `day_bounds(day, tz)` -> `(start_ms, end_ms)`: `[local midnight of day, local midnight of the next day)`;
  a DST day is 23 or 25 hours long. `day` is `YYYY-MM-DD` or a `date`.
- `day_edges(from_day, to_day, tz)` -> the n+1 epoch edges of the n local days `from_day..to_day` inclusive.
- `week_edges(from_day, to_day, tz)` -> epoch edges of the local weeks (Monday start) that cover
  `from_day..to_day`: the first edge is the local midnight of the Monday on or before `from_day`, the last is
  the local midnight of the Monday after `to_day`.
- `hour_edges(start_ms, end_ms, tz)` -> epoch edges of the local hours: the first is the start of the local
  hour holding `start_ms`, the last is the first local-hour start at or after `end_ms`. A repeated fall-back
  hour is two buckets; zones with a :30 or :45 offset have edges at :30 or :15 of the UTC hour.
- `local_day(ms, tz)` -> the local `YYYY-MM-DD` of `ms`.
- Every edge is a multiple of 900 000 ms, so each falls on a `rollup_time` bucket start (DM-41).
EXPECTS: `tz` is an IANA name; `from_day <= to_day`; `start_ms <= end_ms`.
FAILURE BEHAVIOR: an unknown or malformed zone name raises `QueryError` (HTTP 400, CLI exit 2); `resolve_tz`
  raises it for a name found in the environment or the symlink. A malformed day raises `QueryError`.
DOES NOT: touch the store, read `meta`, or cache a zone across calls other than `ZoneInfo`'s own cache.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from botsworth.query import QueryError

log = logging.getLogger(__name__)

LOCALTIME = Path("/etc/localtime")
_MARK = "zoneinfo/"
_STEP_MS = 900_000
_HOUR_MS = 3_600_000


def zone_of(name: str, source: str = "tz") -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise QueryError(f"unknown time zone {name!r} from {source}") from exc


def resolve_tz(env: Mapping[str, str] = os.environ, localtime: Path = LOCALTIME) -> str:
    for var in ("BOTSWORTH_TZ", "TZ"):
        if env.get(var):
            zone_of(env[var], f"env {var}")
            return env[var]
    try:
        target = os.readlink(localtime)
    except OSError:
        target = ""  # FALLBACK-OK: DM-41 — no readable /etc/localtime symlink falls through to UTC
    if _MARK in target:
        name = target.rpartition(_MARK)[2]
        zone_of(name, str(localtime))
        return name
    log.warning("no time zone from BOTSWORTH_TZ, TZ or %s; using UTC", localtime)
    return "UTC"  # FALLBACK-OK: DM-41 — the last step of the resolution order


def _day(day: str | date) -> date:
    if isinstance(day, date):
        return day
    try:
        return date.fromisoformat(day)
    except ValueError as exc:
        raise QueryError(f"bad day {day!r}: expected YYYY-MM-DD") from exc


def _midnight(day: date, zone: ZoneInfo) -> int:
    return int(datetime.combine(day, time(), zone).timestamp()) * 1000


def day_bounds(day: str | date, tz: str) -> tuple[int, int]:
    zone = zone_of(tz)
    d = _day(day)
    return _midnight(d, zone), _midnight(d + timedelta(days=1), zone)


def day_edges(from_day: str | date, to_day: str | date, tz: str) -> list[int]:
    zone = zone_of(tz)
    first, last = _day(from_day), _day(to_day)
    assert first <= last, f"day_edges: {first} is after {last}"
    return [_midnight(first + timedelta(days=i), zone) for i in range((last - first).days + 2)]


def week_edges(from_day: str | date, to_day: str | date, tz: str) -> list[int]:
    zone = zone_of(tz)
    first, last = _day(from_day), _day(to_day)
    assert first <= last, f"week_edges: {first} is after {last}"
    monday = first - timedelta(days=first.weekday())
    end = last - timedelta(days=last.weekday()) + timedelta(days=7)
    return [_midnight(monday + timedelta(days=7 * i), zone) for i in range((end - monday).days // 7 + 1)]


def _offset_ms(ms: int, zone: ZoneInfo) -> int:
    return int(datetime.fromtimestamp(ms / 1000, zone).utcoffset().total_seconds()) * 1000


def hour_edges(start_ms: int, end_ms: int, tz: str) -> list[int]:
    zone = zone_of(tz)
    assert start_ms <= end_ms, f"hour_edges: {start_ms} is after {end_ms}"
    # every local hour start is a multiple of 15 minutes in UTC, so a 15-minute walk finds all of them
    t = start_ms - start_ms % _STEP_MS
    while (t + _offset_ms(t, zone)) % _HOUR_MS:
        t -= _STEP_MS
    edges = [t]
    while edges[-1] < end_ms:
        t = edges[-1] + _STEP_MS
        while (t + _offset_ms(t, zone)) % _HOUR_MS:
            t += _STEP_MS
        edges.append(t)
    return edges


def local_day(ms: int, tz: str) -> str:
    return datetime.fromtimestamp(ms / 1000, zone_of(tz)).date().isoformat()
