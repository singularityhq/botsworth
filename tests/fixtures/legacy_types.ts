/**
 * Wire types for the Botsworth HTTP API (docs/design/design.md §4.2, §4.10,
 * §4.11, §5, §6.3). Field names are the canonical stream names. The UI reads
 * inclusive totals from these records; it never recomputes rollups (one query
 * layer for UI and agents, design §5).
 *
 * Shapes the design leaves unspecified are marked EXT and listed in the B5
 * report so the backend can accept or change them.
 */

export type Provenance = 'present' | 'derived' | 'estimated' | 'missing'
export type OpKind = 'session' | 'engagement' | 'turn' | 'llm_call' | 'tool_call'
export type Provider = 'claude_code' | 'codex' | 'omp'

/** §4.2 op record plus the store's derived inclusive columns (subset the UI reads). */
export interface Op {
  op_id: string
  kind: OpKind
  parent: string | null
  seq: number
  depth: number
  caused_by: string | null
  caused_by_basis: string
  provider: Provider
  session_id: string
  agent_id: string | null
  engagement_ix: number
  project: string
  cwd: string
  account: string
  agent_type: string | null
  agent_name: string | null
  spawn_depth: number
  duplicate_of: string | null
  start_ms: number
  end_ms: number
  dur_ms: number | null
  time_prov: Provenance
  ttft_ms: number | null
  in_tok: number | null
  cache_read_tok: number | null
  cache_write_tok: number | null
  out_tok: number | null
  reasoning_tok: number | null
  context_tokens: number | null
  tok_prov: Provenance
  cost_usd: number | null
  cost_basis: 'reported' | 'computed' | 'missing' | null
  model: string | null
  tool_name: string | null
  command: string | null
  command_norm: string | null
  file_path: string | null
  exit_code: number | null
  is_error: boolean
  error_kind: string | null
  label: string
  src: string
  // ── derived inclusive columns (§4.2 "per engine") ──
  incl_in_tok: number
  incl_cache_read_tok: number
  incl_cache_write_tok: number
  incl_out_tok: number
  tok_total: number
  /** EXT: total of incl_cost_*_usd; design names only the per-bucket columns */
  incl_cost_usd: number | null
  incl_llm_ms: number
  incl_tool_ms: number
  wall_ms: number
  active_ms: number
  busy_sum_ms: number
  idle_ms: number
  max_parallel: number
  n_llm_calls: number
  n_tool_calls: number
  n_engagements: number
  n_errors: number
  n_steps: number
  incl_tok_prov: Provenance
  incl_time_prov: Provenance
  cost_lower_bound: boolean
}

export type Verb =
  | 'think' | 'say' | 'read' | 'write' | 'edit' | 'run' | 'search' | 'fetch'
  | 'spawn' | 'skill' | 'ask-user' | 'answer' | 'other'

/** §4.10 step record. */
export interface Step {
  /** EXT: stable step id; design §6.1a refers to step_id without defining it */
  step_id: string
  turn_id: string
  ordinal: number
  op_id: string
  verb: Verb
  target: string
  outcome: string
  summary: string
  snippet: string
  start_ms: number
  dur_ms: number | null
  /** raw provider tool name, kept beside the lossy verb (R-outline) */
  tool_name: string | null
  skill: string | null
  sub_items?: { tool: string; target: string; outcome: string }[]
  src: string
}

export type EventKind =
  | 'compaction' | 'limit_state' | 'limit_hit' | 'api_error' | 'skill_invocation'
  | 'model_change' | 'resume' | 'rollback'

/** §4.11 event record. */
export interface ProfEvent {
  event_id: string
  kind: EventKind
  ts_ms: number
  session_id: string
  op_id: string | null
  account: string
  basis: Provenance
  fields: Record<string, string | number | boolean | null>
  src: string
}

// ── query (§5) ──────────────────────────────────────────────────────────

export type Scope =
  | { op: string }
  | { sessions: { provider?: Provider; project?: string; account?: string; from?: number; to?: number } }
  | { all: true }

export type Where =
  | { field: string; eq: string | number | boolean }
  | { field: string; in: (string | number)[] }
  | { field: string; contains: string }
  /** EXT: range filter, needed to drill into one usage bucket */
  | { field: string; gte?: number; lt?: number }

export type MetricName =
  | 'cost_usd' | 'tokens.total' | 'tokens.in' | 'tokens.out' | 'tokens.cache_read' | 'tokens.cache_write'
  | 'wall_ms' | 'active_ms' | 'busy_sum_ms' | 'dur_ms' | 'llm_ms' | 'tool_ms' | 'idle_ms'
  | 'context_tokens' | 'cache_hit_ratio' | 'n_errors' | 'max_parallel'
  /** EXT: ordering key for chronological lists */
  | 'start_ms'

export type GroupKey =
  | 'model' | 'tool_name' | 'agent_type' | 'command_norm' | 'file_path' | 'session' | 'project'
  | 'account' | 'skill' | 'verb' | 'provider' | 'kind'

export interface Query {
  scope: Scope
  select: 'children' | 'descendants' | 'self' | 'sessions' | 'events' | 'steps'
  where?: Where[]
  measure?: 'incl' | 'own'
  metrics?: MetricName[]
  group_by?: GroupKey[]
  bucket?: { time: 'hour' | 'day' | 'week' } | null
  agg?: 'sum' | 'count' | 'max' | 'p50' | 'p90' | 'p95' | 'distribution'
  outliers?: { over: 'p90'; with_top_child: boolean } | null
  order_by?: { metric: MetricName | 'n'; dir: 'asc' | 'desc' }
  limit?: number
  cursor?: string | null
}

export interface Distribution {
  p50: number
  p90: number
  max: number
  n: number
}

export interface Freshness {
  last_ingest_ms: number
  parser_version: string
  price_fingerprint: string
}

export interface Row {
  /** present when rows are individual ops */
  op?: Op
  /** present when grouped */
  key?: Partial<Record<GroupKey, string>>
  /** bucket start, epoch ms, when bucketed */
  bucket?: number
  metrics: Partial<Record<MetricName, number | null>>
  prov: Partial<Record<MetricName, Provenance>>
  /** agg = distribution */
  dist?: Partial<Record<MetricName, Distribution>>
  /** row value ÷ scope total, for the order metric */
  share?: number | null
  /** n = items aggregated; sessions = distinct sessions */
  counts?: { n: number; sessions?: number; first_ms?: number; last_ms?: number }
  top_child?: Op | null
  /** EXT: select = events / steps carry the record itself */
  event?: ProfEvent
  step?: Step
}

export interface QueryResponse {
  freshness: Freshness
  scope_totals: Partial<Record<MetricName, number | null>>
  rows: Row[]
  next_cursor: string | null
  availability: Availability
}

// ── other endpoints (§6.3); response shapes are EXT unless noted ─────────

/** GET /api/ops/{op_id} — `show`: all fields, ancestors, children counts, events on the op (§5). */
export interface ShowResponse {
  op: Op
  ancestors: Op[]
  children_counts: Partial<Record<OpKind, number>>
  events: ProfEvent[]
}

/** GET /api/ops/{op_id}/timeline?depth= */
export interface TimelineResponse {
  root: Op
  /** every op in the subtree down to `depth` levels of engagement nesting, root excluded */
  ops: Op[]
  events: ProfEvent[]
}

/** GET /api/turns/{op_id}/steps */
export interface StepsResponse {
  turn: Op
  steps: Step[]
  next_cursor: string | null
}

/** GET /api/source?src=&offset=&limit= — fields from §5 `source()` */
export interface SourceResponse {
  text: string
  total_bytes: number
  drift: boolean
  file: string
  locator: string
}

/** GET /api/sessions?provider=&project=&from=&to= */
export interface SessionsResponse {
  freshness: Freshness
  sessions: Array<Op & { title: string }>
}

/** GET /api/projects */
export interface ProjectRow {
  project: string
  n_sessions: number
  providers: Provider[]
  first_ms: number
  last_ms: number
  incl_cost_usd: number | null
  tok_total: number
}

/** GET /api/availability — design §4.8 matrix */
export interface Availability {
  rows: Array<{ metric: string } & Record<Provider, Provenance | 'n/a'>>
}

/** GET /api/ingest/status */
export interface IngestStatus {
  freshness: Freshness
  sources: Array<{ provider: Provider; files: number; live: number }>
}
