"""CONTRACT: the canonical stream — read, write, validate, and the one legacy normalizer.

A stream file is one bundle (one session: main transcript plus its subagents),
one JSON object per line, `rec` in op | step | event | source_file (design §4.2).
Since D52 the ingest pipeline passes bundles in memory and never reads or writes a stream file; this module's
file functions serve tests, fixtures and the Claude adapter's `run --out` dev command. `validate` is used by
`store.load`.

GUARANTEES:
- `read_bundle(path)` returns the bundle's records in file order with canonical
  field names regardless of legacy names; `read_bundles(dir)` does so for every
  `*.jsonl` in a directory (names starting with `_` skipped), sorted by name.
- `normalize(raw)` is the only place legacy names are accepted (drift list,
  use-cases.md §0.3 plus observed B1-B3 output): `tokens{in|input, out|output,
  cache_read, cache_write, cache_write_5m, cache_write_1h, reasoning}` ->
  flat `*_tok`; `provenance`/`metric_provenance{tokens,time}` -> `tok_prov`,
  `time_prov`; `time_prov: span` -> `derived`; `cost_basis: computed_nominal` ->
  `computed`; omp `ttft_provenance`/`token_provenance` -> `ttft_prov`/`tok_prov`;
  `price_version` -> `price_fingerprint`; flat event fields ->
  `fields`; Claude `limit_hit.window: 5h` -> `session`; `claude-code` ->
  `claude_code`; source_file `sha|sha_head` -> `sha_first_4k`, `mtime` ->
  `mtime_ns`. Keys with no canonical home are dropped and counted per key in
  `Bundle.dropped_fields`.
- `defects(bundle)` returns the exact defect list; `validate(bundle)` raises
  `StreamDefects` carrying that list when it is non-empty. Defect classes:
  `session_count` (not exactly one session op), `duplicate_op_id`, `dangling_ref`
  (op parent/caused_by, step turn_id/op_id, event op_id not in the bundle),
  `container_metric` (an own metric set on session/engagement/turn, invariant 2),
  `leaf_segments` (D19 `segments` set on llm_call/tool_call; containers only),
  `session_only_field` (title/git_branch/harness_version/first_line_ms off the session op),
  `ordinal_gap` (step ordinals per turn not 0..n-1), `over_length`
  (label/summary > 120, snippet > 160, target > 200), `bad_enum` (a closed
  value outside its enumeration, including `turn_kind`, `title_source` and a
  `source_file.ts_list` that is not a sorted int list), `kind_field`
  (`turn_kind`/`turn_origin` off a turn op, `waits_on_user` off a tool_call).
  `title_source`, `entrypoint` and `originator` are session-only fields.
- `write_bundle(bundle, out_dir)` writes `<bundle_id>.jsonl` via temp file +
  rename, records ordered source_file, op, step, event, None fields omitted.
  `source_file.ts_list` (DM-13, DM-40) round-trips through write and read.
EXPECTS: one bundle per file.
FAILURE BEHAVIOR: a trailing line without a newline is skipped and counted in
  `n_partial`; an unparsable complete line is skipped and counted in
  `n_malformed`; an unknown `rec` raises `StreamError`.
DOES NOT: compute metrics; touch the store; resolve cross-bundle ownership
  (`duplicate_of` may name an op in another bundle and is not checked).
KNOWN LIMITATIONS: legacy events without `event_id` get
  `<provider>/<session>/event/<kind>#<n>` (n = per-kind ordinal in file order),
  stable while the file is append-only. Legacy mtime unit (s float vs ms int) is
  inferred from magnitude.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from botsworth.model import (
    ARGS_JSON_MAX,
    CONTAINER_KINDS,
    LABEL_MAX,
    OWN_METRIC_FIELDS,
    REC_NAME,
    REC_TYPES,
    SESSION_ONLY_FIELDS,
    SNIPPET_MAX,
    SUMMARY_MAX,
    TARGET_MAX,
    AdapterTitleSource,
    Bundle,
    CostBasis,
    Event,
    EventBasis,
    EventKind,
    IdProv,
    Kind,
    Op,
    Prov,
    Record,
    Role,
    SourceFile,
    Step,
    SubItem,
    TurnKind,
    Verb,
    is_link_basis,
    is_outcome,
)


class StreamError(ValueError):
    """A stream that cannot be read as canonical records."""


@dataclass(frozen=True, slots=True)
class Defect:
    kind: str
    ref: str
    detail: str


class StreamDefects(StreamError):
    def __init__(self, bundle_id: str, defects: list[Defect]):
        self.defects = defects
        super().__init__(f"bundle {bundle_id}: {len(defects)} defects, first: {defects[0]}")


_FIELDS: dict[str, frozenset[str]] = {
    name: frozenset(f.name for f in fields(cls)) for name, cls in REC_TYPES.items()
}
_TOKEN_KEYS = {
    "in_tok": ("in", "input"),
    "out_tok": ("out", "output"),
    "cache_read_tok": ("cache_read",),
    "cache_write_tok": ("cache_write", "cache_write_total"),
    "cache_write_5m_tok": ("cache_write_5m",),
    "cache_write_1h_tok": ("cache_write_1h",),
    "reasoning_tok": ("reasoning",),
}
_LEGACY_ENUM = {
    "time_prov": {"span": "derived"},
    "cost_basis": {"computed_nominal": "computed"},
}
_EVENT_TOP = frozenset({"event_id", "kind", "ts_ms", "session_id", "op_id", "account", "basis", "src"})
_EVENT_DROPPED = frozenset({"provider", "project"})  # derivable from the session op (store joins them)
_LIMIT_WINDOW = {"5h": "session", "week": "weekly", "7d": "weekly"}


def _mtime_ns(value: float | int) -> int:
    if isinstance(value, float) or value < 10**11:  # seconds
        return int(value * 1_000_000_000)
    if value < 10**14:  # milliseconds
        return int(value) * 1_000_000
    return int(value)


def normalize(raw: dict[str, Any], provider: str | None = None) -> tuple[dict[str, Any], list[str]]:
    """Legacy -> canonical for one record. Returns (canonical dict, dropped keys)."""
    d = dict(raw)
    rec = d.pop("rec", None)
    if rec not in REC_TYPES:
        raise StreamError(f"unknown rec {rec!r}")
    if d.get("provider") == "claude-code":
        d["provider"] = "claude_code"
    if rec == "op":
        tokens = d.pop("tokens", None) or {}  # FALLBACK-OK: design §4.1 inv 2 containers own no tokens
        for col, keys in _TOKEN_KEYS.items():
            if col not in d:
                d[col] = next((tokens[k] for k in keys if k in tokens), None)
        tiers = (d["cache_write_5m_tok"], d["cache_write_1h_tok"])
        if d["cache_write_tok"] is None and tiers != (None, None):
            d["cache_write_tok"] = (d["cache_write_5m_tok"] or 0) + (d["cache_write_1h_tok"] or 0)
        prov = d.pop("provenance", None) or d.pop("metric_provenance", None) or {}  # FALLBACK-OK: §4.2
        d.pop("metric_provenance", None)
        for col, legacy in (("ttft_prov", "ttft_provenance"), ("tok_prov", "token_provenance")):
            if legacy in d and d.get(col) is None:
                d[col] = d.pop(legacy)
        for col, key in (("tok_prov", "tokens"), ("time_prov", "time")):
            if d.get(col) not in Prov.__members__.values() and key in prov:
                d[col] = prov[key]
        if "price_version" in d and d.get("price_fingerprint") is None:
            d["price_fingerprint"] = d.pop("price_version")
        for col, table in _LEGACY_ENUM.items():
            if d.get(col) in table:
                d[col] = table[d[col]]
    elif rec == "event":
        payload = dict(d.pop("fields", None) or {})  # FALLBACK-OK: design §4.11 legacy flat fields
        for k in list(d):
            if k not in _EVENT_TOP and k not in _EVENT_DROPPED:
                payload[k] = d.pop(k)
        if d.get("kind") == "limit_hit" and payload.get("window") in _LIMIT_WINDOW:
            payload["window"] = _LIMIT_WINDOW[payload["window"]]
        d["fields"] = payload
    elif rec == "source_file":
        for legacy in ("sha", "sha_head"):
            if legacy in d and d.get("sha_first_4k") is None:
                d["sha_first_4k"] = d.pop(legacy)
        if d.get("mtime_ns") is None and d.get("mtime") is not None:
            d["mtime_ns"] = _mtime_ns(d.pop("mtime"))
        if d.get("provider") is None and provider is not None:
            d["provider"] = provider
    known = _FIELDS[rec]
    dropped = [k for k in d if k not in known]
    for k in dropped:
        del d[k]
    d["rec"] = rec
    return d, dropped


def _build(d: dict[str, Any]) -> Record:
    rec = d.pop("rec")
    if rec == "step" and d.get("sub_items") is not None:
        d["sub_items"] = [SubItem(**s) for s in d["sub_items"]]
    try:
        return REC_TYPES[rec](**d)
    except TypeError as exc:  # missing required field
        raise StreamError(f"{rec}: {exc}") from exc


def _lines(path: Path) -> Iterator[tuple[bytes, bool]]:
    with path.open("rb") as f:
        for line in f:
            yield line, line.endswith(b"\n")


def read_bundle(path: Path | str) -> Bundle:
    path = Path(path)
    raws: list[dict[str, Any]] = []
    n_malformed = n_partial = 0
    for line, complete in _lines(path):
        if not complete:
            n_partial += 1
            continue
        if not line.strip():
            continue
        try:
            raws.append(json.loads(line))
        except json.JSONDecodeError:
            n_malformed += 1
    sessions = [r for r in raws if r.get("rec") == "op" and r.get("kind") == "session"]
    if len(sessions) == 1:
        provider, session_id = sessions[0].get("provider"), sessions[0].get("session_id")
        if not provider or not session_id:
            raise StreamError(f"{path}: session op lacks provider or session_id")
    else:  # defects() reports session_count; keep reading so the defect list is complete
        provider, session_id = "", ""
    provider = "claude_code" if provider == "claude-code" else provider
    bundle = Bundle(provider=provider, bundle_id=path.stem, session_id=session_id)
    dropped: Counter[str] = Counter()
    per_kind: Counter[str] = Counter()
    for raw in raws:
        d, lost = normalize(raw, provider)
        dropped.update(lost)
        if (
            d["rec"] in ("event", "step")
            and d.get("session_id") is None
            and "session_id" in _FIELDS[d["rec"]]
        ):
            d["session_id"] = session_id
        if d["rec"] == "event" and not d.get("event_id"):
            kind = d.get("kind")
            d["event_id"] = f"{provider}/{d.get('session_id', session_id)}/event/{kind}#{per_kind[kind]}"
            per_kind[kind] += 1
        record = _build(d)
        _bucket(bundle, record).append(record)
    bundle.n_malformed, bundle.n_partial = n_malformed, n_partial
    bundle.dropped_fields = dict(dropped)
    return bundle


def read_bundles(directory: Path | str) -> Iterator[Bundle]:
    for path in sorted(Path(directory).glob("*.jsonl")):
        if not path.name.startswith("_"):
            yield read_bundle(path)


def _bucket(bundle: Bundle, record: Record) -> list:
    if isinstance(record, Op):
        return bundle.ops
    if isinstance(record, Step):
        return bundle.steps
    if isinstance(record, Event):
        return bundle.events
    return bundle.source_files


def to_dict(record: Record) -> dict[str, Any]:
    out: dict[str, Any] = {"rec": REC_NAME[type(record)]}
    for f in fields(record):
        value = getattr(record, f.name)
        if value is None:
            continue
        if f.name == "sub_items":
            value = [{k: getattr(s, k) for k in ("tool", "target", "outcome")} for s in value]
        out[f.name] = value
    return out


def write_bundle(bundle: Bundle, out_dir: Path | str) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{bundle.bundle_id}.jsonl"
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{bundle.bundle_id}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for record in (*bundle.source_files, *bundle.ops, *bundle.steps, *bundle.events):
                f.write(json.dumps(to_dict(record), ensure_ascii=False) + "\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def _enum(defects: list[Defect], ref: str, name: str, value: Any, allowed) -> None:
    if value is not None and value not in allowed:
        defects.append(Defect("bad_enum", ref, f"{name}={value!r}"))


def _long(defects: list[Defect], ref: str, name: str, value: str | None, limit: int) -> None:
    if value is not None and len(value) > limit:
        defects.append(Defect("over_length", ref, f"{name} {len(value)} > {limit}"))


def defects(bundle: Bundle) -> list[Defect]:
    out: list[Defect] = []
    sessions = [o for o in bundle.ops if o.kind == Kind.SESSION]
    if len(sessions) != 1:
        out.append(Defect("session_count", bundle.bundle_id, f"{len(sessions)} session ops"))
    ids: set[str] = set()
    for o in bundle.ops:
        if o.op_id in ids:
            out.append(Defect("duplicate_op_id", o.op_id, "op_id repeated"))
        ids.add(o.op_id)
    provs = Prov.__members__.values()
    for o in bundle.ops:
        for name in ("parent", "caused_by"):
            target = getattr(o, name)
            if target is not None and target not in ids:
                out.append(Defect("dangling_ref", o.op_id, f"{name} -> {target}"))
        if o.kind != Kind.SESSION:
            for name in SESSION_ONLY_FIELDS:
                if getattr(o, name) is not None:
                    out.append(Defect("session_only_field", o.op_id, f"{name} set on {o.kind}"))
        if o.segments is not None and o.kind not in CONTAINER_KINDS:
            out.append(Defect("leaf_segments", o.op_id, f"segments set on {o.kind}"))
        if o.kind in CONTAINER_KINDS:
            for name in OWN_METRIC_FIELDS:
                if getattr(o, name) is not None:
                    out.append(Defect("container_metric", o.op_id, f"{name} set on {o.kind}"))
        _enum(out, o.op_id, "kind", o.kind, Kind.__members__.values())
        for name in ("time_prov", "tok_prov", "ttft_prov", "exit_code_prov"):
            _enum(out, o.op_id, name, getattr(o, name), provs)
        _enum(out, o.op_id, "cost_basis", o.cost_basis, CostBasis.__members__.values())
        _enum(out, o.op_id, "id_prov", o.id_prov, IdProv.__members__.values())
        _enum(out, o.op_id, "role", o.role, Role.__members__.values())
        _enum(out, o.op_id, "turn_kind", o.turn_kind, TurnKind.__members__.values())
        _enum(out, o.op_id, "title_source", o.title_source, AdapterTitleSource.__members__.values())
        if o.kind != Kind.TURN:
            for name in ("turn_kind", "turn_origin"):
                if getattr(o, name) is not None:
                    out.append(Defect("kind_field", o.op_id, f"{name} set on {o.kind}"))
        if o.waits_on_user is not None and o.kind != Kind.TOOL_CALL:
            out.append(Defect("kind_field", o.op_id, f"waits_on_user set on {o.kind}"))
        if o.caused_by_basis is not None and not is_link_basis(o.caused_by_basis):
            out.append(Defect("bad_enum", o.op_id, f"caused_by_basis={o.caused_by_basis!r}"))
        _long(out, o.op_id, "label", o.label, LABEL_MAX)
        _long(out, o.op_id, "args_json", o.args_json, ARGS_JSON_MAX)
    ordinals: dict[str, list[int]] = defaultdict(list)
    for s in bundle.steps:
        ref = f"{s.turn_id}@{s.ordinal}"
        ordinals[s.turn_id].append(s.ordinal)
        for name in ("turn_id", "op_id"):
            if getattr(s, name) not in ids:
                out.append(Defect("dangling_ref", ref, f"step {name} -> {getattr(s, name)}"))
        _enum(out, ref, "verb", s.verb, Verb.__members__.values())
        if s.outcome is not None and not is_outcome(s.outcome):
            out.append(Defect("bad_enum", ref, f"outcome={s.outcome!r}"))
        _long(out, ref, "summary", s.summary, SUMMARY_MAX)
        _long(out, ref, "snippet", s.snippet, SNIPPET_MAX)
        _long(out, ref, "target", s.target, TARGET_MAX)
    for turn, seen in ordinals.items():
        if sorted(seen) != list(range(len(seen))):
            out.append(Defect("ordinal_gap", turn, f"ordinals {sorted(seen)[:10]}"))
    for f in bundle.source_files:
        ts = f.ts_list
        if ts is not None and not (
            isinstance(ts, list)
            and all(isinstance(t, int) and not isinstance(t, bool) for t in ts)
            and all(a <= b for a, b in zip(ts, ts[1:], strict=False))
        ):
            out.append(Defect("bad_enum", f.path, "ts_list must be a sorted list of ints"))
    for e in bundle.events:
        if e.op_id is not None and e.op_id not in ids:
            out.append(Defect("dangling_ref", e.event_id, f"event op_id -> {e.op_id}"))
        _enum(out, e.event_id, "kind", e.kind, EventKind.__members__.values())
        _enum(out, e.event_id, "basis", e.basis, EventBasis.__members__.values())
    return out


def validate(bundle: Bundle) -> None:
    found = defects(bundle)
    if found:
        raise StreamDefects(bundle.bundle_id, found)


__all__ = [
    "Defect",
    "SourceFile",
    "StreamDefects",
    "StreamError",
    "defects",
    "normalize",
    "read_bundle",
    "read_bundles",
    "to_dict",
    "validate",
    "write_bundle",
]
