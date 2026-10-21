"""CONTRACT: the canonical record vocabulary shared by every layer.

GUARANTEES:
- `Op`, `Step`, `Event`, `SourceFile` carry exactly the canonical field names of
  design.md §4.2 (op), §4.10 (step), §4.11 (event), §4.2 `source_files`
  (source_file). Field names are the contract (design §4.10a); the only place
  legacy names are accepted is `botsworth.stream.normalize`.
- Enumerations name every closed value set of the design: op kind, provenance,
  cost basis, link basis, verb, outcome, event kind, error kind, role, id
  provenance. `is_link_basis` and `is_outcome` cover the two open-ended forms
  (`<basis>+cycle`, `exit:<n>`).
- `OWN_METRIC_FIELDS` lists the fields only leaf ops may set (invariant 2).
- v7 fields (data-model.md §2.2, §3): `Op.turn_kind`/`turn_origin` (turn only), `Op.user_wait`
  (tool_call only), `Op.title_source`/`entrypoint`/`originator` (session only, in
  `SESSION_ONLY_FIELDS`), `SourceFile.ts_list` (sorted epoch ms, stream only, DM-13). `TurnKind` and
  `AdapterTitleSource` name the closed values; `PREVIEW_MAX` is the stored-preview cap (DM-9).
- D55 fields (tool_call only): `Op.bg_end_ms` (DM-41), `Op.command_class` (DM-28, enum `CommandClass`),
  `Op.tests_passed`/`tests_failed` (DM-29, counts only). `EventKind.NOTICE` is the DM-41 task notice.
- `Bundle` is one session's records in file order plus reader counters.
EXPECTS: callers construct records with keyword arguments.
FAILURE BEHAVIOR: none here; validation lives in `botsworth.stream`.
DOES NOT: validate, compute, read or write files.
KNOWN LIMITATIONS: steps have no stable `step_id` field in design §4.10; the
  query layer derives one (T5). Store-derived columns (rowid, inclusive rollups,
  `cost_lower_bound`, `path_sig`) are not record fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Kind(StrEnum):
    SESSION = "session"
    ENGAGEMENT = "engagement"
    TURN = "turn"
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"


LEAF_KINDS = frozenset({Kind.LLM_CALL, Kind.TOOL_CALL})
CONTAINER_KINDS = frozenset({Kind.SESSION, Kind.ENGAGEMENT, Kind.TURN})


class Provider(StrEnum):
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    OMP = "omp"


class Prov(StrEnum):
    """Metric provenance (design §2 D4)."""

    PRESENT = "present"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    MISSING = "missing"


class CostBasis(StrEnum):
    REPORTED = "reported"
    COMPUTED = "computed"
    MISSING = "missing"


class LinkBasis(StrEnum):
    """`caused_by_basis` (design §4.5); may carry the `+cycle` suffix."""

    ID = "id"
    CONTENT_HASH = "content_hash"
    NAME = "name"
    TIME_HEURISTIC = "time-heuristic"
    NONE = "none"


CYCLE_SUFFIX = "+cycle"


def is_link_basis(value: str) -> bool:
    base = value.removesuffix(CYCLE_SUFFIX)
    return base in LinkBasis.__members__.values()


class IdProv(StrEnum):
    NATURAL = "natural"
    DERIVED = "derived"


class Role(StrEnum):
    MAIN = "main"
    AUX = "aux"


class ErrorKind(StrEnum):
    RATE_LIMIT = "rate_limit"
    OVERLOADED = "overloaded"
    ABORTED = "aborted"
    ERROR = "error"


class Verb(StrEnum):
    THINK = "think"
    SAY = "say"
    READ = "read"
    WRITE = "write"
    EDIT = "edit"
    RUN = "run"
    SEARCH = "search"
    FETCH = "fetch"
    SPAWN = "spawn"
    SKILL = "skill"
    ASK_USER = "ask-user"
    ANSWER = "answer"
    OTHER = "other"


class Outcome(StrEnum):
    """Closed outcomes; `exit:<n>` is the open form (see `is_outcome`)."""

    OK = "ok"
    ERROR = "error"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


_EXIT = re.compile(r"exit:-?\d+")


def is_outcome(value: str) -> bool:
    return value in Outcome.__members__.values() or _EXIT.fullmatch(value) is not None


class UserWait(StrEnum):
    """DM-40 [D54]: why a tool_call waited on the user."""

    ASK = "ask"
    PERMISSION = "permission"


class CommandClass(StrEnum):
    """DM-28 [D55]: what a shell command is, when it is one of these."""

    TEST = "test"
    WAIT = "wait"


class TurnKind(StrEnum):
    """DM-20: what a main turn is. `turn_origin` carries the open-ended detail."""

    HUMAN = "human"
    MESSAGE = "message"
    HARNESS = "harness"


class AdapterTitleSource(StrEnum):
    """DM-10: where the adapter found the session `title` (the store adds `first_ask`, `fallback`)."""

    RENAME = "rename"
    GENERATED = "generated"
    THREAD_TITLE = "thread_title"


class EventKind(StrEnum):
    COMPACTION = "compaction"
    LIMIT_STATE = "limit_state"
    LIMIT_HIT = "limit_hit"
    API_ERROR = "api_error"
    SKILL_INVOCATION = "skill_invocation"
    MODEL_CHANGE = "model_change"
    RESUME = "resume"
    ROLLBACK = "rollback"
    NOTICE = "notice"  # DM-41 [D55]: a task notification queued to a thread


class EventBasis(StrEnum):
    PRESENT = "present"
    DERIVED = "derived"
    ESTIMATED = "estimated"


# Display bounds (invariant 7, §4.10).
LABEL_MAX = 120
SUMMARY_MAX = 120
SNIPPET_MAX = 160
TARGET_MAX = 200
PREVIEW_MAX = 160  # DM-9: longest text the store keeps for a command, snippet, target or file path
ARGS_JSON_MAX = 2048

TOKEN_FIELDS = (
    "in_tok",
    "cache_read_tok",
    "cache_write_tok",
    "cache_write_5m_tok",
    "cache_write_1h_tok",
    "out_tok",
    "reasoning_tok",
)
COST_FIELDS = ("cost_usd", "cost_in_usd", "cost_out_usd", "cost_cache_read_usd", "cost_cache_write_usd")
# Invariant 2: non-null only on llm_call / tool_call.
SESSION_ONLY_FIELDS = (
    "title",
    "git_branch",
    "harness_version",
    "first_line_ms",
    "title_source",
    "entrypoint",
    "originator",
)
OWN_METRIC_FIELDS = (*TOKEN_FIELDS, "context_tokens", *COST_FIELDS, "dur_ms")


@dataclass(slots=True, kw_only=True)
class Op:
    """design §4.2 `rec = "op"`; `op_id` grammar in §4.3."""

    op_id: str
    kind: str
    provider: str
    session_id: str
    src: str
    parent: str | None = None  # None only on the session op
    seq: int | None = None
    depth: int | None = None
    caused_by: str | None = None
    caused_by_basis: str | None = None
    agent_id: str | None = None
    engagement_ix: int | None = None
    project: str | None = None
    cwd: str | None = None
    account: str | None = None
    agent_type: str | None = None
    agent_name: str | None = None
    spawn_depth: int | None = None
    is_fork: bool | None = None
    occ_key: str | None = None
    duplicate_of: str | None = None
    id_prov: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    dur_ms: int | None = None
    time_prov: str | None = None
    ttft_ms: int | None = None
    ttft_prov: str | None = None
    is_async: bool | None = None
    in_tok: int | None = None
    cache_read_tok: int | None = None
    cache_write_tok: int | None = None
    cache_write_5m_tok: int | None = None
    cache_write_1h_tok: int | None = None
    out_tok: int | None = None
    reasoning_tok: int | None = None
    context_tokens: int | None = None
    tok_prov: str | None = None
    cost_usd: float | None = None
    cost_in_usd: float | None = None
    cost_out_usd: float | None = None
    cost_cache_read_usd: float | None = None
    cost_cache_write_usd: float | None = None
    cost_basis: str | None = None
    price_alias: str | None = None
    price_fingerprint: str | None = None
    cut_off: bool | None = None
    stop_reason: str | None = None
    error_kind: str | None = None
    model: str | None = None
    model_context_window: int | None = None
    tool_name: str | None = None
    command: str | None = None
    command_norm: str | None = None  # D17 full normalized command
    command_key: str | None = None  # D17 coarse grouping key
    command_class: str | None = None  # DM-28 [D55] tool_call only: CommandClass
    bg_end_ms: int | None = None  # DM-41 [D55] tool_call only: queue time of a background command's notice
    tests_passed: int | None = None  # DM-29 [D55] tool_call only, command_class "test"
    tests_failed: int | None = None  # DM-29 [D55] tool_call only: failed + errors
    file_path: str | None = None
    exit_code: int | None = None
    exit_code_prov: str | None = None
    is_error: bool | None = None
    interrupted: bool | None = None
    result_bytes: int | None = None
    role: str | None = None
    label: str | None = None
    args_json: str | None = None
    title: str | None = None  # session only
    git_branch: str | None = None  # session only
    harness_version: str | None = None  # session only
    first_line_ms: int | None = None  # D23 session only: first timestamped record of the main file
    title_source: str | None = None  # DM-10 session only: AdapterTitleSource
    entrypoint: str | None = None  # DM-26 session only
    originator: str | None = None  # DM-26 session only
    turn_kind: str | None = None  # DM-20 turn only: TurnKind
    turn_origin: str | None = None  # DM-20 turn only
    user_wait: str | None = None  # DM-40, D54 tool_call only: "ask" | "permission" (enum UserWait)
    segments: list[list[int]] | None = None  # D19: [[start_ms, end_ms], ...]; containers only


@dataclass(slots=True, kw_only=True)
class SubItem:
    """One inner call of a Codex `exec` script step (§4.10 R-outline)."""

    tool: str
    target: str | None = None
    outcome: str | None = None


@dataclass(slots=True, kw_only=True)
class Step:
    """design §4.10 `rec = "step"`; a display unit that owns no metrics."""

    turn_id: str
    ordinal: int
    op_id: str
    verb: str
    src: str
    tool_name: str | None = None
    target: str | None = None
    outcome: str | None = None
    summary: str | None = None
    snippet: str | None = None
    start_ms: int | None = None
    dur_ms: int | None = None
    skill: str | None = None  # D15: always None in V1
    sub_items: list[SubItem] | None = None


@dataclass(slots=True, kw_only=True)
class Event:
    """design §4.11 `rec = "event"`; per-kind payload under `fields`."""

    event_id: str
    kind: str
    session_id: str
    src: str
    ts_ms: int | None = None
    op_id: str | None = None
    account: str | None = None
    basis: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class SourceFile:
    """design §6.4 `rec = "source_file"`: change-detection identity of one file."""

    provider: str
    path: str
    size: int
    mtime_ns: int | None = None
    sha_first_4k: str | None = None
    ts_list: list[int] | None = None  # DM-13: sorted epoch ms of every timestamped record; stream only


Record = Op | Step | Event | SourceFile

REC_TYPES: dict[str, type] = {"op": Op, "step": Step, "event": Event, "source_file": SourceFile}
REC_NAME: dict[type, str] = {v: k for k, v in REC_TYPES.items()}


@dataclass(slots=True, kw_only=True)
class Bundle:
    """One session's canonical stream (main transcript plus its subagents)."""

    provider: str
    bundle_id: str
    session_id: str
    ops: list[Op] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    source_files: list[SourceFile] = field(default_factory=list)
    n_malformed: int = 0
    n_partial: int = 0
    dropped_fields: dict[str, int] = field(default_factory=dict)
