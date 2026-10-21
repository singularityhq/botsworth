import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { client, urls } from '../../api/client'
import { ApiError } from '../../api/errors'
import type { Evidence, GapRow as GapRowWire, Histogram as HistogramWire, ReviewReport, SessionDetail, TurnList as TurnListWire } from '../../api/types'
import { useApi, useDebounced } from '../../api/useApi'
import { useCrumb } from '../../shell/crumbs'
import { useBladeKeys, useEscape } from '../../shell/keys'
import { useNav, type Patch } from '../../state/nav'
import { useUrlState, type SessionView } from '../../state/url'
import { useBrowserZone } from '../../state/zone'
import { EmptyState } from '../../ui/EmptyState'
import { ErrorCard } from '../../ui/ErrorCard'
import { Icon } from '../../ui/Icon'
import { Tooltip } from '../../ui/Tooltip'
import { planJump, jumpToast, longestStalled, type DisplayKey } from '../shared/answers.logic'
import {
  biggestTurnToast, finderToast, goPlan, noHitToast, rankStep, rankTurns, stepHit, turnPositions, turnValue, type FinderKind, type From, type Hit,
} from '../shared/finders'
import { FinderChips } from '../shared/FinderChips'
import { KeysOverlay } from '../shared/KeysOverlay'
import { StatusPos } from '../shared/StatusBar'
import { useStatus } from '../shared/statusCtx'
import { sessionStatus } from '../shared/statusBar.logic'
import { useFinders } from '../shared/useFinders'
import { useToast } from '../shared/useToast'
import { FinderLine } from './FinderLine'
import { OverTime } from './OverTime'
import { rangeCost } from '../shared/histogram.logic'
import { DetailsPanel, SessionHeader } from './SessionHeader'
import { ReviewView } from './ReviewView'
import { SessionRail } from './SessionRail'
import { firstKept, keptTurns, SEARCH_DEBOUNCE_MS, searchRequest } from './sessionSearch'
import { SessionWaterfall, ViewToggle } from './SessionWaterfall'
import { TurnList } from './TurnList'
import { buildEntries, cursorStops, initialCursor, moveCursor, withinOneDay, type ListEntry } from './turnList.logic'

const EMPTY_ITEMS: TurnListWire['items'] = []

/** True when the 404 means this session does not exist (SES-81). */
function isNotFound(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404
}

/** The session the blade showed last (SES-3). */
const last: { s: string | null } = { s: null }

/** The session blade (SES-1 to SES-38): header, answers, Details, finder line, turn list; the session rail at 400 px (SES-4). */
export function SessionBlade({ width, compact }: { width: number; compact: boolean }) {
  const url = useUrlState()
  const nav = useNav()
  // SES-3: while the blade slides out the URL has already lost `s`. The strip mounts the sliding-out blade as a new instance, so the
  // session it showed is kept outside the component (as the turn blade does), not in a ref.
  if (url.s !== null) last.s = url.s
  const session = url.s ?? last.s // FALLBACK-OK: SES-3 (a closing blade keeps its session until the transition ends)
  if (session === null) throw new Error('SessionBlade rendered with no session in the URL')
  const detail = useApi<SessionDetail>(urls.sessionDetail(session), (signal) => client.sessionDetail(session, signal))
  const turns = useApi<TurnListWire>(urls.sessionTurns(session), (signal) => client.sessionTurns(session, undefined, signal))
  const review = useApi<ReviewReport>(urls.sessionReview(session), (signal) => client.sessionReview(session, signal))
  const debounced = useDebounced(url.find, SEARCH_DEBOUNCE_MS)
  const req = searchRequest(debounced)
  const search = useApi(req ? urls.sessionFind(session, req) : null, (signal) => client.sessionFind(session, req!, signal))
  // SES-13: with a range set the cost answer is the clock-rule sum from the histogram bins (D47); the request is the one Over time uses.
  const rangeHist = useApi<HistogramWire>(url.range ? urls.sessionHistogram(session) : null, (signal) => client.sessionHistogram(session, {}, signal))
  const { show, node: toastNode } = useToast()
  const fieldRef = useRef<HTMLInputElement>(null)
  const [cursor, setCursor] = useState<string | null>(null)
  const [keysOpen, setKeysOpen] = useState(false)
  // REV-3: `r` and Esc close Review back to the last of Ledger or Waterfall.
  const lastList = useRef<Exclude<SessionView, 'review'>>('ledger')
  if (url.sview !== 'review') lastList.current = url.sview
  // REV-3: a jump from Review sets the view to Ledger first, as its own history step, so Esc from the opened turn goes back to Ledger.
  const leaveReview = useCallback(() => {
    if (url.sview === 'review') nav.replace({ sview: 'ledger' })
  }, [nav, url.sview])
  const rank = useRef(-1) // SES-52: position in the `b` ranking; -1 before the first `b`

  const s = detail.data?.session
  const tz = useBrowserZone()
  useCrumb('session', s?.name ?? null) // FALLBACK-OK: SES-80 (no name until the session data arrives; the shell shows its own label)

  const searching = url.find.trim() !== ''
  const kept = searching ? keptTurns(search.data) : null
  const items = turns.data?.items ?? EMPTY_ITEMS // FALLBACK-OK: SES-80 (no rows until the turn list arrives; the list shows skeletons)
  // The entries of the whole session (for turn numbers and "has numbered turns"), and the entries the search and range keep.
  const all = useMemo(() => buildEntries(items), [items])
  const entries = useMemo(() => buildEntries(items, { kept, range: url.range }), [items, kept, url.range])
  const stops = useMemo(() => cursorStops(entries), [entries])
  const searchLoading = searching && search.data === undefined && search.error === null
  const listLoading = turns.data === undefined && turns.error === null

  const ixByOp = useMemo(() => new Map(cursorStops(all).map((e) => [e.row.turn, e.ix])), [all])
  const keyByIx = useMemo(() => new Map(cursorStops(all).map((e) => [e.ix, e.key])), [all])
  const openKey = url.t === null ? null : (keyByIx.get(url.t) ?? null) // FALLBACK-OK: SES-11 (a `t` that names no numbered turn marks no row)

  // ---- finders (SES-50 to SES-57) ----
  const positions = useMemo(() => turnPositions(cursorStops(all).map((e) => e.row)), [all])
  const finders = useFinders(session, turns.data ? positions : undefined, { active: searching, result: search.data })
  // SES-52: changing the measure resets the ranking; the next `b` starts from the biggest.
  useEffect(() => {
    rank.current = -1
  }, [url.bars])

  // SES-38: the cursor starts on the first numbered turn with rows; a cursor the filters removed returns to the first turn left.
  useEffect(() => {
    if (stops.length === 0) return
    setCursor((cur) => (cur !== null && stops.some((s) => s.key === cur) ? cur : (openKey && stops.some((s) => s.key === openKey) ? openKey : initialCursor(entries))))
  }, [stops, entries, openKey])
  // SES-6 step 5: when the turn blade changes turn, the cursor follows it.
  useEffect(() => {
    if (openKey !== null) setCursor(openKey)
  }, [openKey])

  // ---- opening (SES-1, SES-4, SES-37) ----
  const openTurn = useCallback(
    (ix: number, patch: Patch = {}) => {
      if (url.t === null) nav.openBlade('turn', { t: ix, ...patch })
      else nav.replace({ t: ix, step: null, detail: false, ...patch })
    },
    [nav, url.t],
  )
  const openTurnKey = useCallback(
    (key: string) => {
      const e = stops.find((x) => x.key === key)
      if (!e) return
      setCursor(key)
      openTurn(e.ix)
    },
    [stops, openTurn],
  )
  const openGap = useCallback(
    (key: string) => {
      const e = entries.find((x): x is Extract<ListEntry, { type: 'gap' }> => x.type === 'gap' && x.key === key)
      // FALLBACK-OK: SES-37 (a stretch attached to no numbered turn has no ledger to open; the click does nothing)
      if (!e || e.turnIx === null) return
      openTurn(e.turnIx, { step: e.gap.id, detail: true })
    },
    [entries, openTurn],
  )

  // ---- Review evidence (REV-6) ----
  // A link opens its row as a finder jump does (SES-51): the turn opens, the row is the cursor, its detail opens; the view returns to Ledger.
  const openEvidence = useCallback(
    (ev: Evidence) => {
      const ix = ev.turn === null ? null : (ixByOp.get(ev.turn) ?? null) // FALLBACK-OK: QRY-42 (a stretch attached to no numbered turn has no ledger to open; the toast says why)
      // FALLBACK-OK: QRY-42 (evidence with no row, or a row outside the numbered turns, has nothing to open)
      if (ix === null || ev.row === null) {
        show(['That stretch is not inside a numbered turn.'])
        return
      }
      // SES-10 [D56]: the jump adds a history entry over Review, so Back returns to it; the view is Ledger in the new entry, so Esc from the turn returns to Ledger.
      const patch: Patch = { t: ix, step: ev.row.type === 'turn' ? null : ev.row.id, detail: true, zoom: null, order: null, sview: 'ledger' }
      if (url.t === null) nav.openBlade('turn', patch)
      else nav.push(patch)
    },
    [ixByOp, nav, show, url.t],
  )

  // ---- legend jump (SES-27) ----
  const jump = useCallback(
    (state: DisplayKey) => {
      if (!s) return
      const plan = planJump(state, { ...s.longest, stalled: longestStalled(items.filter((i): i is GapRowWire => i.type === 'gap')) })
      if (plan.kind === 'none') {
        show(plan.toast)
        return
      }
      const ix = ixByOp.get(plan.turn) ?? null // FALLBACK-OK: SES-27 (a target outside the numbered turns; handled on the next line)
      // FALLBACK-OK: SES-27 (the target turn is not a numbered turn of this list: no navigation, the toast says why)
      if (ix === null) {
        show(['That stretch is not inside a numbered turn.'])
        return
      }
      show(jumpToast(plan, ix, tz))
      openTurn(ix, { step: plan.row.id, detail: true })
    },
    [s, items, ixByOp, openTurn, show, tz],
  )

  // ---- finders: go to a hit (SES-50, SES-57) ----
  const land = useCallback(
    (hit: Hit, kind: FinderKind) => {
      const ix = ixByOp.get(hit.turn)
      // Hits are filtered to numbered turns, so the turn is always in the map.
      if (ix === undefined) throw new Error(`finder hit ${hit.row.id} is in turn ${hit.turn}, which is not a numbered turn`)
      const plan = goPlan(hit, kind, ix, { current: { t: url.t, zoom: url.zoom, order: url.order, detail: url.detail } })
      if (url.t === null) nav.openBlade('turn', plan.patch)
      else nav.replace(plan.patch) // SES-57: the turn blade swaps in place
    },
    [ixByOp, nav, url.t, url.zoom, url.order, url.detail],
  )
  const runFinder = useCallback(
    (kind: FinderKind, dir: 1 | -1) => {
      const set = finders[kind]
      if (set.failed) {
        set.retry() // D41 M8: a click or the key retries
        return
      }
      if (set.pool === undefined) return // still loading (D38 M8)
      // A turn is open on the row a finder landed on (the rail has focus): continue from that hit, so the same hit is not found again.
      const from: From | null = cursor === null ? null : url.t !== null && url.step !== null ? { at: 'hit', turn: cursor, row: url.step } : { at: 'turn', turn: cursor }
      const r = stepHit(set.pool, positions, from, dir)
      if (r === null) {
        show(noHitToast(kind))
        return
      }
      show(finderToast(kind, r.index, set.pool.length, r.hit))
      leaveReview()
      land(r.hit, kind)
    },
    [finders, cursor, positions, show, land, leaveReview, url.t, url.step],
  )
  // SES-52, session blade: `b` ranks the numbered turns the list keeps by the measure and selects the next in the ranking (cursor only).
  const biggest = useCallback(
    (dir: 1 | -1) => {
      const ranking = rankTurns(stops.map((x) => x.row), url.bars)
      if (ranking.length === 0) return
      leaveReview()
      rank.current = rankStep(rank.current, dir, ranking.length)
      const t = ranking[rank.current]
      setCursor(t.turn)
      show(biggestTurnToast(rank.current, url.bars, turnValue(t, url.bars)!, ixByOp.get(t.turn)!)) // rankTurns keeps only turns with a value; a ranked turn is a numbered turn
    },
    [stops, url.bars, show, ixByOp, leaveReview],
  )

  // ---- keys (SES-73, SES-6) ----
  const move = useCallback((delta: number) => setCursor((cur) => moveCursor(stops, cur, delta)), [stops])
  useBladeKeys('session', (e) => {
    // REV-3: Review has no turn cursor; these keys do nothing there (the arrows are left to scroll the page).
    if (url.sview === 'review') {
      if (['j', 'k', 't', 'T', 'Enter'].includes(e.key)) return true
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') return false
    }
    switch (e.key) {
      case 'j':
      case 'ArrowDown':
      case 't':
        move(1)
        return true
      case 'k':
      case 'ArrowUp':
      case 'T':
        move(-1)
        return true
      case 'Enter': {
        if (cursor !== null) openTurnKey(cursor)
        return true
      }
      case '/':
        fieldRef.current?.focus()
        fieldRef.current?.select()
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
      case '0':
        nav.replace({ range: null, zoom: null })
        return true
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
      case 'v':
        // REV-3: from Review `v` opens the last of Ledger and Waterfall.
        nav.replace({ sview: url.sview === 'review' ? lastList.current : url.sview === 'waterfall' ? 'ledger' : 'waterfall' })
        return true
      case 'r':
        nav.replace({ sview: url.sview === 'review' ? lastList.current : 'review' })
        return true
      case '?':
        setKeysOpen((o) => !o)
        return true
      default:
        return false
    }
  })
  useEscape('session', () => {
    if (url.find !== '') {
      nav.replace({ find: '' }) // SES-6 step 6
      return true
    }
    if (url.range) {
      nav.replace({ range: null }) // SES-6 step 7
      return true
    }
    if (url.sview === 'review') {
      nav.replace({ sview: lastList.current }) // SES-6 step 7a [D56]
      return true
    }
    return false
  })

  // SES-70: the stage's status bar shows "Turn N selected" while this is the newest blade.
  const statusText = sessionStatus(stops.find((e) => e.key === cursor)?.ix ?? null) // FALLBACK-OK: SES-70 (no cursor turn: nothing is selected, the bar stays empty)
  const statusNode = useMemo(() => (statusText && !compact ? <StatusPos>{statusText}</StatusPos> : null), [statusText, compact])
  useStatus('session', statusNode)

  // ---- states (SES-81, SES-82) ----
  if (detail.error && isNotFound(detail.error)) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState icon="search-x" action={{ label: 'Back to sessions', onClick: () => nav.closeTo('explorer') }}>
          Couldn't find session {session}
        </EmptyState>
      </div>
    )
  }

  const numbered = cursorStops(all).length > 0
  const gaps = items.filter((i): i is GapRowWire => i.type === 'gap')
  // SES-29: the open turn's start is the cursor line of the histogram.
  const openStart = openKey === null ? null : (cursorStops(all).find((e) => e.key === openKey)?.row.start_ms ?? null) // FALLBACK-OK: SES-85 (an open turn with no start draws no cursor line)
  const singleDay = s ? withinOneDay(s.start_ms, s.end_ms, tz) : true

  if (compact) {
    return (
      <>
        <SessionRail
          session={s}
          entries={stops}
          loading={listLoading || searchLoading}
          tz={tz}
          find={searching ? url.find : ''}
          search={search.data}
          openKey={openKey}
          cursor={cursor}
          onOpenTurn={openTurnKey}
        />
        {toastNode}
        <KeysOverlay open={keysOpen} onClose={() => setKeysOpen(false)} />
      </>
    )
  }

  // SES-42: the Waterfall replaces the Ledger once there are rows to draw; the Ledger's own empty and loading states cover the rest.
  const waterfall =
    url.sview === 'waterfall' && s && !listLoading && !searchLoading && stops.length > 0 ? (
      <SessionWaterfall
        entries={entries}
        gaps={gaps}
        startMs={s.start_ms}
        endMs={s.end_ms}
        realTime={url.sreal}
        width={width}
        tz={tz}
        singleDay={singleDay}
        cursor={cursor}
        openKey={openKey}
        onOpenTurn={openTurnKey}
        onOpenGap={openGap}
      />
    ) : null
  return (
    <div className="flex h-full flex-col">
      {detail.error ? (
        <ErrorCard noun="session" message={detail.error.message} onRetry={detail.retry} />
      ) : (
        <SessionHeader
          session={s}
          tz={tz}
          over={url.over}
          info={url.info}
          range={url.range}
          onToggleOver={() => nav.replace({ over: !url.over })}
          onToggleInfo={() => nav.replace({ info: !url.info })}
          onClearRange={() => nav.replace({ range: null })}
          onJump={turns.data ? jump : undefined}
          lostMs={review.data?.lost_ms}
          onOpenReview={() => nav.replace({ sview: 'review' })}
          rangeCost={
            url.range === null || !s
              ? undefined
              : rangeHist.data
                ? { ...s.cost, ...rangeCost(rangeHist.data, url.range) }
                : null
          }
          keysButton={
            <Tooltip content="Keyboard (?)">
              <button type="button" aria-label="Keyboard" onClick={() => setKeysOpen(true)} className="inline-flex h-8 w-8 items-center justify-center rounded-control text-ink3 hover:text-struct">
                <Icon name="keyboard" tone="inherit" />
              </button>
            </Tooltip>
          }
        />
      )}
      {url.info && s ? <DetailsPanel session={s} /> : null}
      {url.over ? <OverTime session={session} bars={url.bars} tz={tz} range={url.range} cursorMs={openStart} /> : null}
      <FinderLine
        find={url.find}
        bars={url.bars}
        fieldRef={fieldRef}
        onFind={(text) => nav.replace({ find: text })}
        onBars={(bars) => nav.replace({ bars })}
        onEnter={() => {
          const first = firstKept(entries)
          if (first === null) return
          leaveReview() // REV-3: a search jump leaves Review
          setCursor(first)
        }}
        viewToggle={
          <ViewToggle
            view={url.sview}
            real={url.sreal}
            onView={(sview) => nav.replace({ sview })}
            onReal={(real) => nav.replace({ sreal: real })}
          />
        }
        finderChips={<FinderChips sets={finders} onFind={(kind) => runFinder(kind, 1)} onBiggest={() => biggest(1)} />}
      />
      {url.sview === 'review' ? (
        <ReviewView
          review={review.data}
          error={review.error ?? detail.error}
          onRetry={() => {
            review.retry()
            if (detail.error) detail.retry()
          }}
          tz={tz}
          onOpenEvidence={turns.data ? openEvidence : undefined}
        />
      ) : turns.error ? (
        <ErrorCard noun="turns" message={turns.error.message} onRetry={turns.retry} />
      ) : search.error ? (
        <ErrorCard noun="search results" message={search.error.message} onRetry={search.retry} />
      ) : (
        (waterfall ?? (
          <TurnList
            entries={entries}
            loading={listLoading || searchLoading}
            width={width}
            tz={tz}
            singleDay={singleDay}
            bars={url.bars}
            find={searching ? url.find : ''}
            search={search.data}
            hasNumbered={numbered}
            range={url.range !== null}
            cursor={cursor}
            openKey={openKey}
            onOpenTurn={openTurnKey}
            onOpenGap={openGap}
            onClearRange={() => nav.replace({ range: null })}
          />
        ))
      )}
      {toastNode}
      <KeysOverlay open={keysOpen} onClose={() => setKeysOpen(false)} />
    </div>
  )
}
