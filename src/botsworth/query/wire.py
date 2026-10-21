"""CONTRACT: the wire types of docs/specs/interfaces.md §3.3–§3.9 and §5.2, as frozen dataclasses (QRY-21).

GUARANTEES:
- One dataclass per wire type; the name is the spec name (`SessionRow`, `TurnRows`, ...) and the field
  order is the JSON key order.
- `Money` fields are flattened into the object that uses them (§3.3): `cost_usd`, `cost_lower_bound`,
  `n_unpriced`. A field named `cost: Money` holds a `Money` object, as the spec does.
- Order rule for composite types: `A & B & {own}` and `extends A, B` list A's fields, then B's, then own
  fields; `{own} & Omit<X, 'type'>` lists own fields, then X's fields without `type`.
- `type`, `kind` and `api_version` are keyword-only fields with their fixed value as default, so a builder
  cannot omit or misplace them and the JSON order is still the spec order.
- Unions (`Detail`, the row unions) are separate dataclasses discriminated by `kind` or `type`.
- Optional keys of the spec (`by_model?`, `turn_starts?`) are two classes, not a nullable field: a session
  histogram bin has no `by_model` key.
- `CallRow.tok` is a `Tok` TypedDict (a dict): its key `in` is a Python keyword.
- `to_json(obj)` is the one serialiser of HTTP and CLI: `dataclasses.asdict`, then one-line UTF-8 JSON.
EXPECTS: callers pass values of the annotated types; nothing is validated at runtime.
FAILURE BEHAVIOR: `to_json` raises `ValueError` on NaN or infinity and `TypeError` on a value JSON cannot
  hold; both reach the HTTP layer as a 500.
DOES NOT: read the store, compute figures or hold logic.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, TypedDict

# ── §3.3 shared ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Freshness:
    last_ingest_ms: int | None
    parser_version: str
    price_fingerprint: str
    schema_version: int
    api_version: int = field(default=1, kw_only=True)


@dataclass(frozen=True)
class Money:
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int


@dataclass(frozen=True)
class TimePart:
    state: str
    substate: str | None
    ms: int


@dataclass(frozen=True)
class TimeSplit:
    """DM-51, DM-53 [D54]: the parts above 0 ms in SES-15 order, then substate order."""

    parts: list[TimePart]


@dataclass(frozen=True)
class ModelLine:
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    model: str
    n_calls: int
    model_ms: int
    speed_tok_s: float | None
    speed_basis: str
    ttft_ms_mean: float | None


@dataclass(frozen=True)
class ProjectRef:
    project: str
    project_name: str
    color_slot: int | None


@dataclass(frozen=True)
class RowRef:
    type: str
    id: str


# ── §3.4 explorer ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PeriodTotals:
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    tok_total: int | None
    n_sessions: int
    n_sessions_period: int
    n_automated_hidden: int


@dataclass(frozen=True)
class PeriodProject:
    project: str
    project_name: str
    color_slot: int | None
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    tok_total: int | None
    n_sessions: int


@dataclass(frozen=True)
class DayCount:
    day: str
    n_sessions: int


@dataclass(frozen=True)
class PeriodEmpty:
    all_time_matches: int | None
    prev_day: DayCount | None
    next_day: DayCount | None


@dataclass(frozen=True)
class PeriodSummary:
    freshness: Freshness
    first_day: str | None
    last_day: str | None
    totals: PeriodTotals
    projects: list[PeriodProject]
    empty: PeriodEmpty | None


@dataclass(frozen=True)
class DayProject:
    project: str
    project_name: str
    color_slot: int | None
    cost_usd: float
    tok_total: int


@dataclass(frozen=True)
class DayBar:
    day: str
    from_day: str
    to_day: str
    cost_usd: float
    cost_lower_bound: bool
    n_unpriced: int
    tok_total: int
    n_sessions: int
    by_project: list[DayProject]


@dataclass(frozen=True)
class DaySeries:
    freshness: Freshness
    days: list[DayBar]


@dataclass(frozen=True)
class Activity:
    bin_ms: int
    tok: list[int]


@dataclass(frozen=True)
class DaySplit:
    day: str
    cost_usd: float
    tok_total: int


@dataclass(frozen=True)
class SessionRow:
    project: str
    project_name: str
    color_slot: int | None
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    session: str
    session_id: str
    provider: str
    name: str
    title_source: str
    start_ms: int
    end_ms: int
    ends_after_days: int
    tok_total: int | None
    wall_ms: int
    active_ms: int
    is_automated: bool
    n_agents: int
    git_branch: str | None
    activity: Activity
    day_split: list[DaySplit]


@dataclass(frozen=True)
class PeriodActivity:
    bin_ms: int
    start_ms: int
    tok: list[int]


@dataclass(frozen=True)
class SessionGroup:
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    key: str
    name: str
    color_slot: int | None
    n_sessions: int
    tok_total: int | None
    wall_ms: int
    active_ms: int
    period_activity: PeriodActivity
    rows: list[SessionRow]


@dataclass(frozen=True)
class SessionStats:
    max_activity_tok: int
    max_cost_usd: float
    cost_p90: float | None


@dataclass(frozen=True)
class SessionPage:
    freshness: Freshness
    total: int
    offset: int
    limit: int
    stats: SessionStats
    rows: list[SessionRow]
    groups: list[SessionGroup] | None


@dataclass(frozen=True)
class FacetValue:
    value: str
    n: int


@dataclass(frozen=True)
class AutomatedFacet:
    n: int


@dataclass(frozen=True)
class Facets:
    freshness: Freshness
    providers: list[FacetValue]
    models: list[FacetValue]
    branches: list[FacetValue]
    automated: AutomatedFacet


@dataclass(frozen=True)
class FilterValue:
    kind: str
    value: str
    label: str
    n: int


@dataclass(frozen=True)
class PaletteProject:
    project: str
    project_name: str
    color_slot: int | None
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    n_sessions: int
    n_automated: int


@dataclass(frozen=True)
class PaletteSession:
    session: str
    session_id: str
    provider: str
    name: str
    title_source: str
    project: str
    project_name: str
    color_slot: int | None
    start_ms: int
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    is_automated: bool
    first_day: str
    in_period: bool
    in_filters: bool


@dataclass(frozen=True)
class PaletteResult:
    freshness: Freshness
    filter_values: list[FilterValue]
    projects: list[PaletteProject]
    sessions: list[PaletteSession]


# ── §3.5 ingest status ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class IngestRunning:
    running: bool
    started_ms: int | None
    pid: int | None


@dataclass(frozen=True)
class IngestSource:
    provider: str
    root: str
    files: int
    bundles: int
    live: int
    last_ingest_ms: int | None
    rejected: int


@dataclass(frozen=True)
class LastRun:
    started_ms: int
    ended_ms: int
    bundles_changed: int
    rejected: list[Any]


@dataclass(frozen=True)
class IngestStatus:
    store_exists: bool
    store_state: str
    freshness: Freshness
    ingest: IngestRunning
    sources: list[IngestSource]
    last_run: LastRun | None


# ── §3.6 session blade ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class NextTurn:
    turn: str
    kind: str
    origin: str
    label: str
    display_id: str | None


@dataclass(frozen=True)
class RunningEngagement:
    row: str
    agent_name: str | None
    start_ms: int
    end_ms: int
    cost_usd: float | None


@dataclass(frozen=True)
class Stretch:
    start_ms: int
    end_ms: int
    dur_ms: int
    state: str
    substate: str | None
    turn: str | None
    next_turn: NextTurn | None
    running: list[RunningEngagement]


@dataclass(frozen=True)
class LongestTarget:
    row: RowRef
    turn: str
    dur_ms: int


@dataclass(frozen=True)
class Longest:
    model: LongestTarget | None
    tool: LongestTarget | None
    agents: LongestTarget | None
    you: LongestTarget | None
    agentmsg: LongestTarget | None
    limit: LongestTarget | None
    none: LongestTarget | None


@dataclass(frozen=True)
class SessionCost:
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    main: float | None
    agents: float | None
    unattributed: float | None


@dataclass(frozen=True)
class SessionInfo:
    project: str
    project_name: str
    color_slot: int | None
    session: str
    session_id: str
    provider: str
    name: str
    title_source: str
    cwd: str
    git_branch: str | None
    harness_version: str | None
    entrypoint: str | None
    originator: str | None
    start_ms: int
    end_ms: int
    ends_after_days: int
    is_automated: bool
    n_turns: int
    n_harness_turns: int
    cost: SessionCost
    tok_total: int | None
    wall_ms: int
    active_ms: int
    time_split: TimeSplit
    longest: Longest
    n_agents: int
    n_skill_loads: int
    n_failed: int
    n_llm_calls: int
    models: list[ModelLine]


@dataclass(frozen=True)
class SessionDetail:
    freshness: Freshness
    session: SessionInfo


@dataclass(frozen=True)
class TurnModel:
    model: str
    cost_usd: float | None


@dataclass(frozen=True)
class AgentRun:
    engagement: str
    agent_name: str | None
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class TurnRow:
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    type: str = field(default="turn", kw_only=True)
    turn: str
    display_id: str | None
    kind: str
    origin: str
    label: str
    start_ms: int | None
    end_ms: int | None
    wall_ms: int | None
    time_split: TimeSplit | None
    waiting_ms: int | None
    cost_main: float | None
    tok_total: int | None
    n_llm_calls: int
    models: list[TurnModel]
    n_agents: int
    n_skill_loads: int
    n_failed: int
    agent_runs: list[AgentRun]


@dataclass(frozen=True)
class GapRow:
    """`Stretch` fields, then `type` and `id` (`gap-<start_ms>`)."""

    start_ms: int
    end_ms: int
    dur_ms: int
    state: str
    substate: str | None
    turn: str | None
    next_turn: NextTurn | None
    running: list[RunningEngagement]
    type: str = field(default="gap", kw_only=True)
    id: str


@dataclass(frozen=True)
class TurnList:
    freshness: Freshness
    items: list[TurnRow | GapRow]


@dataclass(frozen=True)
class FindHit:
    row: RowRef
    turn: str
    display_id: str | None
    depth: int
    label: str
    value: float | None
    start_ms: int | None


@dataclass(frozen=True)
class FindResult:
    freshness: Freshness
    kind: str
    total: int
    hits: list[FindHit]
    per_turn: dict[str, int]


@dataclass(frozen=True)
class Resolved:
    freshness: Freshness
    row: RowRef
    turn: str
    depth: int


@dataclass(frozen=True)
class HistBin:
    """A session histogram bin: no `by_model` key. `main_cost_usd` counts main-thread calls only (D47)."""

    cost_usd: float
    main_cost_usd: float
    tok_total: int
    state_ms: TimeSplit


@dataclass(frozen=True)
class HistBinByModel:
    """A turn histogram bin; `by_model` maps model to `[cost_usd, tok_total]`."""

    cost_usd: float
    main_cost_usd: float
    tok_total: int
    state_ms: TimeSplit
    by_model: dict[str, list[float]]


@dataclass(frozen=True)
class TurnStartMark:
    turn: str
    display_id: str | None
    start_ms: int


@dataclass(frozen=True)
class SkillMark:
    row: RowRef
    start_ms: int
    skill: str


@dataclass(frozen=True)
class HistMarks:
    """Turn histogram marks: no `turn_starts` key."""

    skill_loads: list[SkillMark]


@dataclass(frozen=True)
class SessionHistMarks:
    turn_starts: list[TurnStartMark]
    skill_loads: list[SkillMark]


@dataclass(frozen=True)
class Histogram:
    freshness: Freshness
    start_ms: int
    bin_ms: int
    tail_from_ms: int | None
    bins: list[HistBin | HistBinByModel]
    marks: HistMarks | SessionHistMarks
    stretches: list[Stretch]


# ── §3.7 turn blade ──────────────────────────────────────────────────────────

Tok = TypedDict(
    "Tok",
    {
        "in": int | None,
        "cache_read": int | None,
        "cache_write": int | None,
        "out": int | None,
        "reasoning": int | None,
        "total": int | None,
    },
)


@dataclass(frozen=True)
class CallStep:
    step_id: str
    verb: str
    summary: str
    snippet: str


@dataclass(frozen=True)
class CallRow:
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    type: str = field(default="call", kw_only=True)
    id: str
    depth: int
    start_ms: int
    end_ms: int
    dur_ms: int | None
    time_prov: str
    model: str | None
    tok: Tok
    ttft_ms: int | None
    stop_reason: str | None
    error_kind: str | None
    is_final: bool
    steps: list[CallStep]


@dataclass(frozen=True)
class ToolRow:
    type: str = field(default="tool", kw_only=True)
    id: str
    depth: int
    step_id: str | None
    tool_name: str | None
    verb: str
    summary: str
    target: str
    command: str | None
    command_src: str | None
    file_path: str | None
    start_ms: int
    end_ms: int | None
    dur_ms: int | None
    time_prov: str
    status: str | None
    failed: bool
    exit_code: int | None
    is_error: bool
    interrupted: bool
    skill_load: str | None
    skill_via: str | None
    user_wait: str | None
    max_silence_ms: int | None
    result_bytes: int | None
    src: str


@dataclass(frozen=True)
class AgentRowRun:
    engagement: str
    agent_name: str | None
    start_ms: int
    end_ms: int
    wall_ms: int


@dataclass(frozen=True)
class AgentRow:
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    type: str = field(default="agent", kw_only=True)
    id: str
    depth: int
    caused_by: str
    mode: str
    description: str | None
    agent_names: list[str]
    agent_type: str | None
    tok_total: int | None
    n_llm_calls: int
    n_tool_calls: int
    n_errors: int
    models: list[str]
    run_ms: int
    n_rows: int
    runs: list[AgentRowRun]


@dataclass(frozen=True)
class TurnCost:
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    main: float | None
    agents: float | None


@dataclass(frozen=True)
class TurnHeader:
    """`TurnRow` fields, then `cost`, `models_full`, `text_src`, `text_bytes`, `n_turns_in_session`."""

    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    type: str = field(default="turn", kw_only=True)
    turn: str
    display_id: str | None
    kind: str
    origin: str
    label: str
    start_ms: int | None
    end_ms: int | None
    wall_ms: int | None
    time_split: TimeSplit | None
    waiting_ms: int | None
    cost_main: float | None
    tok_total: int | None
    n_llm_calls: int
    models: list[TurnModel]
    n_agents: int
    n_skill_loads: int
    n_failed: int
    agent_runs: list[AgentRun]
    cost: TurnCost
    models_full: list[ModelLine]
    text_src: str
    text_bytes: int | None
    n_turns_in_session: int


@dataclass(frozen=True)
class RangeAgent:
    row: str
    cost_usd: float | None
    tok_total: int | None
    n_calls_in_range: int
    n_calls: int


@dataclass(frozen=True)
class TurnRange:
    from_ms: int
    to_ms: int
    cost: Money
    cost_main: float | None
    cost_agents: float | None
    time_split: TimeSplit
    agents: list[RangeAgent]


AnyRow = CallRow | ToolRow | AgentRow | GapRow


@dataclass(frozen=True)
class TurnRows:
    freshness: Freshness
    turn: TurnHeader
    rows: list[AnyRow]
    range: TurnRange | None


@dataclass(frozen=True)
class AgentRowPage:
    freshness: Freshness
    total: int
    offset: int
    limit: int
    rows: list[AnyRow]


# ── §3.8 detail blade ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Position:
    session: str
    turn: str | None
    display_id: str | None
    row: RowRef
    depth: int


@dataclass(frozen=True)
class CallSection:
    part: str
    src: str
    snippet: str
    bytes: int | None
    row: RowRef | None


@dataclass(frozen=True)
class CallDetail:
    """Detail own fields, then `CallRow` fields without `type`."""

    freshness: Freshness
    kind: str = field(default="call", kw_only=True)
    position: Position
    turn_cost_usd: float | None
    cost_in_usd: float | None
    cost_out_usd: float | None
    cost_cache_read_usd: float | None
    cost_cache_write_usd: float | None
    cost_basis: str | None
    context_tokens: int | None
    speed_tok_s: float | None
    speed_basis: str
    sections: list[CallSection]
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    id: str
    depth: int
    start_ms: int
    end_ms: int
    dur_ms: int | None
    time_prov: str
    model: str | None
    tok: Tok
    ttft_ms: int | None
    stop_reason: str | None
    error_kind: str | None
    is_final: bool
    steps: list[CallStep]


@dataclass(frozen=True)
class Neighbours:
    before: list[AnyRow]
    after: list[AnyRow]


@dataclass(frozen=True)
class ToolDetail:
    """Detail own fields, then `ToolRow` fields without `type`."""

    freshness: Freshness
    kind: str = field(default="tool", kw_only=True)
    position: Position
    input_src: str
    output_src: str
    neighbours: Neighbours
    id: str
    depth: int
    step_id: str | None
    tool_name: str | None
    verb: str
    summary: str
    target: str
    command: str | None
    command_src: str | None
    file_path: str | None
    start_ms: int
    end_ms: int | None
    dur_ms: int | None
    time_prov: str
    status: str | None
    failed: bool
    exit_code: int | None
    is_error: bool
    interrupted: bool
    skill_load: str | None
    skill_via: str | None
    user_wait: str | None
    max_silence_ms: int | None
    result_bytes: int | None
    src: str


@dataclass(frozen=True)
class AgentDetailRun:
    engagement: str
    agent_name: str | None
    start_ms: int
    end_ms: int
    wall_ms: int
    ask_src: str | None


@dataclass(frozen=True)
class OtherCalls:
    n: int
    cost_usd: float | None


@dataclass(frozen=True)
class AgentDetail:
    """Detail own fields, then `AgentRow` fields without `type` and `runs`."""

    freshness: Freshness
    kind: str = field(default="agent", kw_only=True)
    position: Position
    turn_cost_usd: float | None
    focus_engagement: str
    runs: list[AgentDetailRun]
    top_calls: list[CallRow]
    other_calls: OtherCalls
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    id: str
    depth: int
    caused_by: str
    mode: str
    description: str | None
    agent_names: list[str]
    agent_type: str | None
    tok_total: int | None
    n_llm_calls: int
    n_tool_calls: int
    n_errors: int
    models: list[str]
    run_ms: int
    n_rows: int


@dataclass(frozen=True)
class MessageDetail:
    freshness: Freshness
    kind: str = field(default="message", kw_only=True)
    position: Position
    turn: str
    display_id: str | None
    turn_kind: str
    turn_origin: str
    label: str
    text_src: str
    text_bytes: int | None


Detail = CallDetail | ToolDetail | AgentDetail | MessageDetail

# ── §3.9 lazy full text ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class SourceResponse:
    freshness: Freshness
    text: str
    total_bytes: int
    drift: bool
    file: str
    locator: str
    block: str | None


# ── §5.2 use-case commands ───────────────────────────────────────────────────


@dataclass(frozen=True)
class SlowBlock:
    n_sessions: int
    wall_ms: int
    active_ms: int
    time_split: TimeSplit
    n_llm_calls: int
    n_tool_calls: int
    tok_total: int | None
    cost: Money


@dataclass(frozen=True)
class SlowSession:
    session: str
    session_id: str
    name: str
    project_name: str
    start_ms: int
    wall_ms: int
    active_ms: int
    time_split: TimeSplit
    cost: Money
    tok_total: int | None
    longest_wait: Stretch | None


@dataclass(frozen=True)
class ToolGroup:
    tool_name: str | None
    command_key: str | None
    n: int
    n_sessions: int
    dur_ms: int
    p50_ms: int | None
    p90_ms: int | None
    max_ms: int | None
    n_derived: int
    n_no_records: int


@dataclass(frozen=True)
class SlowestTool:
    row: RowRef
    session: str
    turn: str | None
    display_id: str | None
    tool_name: str | None
    command_key: str | None
    summary: str
    dur_ms: int
    time_prov: str
    max_silence_ms: int | None


@dataclass(frozen=True)
class UserWaitCount:
    n: int
    dur_ms: int


@dataclass(frozen=True)
class UserWaits:
    """CLI-20 `user_waits` (D54): calls with that `user_wait`."""

    ask: UserWaitCount
    permission: UserWaitCount


@dataclass(frozen=True)
class SessionWait:
    """`Stretch` fields, then `session` and `display_id` of the attached turn."""

    start_ms: int
    end_ms: int
    dur_ms: int
    state: str
    substate: str | None
    turn: str | None
    next_turn: NextTurn | None
    running: list[RunningEngagement]
    session: str
    display_id: str | None


@dataclass(frozen=True)
class SlowReport:
    freshness: Freshness
    current: SlowBlock
    previous: SlowBlock | None
    sessions: list[SlowSession]
    tools: list[ToolGroup]
    slowest_tools: list[SlowestTool]
    user_waits: UserWaits
    waits: list[SessionWait]
    models: list[ModelLine]


@dataclass(frozen=True)
class BreakdownSession:
    session: str
    session_id: str
    name: str
    project_name: str
    provider: str
    start_ms: int
    end_ms: int
    wall_ms: int
    active_ms: int
    cost: Money
    tok_total: int | None
    time_split: TimeSplit


@dataclass(frozen=True)
class BreakdownAgent:
    row: RowRef
    turn: str
    display_id: str | None
    depth: int
    agent_names: list[str]
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    tok_total: int | None
    run_ms: int
    n_llm_calls: int


@dataclass(frozen=True)
class SessionBreakdown:
    freshness: Freshness
    session: BreakdownSession
    turns_by_time: list[TurnRow]
    turns_by_tokens: list[TurnRow]
    rows_by_time: list[FindHit]
    rows_by_cost: list[FindHit]
    rows_by_tokens: list[FindHit]
    tools: list[ToolGroup]
    agents: list[BreakdownAgent]
    waits: list[Stretch]
    models: list[ModelLine]


@dataclass(frozen=True)
class TaskExample:
    row: RowRef
    session: str


@dataclass(frozen=True)
class TaskGroup:
    key: str
    n: int
    n_sessions: int
    tok_total: float | None
    cost_usd: float | None
    cost_lower_bound: bool
    n_unpriced: int
    dur_ms: float | None
    p50_ms: int | None
    p90_ms: int | None
    share: float | None
    example: TaskExample


@dataclass(frozen=True)
class ScopeTotals:
    tok_total: int | None
    cost: Money
    dur_ms: int | None


@dataclass(frozen=True)
class Unassigned:
    tok_total: float | None
    cost_usd: float | None


@dataclass(frozen=True)
class TaskGroups:
    freshness: Freshness
    by: str
    measure: str
    basis: str
    scope_totals: ScopeTotals
    unassigned: Unassigned
    total_groups: int
    groups: list[TaskGroup]


# ── serialisation ────────────────────────────────────────────────────────────


def to_json(obj: Any) -> bytes:
    """One line of UTF-8 JSON for a wire dataclass (or a dict/list of plain values)."""
    data = asdict(obj) if is_dataclass(obj) and not isinstance(obj, type) else obj
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
