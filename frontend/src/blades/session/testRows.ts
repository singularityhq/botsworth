import type { GapRow, TurnRow } from '../../api/types'

// Builders for tests: a TurnRow or GapRow with sensible defaults. Test-only; the product never imports this file.
const NO_SPLIT = { model_ms: 0, tool_ms: 0, agents_ms: 0, none_ms: 0 }

export function turn(n: number | null, o: Partial<TurnRow> = {}): TurnRow {
  return {
    type: 'turn', turn: `s/turn/${n ?? 'h' + Math.random().toString(36).slice(2, 6)}`, display_id: n === null ? null : `T${n}`,
    kind: n === null ? 'harness' : 'human', origin: n === null ? 'interrupt' : 'typed', label: n === null ? '/clear' : `ask ${n}`,
    start_ms: n === null ? null : n * 1_000_000, end_ms: n === null ? null : n * 1_000_000 + 100_000, wall_ms: n === null ? null : 100_000,
    time_split: n === null ? null : NO_SPLIT, waiting_ms: n === null ? null : 0, cost_usd: n === null ? null : 1, cost_lower_bound: false, n_unpriced: 0,
    cost_main: 1, tok_total: n === null ? null : 1000, n_llm_calls: n === null ? 0 : 2, models: [], n_agents: 0, n_skill_loads: 0, n_failed: 0, agent_runs: [], ...o,
  }
}

export function gap(start: number, dur: number, o: Partial<GapRow> = {}): GapRow {
  return {
    type: 'gap', id: `gap-${start}`, start_ms: start, end_ms: start + dur, dur_ms: dur, state: 'none', label: 'you', turn: null, next_turn: null, running: [], ...o,
  }
}
