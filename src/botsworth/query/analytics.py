"""CONTRACT: quantile math and the named analytics of design §5.1 (Q11–Q17, Q19).

GUARANTEES:
- `percentile(values, q)` is linear interpolation between closest ranks over
  the non-null values; no values -> None (missing, never 0).
- `register(conn)` adds the SQLite aggregate `pctl(x, q)` with the same rule,
  so grouped percentiles run inside one GROUP BY.
- Each named builder (`distribution`, `outliers`, `trend`, `usage`,
  `commands`, `compactions`, `skills`, `skill_events`, `by_model`) returns a
  plain §5 query dict; running it is `botsworth.query.query(...)`. They are
  shorthands for the CLI and agents, not a second query language.
EXPECTS: epoch-ms bounds; project strings as stored (session's first cwd, D10).
DOES NOT: execute SQL (except the registered aggregate); attribute cost or time
  to skills (D15); model limit windows (D16).
"""

from __future__ import annotations

import math
import sqlite3
from typing import Any


def percentile(values: list[float], q: float) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    p = (len(vals) - 1) * q
    lo, hi = math.floor(p), math.ceil(p)
    return vals[lo] + (vals[hi] - vals[lo]) * (p - lo)


class _Pctl:
    def __init__(self) -> None:
        self.values: list[float] = []
        self.q: float | None = None

    def step(self, value: float | None, q: float) -> None:
        self.q = q
        if value is not None:
            self.values.append(value)

    def finalize(self) -> float | None:
        return percentile(self.values, self.q) if self.q is not None else None


def register(conn: sqlite3.Connection) -> None:
    conn.create_aggregate("pctl", 2, _Pctl)


def _sessions_scope(project: str | None, from_ms: int | None, to_ms: int | None) -> dict[str, Any]:
    f: dict[str, Any] = {}
    if project is not None:
        f["project"] = project
    if from_ms is not None:
        f["from"] = from_ms
    if to_ms is not None:
        f["to"] = to_ms
    return {"sessions": f} if f else {"all": True}


SESSION_DIST_METRICS = ["cost_usd", "tokens.total", "wall_ms", "llm_ms", "tool_ms"]


def distribution(project=None, from_ms=None, to_ms=None, by: str | None = None) -> dict[str, Any]:
    """Q11 / D7a: per-session distributions; `by` groups descendants instead (agent_type|model|tool_name)."""
    scope = _sessions_scope(project, from_ms, to_ms)
    if by is None:
        return {"scope": scope, "select": "sessions", "metrics": SESSION_DIST_METRICS, "agg": "distribution"}
    kind = "tool_call" if by == "tool_name" else "llm_call" if by == "model" else "engagement"
    return {
        "scope": scope,
        "select": "descendants",
        "where": [{"field": "kind", "eq": kind}],
        "metrics": ["cost_usd", "tokens.total", "dur_ms"] if kind != "engagement" else SESSION_DIST_METRICS,
        "group_by": [by],
        "agg": "distribution",
        "limit": 200,
    }


def outliers(project=None, from_ms=None, to_ms=None, metric: str = "cost_usd") -> dict[str, Any]:
    """Q12 / D7c: sessions above the scope's p90, each with its argmax child."""
    return {
        "scope": _sessions_scope(project, from_ms, to_ms),
        "select": "sessions",
        "metrics": [metric, "tokens.total", "wall_ms"],
        "outliers": {"over": "p90", "with_top_child": True},
        "order_by": {"metric": metric, "dir": "desc"},
    }


def trend(
    bucket: str = "day", by: str = "model", from_ms=None, to_ms=None, present_only=False
) -> dict[str, Any]:
    """Q13: are calls getting slower — p50 of dur, tok/s, ttft per model per bucket."""
    where: list[dict[str, Any]] = [{"field": "kind", "eq": "llm_call"}]
    if present_only:
        where.append({"field": "time_prov", "eq": "present"})
    if from_ms is not None or to_ms is not None:
        rng: dict[str, Any] = {"field": "start_ms"}
        if from_ms is not None:
            rng["gte"] = from_ms
        if to_ms is not None:
            rng["lt"] = to_ms
        where.append(rng)
    return {
        "scope": {"all": True},
        "select": "descendants",
        "measure": "own",
        "where": where,
        "metrics": ["dur_ms", "out_tok_per_s", "ttft_ms"],
        "group_by": [by],
        "bucket": {"time": bucket},
        "agg": "p50",
        "limit": 1000,
    }


def usage(bucket: str = "day", by: str = "model", project=None, from_ms=None, to_ms=None) -> dict[str, Any]:
    """Q14 (D16): tokens and cost per bucket by model|project|session; no windows."""
    return {
        "scope": _sessions_scope(project, from_ms, to_ms),
        "select": "descendants",
        "measure": "own",
        "where": [{"field": "kind", "eq": "llm_call"}],
        "metrics": ["tokens.total", "cost_usd"],
        "group_by": [by],
        "bucket": {"time": bucket},
        "compare": "previous_bucket",
        "limit": 1000,
    }


def by_model(project=None, from_ms=None, to_ms=None) -> dict[str, Any]:
    """Q6: cost and tokens per model."""
    return {
        "scope": _sessions_scope(project, from_ms, to_ms),
        "select": "descendants",
        "measure": "own",
        "where": [{"field": "kind", "eq": "llm_call"}],
        "metrics": ["cost_usd", "tokens.total"],
        "group_by": ["model"],
        "limit": 500,
    }


def commands(project=None, from_ms=None, to_ms=None, key: str | None = None) -> dict[str, Any]:
    """Q16 / D7d: run commands grouped by command_key (D17), distribution of dur_ms.

    With `key`, drills into one command_key grouped by the full command_norm.
    """
    where: list[dict[str, Any]] = [{"field": "kind", "eq": "tool_call"}]
    if key is not None:
        where.append({"field": "command_key", "eq": key})
    else:
        where.append({"field": "command_key", "ne": None})
    return {
        "scope": _sessions_scope(project, from_ms, to_ms),
        "select": "descendants",
        "measure": "own",
        "where": where,
        "metrics": ["dur_ms"],
        "group_by": ["command_norm" if key is not None else "command_key"],
        "agg": "distribution",
        "order_by": {"metric": "n", "dir": "desc"},
        "limit": 200,
    }


def compactions(project=None, from_ms=None, to_ms=None) -> dict[str, Any]:
    """Q17: compaction events per session."""
    return {
        "scope": _sessions_scope(project, from_ms, to_ms),
        "select": "events",
        "where": [{"field": "kind", "eq": "compaction"}],
        "group_by": ["session"],
        "order_by": {"metric": "n", "dir": "desc"},
        "limit": 500,
    }


def skills(project=None, from_ms=None, to_ms=None, bucket: str | None = None) -> dict[str, Any]:
    """Q15 (D15): invocations per skill (events only; no cost/time)."""
    q: dict[str, Any] = {
        "scope": _sessions_scope(project, from_ms, to_ms),
        "select": "events",
        "where": [{"field": "kind", "eq": "skill_invocation"}],
        "group_by": ["skill"],
        "order_by": {"metric": "n", "dir": "desc"},
        "limit": 500,
    }
    if bucket is not None:
        q["bucket"] = {"time": bucket}
        q.pop("order_by")
    return q


def skill_events(skill: str, project=None, from_ms=None, to_ms=None) -> dict[str, Any]:
    """Q19: the invocation events of one skill, chronological; follow each with steps(turn)."""
    return {
        "scope": _sessions_scope(project, from_ms, to_ms),
        "select": "events",
        "where": [{"field": "kind", "eq": "skill_invocation"}, {"field": "skill", "eq": skill}],
        "order_by": {"metric": "start_ms", "dir": "asc"},
        "limit": 500,
    }
