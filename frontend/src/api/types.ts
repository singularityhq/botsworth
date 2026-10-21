/**
 * Wire types for every endpoint the v4 UI calls (docs/specs/interfaces.md §3).
 * Names and field lists follow the spec; Money fields are flattened into the
 * object that uses them (§3.3).
 */

export type Day = string // 'YYYY-MM-DD', browser time zone (FE-9 [D50])
export type Provider = 'claude_code' | 'codex' | 'omp'
export type Provenance = 'present' | 'derived' | 'estimated' | 'missing'
export type TitleSource = 'rename' | 'generated' | 'first_ask' | 'fallback'
export type TurnKind = 'human' | 'message' | 'harness'
export type ColorSlot = 1 | 2 | 3 | 4 | 5 | 6

export interface Freshness {
  last_ingest_ms: number | null
  parser_version: string
  price_fingerprint: string
  schema_version: number
  api_version: 1
}

/** QRY-11: cost_usd is null only when the item has no owned llm_call. */
export interface Money {
  cost_usd: number | null
  cost_lower_bound: boolean
  n_unpriced: number
}

/** DM-50 [D54]: the seven wall states and the substates of `you` and `none`. */
export type WallState = 'model' | 'tool' | 'agents' | 'you' | 'agentmsg' | 'limit' | 'none'
export type Substate = 'ask' | 'permission' | 'reply' | 'test' | 'wait' | 'background' | 'stalled' | 'before_first' | 'none' | null

/** DM-51, DM-53: time per (state, substate), in SES-15 order. */
export interface TimeSplit {
  parts: { state: WallState; substate: Substate; ms: number }[]
}

export interface ModelLine extends Money {
  model: string
  n_calls: number
  model_ms: number
  speed_tok_s: number | null
  speed_basis: Provenance
  ttft_ms_mean: number | null
}

export interface ProjectRef {
  project: string
  project_name: string
  color_slot: ColorSlot | null
}

/** QRY-3. Both days null = all time; exactly one null is invalid. */
export interface Scope {
  from_day: Day | null
  to_day: Day | null
  q: string
  providers: string[]
  models: string[]
  branches: string[]
  projects: string[]
  show_automated: boolean
}

// ---- 3.4 Explorer -------------------------------------------------------

export interface PeriodSummary {
  freshness: Freshness
  first_day: Day | null
  last_day: Day | null
  totals: Money & {
    tok_total: number | null
    n_sessions: number
    n_sessions_period: number
    n_automated_hidden: number
  }
  projects: (ProjectRef & Money & { tok_total: number | null; n_sessions: number })[]
  empty: null | {
    all_time_matches: number | null
    prev_day: { day: Day; n_sessions: number } | null
    next_day: { day: Day; n_sessions: number } | null
  }
}

export interface DayBar {
  day: Day
  /** Both equal `day` for a day bar; a week bar names the dates it covers after clipping (API-11, D41 M10). */
  from_day: Day
  to_day: Day
  cost_usd: number
  cost_lower_bound: boolean
  n_unpriced: number
  tok_total: number
  n_sessions: number
  by_project: (ProjectRef & { cost_usd: number; tok_total: number })[]
}

export interface DaySeries {
  freshness: Freshness
  days: DayBar[]
}

export interface SessionRow extends ProjectRef, Money {
  session: string
  session_id: string
  provider: Provider
  name: string
  title_source: TitleSource
  start_ms: number
  end_ms: number
  ends_after_days: number
  tok_total: number | null
  wall_ms: number
  active_ms: number
  is_automated: boolean
  n_agents: number
  git_branch: string | null
  activity: { bin_ms: number; tok: number[] }
  day_split: { day: Day; cost_usd: number; tok_total: number }[]
}

export interface SessionGroup extends Money {
  key: string
  name: string
  color_slot: ColorSlot | null
  n_sessions: number
  tok_total: number | null
  wall_ms: number
  active_ms: number
  period_activity: { bin_ms: number; start_ms: number; tok: number[] }
  rows: SessionRow[]
}

export interface SessionPage {
  freshness: Freshness
  total: number
  offset: number
  limit: number
  stats: { max_activity_tok: number; max_cost_usd: number; cost_p90: number | null }
  rows: SessionRow[]
  groups: SessionGroup[] | null
}

export interface Facets {
  freshness: Freshness
  providers: { value: Provider; n: number }[]
  models: { value: string; n: number }[]
  branches: { value: string; n: number }[]
  automated: { n: number }
}

export interface PaletteResult {
  freshness: Freshness
  filter_values: { kind: 'model' | 'branch' | 'provider' | 'project'; value: string; label: string; n: number }[]
  projects: (ProjectRef & Money & { n_sessions: number; n_automated: number })[]
  sessions: (Pick<
    SessionRow,
    | 'session' | 'session_id' | 'provider' | 'name' | 'title_source' | 'project' | 'project_name'
    | 'color_slot' | 'start_ms' | 'cost_usd' | 'cost_lower_bound' | 'n_unpriced' | 'is_automated'
  > & { first_day: Day; in_period: boolean; in_filters: boolean })[]
}

// ---- 3.5 Ingest status --------------------------------------------------

export type StoreState = 'missing' | 'schema_mismatch' | 'ready'

export interface IngestStatus {
  store_exists: boolean
  store_state: StoreState
  freshness: Freshness
  ingest: { running: boolean; started_ms: number | null; pid: number | null }
  sources: {
    provider: Provider
    root: string
    files: number
    bundles: number
    live: number
    last_ingest_ms: number | null
    rejected: number
  }[]
  last_run: { started_ms: number; ended_ms: number; bundles_changed: number; rejected: unknown[] } | null
}

// ---- 3.6 Session blade --------------------------------------------------

export type RowRef = { type: 'call' | 'tool' | 'agent' | 'gap' | 'turn'; id: string }

export interface NextTurn {
  turn: string
  kind: TurnKind
  origin: string
  label: string
  display_id: string | null
}

export interface Stretch {
  start_ms: number
  end_ms: number
  dur_ms: number
  state: 'agents' | 'tool' | 'you' | 'agentmsg' | 'limit' | 'none'
  substate: Substate
  turn: string | null
  next_turn: NextTurn | null
  running: { row: string; agent_name: string | null; start_ms: number; end_ms: number; cost_usd: number | null }[]
}

export type LongestState = WallState

export interface SessionDetail {
  freshness: Freshness
  session: ProjectRef & {
    session: string
    session_id: string
    provider: Provider
    name: string
    title_source: TitleSource
    cwd: string
    git_branch: string | null
    harness_version: string | null
    entrypoint: string | null
    originator: string | null
    start_ms: number
    end_ms: number
    ends_after_days: number
    is_automated: boolean
    n_turns: number
    n_harness_turns: number
    cost: Money & { main: number | null; agents: number | null; unattributed: number | null }
    tok_total: number | null
    wall_ms: number
    active_ms: number
    time_split: TimeSplit
    longest: Record<LongestState, { row: RowRef; turn: string; dur_ms: number } | null>
    n_agents: number
    n_skill_loads: number
    n_failed: number
    n_llm_calls: number
    models: ModelLine[]
  }
}

export interface TurnRow extends Money {
  type: 'turn'
  turn: string
  display_id: string | null
  kind: TurnKind
  origin: string
  label: string
  start_ms: number | null
  end_ms: number | null
  wall_ms: number | null
  time_split: TimeSplit | null
  waiting_ms: number | null
  cost_main: number | null
  tok_total: number | null
  n_llm_calls: number
  models: { model: string; cost_usd: number | null }[]
  n_agents: number
  n_skill_loads: number
  n_failed: number
  agent_runs: { engagement: string; agent_name: string | null; start_ms: number; end_ms: number }[]
}

export interface GapRow extends Stretch {
  type: 'gap'
  id: string // gap-<start_ms>
}

export interface TurnList {
  freshness: Freshness
  items: (TurnRow | GapRow)[]
}

export type FindKind = 'biggest' | 'waits' | 'failures' | 'skills' | 'agents' | 'search'
export type FindMeasure = 'cost' | 'time' | 'tokens'

export interface FindResult {
  freshness: Freshness
  kind: string
  total: number
  hits: {
    row: RowRef
    turn: string
    display_id: string | null
    depth: number
    label: string
    value: number | null
    start_ms: number | null
  }[]
  per_turn: Record<string, number>
}

export interface Resolved {
  freshness: Freshness
  row: RowRef
  turn: string
  depth: number
}

export interface Histogram {
  freshness: Freshness
  start_ms: number
  bin_ms: number
  /** Turn only: where the tail zone after the turn's end starts; null for a session (API-31, D41 N1). */
  tail_from_ms: number | null
  bins: {
    cost_usd: number
    /** Cost of owned main-transcript model calls starting in the bin (D47, SES-13). */
    main_cost_usd: number
    tok_total: number
    state_ms: TimeSplit
    by_model?: Record<string, [number, number]>
  }[]
  marks: {
    turn_starts?: { turn: string; display_id: string | null; start_ms: number }[]
    skill_loads: { row: RowRef; start_ms: number; skill: string }[]
  }
  stretches: Stretch[]
}

// ---- 3.7 Turn blade -----------------------------------------------------

export interface CallRow extends Money {
  type: 'call'
  id: string
  depth: number
  start_ms: number
  end_ms: number
  dur_ms: number | null
  time_prov: Provenance
  model: string | null
  tok: {
    in: number | null
    cache_read: number | null
    cache_write: number | null
    out: number | null
    reasoning: number | null
    total: number | null
  }
  ttft_ms: number | null
  stop_reason: string | null
  error_kind: string | null
  is_final: boolean
  steps: { step_id: string; verb: 'think' | 'say' | 'answer'; summary: string; snippet: string }[]
}

export interface ToolRow {
  type: 'tool'
  id: string
  depth: number
  step_id: string | null
  tool_name: string | null
  verb: string
  summary: string
  target: string
  command: string | null // stored 160-char preview (API-22, D39, D42)
  command_src: string | null // reference to the full command, loaded through /api/source (API-22, D42)
  file_path: string | null
  start_ms: number
  end_ms: number | null
  dur_ms: number | null
  time_prov: Provenance
  status: string | null
  failed: boolean
  exit_code: number | null
  is_error: boolean
  interrupted: boolean
  skill_load: string | null
  skill_via: 'tool' | 'read' | 'slash' | null
  user_wait: 'ask' | 'permission' | null
  max_silence_ms: number | null
  result_bytes: number | null
  src: string
}

export interface AgentRow extends Money {
  type: 'agent'
  id: string
  depth: number
  caused_by: string
  mode: 'started' | 'messaged'
  description: string | null
  agent_names: string[]
  agent_type: string | null
  tok_total: number | null
  n_llm_calls: number
  n_tool_calls: number
  n_errors: number
  models: string[]
  run_ms: number
  n_rows: number
  runs: { engagement: string; agent_name: string | null; start_ms: number; end_ms: number; wall_ms: number }[]
}

export type AnyRow = CallRow | ToolRow | AgentRow | GapRow

export interface TurnRows {
  freshness: Freshness
  turn: TurnRow & {
    cost: Money & { main: number | null; agents: number | null }
    models_full: ModelLine[]
    text_src: string
    text_bytes: number | null
    n_turns_in_session: number
  }
  rows: AnyRow[]
  range: null | {
    from_ms: number
    to_ms: number
    cost: Money
    cost_main: number | null
    cost_agents: number | null
    time_split: TimeSplit
    agents: {
      row: string
      cost_usd: number | null
      tok_total: number | null
      n_calls_in_range: number
      n_calls: number
    }[]
  }
}

export interface AgentRowPage {
  freshness: Freshness
  total: number
  offset: number
  limit: number
  rows: AnyRow[]
}

// ---- 3.8 Detail blade ---------------------------------------------------

export interface Position {
  session: string
  turn: string | null
  display_id: string | null
  row: RowRef
  depth: number
}

export type CallDetail = {
  freshness: Freshness
  kind: 'call'
  position: Position
  turn_cost_usd: number | null
  cost_in_usd: number | null
  cost_out_usd: number | null
  cost_cache_read_usd: number | null
  cost_cache_write_usd: number | null
  cost_basis: string | null
  context_tokens: number | null
  speed_tok_s: number | null
  speed_basis: Provenance
  sections: {
    part: 'thinking' | 'text' | 'tool_use'
    src: string
    snippet: string
    bytes: number | null
    row: RowRef | null
  }[]
} & Omit<CallRow, 'type'>

export type ToolDetail = {
  freshness: Freshness
  kind: 'tool'
  position: Position
  input_src: string
  output_src: string
  neighbours: { before: AnyRow[]; after: AnyRow[] }
} & Omit<ToolRow, 'type'>

export type AgentDetail = {
  freshness: Freshness
  kind: 'agent'
  position: Position
  turn_cost_usd: number | null
  focus_engagement: string
  runs: {
    engagement: string
    agent_name: string | null
    start_ms: number
    end_ms: number
    wall_ms: number
    ask_src: string | null
  }[]
  top_calls: CallRow[]
  other_calls: { n: number; cost_usd: number | null }
} & Omit<AgentRow, 'type' | 'runs'>

export interface MessageDetail {
  freshness: Freshness
  kind: 'message'
  position: Position
  turn: string
  display_id: string | null
  turn_kind: TurnKind
  turn_origin: string
  label: string
  text_src: string
  text_bytes: number | null
}

export type Detail = CallDetail | ToolDetail | AgentDetail | MessageDetail

// ---- 3.9 Lazy full text -------------------------------------------------

export type SourcePart = 'input' | 'output' | 'thinking' | 'text'

export interface SourceResponse {
  freshness: Freshness
  text: string
  total_bytes: number
  drift: boolean
  file: string
  locator: string
  block: string | null
}

// ---- Review (QRY-42, API-33) ----------------------------------------------

/** CLI-20 [D55]: test runs of a span; `run_ms` counts a background run to its notice. */
export interface TestRuns {
  n: number
  run_ms: number
  n_failed: number
  n_passed: number
  n_no_result: number
  n_background: number
}

/** DM-56 causes of wall time, in DM-56 order. */
export type Cause = 'stalled' | 'tests' | 'polling' | 'you' | 'message' | 'limit' | 'working' | 'idle'
export interface CausePart {
  cause: Cause
  ms: number
}

export interface Evidence {
  session: string
  session_id: string
  row: RowRef | null
  turn: string | null
  display_id: string | null
  start_ms: number
  end_ms: number
  dur_ms: number
}

export interface Finding {
  cause: 'stalled' | 'tests' | 'polling' | 'you' | 'message'
  ms: number
  lost: boolean
  n: number
  longest: Evidence | null
  threads: string[]
  sessions: { session: string; session_id: string; name: string; ms: number }[]
}

/** QRY-42 `Review` over API-33 (`GET /api/sessions/{session}/review`), with the freshness. */
export interface ReviewReport {
  freshness: Freshness
  wall_ms: number
  lost_ms: number
  n_sessions: number
  n_unmeasured: number
  causes: CausePart[]
  findings: Finding[]
  test_runs: TestRuns
}

// ---- Query argument shapes (client.ts) ----------------------------------

export type SortKey = 'start' | 'cost' | 'wall' | 'active' | 'tokens'
export type SortDir = 'asc' | 'desc'
export type GroupKey = 'project' | 'day'
