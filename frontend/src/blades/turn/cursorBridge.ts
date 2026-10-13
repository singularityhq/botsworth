import { useSyncExternalStore } from 'react'
import type { TurnRows } from '../../api/types'
import type { FinderKind } from '../shared/finders'
import type { Item, RowNode } from './rowList.logic'
import type { AgentBands } from './useAgentBands'

/**
 * What the detail blade (DET-4, DET-5, DET-33, DET-34) may use of the turn blade's cursor. The turn blade owns the list, the agent bands and
 * the hand-off across turns; it publishes them here while it is mounted, and the detail drives the same cursor through `move` and `goTo`
 * instead of keeping a second copy of the list.
 */
export interface TurnCursorApi {
  ix: number
  turnOp: string | null
  /** The turn's rows (API-22); undefined until they arrive. */
  rows: TurnRows | undefined
  /** Every row with the loaded agent pages spliced in, the rows of the current list in order, and the list's items. */
  nodes: readonly RowNode[]
  flat: readonly RowNode[]
  items: readonly Item[]
  /** The cursor row of the current list (the first row when the step names none). */
  cursorId: string | null
  toolNames: ReadonlyMap<string, readonly string[]>
  bands: AgentBands
  openAgents: ReadonlySet<string>
  /** `j` / `k`: one row on in the list, or into the next turn at the edge (TRN-50), exactly as the turn's own keys do. */
  move: (delta: number) => void
  /** Put the cursor on a row, opening the bands that hold it. `via` is an agent whose rows may hold it though they are not loaded yet. */
  goTo: (rowId: string, via?: string) => void
  /** Show or fold an agent's rows in the turn (TRN-35). */
  toggleAgent: (agentId: string) => void
  /** The finder keys `w W e s S a` (SES-73): the focused blade may be the detail, which has no finders of its own. */
  find: (kind: FinderKind, dir: 1 | -1) => void
}

let current: TurnCursorApi | null = null
const listeners = new Set<() => void>()

/** Called by the turn blade: publish its cursor, or null when it unmounts. */
export function publishTurnCursor(api: TurnCursorApi | null): void {
  current = api
  for (const l of listeners) l()
}

const subscribe = (l: () => void): (() => void) => {
  listeners.add(l)
  return () => void listeners.delete(l)
}

/** The published cursor of the mounted turn blade, or null when none is mounted. */
export function useTurnCursorApi(): TurnCursorApi | null {
  return useSyncExternalStore(subscribe, () => current, () => null)
}
