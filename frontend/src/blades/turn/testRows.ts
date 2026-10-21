import type { AgentRow, AnyRow, CallRow, GapRow, ToolRow } from '../../api/types'

/** Typed builders for the turn blade's tests. Test files only: the product never imports this module. */
const S = 'claude_code/a11ce5e5-1111-4222-8333-944455556666'
export const T0 = 1_791_790_200_000
export const at = (sec: number): number => T0 + sec * 1000

export function call(id: string, o: Partial<CallRow> & { sec?: number } = {}): CallRow {
  const { sec = 0, ...rest } = o
  return {
    type: 'call', id: `${S}/llm/${id}`, cost_usd: 0.01, cost_lower_bound: false, n_unpriced: 0, depth: 0,
    start_ms: at(sec), end_ms: at(sec + 5), dur_ms: 5000, time_prov: 'present', model: 'claude-opus-5-5',
    tok: { in: 1, cache_read: 0, cache_write: 0, out: 1, reasoning: null, total: 100 }, ttft_ms: null, stop_reason: null, error_kind: null,
    is_final: false, steps: [], ...rest,
  }
}

export function tool(id: string, o: Partial<ToolRow> & { sec?: number } = {}): ToolRow {
  const { sec = 0, ...rest } = o
  return {
    type: 'tool', id: `${S}/tool/${id}`, depth: 0, step_id: null, tool_name: 'Bash', verb: 'run', summary: 'npm test', target: 'npm test',
    command: 'npm test', command_src: null, file_path: null, start_ms: at(sec), end_ms: at(sec + 2), dur_ms: 2000, time_prov: 'present',
    status: 'ok', failed: false, exit_code: 0, is_error: false, interrupted: false, skill_load: null, skill_via: null, user_wait: null,
    max_silence_ms: null, result_bytes: null, src: 'src://x', ...rest,
  }
}

export function agent(id: string, o: Partial<AgentRow> & { sec?: number } = {}): AgentRow {
  const { sec = 0, ...rest } = o
  return {
    type: 'agent', id: `${S}/engagement/${id}`, cost_usd: 1, cost_lower_bound: false, n_unpriced: 0, depth: 0, caused_by: `${S}/tool/c-${id}`,
    mode: 'started', description: null, agent_names: [id], agent_type: null, tok_total: 1000, n_llm_calls: 3, n_tool_calls: 2, n_errors: 0,
    models: ['claude-opus-5-5'], run_ms: 60_000, n_rows: 5,
    runs: [{ engagement: `${S}/engagement/${id}`, agent_name: id, start_ms: at(sec), end_ms: at(sec + 60), wall_ms: 60_000 }], ...rest,
  }
}

export function gap(sec: number, durSec: number, o: Partial<GapRow> = {}): GapRow {
  return {
    type: 'gap', id: `gap-${at(sec)}`, start_ms: at(sec), end_ms: at(sec + durSec), dur_ms: durSec * 1000, state: 'none', substate: 'none',
    turn: `${S}/turn/1`, next_turn: null, running: [], ...o,
  }
}

export const ids = (rows: readonly { id: string }[]): string[] => rows.map((r) => (r.id.startsWith(S) ? r.id.slice(S.length + 1) : r.id))
export type { AnyRow }
