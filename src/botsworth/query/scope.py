"""CONTRACT: the explorer scope (QRY-3): a period plus filters, as one value.

GUARANTEES:
- `Scope` is frozen. Both days None = all time. Defaults: no filters, `q = ""`, `show_automated = False`.
- `parse_scope(dict)` returns a `Scope` or raises `QueryError` naming the offending field:
  a day that is not a real `YYYY-MM-DD` date, exactly one of `from_day`/`to_day` None, `from_day` after
  `to_day`, a list field that is not a list of strings, `q` not a string, `show_automated` not a bool,
  `tz` that is not an IANA zone name, or a key that is not a Scope field. Missing keys take the defaults;
  a missing `tz` is the zone of the machine answering (`zone.resolve_tz()`, D50).
- `Scope.tz` is the zone of every local day, week and hour of the request (QRY-3, QRY-4).
EXPECTS: a dict with the Scope keys of interfaces.md QRY-3.
FAILURE BEHAVIOR: `QueryError` (HTTP 400, CLI exit 1).
DOES NOT: read the store or resolve presets ("Last 7 days") to days (FE-9, CLI-5 do that).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from datetime import date
from typing import Any

from botsworth.query import QueryError
from botsworth.query.zone import resolve_tz, zone_of

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_LISTS = ("providers", "models", "branches", "projects")


@dataclass(frozen=True)
class Scope:
    from_day: str | None = None
    to_day: str | None = None
    q: str = ""
    providers: tuple[str, ...] = ()
    models: tuple[str, ...] = ()
    branches: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    show_automated: bool = False
    # FALLBACK-OK: D50 — a Scope built without a zone takes the answering machine's zone (QRY-3)
    tz: str = field(default_factory=resolve_tz)

    def __post_init__(self) -> None:
        zone_of(self.tz)


def check_day(name: str, value: Any) -> str:
    """`value` as a valid `YYYY-MM-DD` day; `QueryError` naming `name` otherwise."""
    if not isinstance(value, str) or not _DAY.fullmatch(value):
        raise QueryError(f"{name} must be a day YYYY-MM-DD, got {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise QueryError(f"{name} must be a real calendar day, got {value!r}") from None
    return value


def parse_scope(raw: dict[str, Any]) -> Scope:
    known = {f.name for f in fields(Scope)}
    for key in raw:
        if key not in known:
            raise QueryError(f"unknown scope field {key!r}")
    from_day, to_day = raw.get("from_day"), raw.get("to_day")
    if (from_day is None) != (to_day is None):
        raise QueryError("from_day and to_day must both be set or both be null")
    if from_day is not None:
        check_day("from_day", from_day)
        check_day("to_day", to_day)
        if from_day > to_day:
            raise QueryError(f"from_day {from_day} is after to_day {to_day}")
    q = raw.get("q", "")
    if not isinstance(q, str):
        raise QueryError(f"q must be a string, got {q!r}")
    lists: dict[str, tuple[str, ...]] = {}
    for name in _LISTS:
        value = raw.get(name, ())
        if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
            raise QueryError(f"{name} must be a list of strings, got {value!r}")
        lists[name] = tuple(value)
    show = raw.get("show_automated", False)
    if not isinstance(show, bool):
        raise QueryError(f"show_automated must be a boolean, got {show!r}")
    tz = raw.get("tz")
    if tz is None:
        return Scope(from_day, to_day, q, **lists, show_automated=show)
    if not isinstance(tz, str):
        raise QueryError(f"tz must be a zone name, got {tz!r}")
    return Scope(from_day, to_day, q, **lists, show_automated=show, tz=tz)
