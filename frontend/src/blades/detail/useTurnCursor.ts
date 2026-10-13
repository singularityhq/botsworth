import { useEffect, useMemo } from 'react'
import { client, urls } from '../../api/client'
import type { SessionDetail, TurnList, TurnRow, TurnRows } from '../../api/types'
import { useApi } from '../../api/useApi'
import { useNav } from '../../state/nav'
import { useUrlState } from '../../state/url'
import type { FinderKind } from '../shared/finders'
import { useBrowserZone } from '../../state/zone'
import { buildEntries, cursorStops } from '../session/turnList.logic'
import { useTurnCursorApi } from '../turn/cursorBridge'
import { positionOf, rangeAgentMap, type Item, type RowNode } from '../turn/rowList.logic'
import type { AgentBands } from '../turn/useAgentBands'

const NO_NODES: readonly RowNode[] = []

/** What the blade last showed: a detail sliding out has already lost `s`, `t` and `step` from the URL (SES-3). */
const last: { s: string | null; t: number | null; step: string | null } = { s: null, t: null, step: null }

/** A live detail needs a session and a turn in the URL; a closing one (the strip keeps it mounted while it slides out) uses what it showed last. */
export function turnKnown(url: { s: string | null; t: number | null; detail: boolean }): boolean {
  return url.detail ? url.s !== null && url.t !== null : last.s !== null && last.t !== null
}

/** A detail that never had a turn shows nothing while it slides out. */
export function forgetLast(): void {
  last.s = null
  last.t = null
  last.step = null
}

export interface TurnCursor {
  session: string
  ix: number
  turnOp: string | null
  turnRow: TurnRow | undefined
  tz: string
  sessionWallMs: number | null
  rangeAgents: ReturnType<typeof rangeAgentMap>
  /** The turn's rows (API-22); undefined until they arrive. */
  rows: TurnRows | undefined
  rowsError: { message: string; retry: () => void } | null
  /** The zoom's figures (TRN-24), when a zoom is set and they have arrived. */
  range: TurnRows['range'] | undefined
  nodes: readonly RowNode[]
  flat: readonly RowNode[]
  items: readonly Item[]
  /** What the URL's `step` names: a row, a gap, or the turn's own op id. */
  step: string | null
  cursorNode: RowNode | undefined
  pos: { r: number; m: number }
  /** The agent that holds the cursor's row, by name (DET-2). */
  inside: string | null
  toolNames: ReadonlyMap<string, readonly string[]>
  bands: Pick<AgentBands, 'open' | 'setOpen'>
  /** j / k and the stepper: the turn's own `move`, hand-off included (TRN-50). */
  move: (delta: number) => void
  /** `w W e s S a` (SES-73): the turn's finders, run from the detail, which takes the keys when a wait opens it. */
  find: (kind: FinderKind, dir: 1 | -1) => void
  /** DET-5: put the cursor on a row. `via` is the agent whose rows may hold it when they are not loaded yet. */
  goTo: (id: string, via?: string) => void
}

const NO_BANDS: Pick<AgentBands, 'open' | 'setOpen'> = { open: new Set(), setOpen: () => {} }

/**
 * The turn's cursor as the detail blade sees it. The cursor is the URL's `step` (SES-8, SES-9). The list, the agent bands and the moves
 * belong to the turn blade, which publishes them (`cursorBridge`); this hook reads them, so the detail steps over the very list the turn shows.
 * Until the turn blade has published (or while it is not mounted) the list is empty and the moves do nothing.
 */
export function useTurnCursor(): TurnCursor {
  const url = useUrlState()
  const nav = useNav()
  if (url.s !== null) last.s = url.s
  if (url.t !== null) last.t = url.t
  if (url.detail) last.step = url.step
  const session = url.s ?? last.s // FALLBACK-OK: SES-3 (a closing blade keeps its session until the transition ends)
  const ix = url.t ?? last.t // FALLBACK-OK: SES-3 (same: a closing blade keeps its turn)
  const step = url.detail ? url.step : last.step // FALLBACK-OK: SES-3 (same: a closing blade keeps its row)
  if (session === null || ix === null) throw new Error('DetailBlade rendered with no session and turn in the URL')

  const sessionQ = useApi<SessionDetail>(urls.sessionDetail(session), (signal) => client.sessionDetail(session, signal))
  const turnsQ = useApi<TurnList>(urls.sessionTurns(session), (signal) => client.sessionTurns(session, undefined, signal))
  const stops = useMemo(() => cursorStops(buildEntries(turnsQ.data?.items ?? [])), [turnsQ.data]) // FALLBACK-OK: SES-80 (no turns until the list arrives)
  const here = stops.find((e) => e.ix === ix)
  const turnOp = here?.row.turn ?? null // FALLBACK-OK: TRN-60 (the turn's op id is known once the session's turn list arrives)
  const tz = useBrowserZone()

  const rangeQ = useApi<TurnRows>(turnOp && url.zoom ? urls.turnRows(turnOp, { from_ms: url.zoom.start_ms, to_ms: url.zoom.end_ms }) : null, (signal) =>
    client.turnRows(turnOp!, { from_ms: url.zoom!.start_ms, to_ms: url.zoom!.end_ms }, signal),
  )
  const rangeAgents = useMemo(() => rangeAgentMap(rangeQ.data?.range ?? null), [rangeQ.data]) // FALLBACK-OK: TRN-33 (outside a zoom agent rows show their whole figures)

  // The turn blade owns the rows; this request is the same URL, so it is shared. The detail reads it only to show its failure.
  const rowsQ = useApi<TurnRows>(turnOp ? urls.turnRows(turnOp) : null, (signal) => client.turnRows(turnOp!, undefined, signal))
  const api = useTurnCursorApi()
  const mine = api !== null && api.ix === ix && api.turnOp === turnOp ? api : null // FALLBACK-OK: DET-4 (a published cursor of another turn is not this turn's list)
  const nodes = mine?.nodes ?? NO_NODES // FALLBACK-OK: DET-4 (the turn blade has not published this turn yet)
  const flat = mine?.flat ?? NO_NODES // FALLBACK-OK: DET-4 (same)
  const items = mine?.items ?? []
  const rows = mine?.rows
  const cursorNode = step === null ? undefined : nodes.find((n) => n.id === step)
  const pos = positionOf(step, items)
  const owner = cursorNode && cursorNode.ancestors.length > 0 ? nodes.find((n) => n.id === cursorNode.ancestors[cursorNode.ancestors.length - 1]) : undefined
  const inside = owner && owner.row.type === 'agent' ? (owner.row.agent_names[0] ?? owner.row.agent_type ?? 'agent') : null // FALLBACK-OK: TRN-33 (an agent with neither a name nor a type is called "agent")

  // With the detail open and no step in the URL, the cursor rests on the first row; the URL follows (TRN-36).
  useEffect(() => {
    if (!url.detail || url.step !== null || mine === null || mine.cursorId === null || rows === undefined) return
    nav.replace({ step: mine.cursorId })
  }, [url.detail, url.step, mine, rows, nav])

  return {
    session, ix, turnOp, turnRow: here?.row, tz, rows, rangeAgents,
    sessionWallMs: sessionQ.data?.session.wall_ms ?? null, // FALLBACK-OK: DET-40 (no session wall until the session arrives: no caption)
    rowsError: rowsQ.error ? { message: rowsQ.error.message, retry: rowsQ.retry } : null,
    range: url.zoom ? rangeQ.data?.range : undefined,
    nodes, flat, items, step, cursorNode, pos, inside,
    toolNames: mine?.toolNames ?? new Map(), // FALLBACK-OK: DET-3 (no tool names until the turn blade has published)
    bands: mine?.bands ?? NO_BANDS, // FALLBACK-OK: DET-33 (no bands until the turn blade has published)
    move: mine?.move ?? (() => {}), // FALLBACK-OK: DET-4 (nothing to move over until the turn blade has published)
    find: mine?.find ?? (() => {}), // FALLBACK-OK: DET-4 (no finders until the turn blade has published)
    goTo: mine?.goTo ?? ((id) => nav.replace({ step: id })), // FALLBACK-OK: DET-5 (with no turn blade the URL alone names the row)
  }
}
