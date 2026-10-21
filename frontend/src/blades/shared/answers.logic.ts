import type { RowRef, SessionDetail, TimeSplit } from '../../api/types'
import { fmt } from '../../lib/format'
import { GAP_MIN_MS, LEGEND_MAX, LEGEND_MIN_MS, STATE_KEYS, STATES, LEGEND_SUBSTATES, type StateKey, type WordedSubstate } from './states'
import type { Rich } from './toastText'

/** The two answers (SES-26; R3 top 5 #1): where the money went, where the time went. Pure rules, shared with the turn header. */

export type StateTotals = Record<StateKey, number>

/** Seven state totals: each state's parts summed over its substates (SES-15: the legend shows one `you` item). */
export function stateTotals(split: TimeSplit): StateTotals {
  const t: StateTotals = { model: 0, tool: 0, agents: 0, you: 0, agentmsg: 0, limit: 0, none: 0 }
  for (const p of split.parts) t[p.state] += p.ms
  return t
}

/** The time per substate of `you`, `tool`, `agents` or `none`, for the legend tooltip (SES-15). Substates with no time are left out. */
export function substateBreakdown(split: TimeSplit, state: StateKey): { substate: WordedSubstate; ms: number }[] {
  return (LEGEND_SUBSTATES[state] ?? []).map((substate) => ({ // FALLBACK-OK: SES-15 (only you, tool, agents and none list substates)
    substate,
    ms: split.parts.filter((p) => p.state === state && p.substate === substate).reduce((a, p) => a + p.ms, 0),
  })).filter((r) => r.ms > 0)
}

export function totalsSum(t: StateTotals): number {
  return STATE_KEYS.reduce((a, k) => a + t[k], 0)
}

/** SES-26 line 1: A = model + tools + agents; B = wall - A. */
export function runningSplit(t: StateTotals, wallMs: number): { running: number; nothing: number } {
  const running = t.model + t.tool + t.agents
  return { running, nothing: wallMs - running }
}

/** SES-26: states with a total of 30 s or more, largest first (ties keep SES-15 order), at most 4. */
export function legendStates(t: StateTotals): StateKey[] {
  return STATE_KEYS.filter((k) => t[k] >= LEGEND_MIN_MS)
    .sort((a, b) => t[b] - t[a])
    .slice(0, LEGEND_MAX)
}

/**
 * Widths in px of the positive `values`, in order, summing to `totalPx` minus the gaps between them.
 * A segment narrower than `minPx` is drawn at `minPx` and the rest share what is left (SES-26, SES-20).
 */
export function segmentWidths(values: readonly number[], totalPx: number, minPx = 2, gapPx = 2): number[] {
  const n = values.length
  if (n === 0) return []
  const avail = Math.max(0, totalPx - gapPx * (n - 1))
  const fixed = new Array<boolean>(n).fill(false)
  const w = new Array<number>(n).fill(0)
  for (let pass = 0; pass <= n; pass++) {
    const free = values.reduce((a, v, i) => (fixed[i] ? a : a + v), 0)
    const left = avail - fixed.reduce((a, f) => a + (f ? minPx : 0), 0)
    let changed = false
    for (let i = 0; i < n; i++) {
      if (fixed[i]) {
        w[i] = minPx
        continue
      }
      w[i] = free > 0 ? (values[i] / free) * left : 0
      if (w[i] < minPx) {
        fixed[i] = true
        changed = true
      }
    }
    if (!changed) break
  }
  return w
}

// ---- cost answer ------------------------------------------------------------

export interface CostCaption {
  /** null: no priced model call, show "—" and "no model calls" (SES-84, S2 §5). */
  figure: number | null
  noModelCalls: boolean
  /** The split bar is drawn only when both parts are known and there is something to split. */
  showSplit: boolean
  main: number | null
  agents: number | null
  /** SES-26: the agents figure is shown only at $0.01 or more. */
  showAgents: boolean
}

export function costCaption(cost: { cost_usd: number | null; main: number | null; agents: number | null }): CostCaption {
  const figure = cost.cost_usd
  const known = figure !== null && cost.main !== null && cost.agents !== null
  return {
    figure,
    noModelCalls: figure === null,
    showSplit: known && cost.main! + cost.agents! > 0,
    main: cost.main,
    agents: cost.agents,
    showAgents: cost.agents !== null && cost.agents >= 0.01,
  }
}

// ---- legend jump (SES-27) ---------------------------------------------------

export type Longest = SessionDetail['session']['longest']

export type JumpPlan =
  | { kind: 'go'; state: StateKey; row: RowRef; turn: string; durMs: number }
  | { kind: 'none'; toast: Rich }

/**
 * Where a legend click goes: the longest row or stretch of the state (D38 H2). The states that mean waiting, and agents-only,
 * need a stretch of 2 min or more; model and tool take the longest call or tool run whatever its length.
 */
export function planJump(state: StateKey, longest: Longest): JumpPlan {
  const hit = longest[state]
  const isCall = state === 'model' || state === 'tool'
  if (isCall) {
    if (hit === null) return { kind: 'none', toast: [`No ${state === 'model' ? 'model call' : 'tool run'} in this session.`] }
    return { kind: 'go', state, row: hit.row, turn: hit.turn, durMs: hit.dur_ms }
  }
  if (hit === null || hit.dur_ms < GAP_MIN_MS) {
    return { kind: 'none', toast: [`No single stretch of ${STATES[state].short} is 2 min or longer; the total is many short pauses.`] }
  }
  return { kind: 'go', state, row: hit.row, turn: hit.turn, durMs: hit.dur_ms }
}

/** `gap-<start_ms>` → start_ms; null for any other row id. */
export function gapStartMs(row: RowRef): number | null {
  const m = row.type === 'gap' ? /^gap-(\d+)$/.exec(row.id) : null
  return m ? Number(m[1]) : null
}

/** SES-27 toast: "Longest stretch of <short>: **dur** at HH:MM · turn N", or "Longest model call: **dur** · turn N". */
export function jumpToast(plan: Extract<JumpPlan, { kind: 'go' }>, turnNumber: number | null, tz: string): Rich {
  const dur = fmt.dur(plan.durMs)
  const turn = turnNumber === null ? '' : ` · turn ${turnNumber}`
  if (plan.state === 'model') return ['Longest model call: ', { b: dur }, turn]
  if (plan.state === 'tool') return ['Longest tool run: ', { b: dur }, turn]
  const start = gapStartMs(plan.row)
  const at = start === null ? '' : ` at ${fmt.clock(start, tz)}`
  return [`Longest stretch of ${STATES[plan.state].short}: `, { b: dur }, `${at}${turn}`]
}

/** SES-28: the "Agent work with no turn" line shows from $0.01. */
export function showUnattributed(cost: { unattributed: number | null }): boolean {
  return cost.unattributed !== null && cost.unattributed >= 0.01
}
