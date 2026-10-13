"""CONTRACT: query-JSON validation and wire-shape builders (design §5, §5.2; types.ts).

GUARANTEES:
- `parse_query(q)` returns a validated `Q` or raises `QueryError` (400-class)
  naming the offending key; SQL identifiers used downstream come only from the
  whitelists in this module (`op_metric`, `GROUPS`, `FIELDS`), never from input.
- `op_wire`, `step_wire`, `event_wire` return exactly the key sets of
  `frontend/src/api/types.ts` `Op`, `Step`, `ProfEvent` plus the keys listed in
  `WIRE_EXTRA` (fields the design names that types.ts lacks).
- `select sessions` op rows (and `sessions()`) also carry SESSION_ROW_KEYS
  from the sessions table; container ops carry `segments` (D19) as a list.
- Wire `Op.tok_total` is the inclusive token total (store `incl_tok_total`);
  store `tok_total` is the op's own total.
- `freshness(conn)` / `freshness_from_meta(meta)` return the QRY-17 envelope as a dict, keys in wire
  order: `last_ingest_ms` (null before the first ingest), `parser_version`, `price_fingerprint`,
  `schema_version`, `api_version` (1). A store with no ingest yet reports "" for the two
  versions. There is no `tz` (QRY-17, D50).
- Provenance of a null metric is `missing` (design invariant 6).
EXPECTS: rows from a store created by `botsworth.store.schema`.
FAILURE BEHAVIOR: invalid query -> `QueryError`.
DOES NOT: run queries; read source files.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from botsworth.query import QueryError, zone
from botsworth.store.schema import SCHEMA_VERSION

PROV_RANK = {"present": 3, "derived": 2, "estimated": 1, "missing": 0}
RANK_PROV = {v: k for k, v in PROV_RANK.items()}

# ── wire key sets (types.ts) ────────────────────────────────────────────────

OP_WIRE_KEYS = (
    "op_id", "kind", "parent", "seq", "depth", "caused_by", "caused_by_basis", "provider", "session_id",
    "agent_id", "engagement_ix", "project", "cwd", "account", "agent_type", "agent_name", "spawn_depth",
    "duplicate_of", "start_ms", "end_ms", "dur_ms", "time_prov", "ttft_ms", "in_tok", "cache_read_tok",
    "cache_write_tok", "out_tok", "reasoning_tok", "context_tokens", "tok_prov", "cost_usd", "cost_basis",
    "model", "tool_name", "command", "command_norm", "file_path", "exit_code", "is_error", "error_kind",
    "label", "src", "incl_in_tok", "incl_cache_read_tok", "incl_cache_write_tok", "incl_out_tok",
    "tok_total", "incl_cost_usd", "incl_llm_ms", "incl_tool_ms", "wall_ms", "active_ms", "busy_sum_ms",
    "idle_ms", "max_parallel", "n_llm_calls", "n_tool_calls", "n_engagements", "n_errors", "n_steps",
    "incl_tok_prov", "incl_time_prov", "cost_lower_bound",
)  # fmt: skip
STEP_WIRE_KEYS = (
    "step_id", "turn_id", "ordinal", "op_id", "verb", "target", "outcome", "summary", "snippet",
    "start_ms", "dur_ms", "tool_name", "skill", "sub_items", "src",
)  # fmt: skip
EVENT_WIRE_KEYS = ("event_id", "kind", "ts_ms", "session_id", "op_id", "account", "basis", "fields", "src")
OUTLINE_ROW_KEYS = ("display_id", "id", "kind", "verb", "target", "outcome", "dur_ms", "cost_usd", "level")

# Keys the design names (§4.10 D17, §5, §5.2, §6.1) that types.ts does not carry yet.
# The contract test allows exactly these beyond types.ts; T8 adds them to types.ts.
WIRE_EXTRA: dict[str, frozenset[str]] = {
    "Op": frozenset({"command_key", "blocked_ms", "segments"}),
    "QueryResponse": frozenset({"scope_prov", "cost_lower_bound"}),
    "Row": frozenset({"delta"}),
    "ShowResponse": frozenset({"freshness"}),
    "TimelineResponse": frozenset({"freshness"}),
    "StepsResponse": frozenset({"freshness"}),
    "SourceResponse": frozenset({"freshness", "block"}),  # block: API-26
    "SessionsRow": frozenset({"git_branch", "harness_version", "status"}),
    "ListingRow": frozenset({"name", "title_source", "project_name", "is_automated"}),  # API-27
    "ProjectRow": frozenset({"project_name", "n_automated", "color_slot"}),  # API-28
    "SessionsResponse": frozenset({"total"}),  # API-27
    "MetricName": frozenset({"blocked_ms", "ttft_ms", "out_tok_per_s", "tokens.reasoning"}),
    "GroupKey": frozenset({"command_key", "path_sig"}),
}

SESSION_ROW_KEYS = ("title", "git_branch", "harness_version", "status")  # ss_* columns of the sessions table
_BOOL_OP = ("is_error", "cost_lower_bound")


def op_wire(row: sqlite3.Row, session_fields: bool = False) -> dict[str, Any]:
    out = {k: row[k] for k in OP_WIRE_KEYS if k != "tok_total"}
    out["tok_total"] = row["incl_tok_total"]
    for k in _BOOL_OP:
        out[k] = bool(row[k])
    out["command_key"] = row["command_key"]
    out["blocked_ms"] = row["blocked_ms"]
    out["segments"] = json.loads(row["segments"]) if row["segments"] is not None else None
    if session_fields:  # select sessions / sessions(): sessions-table columns aliased ss_* (§6.3)
        for k in SESSION_ROW_KEYS:
            out[k] = row[f"ss_{k}"]
    return out


def step_wire(row: sqlite3.Row) -> dict[str, Any]:
    out = {k: row[k] for k in STEP_WIRE_KEYS if k not in ("step_id", "sub_items")}
    out["step_id"] = f"{row['turn_id']}#{row['ordinal']}"
    out["sub_items"] = json.loads(row["sub_items"]) if row["sub_items"] is not None else None
    return out


def event_wire(row: sqlite3.Row) -> dict[str, Any]:
    out = {k: row[k] for k in EVENT_WIRE_KEYS if k != "fields"}
    out["fields"] = json.loads(row["fields"])
    return out


API_VERSION = 1


def freshness_from_meta(meta: dict[str, str]) -> dict[str, Any]:
    """QRY-17. `meta` is the store's `meta` table; a missing key means no ingest has written it yet."""
    last = meta.get("last_ingest_ms")
    return {
        "last_ingest_ms": int(last) if last is not None else None,
        # FALLBACK-OK: interfaces.md API-30 — before the first ingest the versions are ""
        "parser_version": meta.get("parser_version", ""),
        "price_fingerprint": meta.get("price_fingerprint", ""),
        # FALLBACK-OK: interfaces.md API-30 — a store without the key is reported as the code's version
        "schema_version": int(meta.get("schema_version", SCHEMA_VERSION)),
        "api_version": API_VERSION,
    }


def freshness(conn: sqlite3.Connection) -> dict[str, Any]:
    return freshness_from_meta(dict(conn.execute("SELECT key, value FROM meta").fetchall()))


# design §4.8 availability matrix, per provider (metric-level provenance).
_AVAIL = (
    ("tokens", "present", "present", "present"),
    ("tokens.cache_write_tiers", "present", "missing", "missing"),
    ("cost_usd", "derived", "derived", "present"),
    ("session_ledger_check", "present", "missing", "missing"),
    ("llm_dur_ms", "derived", "derived", "present"),
    ("ttft_ms", "missing", "missing", "present"),
    ("tool_dur_ms", "derived", "present", "present"),
    ("exit_code", "derived", "present", "present"),
    ("compaction", "present", "derived", "present"),
    ("api_error", "present", "derived", "present"),
    ("limit_state", "n/a", "present", "n/a"),
    ("limit_hit", "derived", "missing", "n/a"),
    ("skill_invocation", "present", "missing", "present"),
)
AVAILABILITY = {"rows": [{"metric": m, "claude_code": c, "codex": x, "omp": o} for m, c, x, o in _AVAIL]}

# ── metrics over ops ────────────────────────────────────────────────────────

TOKENS = {
    "total": "tok_total",
    "in": "in_tok",
    "out": "out_tok",
    "cache_read": "cache_read_tok",
    "cache_write": "cache_write_tok",
    "reasoning": "reasoning_tok",
}
OP_METRICS = frozenset(
    {"tokens." + k for k in TOKENS}
    | {"cost_usd", "wall_ms", "active_ms", "busy_sum_ms", "blocked_ms", "idle_ms", "max_parallel", "dur_ms"}
    | {"llm_ms", "tool_ms", "ttft_ms", "out_tok_per_s", "cache_hit_ratio", "context_tokens", "n_errors"}
    | {"start_ms"}
)
# Metrics whose values add across disjoint rows; share is defined only for these (§10 "share ≤ 1").
ADDITIVE = frozenset(
    {"tokens." + k for k in TOKENS} | {"cost_usd", "busy_sum_ms", "dur_ms", "llm_ms", "tool_ms", "n_errors"}
)
STEP_METRICS = frozenset({"dur_ms", "start_ms"})
EVENT_METRICS = frozenset({"start_ms"})


def _prov_case(col: str) -> str:
    return f"COALESCE({col}, 'missing')"


@dataclass(frozen=True, slots=True)
class MetricSql:
    value: str  # SQL value expression over alias `o`
    prov: str  # SQL expression yielding a provenance string


def op_metric(name: str, measure: str, a: str = "o") -> MetricSql:
    """SQL for one metric over ops alias `a` (whitelisted names only)."""
    incl = measure == "incl"
    tok_prov = _prov_case(f"{a}.incl_tok_prov" if incl else f"{a}.tok_prov")
    time_prov = _prov_case(f"{a}.incl_time_prov" if incl else f"{a}.time_prov")
    if name.startswith("tokens."):
        col = TOKENS[name[7:]]
        return MetricSql(f"{a}.incl_{col}" if incl else f"{a}.{col}", tok_prov)
    if name == "cost_usd":
        if incl:
            return MetricSql(
                f"{a}.incl_cost_usd",
                f"CASE WHEN {a}.cost_lower_bound THEN 'missing' ELSE 'derived' END",
            )
        return MetricSql(
            f"{a}.cost_usd",
            f"CASE {a}.cost_basis WHEN 'reported' THEN 'present' WHEN 'computed' THEN 'derived' "
            "ELSE 'missing' END",
        )
    if name in ("llm_ms", "tool_ms"):
        if incl:
            return MetricSql(f"{a}.incl_{name}", time_prov)
        return MetricSql(f"CASE WHEN {a}.kind = '{name[:-3]}_call' THEN {a}.dur_ms END", time_prov)
    if name in ("wall_ms", "active_ms", "busy_sum_ms", "blocked_ms", "idle_ms", "max_parallel"):
        return MetricSql(f"{a}.{name}", _prov_case(f"{a}.incl_time_prov"))
    if name in ("dur_ms", "start_ms"):
        return MetricSql(f"{a}.{name}", _prov_case(f"{a}.time_prov"))
    if name == "ttft_ms":
        return MetricSql(f"{a}.ttft_ms", _prov_case(f"{a}.ttft_prov"))
    if name == "out_tok_per_s":
        return MetricSql(
            f"CASE WHEN {a}.kind = 'llm_call' AND {a}.dur_ms > 0 THEN {a}.out_tok * 1000.0 / {a}.dur_ms END",
            f"CASE WHEN {a}.time_prov = 'present' THEN 'derived' ELSE 'estimated' END",
        )
    if name == "cache_hit_ratio":
        if incl:
            den = f"({a}.incl_in_tok + {a}.incl_cache_read_tok + {a}.incl_cache_write_tok)"
            return MetricSql(f"{a}.incl_cache_read_tok * 1.0 / NULLIF({den}, 0)", tok_prov)
        return MetricSql(f"{a}.cache_read_tok * 1.0 / NULLIF({a}.context_tokens, 0)", tok_prov)
    if name == "context_tokens":
        return MetricSql(f"{a}.context_tokens", _prov_case(f"{a}.tok_prov"))
    if name == "n_errors":
        if incl:
            return MetricSql(f"{a}.n_errors", "'present'")
        return MetricSql(
            f"CASE WHEN {a}.kind IN ('llm_call', 'tool_call') THEN COALESCE({a}.is_error, 0) END", "'present'"
        )
    raise QueryError(f"unknown metric {name!r}")


# ── where fields and group keys per select ─────────────────────────────────

OP_FIELDS = frozenset(
    {
        "op_id", "kind", "provider", "session_id", "agent_id", "agent_type", "agent_name", "project", "cwd",
        "account", "model", "tool_name", "command", "command_norm", "command_key", "file_path", "exit_code",
        "is_error", "error_kind", "cost_basis", "time_prov", "tok_prov", "ttft_prov", "role", "label",
        "start_ms", "end_ms", "depth", "spawn_depth", "caused_by_basis", "path_sig", "stop_reason",
        "interrupted", "cut_off",
    }
)  # fmt: skip
_OP_GROUPS = {
    "model": "o.model",
    "tool_name": "o.tool_name",
    "agent_type": "o.agent_type",
    "command_norm": "o.command_norm",
    "command_key": "o.command_key",
    "file_path": "o.file_path",
    "session": "o.session_id",
    "project": "o.project",
    "account": "o.account",
    "provider": "o.provider",
    "kind": "o.kind",
    "path_sig": "o.path_sig",
}
_EVENT_GROUPS = {
    "session": "e.session_id",
    "project": "e.project",
    "account": "e.account",
    "provider": "e.provider",
    "kind": "e.kind",
    "skill": "json_extract(e.fields, '$.skill')",
}
_STEP_GROUPS = {
    "verb": "s.verb",
    "tool_name": "s.tool_name",
    "skill": "s.skill",
    "session": "so.session_id",
    "project": "so.project",
    "provider": "so.provider",
    "account": "so.account",
    "kind": "so.kind",
}
GROUPS = {"ops": _OP_GROUPS, "events": _EVENT_GROUPS, "steps": _STEP_GROUPS}

_FIELD_NAME = re.compile(r"^fields\.([A-Za-z0-9_]+)$")


def field_sql(select_kind: str, name: str, measure: str) -> str:
    """SQL expression for a `where` field (whitelisted)."""
    if select_kind == "ops":
        if name in OP_FIELDS:
            return f"o.{name}"
        if name in OP_METRICS:
            return op_metric(name, measure).value
    elif select_kind == "events":
        simple = {"event_id", "op_id", "session_id", "account", "project", "provider", "basis", "ts_ms"}
        if name in simple:
            return f"e.{name}"
        if name in ("kind", "event_kind"):
            return "e.kind"
        if name == "start_ms":
            return "e.ts_ms"
        if name in ("skill", "via"):
            return f"json_extract(e.fields, '$.{name}')"
        m = _FIELD_NAME.match(name)
        if m:
            return f"json_extract(e.fields, '$.{m.group(1)}')"
    else:
        simple = {"turn_id", "ordinal", "op_id", "verb", "tool_name", "target", "outcome", "skill"}
        if name in simple:
            return f"s.{name}"
        if name in ("start_ms", "dur_ms"):
            return f"s.{name}"
        if name in ("session_id", "project", "provider", "account", "model", "kind"):
            return f"so.{name}"
    raise QueryError(f"unknown where field {name!r} for select {select_kind}")


# ── the parsed query ────────────────────────────────────────────────────────

SELECTS = ("children", "descendants", "self", "sessions", "events", "steps")
AGGS = ("sum", "count", "max", "p50", "p90", "p95", "distribution")
BUCKET_MS = {"hour": 3_600_000, "day": 86_400_000, "week": 7 * 86_400_000}
_RANGE_OPS = ("gte", "gt", "lte", "lt")
_KEYS = {
    "scope", "select", "where", "measure", "metrics", "group_by", "bucket", "agg", "outliers", "compare",
    "order_by", "limit", "cursor", "tz",
}  # fmt: skip
MAX_LIMIT = 10_000


@dataclass(frozen=True, slots=True)
class Pred:
    field: str
    op: str  # eq | ne | in | contains | gte | gt | lte | lt
    value: Any


@dataclass(slots=True)
class Q:
    scope: dict[str, Any]
    select: str
    where: list[Pred] = field(default_factory=list)
    measure: str = "incl"
    metrics: list[str] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    bucket: str | None = None
    agg: str | None = None
    outliers: dict[str, Any] | None = None
    compare: str | None = None
    order_metric: str | None = None
    order_dir: str = "desc"
    limit: int = 50
    cursor: str | None = None
    tz: str = ""  # the zone of bucket.time day/week/hour edges (D50); parse_query always sets it

    @property
    def kind(self) -> str:
        """Row source: ops, events or steps."""
        return self.select if self.select in ("events", "steps") else "ops"

    @property
    def aggregated(self) -> bool:
        return bool(self.group_by) or self.bucket is not None or self.agg is not None


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise QueryError(msg)


def _scope(scope: Any) -> dict[str, Any]:
    _need(isinstance(scope, dict) and len(scope) == 1, "scope must have exactly one of op, sessions, all")
    if "op" in scope:
        _need(isinstance(scope["op"], str) and scope["op"] != "", "scope.op must be an id string")
    elif "all" in scope:
        _need(scope["all"] is True, "scope.all must be true")
    elif "sessions" in scope:
        f = scope["sessions"]
        _need(isinstance(f, dict), "scope.sessions must be an object")
        bad = set(f) - {"provider", "project", "account", "from", "to"}
        _need(not bad, f"unknown scope.sessions keys {sorted(bad)}")
        for k in ("from", "to"):
            if k in f:
                _need(_is_int(f[k]), f"scope.sessions.{k} must be epoch ms")
        for k in ("provider", "project", "account"):
            if k in f:
                _need(isinstance(f[k], str), f"scope.sessions.{k} must be a string")
    else:
        raise QueryError(f"unknown scope {sorted(scope)}")
    return scope


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _preds(where: Any) -> list[Pred]:
    _need(isinstance(where, list), "where must be a list")
    out: list[Pred] = []
    for p in where:
        _need(isinstance(p, dict) and isinstance(p.get("field"), str), "each where item needs a field")
        ops = {k: v for k, v in p.items() if k != "field"}
        _need(len(ops) >= 1, f"where {p['field']!r} has no operator")
        if len(ops) > 1:
            _need(set(ops) <= set(_RANGE_OPS), "only gte/gt/lte/lt combine in one where item")
        for op, v in ops.items():
            if op in _RANGE_OPS:
                _need(_is_num(v), f"where {op} needs a number")
            elif op == "in":
                _need(isinstance(v, list) and len(v) > 0, "where in needs a nonempty list")
            elif op == "contains":
                _need(isinstance(v, str), "where contains needs a string")
            elif op in ("eq", "ne"):
                _need(v is None or isinstance(v, (str, int, float, bool)), f"where {op} needs a scalar")
            else:
                raise QueryError(f"unknown where operator {op!r}")
            out.append(Pred(p["field"], op, v))
    return out


def parse_query(q: Any) -> Q:
    _need(isinstance(q, dict), "query must be a JSON object")
    bad = set(q) - _KEYS
    _need(not bad, f"unknown query keys {sorted(bad)}")
    _need("scope" in q and "select" in q, "query needs scope and select")
    _need(q["select"] in SELECTS, f"select must be one of {SELECTS}")
    out = Q(scope=_scope(q["scope"]), select=q["select"])
    tz = q.get("tz")
    _need(tz is None or isinstance(tz, str), "tz must be a zone name")
    # FALLBACK-OK: D50 — a request without a zone takes the answering machine's zone (QRY-3)
    out.tz = zone.resolve_tz() if tz is None else tz
    zone.zone_of(out.tz)
    if q.get("where") is not None:
        out.where = _preds(q["where"])
    out.measure = q.get("measure") or "incl"  # FALLBACK-OK: design §5 measure default incl
    _need(out.measure in ("incl", "own"), "measure must be incl or own")

    allowed = {"ops": OP_METRICS, "events": EVENT_METRICS, "steps": STEP_METRICS}[out.kind]
    default = {"ops": ["cost_usd", "tokens.total", "wall_ms"], "events": [], "steps": ["dur_ms"]}[out.kind]
    metrics = q.get("metrics")
    if metrics is None:
        metrics = default
    _need(isinstance(metrics, list), "metrics must be a list")
    unknown = [m for m in metrics if m not in allowed]
    _need(not unknown, f"metrics {unknown} not available for select {out.select}")
    out.metrics = list(dict.fromkeys(metrics))

    groups = q.get("group_by") or []
    _need(isinstance(groups, list), "group_by must be a list")
    known = GROUPS[out.kind]
    unknown = [g for g in groups if g not in known]
    _need(not unknown, f"group_by {unknown} not available for select {out.select}")
    out.group_by = list(dict.fromkeys(groups))

    bucket = q.get("bucket")
    if bucket is not None:
        _need(isinstance(bucket, dict) and set(bucket) == {"time"}, "bucket must be {time: hour|day|week}")
        _need(bucket["time"] in BUCKET_MS, "bucket.time must be hour, day or week (no rolling windows, D16)")
        out.bucket = bucket["time"]

    agg = q.get("agg")
    if agg is not None:
        _need(agg in AGGS, f"agg must be one of {AGGS}")
        out.agg = agg
    elif out.group_by or out.bucket:
        out.agg = "sum"

    outl = q.get("outliers")
    if outl is not None:
        _need(
            isinstance(outl, dict) and outl.get("over") in ("p50", "p90", "p95"), "outliers.over must be p90"
        )
        _need(set(outl) <= {"over", "with_top_child"}, "outliers keys are over, with_top_child")
        _need(out.kind == "ops", "outliers apply to op rows")
        _need(not out.aggregated, "outliers return op rows; drop agg, group_by and bucket")
        out.outliers = {"over": outl["over"], "with_top_child": bool(outl.get("with_top_child", False))}

    compare = q.get("compare")
    if compare is not None:
        _need(compare == "previous_bucket", "compare must be previous_bucket")
        _need(out.bucket is not None, "compare needs bucket")
        out.compare = compare

    order = q.get("order_by")
    if order is not None:
        _need(isinstance(order, dict) and set(order) <= {"metric", "dir"}, "order_by is {metric, dir}")
        out.order_metric = order.get("metric")
        out.order_dir = order.get("dir", "desc")  # FALLBACK-OK: design §5 order_by dir default desc
        _need(out.order_dir in ("asc", "desc"), "order_by.dir must be asc or desc")
        if out.order_metric == "n":
            _need(out.aggregated, "order_by n needs group_by, bucket or agg")
        else:
            _need(out.order_metric in allowed, f"order_by metric {out.order_metric!r} not available")
    elif out.bucket is not None:
        out.order_metric, out.order_dir = "bucket", "asc"
    elif out.kind != "ops" and not out.aggregated:
        out.order_metric, out.order_dir = "start_ms", "asc"
    elif out.metrics:
        out.order_metric = out.metrics[0]
    elif out.aggregated:
        out.order_metric = "n"
    else:
        out.order_metric, out.order_dir = "start_ms", "asc"

    limit = q.get("limit", 50)
    _need(_is_int(limit) and 1 <= limit <= MAX_LIMIT, f"limit must be 1..{MAX_LIMIT}")
    out.limit = limit
    cursor = q.get("cursor")
    _need(cursor is None or isinstance(cursor, str), "cursor must be a string or null")
    out.cursor = cursor
    return out
