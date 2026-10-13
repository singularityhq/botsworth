import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { client, urls } from '../../api/client'
import type { Resolved, SessionDetail, TurnList as TurnListWire, TurnRows } from '../../api/types'
import type { GapRow } from '../../api/types'
import { useApi, useDebounced } from '../../api/useApi'
import { useCrumb } from '../../shell/crumbs'
import { useBladeKeys, useEscape } from '../../shell/keys'
import { useNav } from '../../state/nav'
import { useUrlState } from '../../state/url'
import { useBrowserZone } from '../../state/zone'
import { ErrorCard } from '../../ui/ErrorCard'
import { Icon } from '../../ui/Icon'
import { planJump, jumpToast } from '../shared/answers.logic'
import {
  biggestRowToast, finderToast, goPlan, matchingRows, noHitToast, rankStep, stepHit, turnPositions, type FinderKind, type Hit,
} from '../shared/finders'
import { KeysOverlay } from '../shared/KeysOverlay'
import { StatusPos } from '../shared/StatusBar'
import { useStatus } from '../shared/statusCtx'
import type { StateKey } from '../shared/states'
import { useFinders } from '../shared/useFinders'
import { useToast } from '../shared/useToast'
import { keptTurns, SEARCH_DEBOUNCE_MS, searchRequest } from '../session/sessionSearch'
import { buildEntries, cursorStops, displayIx } from '../session/turnList.logic'
import { publishTurnCursor } from './cursorBridge'
import { Ledger } from './Ledger'
import { CompactHeader, CompactList } from './TurnCompact'
import { TurnDetails, TurnHeader } from './TurnHeader'
import { TurnFinderLine } from './TurnFinderLine'
import { TurnHistogram } from './TurnHistogram'
import { TurnWaterfall } from './TurnWaterfall'
import type { ScrollRequest } from './RowList'
import {
  activeMeasure, buildNodes, flatRows, foldHolding, moveInList, positionOf, rangeAgentMap, rowStart, visibleRows, type Item, type RowNode, type ViewOpts,
} from './rowList.logic'
import { toolNamesOfCalls } from './rowName'
import {
  handoffToast, histogramShown, ledgerGrid, longestInTurn, maxByMeasure, overTimeToggleShown, sessionEdgeToast, skillCounter, skillCounterText, stepToTurn,
  type NumberedStop,
} from './turn.logic'
import { useAgentBands } from './useAgentBands'

const EMPTY_ITEMS: TurnListWire['items'] = []
const NO_SET: ReadonlySet<string> = new Set()
const NO_ROWS: TurnRows['rows'] = []

/** The session and turn the blade showed last: a blade sliding out has already lost them from the URL (SES-3). */
const last: { s: string | null; t: number | null } = { s: null, t: null }

/**
 * The turn blade (TRN-1 to TRN-64): header, histogram, finder line and the row list as a Ledger or a Waterfall; at 400 px (detail open) the
 * compact form. The cursor is the URL's `step`; `j`/`k` move it and hand off across turns.
 */
export function TurnBlade({ width, compact }: { width: number; compact: boolean }) {
  const url = useUrlState()
  const nav = useNav()
  if (url.s !== null) last.s = url.s
  if (url.t !== null) last.t = url.t
  const session = url.s ?? last.s // FALLBACK-OK: SES-3 (a closing blade keeps its session until the transition ends)
  const ix = url.t ?? last.t // FALLBACK-OK: SES-3 (same: a closing blade keeps its turn)
  if (session === null || ix === null) throw new Error('TurnBlade rendered with no session and turn in the URL')

  // ---- data ----
  const sessionQ = useApi<SessionDetail>(urls.sessionDetail(session), (signal) => client.sessionDetail(session, signal))
  const turnsQ = useApi<TurnListWire>(urls.sessionTurns(session), (signal) => client.sessionTurns(session, undefined, signal))
  const all = useMemo(() => buildEntries(turnsQ.data?.items ?? EMPTY_ITEMS), [turnsQ.data]) // FALLBACK-OK: SES-80 (no turns until the list arrives)
  const stops = useMemo(() => cursorStops(all), [all])
  const here = stops.find((e) => e.ix === ix)
  const turnOp = here?.row.turn ?? null // FALLBACK-OK: TRN-60 (the turn's op id is known once the session's turn list arrives)
  const tz = useBrowserZone()

  const rowsQ = useApi<TurnRows>(turnOp ? urls.turnRows(turnOp) : null, (signal) => client.turnRows(turnOp!, undefined, signal))
  const rangeQ = useApi<TurnRows>(turnOp && url.zoom ? urls.turnRows(turnOp, { from_ms: url.zoom.start_ms, to_ms: url.zoom.end_ms }) : null, (signal) =>
    client.turnRows(turnOp!, { from_ms: url.zoom!.start_ms, to_ms: url.zoom!.end_ms }, signal),
  )
  const rows = rowsQ.data !== undefined && !rowsQ.stale && rowsQ.data.turn.turn === turnOp ? rowsQ.data : undefined
  const top = rows?.rows ?? NO_ROWS // FALLBACK-OK: TRN-60 (no rows until the turn's rows arrive; the list shows skeletons)

  const debounced = useDebounced(url.find, SEARCH_DEBOUNCE_MS)
  const req = searchRequest(debounced)
  const searchQ = useApi(req ? urls.sessionFind(session, req) : null, (signal) => client.sessionFind(session, req!, signal))
  const searching = url.find.trim() !== ''
  const searchLoading = searching && searchQ.data === undefined && searchQ.error === null
  const matchIds = searching ? matchingRows(searchQ.data) : null
  const kept = searching ? keptTurns(searchQ.data) : null

  const positions = useMemo(() => turnPositions(stops.map((e) => e.row)), [stops])
  const finders = useFinders(session, turnsQ.data ? positions : undefined, { active: searching, result: searchQ.data })
  const biggestQ = useApi(urls.sessionFind(session, { kind: 'biggest', measure: url.bars }), (signal) => client.sessionFind(session, { kind: 'biggest', measure: url.bars }, signal))

  const bandsApi = useAgentBands(top)
  const { nodes, open: openAgents, bands } = bandsApi
  const toolNames = useMemo(() => toolNamesOfCalls(nodes), [nodes])
  const rangeAgents = useMemo(() => rangeAgentMap(rangeQ.data?.range ?? null), [rangeQ.data]) // FALLBACK-OK: TRN-33 (outside a zoom, or while its figures load, agent rows show their whole figures)

  // ---- view state that resets with the turn (TRN-37, TRN-14) ----
  const [unfoldedFor, setUnfolded] = useState<{ turn: string | null; keys: ReadonlySet<string> }>({ turn: null, keys: NO_SET })
  const unfolded = unfoldedFor.turn === turnOp ? unfoldedFor.keys : NO_SET
  const [showAllFor, setShowAll] = useState<{ turn: string | null; on: boolean }>({ turn: null, on: false })
  const showAll = showAllFor.turn === turnOp && showAllFor.on
  const unfold = useCallback((key: string) => setUnfolded((u) => ({ turn: turnOp, keys: new Set([...(u.turn === turnOp ? u.keys : NO_SET), key]) })), [turnOp])

  const turnCost = rows?.turn.cost.cost_usd ?? here?.row.cost_usd ?? null // FALLBACK-OK: TRN-60 (the quiet rule's 2 % uses the turn's cost; none known yet means no threshold)
  const base: ViewOpts = useMemo(
    () => ({ openAgents, zoom: url.zoom, search: matchIds, order: url.order, showAll, unfolded, turnCost, rangeAgents, bands }),
    [openAgents, url.zoom, matchIds, url.order, showAll, unfolded, turnCost, rangeAgents, bands],
  )
  const listReady = rows !== undefined && !searchLoading
  const bareItems = useMemo(() => visibleRows(nodes, { ...base, cursor: null }), [nodes, base])
  const bareRows = useMemo(() => flatRows(bareItems), [bareItems])

  // ---- the cursor is the URL's `step` (SES-8, SES-9) ----
  const isMessage = url.step !== null && url.step === turnOp
  const stepInList = url.step !== null && bareRows.some((n) => n.id === url.step)
  const cursorId = isMessage ? null : stepInList ? url.step : (bareRows[0]?.id ?? null) // FALLBACK-OK: SES-9 (no step, or one the list does not show: the first row)
  const items: Item[] = useMemo(() => visibleRows(nodes, { ...base, cursor: cursorId }), [nodes, base, cursorId])
  const flat = useMemo(() => flatRows(items), [items])

  // ---- scrolling (TRN-54) ----
  const [scroll, setScroll] = useState<ScrollRequest>({ n: 0, mode: 'center' })
  const requestScroll = useCallback((mode: ScrollRequest['mode']) => setScroll((s) => ({ n: s.n + 1, mode })), [])
  const selfStep = useRef<string | null>(url.step)
  useEffect(() => {
    if (url.step !== selfStep.current) {
      selfStep.current = url.step
      requestScroll('center') // a finder, a deep link or a legend jump moved the cursor
    }
  }, [url.step, requestScroll])
  useEffect(() => requestScroll('center'), [turnOp, requestScroll])

  // ---- moving the cursor ----
  const toast = useToast()
  const setCursor = useCallback(
    (id: string, mode: ScrollRequest['mode'] | null = 'nearest') => {
      selfStep.current = id
      nav.replace({ step: id })
      if (mode) requestScroll(mode)
    },
    [nav, requestScroll],
  )
  // A cursor that lands inside a quiet run opens it, and it stays open until the turn changes (TRN-37).
  useEffect(() => {
    if (cursorId === null) return
    const k = foldHolding(visibleRows(nodes, { ...base, cursor: null }), cursorId)
    if (k !== null) unfold(k)
  }, [cursorId, nodes, base, unfold])

  const openRow = useCallback(
    (id: string) => {
      selfStep.current = id
      if (url.detail) nav.replace({ step: id })
      else nav.openBlade('detail', { step: id, detail: true })
    },
    [nav, url.detail],
  )
  const openMessage = useCallback(() => {
    if (turnOp === null) return
    if (url.detail) nav.replace({ step: turnOp })
    else nav.openBlade('detail', { step: turnOp, detail: true })
  }, [nav, turnOp, url.detail])

  // ---- turns: t / T, the buttons, and the j / k hand-off (TRN-50, TRN-53) ----
  const landing = useRef<'first' | 'last' | null>(null)
  const swapTurn = useCallback(
    (to: NumberedStop, where: 'first' | 'last' | null) => {
      landing.current = where
      selfStep.current = null
      nav.replace({ t: to.ix, step: null, zoom: null })
    },
    [nav],
  )
  const neighbour = useCallback((dir: 1 | -1) => stepToTurn(stops, ix, dir, kept), [stops, ix, kept])
  const switchTurn = useCallback(
    (dir: 1 | -1) => {
      const to = neighbour(dir)
      if (to) swapTurn(to, 'first')
    },
    [neighbour, swapTurn],
  )
  const handoff = useCallback(
    (dir: 1 | -1) => {
      const to = neighbour(dir)
      if (!to) {
        toast.show(sessionEdgeToast(dir))
        return
      }
      toast.show(handoffToast(ix, to))
      swapTurn(to, dir > 0 ? 'first' : 'last')
    },
    [neighbour, swapTurn, toast, ix],
  )
  // The new turn's cursor lands on its first row (j) or last row (k) once its rows are in.
  useEffect(() => {
    const where = landing.current
    if (where === null || !listReady || flat.length === 0) return
    landing.current = null
    setCursor((where === 'last' ? flat[flat.length - 1] : flat[0]).id, 'center')
  }, [listReady, flat, setCursor])

  const move = useCallback(
    (delta: number) => {
      const m = moveInList(flat, cursorId, delta)
      if (m.kind === 'to') setCursor(m.id)
      else if (m.kind === 'edge') handoff(m.dir)
    },
    [flat, cursorId, setCursor, handoff],
  )

  // ---- agents (TRN-35) ----
  const cursorNode: RowNode | undefined = flat.find((n) => n.id === cursorId)
  const afterOpen = useRef<string | null>(null)
  const toggleAgent = useCallback(
    (id: string) => {
      const on = !openAgents.has(id)
      bandsApi.setOpen(id, on)
      if (on) afterOpen.current = id
    },
    [openAgents, bandsApi],
  )
  // `l` puts the cursor on the agent's first row once its rows are in.
  useEffect(() => {
    const a = afterOpen.current
    if (a === null) return
    const child = nodes.find((n) => n.ancestors[n.ancestors.length - 1] === a)
    if (child) {
      afterOpen.current = null
      setCursor(child.id, 'nearest')
    }
  }, [nodes, setCursor])
  const showAgent = useCallback(() => {
    if (cursorNode?.row.type !== 'agent' || url.order !== null) return false // TRN-14: no agent bands in the Cost and Duration orders
    bandsApi.setOpen(cursorNode.id, true)
    afterOpen.current = cursorNode.id
    return true
  }, [cursorNode, bandsApi, url.order])
  const foldAgent = useCallback(() => {
    if (!cursorNode) return false
    if (cursorNode.ancestors.length > 0) {
      const owner = cursorNode.ancestors[cursorNode.ancestors.length - 1]
      bandsApi.setOpen(owner, false)
      setCursor(owner, 'nearest')
      return true
    }
    if (cursorNode.row.type === 'agent' && openAgents.has(cursorNode.id)) {
      bandsApi.setOpen(cursorNode.id, false)
      return true
    }
    return false
  }, [cursorNode, openAgents, bandsApi, setCursor])

  // ---- finding a row that is inside an agent whose rows are not loaded (SES-9, SES-50 step 1) ----
  const [locating, setLocating] = useState<string | null>(null)
  useEffect(() => {
    if (locating === null || rows === undefined) return
    const found = nodes.find((n) => n.id === locating)
    if (found) {
      found.ancestors.forEach((a) => bandsApi.setOpen(a, true))
      setLocating(null)
      requestScroll('center')
      return
    }
    if (nodes.some((n) => n.row.type === 'agent' && bands.get(n.id)?.status === 'loading')) return
    const next = nodes.find((n) => n.row.type === 'agent' && n.row.n_rows > 0 && !bandsApi.isAsked(n.id))
    if (next) bandsApi.ask(next.id)
    else {
      setLocating(null) // SES-9: a step that names no row is dropped; the turn opens at its first row
      if (url.step === locating) nav.replace({ step: null })
    }
  }, [locating, rows, nodes, bands, bandsApi, requestScroll, nav, url.step])

  // SES-9: a `step` the turn's rows do not name is resolved (a request id, a tool_use id, a row of another turn or inside an agent).
  const inAnyNode = url.step !== null && nodes.some((n) => n.id === url.step)
  const gapStep = url.step !== null && /^gap-\d+$/.test(url.step)
  const needResolve = rows !== undefined && url.step !== null && !isMessage && !inAnyNode && !gapStep && locating !== url.step
  const resolveQ = useApi<Resolved>(needResolve ? urls.sessionResolve(session, url.step!) : null, (signal) => client.sessionResolve(session, url.step!, signal))
  const handled = useRef<string | null>(null)
  useEffect(() => {
    if (!needResolve) return
    const step = url.step!
    if (resolveQ.error) {
      if (handled.current !== step) {
        handled.current = step
        nav.replace({ step: null }) // SES-9: a step that names no row is dropped
      }
      return
    }
    const r = resolveQ.data
    if (!r || handled.current === step) return
    handled.current = step
    const target = stops.find((e) => e.row.turn === r.turn)
    if (target && target.ix !== ix) {
      nav.replace({ t: target.ix, step: r.row.id, zoom: null }) // a row of another turn opens that turn
      setLocating(r.row.id)
      return
    }
    if (r.row.id !== step) nav.replace({ step: r.row.id }) // rewritten to the op_id form
    setLocating(r.row.id)
  }, [needResolve, resolveQ.data, resolveQ.error, url.step, stops, ix, nav])
  // SES-11: a `t` that names no numbered turn is dropped.
  useEffect(() => {
    if (turnsQ.data && !here) nav.replace({ t: null })
  }, [turnsQ.data, here, nav])
  // A step that the list hides (a zoom, the order, a search) moves to the first row shown; with the detail open the URL follows the cursor.
  useEffect(() => {
    if (!listReady || isMessage || (url.step === null && !url.detail)) return
    if (url.step !== null && (!inAnyNode || locating !== null)) return // being resolved or revealed
    if (url.step !== null && !stepInList && cursorId !== null) setCursor(cursorId, 'center')
    else if (url.step === null && url.detail && cursorId !== null) setCursor(cursorId, null)
  }, [listReady, isMessage, url.step, url.detail, inAnyNode, locating, stepInList, cursorId, setCursor])

  // ---- finders (SES-50 to SES-57) ----
  const rank = useRef(-1)
  useEffect(() => {
    rank.current = -1
  }, [url.bars])
  const land = useCallback(
    (hit: Hit, kind: FinderKind | 'biggest') => {
      const hitIx = displayIx(hit.display_id)
      if (hitIx === null) throw new Error(`finder hit ${hit.row.id} is in a turn with no number`)
      const sameTurn = hit.turn === turnOp
      const plan = goPlan(hit, kind, hitIx, { current: { t: url.t, zoom: url.zoom, order: url.order, detail: url.detail }, rowInOrderedList: sameTurn && flat.some((n) => n.id === hit.row.id) })
      selfStep.current = plan.patch.step ?? null // FALLBACK-OK: SES-55 (a hit on the turn itself has no step: the turn opens at its first row)
      nav.replace(plan.patch)
      if (!sameTurn) landing.current = null
      if (hit.depth > 0 && hit.row.type !== 'turn') setLocating(hit.row.id)
    },
    [nav, url.t, url.zoom, url.order, url.detail, turnOp, flat],
  )
  const runFinder = useCallback(
    (kind: FinderKind, dir: 1 | -1) => {
      const set = finders[kind]
      if (set.failed) {
        set.retry()
        return
      }
      if (set.pool === undefined || turnOp === null) return
      const order = nodes.map((n) => n.id)
      const agentStarts = nodes.flatMap((n) => (n.depth === 0 && n.row.type === 'agent' ? [{ id: n.id, start_ms: rowStart(n.row) ?? Number.POSITIVE_INFINITY }] : [])) // FALLBACK-OK: TRN-64 (an agent with no start owns no hit by time)
      const r = stepHit(set.pool, positions, cursorId === null ? { at: 'turn', turn: turnOp } : { at: 'row', turn: turnOp, row: cursorId, order, agentStarts }, dir)
      if (r === null) {
        toast.show(noHitToast(kind))
        return
      }
      toast.show(finderToast(kind, r.index, set.pool.length, r.hit))
      land(r.hit, kind)
    },
    [finders, turnOp, nodes, positions, cursorId, toast, land],
  )
  const biggest = useCallback(
    (dir: 1 | -1) => {
      const hits = (biggestQ.data?.hits ?? []).filter((h) => positions.has(h.turn)) // FALLBACK-OK: SES-52 (no ranking until the request returns)
      if (hits.length === 0) return
      rank.current = rankStep(rank.current, dir, hits.length)
      toast.show(biggestRowToast(rank.current, hits.length, url.bars, hits[rank.current]))
      land(hits[rank.current], 'biggest')
    },
    [biggestQ.data, positions, url.bars, toast, land],
  )

  // ---- legend jump inside this turn (TRN-4) ----
  const jump = useCallback(
    (state: StateKey) => {
      if (turnOp === null) return
      const plan = planJump(state, longestInTurn(top, turnOp))
      if (plan.kind === 'none') {
        toast.show(plan.toast)
        return
      }
      toast.show(jumpToast(plan, ix, tz))
      const row = top.find((r) => r.id === plan.row.id)
      const hit: Hit = { row: plan.row, turn: turnOp, display_id: `T${ix}`, depth: 0, label: '', value: plan.durMs, start_ms: row ? rowStart(row) : null }
      const g = goPlan(hit, 'waits', ix, { current: { t: url.t, zoom: url.zoom, order: url.order, detail: url.detail }, rowInOrderedList: flat.some((n) => n.id === plan.row.id) })
      selfStep.current = plan.row.id
      if (url.detail) nav.replace(g.patch)
      else nav.openBlade('detail', g.patch)
    },
    [turnOp, top, toast, ix, tz, nav, url.t, url.zoom, url.order, url.detail, flat],
  )

  // ---- keys (SES-73, SES-6) ----
  const [keysOpen, setKeysOpen] = useState(false)
  const setOrder = useCallback(
    (order: 'cost' | 'duration' | null) => {
      setShowAll({ turn: turnOp, on: false })
      nav.replace({ order })
    },
    [nav, turnOp],
  )
  useBladeKeys('turn', (e) => {
    switch (e.key) {
      case 'j':
      case 'ArrowDown':
        move(1)
        return true
      case 'k':
      case 'ArrowUp':
        move(-1)
        return true
      case 'J':
        move(10)
        return true
      case 'K':
        move(-10)
        return true
      case 't':
        switchTurn(1)
        return true
      case 'T':
        switchTurn(-1)
        return true
      case 'Enter':
        if (cursorId !== null) openRow(cursorId)
        return true
      case 'l':
        return showAgent()
      case 'h':
        return foldAgent()
      case 'b':
        biggest(1)
        return true
      case 'B':
        biggest(-1)
        return true
      case 'w':
        runFinder('waits', 1)
        return true
      case 'W':
        runFinder('waits', -1)
        return true
      case 'e':
        runFinder('failures', 1)
        return true
      case 's':
        runFinder('skills', 1)
        return true
      case 'S':
        runFinder('skills', -1)
        return true
      case 'a':
        runFinder('agents', 1)
        return true
      case '1':
        nav.replace({ bars: 'cost' })
        return true
      case '2':
        nav.replace({ bars: 'time' })
        return true
      case '3':
        nav.replace({ bars: 'tokens' })
        return true
      case 'v':
        nav.replace({ view: url.view === 'waterfall' ? 'ledger' : 'waterfall' })
        return true
      case '0':
        nav.replace({ zoom: null, range: null })
        return true
      case '/':
        // The search field is the session's: close the turn (and the detail) first, then focus it once the session is wide again.
        nav.closeTo('session')
        window.setTimeout(() => {
          const field = document.querySelector<HTMLInputElement>('input[aria-label="Find a step"]')
          field?.focus()
          field?.select()
        }, 320)
        return true
      case '?':
        setKeysOpen((o) => !o)
        return true
      default:
        return false
    }
  })
  useEscape('turn', () => {
    if (url.zoom) {
      nav.replace({ zoom: null }) // SES-6 step 4
      return true
    }
    return false
  })
  useCrumb('turn', `Turn ${ix}`)

  // ---- status bar (SES-70, TRN-52) ----
  const pos = positionOf(cursorId, items)
  const agentOwner = cursorNode && cursorNode.ancestors.length > 0 ? nodes.find((n) => n.id === cursorNode.ancestors[cursorNode.ancestors.length - 1]) : undefined
  const inside = agentOwner && agentOwner.row.type === 'agent' ? (agentOwner.row.agent_names[0] ?? agentOwner.row.agent_type ?? 'agent') : null // FALLBACK-OK: TRN-33 (an agent with neither a name nor a type is called "agent")
  const previous = stops[stops.findIndex((e) => e.ix === ix) - 1]
  const ownCounter = useMemo(() => skillCounter(nodes, cursorId, null), [nodes, cursorId])
  const needPrev = ownCounter === null && cursorNode !== undefined && cursorNode.ancestors.length === 0 && previous !== undefined
  const prevTurn = needPrev ? previous.row.turn : null
  const prevQ = useApi<TurnRows>(prevTurn ? urls.turnRows(prevTurn) : null, (signal) => client.turnRows(prevTurn!, undefined, signal))
  const prevNodes = useMemo(() => (prevQ.data ? buildNodes(prevQ.data.rows) : null), [prevQ.data])
  const counter = useMemo(
    () => ownCounter ?? (needPrev && prevNodes && previous ? skillCounter(nodes, cursorId, { ix: previous.ix, nodes: prevNodes }) : null),
    [ownCounter, needPrev, prevNodes, nodes, cursorId, previous],
  )
  const statusNode = useMemo(() => {
    if (pos.m === 0) return null
    const skill = counter ? skillCounterText(counter) : null
    return (
      <>
        <StatusPos>
          Turn {ix} · row {pos.r} of {pos.m} · {inside ? `inside ${inside}` : 'main thread'}
        </StatusPos>
        {skill ? (
          <span className="inline-flex items-center gap-1.5">
            <Icon name="book-open" tone="quiet" />
            <span>
              {skill.before}
              <b className="font-medium text-ink1">{skill.skill}</b>
              {skill.after}
            </span>
          </span>
        ) : null}
        {searching ? (
          <span className="inline-flex items-center gap-1.5">
            <Icon name="search" tone="quiet" />“{url.find}” · Esc clears
          </span>
        ) : null}
      </>
    )
  }, [pos.m, pos.r, ix, inside, counter, searching, url.find])
  useStatus('turn', statusNode)

  // ---- the cursor, for the detail blade (DET-4, DET-5, DET-33, DET-34) ----
  const goTo = useCallback(
    (id: string, via?: string) => {
      const n = nodes.find((x) => x.id === id)
      if (n) n.ancestors.forEach((a) => bandsApi.setOpen(a, true)) // opened in the same update as the step, so the list shows the row and the cursor stays
      else if (via !== undefined) bandsApi.setOpen(via, true)
      setCursor(id, 'center')
    },
    [nodes, bandsApi, setCursor],
  )
  useEffect(() => {
    publishTurnCursor({ ix, turnOp, rows, nodes, flat, items, cursorId, toolNames, bands: bandsApi, openAgents, move, goTo, toggleAgent: (id) => bandsApi.setOpen(id, !openAgents.has(id)), find: runFinder })
  }, [ix, turnOp, rows, nodes, flat, items, cursorId, toolNames, bandsApi, openAgents, move, goTo, runFinder])
  useEffect(() => () => publishTurnCursor(null), [])

  // ---- render ----
  const total = stops.length
  const isFirst = neighbour(-1) === null
  const isLast = neighbour(1) === null
  const nAgentRows = rows ? rows.rows.filter((r) => r.type === 'agent').length : (here?.row.n_agents ?? 0) // FALLBACK-OK: TRN-20 (before the rows arrive the session list's agent count stands in)
  const wall = here?.row.wall_ms ?? null // FALLBACK-OK: TRN-60 (no turn row yet: no wall)
  const histOn = histogramShown(nAgentRows, wall, url.thist)
  const cursorStart = cursorNode ? rowStart(cursorNode.row) : null
  const firstTurnIsHuman = here?.row.kind === 'human' // FALLBACK-OK: TRN-34 (before the list arrives the reason wording for a gap before the first message is the "your" form)
  const gaps = useMemo(() => top.filter((r): r is GapRow => r.type === 'gap'), [top])
  const measure = activeMeasure(url.order, url.bars)
  const grid = useMemo(() => ledgerGrid(width), [width])
  const max = useMemo(() => maxByMeasure(items, rangeAgents), [items, rangeAgents])
  const maxGapMs = useMemo(() => Math.max(0, ...flat.map((n) => (n.row.type === 'gap' ? n.row.dur_ms : 0))), [flat])
  const nTop = top.length

  const common = {
    items,
    toolNames,
    openAgents,
    firstTurnIsHuman: firstTurnIsHuman ?? true, // FALLBACK-OK: TRN-34 (as above)
    scroll,
    onToggleAgent: toggleAgent,
    onUnfold: unfold,
    onShowAll: () => setShowAll({ turn: turnOp, on: true }),
    onBandMore: bandsApi.more,
    onBandRetry: bandsApi.retry,
  }

  if (compact) {
    return (
      <div className="flex h-full flex-col">
        <CompactHeader
          row={here?.row}
          turn={rows?.turn}
          ix={ix}
          total={total}
          isFirst={isFirst}
          isLast={isLast}
          onPrev={() => switchTurn(-1)}
          onNext={() => switchTurn(1)}
          onClose={() => nav.closeTo('session')}
          onOpenMessage={openMessage}
        />
        <div className="flex min-h-0 flex-1 flex-col border-t border-hair">
          {rowsQ.error ? (
            <ErrorCard noun="the turn" message={rowsQ.error.message} onRetry={rowsQ.retry} />
          ) : (
            <CompactList {...common} tz={tz} cursorId={cursorId} rangeAgents={rangeAgents} onOpen={openRow} />
          )}
        </div>
        {bandsApi.loaders}
        {toast.node}
        <KeysOverlay open={keysOpen} onClose={() => setKeysOpen(false)} />
      </div>
    )
  }

  let body: React.ReactNode
  if (rowsQ.error) body = <ErrorCard noun="the turn's rows" message={rowsQ.error.message} onRetry={rowsQ.retry} />
  else if (searchQ.error && searching) body = <ErrorCard noun="search results" message={searchQ.error.message} onRetry={searchQ.retry} />
  else if (url.view === 'waterfall' && here && listReady && items.length > 0) {
    body = (
      <TurnWaterfall
        {...common}
        turn={here.row}
        gaps={gaps}
        zoom={url.zoom}
        real={url.real}
        width={width}
        tz={tz}
        cursorId={cursorId}
        rowCount={pos.m}
        onOpen={openRow}
      />
    )
  } else {
    body = (
      <Ledger
        {...common}
        turnEmpty={listReady && nTop === 0}
        loading={!listReady}
        search={searching ? url.find : ''}
        zoomed={url.zoom !== null}
        order={url.order}
        env={{ tz, grid, measure, max, cursorId, onOpen: openRow }}
        rangeAgents={rangeAgents}
        maxGapMs={maxGapMs}
      />
    )
  }

  return (
    <div className="flex h-full flex-col">
      <TurnHeader
        tz={tz}
        ix={ix}
        total={total}
        row={here?.row}
        turn={rows?.turn}
        zoom={url.zoom}
        range={url.zoom ? (rangeQ.data ? rangeQ.data.range : undefined) : undefined}
        isFirst={isFirst}
        isLast={isLast}
        overTimeToggle={overTimeToggleShown(nAgentRows, wall, url.thist)}
        overTimeOpen={histOn}
        info={url.tinfo}
        onPrev={() => switchTurn(-1)}
        onNext={() => switchTurn(1)}
        onClose={() => nav.closeTo('session')}
        onToggleOver={() => nav.replace({ thist: !histOn })}
        onToggleInfo={() => nav.replace({ tinfo: !url.tinfo })}
        onClearZoom={() => nav.replace({ zoom: null })}
        onOpenMessage={openMessage}
        onJump={rows ? jump : undefined}
        answersError={rowsQ.error ? <ErrorCard noun="the turn's figures" message={rowsQ.error.message} onRetry={rowsQ.retry} /> : undefined}
      />
      {url.tinfo && rows && sessionQ.data ? <TurnDetails turn={rows.turn} provider={sessionQ.data.session.provider} onOpenMessage={openMessage} /> : null}
      {histOn && turnOp ? <TurnHistogram turn={turnOp} bars={url.bars} tz={tz} zoom={url.zoom} cursorMs={cursorStart} onBrush={(zoom) => nav.replace({ zoom })} /> : null}
      <TurnFinderLine
        waterfall={url.view === 'waterfall'}
        real={url.real}
        zoomed={url.zoom !== null}
        order={url.order}
        turn={turnOp ?? ''} // FALLBACK-OK: TRN-60 (before the turn is known no hit belongs to it, so no chip shows)
        finders={finders}
        onView={(w) => nav.replace({ view: w ? 'waterfall' : 'ledger' })}
        onOrder={setOrder}
        onReal={(real) => nav.replace({ real })}
        onFind={(kind) => runFinder(kind, 1)}
      />
      <div className="flex min-h-0 flex-1 flex-col">{body}</div>
      {bandsApi.loaders}
      {toast.node}
      <KeysOverlay open={keysOpen} onClose={() => setKeysOpen(false)} />
    </div>
  )
}
