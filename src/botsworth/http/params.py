"""CONTRACT: query-string parsing for the HTTP layer (interfaces.md API-2, API-3).

GUARANTEES:
- `parse_query(qs)` returns `dict[str, list[str]]`; a key with an empty value counts as absent.
- Each helper takes that dict and a parameter name. A repeated key: scalar helpers (`one`, `day`,
  `int_in`, `bool01`, `choice`, `required`) take the last value (legacy behaviour); `repeated` returns
  every value in order.
- A malformed value raises `QueryError` whose message starts with the parameter name.
- `tz(q)` is the `tz` parameter: an IANA zone name, or None when absent (the view then takes the answering
  machine's zone, D50); an unknown zone raises `QueryError`.
- `scope_from_query` maps `from`, `to`, `tz`, `q`, `provider`*, `model`*, `branch`*, `project`*, `auto` to a
  `Scope` (QRY-3); `auto=show` sets `show_automated`, absent hides. Unknown parameters are ignored.
EXPECTS: nothing.
FAILURE BEHAVIOR: `QueryError` (HTTP 400, code `bad_request`).
DOES NOT: touch the store or the request.
"""

from __future__ import annotations

from collections.abc import Collection
from urllib.parse import parse_qs

from botsworth.query import QueryError
from botsworth.query.scope import Scope, check_day, parse_scope
from botsworth.query.zone import zone_of

Query = dict[str, list[str]]


def parse_query(qs: str) -> Query:
    return parse_qs(qs, keep_blank_values=False)


def one(q: Query, name: str, default: str | None = None) -> str | None:
    values = q.get(name)
    if not values:
        return default
    return values[
        -1
    ]  # FALLBACK-OK: interfaces.md API-2 is silent on a repeated scalar; legacy server.py: last wins


def repeated(q: Query, name: str) -> tuple[str, ...]:
    return tuple(q.get(name, ()))


def day(q: Query, name: str, default: str | None = None) -> str | None:
    value = one(q, name)
    return default if value is None else check_day(name, value)


def int_in(q: Query, name: str, lo: int, hi: int | None, default: int | None = None) -> int | None:
    value = one(q, name)
    if value is None:
        return default
    try:
        n = int(value, 10)
    except ValueError:
        raise QueryError(f"{name} must be an integer, got {value!r}") from None
    if n < lo or (hi is not None and n > hi):
        bound = f"at least {lo}" if hi is None else f"between {lo} and {hi}"
        raise QueryError(f"{name} must be {bound}, got {n}")
    return n


def bool01(q: Query, name: str, default: bool = False) -> bool:
    value = one(q, name)
    if value is None:
        return default
    if value not in ("0", "1"):
        raise QueryError(f"{name} must be 0 or 1, got {value!r}")
    return value == "1"


def choice(q: Query, name: str, allowed: Collection[str], default: str | None = None) -> str | None:
    value = one(q, name)
    if value is None:
        return default
    if value not in allowed:
        raise QueryError(f"{name} must be one of {', '.join(sorted(allowed))}; got {value!r}")
    return value


def required(q: Query, name: str) -> str:
    value = one(q, name)
    if value is None:
        raise QueryError(f"{name} is required")
    return value


def tz(q: Query) -> str | None:
    name = one(q, "tz")
    if name is not None:
        zone_of(name, "tz")
    return name


def scope_from_query(q: Query, with_period: bool = True) -> Scope:
    """`with_period=False` ignores `from`/`to` (API-11 sends `window_from`/`window_to` instead)."""
    from_day, to_day = (day(q, "from"), day(q, "to")) if with_period else (None, None)
    if (from_day is None) != (to_day is None):
        raise QueryError("from and to must be given together")
    if from_day is not None and to_day is not None and from_day > to_day:
        raise QueryError(f"from {from_day} is after to {to_day}")
    auto = one(q, "auto")
    if auto not in (None, "show"):
        raise QueryError(f"auto must be 'show', got {auto!r}")
    return parse_scope(
        {
            "from_day": from_day,
            "to_day": to_day,
            "tz": tz(q),
            "q": one(q, "q", ""),
            "providers": repeated(q, "provider"),
            "models": repeated(q, "model"),
            "branches": repeated(q, "branch"),
            "projects": repeated(q, "project"),
            "show_automated": auto == "show",
        }
    )
