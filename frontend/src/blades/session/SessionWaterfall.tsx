import { useEffect, useMemo, useRef } from 'react'
import type { GapRow } from '../../api/types'
import { fmt } from '../../lib/format'
import type { SessionView } from '../../state/url'
import { Duration, Money } from '../../ui/figures'
import { Icon } from '../../ui/Icon'
import { Segmented } from '../../ui/Segmented'
import { Skeleton } from '../../ui/Skeleton'
import { makeAxis } from '../shared/histogram.logic'
import { GAP_GLYPH, GAP_LABEL, gapKind, kindOf, stateFill } from '../shared/states'
import { AxisHead, Bar, Track } from '../shared/Waterfall'
import { useElementWidth } from '../shared/useElementWidth'
import {
  agentTails, axisCaption, barBox, gapBarState, gapFolds, SESSION_FOLD_MIN_MS, SESSION_FOLD_PX, turnSpan, TAIL_PX,
} from '../shared/waterfall.logic'
import { HarnessFold } from './HarnessFold'
import { waterfallTemplate } from './sessionWaterfall.logic'
import { WhenStack } from './TurnRow'
import { findRow, GRID_GAP, GRID_PAD, scrollTarget, type ListEntry, type TurnEntry } from './turnList.logic'

type GapEntry = Extract<ListEntry, { type: 'gap' }>

const BAR_H = 12
const ROW_H = 32

interface ViewToggleProps {
  view: SessionView
  real: boolean
  onView: (view: SessionView) => void
  onReal: (real: boolean) => void
}

/** SES-41, REV-3: the Ledger | Waterfall | Review segment before the search field and, in Waterfall mode, the axis segment (SES-40). */
export function ViewToggle({ view, real, onView, onReal }: ViewToggleProps) {
  return (
    <>
      <span title="Ledger, Waterfall or Review (v, r)">
        <Segmented
          label="View"
          value={view}
          onChange={onView}
          options={[{ value: 'ledger', label: 'Ledger' }, { value: 'waterfall', label: 'Waterfall' }, { value: 'review', label: 'Review' }]}
        />
      </span>
      {view === 'waterfall' ? (
        <Segmented
          label="Axis"
          value={real ? 'real' : 'fold'}
          onChange={(v) => onReal(v === 'real')}
          options={[{ value: 'fold', label: 'Fold gaps' }, { value: 'real', label: 'Real time' }]}
        />
      ) : null}
    </>
  )
}

interface WaterfallProps {
  entries: ListEntry[]
  /** The session's gap items (SES-42 folds every wait of 2 min or more, whether or not a search or range hides its row). */
  gaps: readonly GapRow[]
  /** The session's first and last record (SES-42). */
  startMs: number
  endMs: number
  realTime: boolean
  width: number
  tz: string
  singleDay: boolean
  cursor: string | null
  openKey: string | null
  onOpenTurn: (key: string) => void
  onOpenGap: (key: string) => void
}

/** SES-42: the same rows as the Ledger (turn rows, gap rows, harness folds) on the session's time axis. */
export function SessionWaterfall(p: WaterfallProps) {
  const [headRef, trackW] = useElementWidth<HTMLDivElement>()
  const scroller = useRef<HTMLDivElement>(null)
  const template = waterfallTemplate(p.width)
  const axis = useMemo(
    () => (trackW > 0 ? makeAxis(p.startMs, Math.max(p.endMs, p.startMs + 1), trackW, gapFolds(p.gaps, p.startMs, p.endMs, SESSION_FOLD_MIN_MS, p.realTime), SESSION_FOLD_PX) : null),
    [trackW, p.startMs, p.endMs, p.gaps, p.realTime],
  )

  // SES-38: the cursor row scrolls to about 30 % from the top when it leaves the view.
  useEffect(() => {
    const box = scroller.current
    const el = p.cursor === null ? null : findRow(box, p.cursor)
    if (!box || !el) return
    const rowTop = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop
    const to = scrollTarget(rowTop, el.offsetHeight, box.scrollTop, box.clientHeight)
    if (to !== null) box.scrollTop = to
  }, [p.cursor])

  const folded = !p.realTime
  let prevStart: number | null = null
  const rows: React.ReactNode[] = []
  for (const e of p.entries) {
    const shared = { tz: p.tz, singleDay: p.singleDay, template, axis }
    if (e.type === 'turn') {
      rows.push(<WfTurnRow key={e.key} {...shared} entry={e} prevStartMs={prevStart} open={p.openKey === e.key} cursor={p.cursor === e.key} onOpen={() => p.onOpenTurn(e.key)} />)
      prevStart = e.row.start_ms ?? prevStart // FALLBACK-OK: SES-85 (a turn with no start leaves the row above as the weekday reference)
    } else if (e.type === 'gap') {
      rows.push(<WfGapRow key={e.key} {...shared} entry={e} prevStartMs={prevStart} onOpen={() => p.onOpenGap(e.key)} />)
      prevStart = e.gap.start_ms
    } else {
      rows.push(<HarnessFold key={e.key} rows={e.rows} tz={p.tz} columns={{ ids: ['when', 'kind', 'name', 'cost'], template }} />)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div data-waterfall-head className="grid h-[76px] shrink-0 items-start border-b border-hair text-ink3" style={{ gridTemplateColumns: template, columnGap: GRID_GAP, padding: '8px 16px 0 20px' }}>
        <span>When</span>
        <span />
        <div className="flex h-[68px] flex-col justify-between">
          <span>Turns</span>
          <Key />
        </div>
        <span className="text-right">Cost</span>
        <span className="text-right">Wall</span>
        <div ref={headRef} className="relative h-[68px] min-w-0">
          <span className="block truncate">{axisCaption(!folded, SESSION_FOLD_MIN_MS)}</span>
          <AxisHead axis={axis} tz={p.tz} />
        </div>
        <span />
      </div>
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto" aria-label="Turns on the session time axis">
        {axis ? rows : <Skeleton height={ROW_H} />}
        <div style={{ height: 160 }} />
      </div>
    </div>
  )
}

/** The key under "Turns": the turn bar and the agent tail. */
function Key() {
  return (
    <span className="inline-flex items-center gap-4 text-ink3">
      <span className="inline-flex items-center gap-1.5">
        <i className="inline-block h-2.5 w-4 rounded-[2px] bg-ink2" /> turn
      </span>
      <span className="inline-flex items-center gap-1.5">
        <i className="inline-block w-4" style={{ height: TAIL_PX, background: 'var(--st-agents)' }} /> agents still running
      </span>
    </span>
  )
}

const ROW_CLASS =
  'group/row grid cursor-pointer items-center border-b border-rule-soft text-body transition-colors duration-150 ease-[var(--ease-standard)] hover:bg-l3 focus-visible:outline-offset-[-2px] data-[open=true]:bg-struct-soft data-[open=true]:hover:bg-struct-soft data-[cursor=true]:outline-2 data-[cursor=true]:-outline-offset-2 data-[cursor=true]:outline-struct'

interface RowShared {
  tz: string
  singleDay: boolean
  template: string
  axis: ReturnType<typeof makeAxis> | null
}

function WfTurnRow({ entry, prevStartMs, open, cursor, onOpen, tz, singleDay, template, axis }: RowShared & { entry: TurnEntry; prevStartMs: number | null; open: boolean; cursor: boolean; onOpen: () => void }) {
  const r = entry.row
  const kind = kindOf(r.kind, r.origin, r.label)
  const span = turnSpan(r)
  return (
    <div
      data-turn-key={entry.key}
      data-open={open}
      data-cursor={cursor}
      role="button"
      tabIndex={-1}
      aria-current={open || undefined}
      onClick={onOpen}
      className={ROW_CLASS}
      style={{ gridTemplateColumns: template, columnGap: GRID_GAP, padding: GRID_PAD, height: ROW_H }}
    >
      <WhenStack startMs={r.start_ms} prevStartMs={prevStartMs} tz={tz} singleDay={singleDay} />
      <Icon name={kind.icon} tone="quiet" label={kind.label} />
      <span className="flex min-w-0 items-baseline gap-2">
        <span className="fig min-w-5 shrink-0 text-right text-ink3">{entry.ix}</span>
        <span title={r.label} className={`truncate group-data-[open=true]/row:text-struct ${kind.lead ? 'font-medium text-ink1' : 'font-normal text-ink2'}`}>
          {r.label}
        </span>
      </span>
      <span className="flex justify-end">
        <Money value={r.cost_usd} lowerBound={r.cost_lower_bound} nUnpriced={r.n_unpriced} weight={400} />
      </span>
      <span className="flex justify-end">
        <Duration ms={r.wall_ms} />
      </span>
      <Track>
        {axis && span ? (
          <>
            <Bar box={barBox(axis, span.start, span.end)} height={BAR_H} style={{ background: 'var(--color-ink2)' }} outline={open} title={`${fmt.clock(span.start, tz)}–${fmt.clock(span.end, tz)}`} />
            {agentTails(r, axis, tz).map((t, i) => (
              <Bar key={i} box={t} height={TAIL_PX} style={{ background: 'var(--st-agents)' }} title={t.title} />
            ))}
          </>
        ) : null}
      </Track>
      <Icon name="chevron-right" />
    </div>
  )
}

function WfGapRow({ entry, prevStartMs, onOpen, tz, singleDay, template, axis }: RowShared & { entry: GapEntry; prevStartMs: number | null; onOpen: () => void }) {
  const g = entry.gap
  return (
    <div
      data-gap-key={entry.key}
      role="button"
      tabIndex={-1}
      title={`${fmt.dur(g.dur_ms)} ${GAP_LABEL[gapKind(g)]}, ${entry.reason}`}
      onClick={onOpen}
      className={`${ROW_CLASS} bg-l2`}
      style={{ gridTemplateColumns: template, columnGap: GRID_GAP, padding: GRID_PAD, height: ROW_H }}
    >
      <WhenStack startMs={g.start_ms} prevStartMs={prevStartMs} tz={tz} singleDay={singleDay} />
      <Icon name={GAP_GLYPH[gapKind(g)]} tone="quiet" />
      <span className="flex min-w-0 items-baseline gap-2 whitespace-nowrap">
        <b className="fig font-medium text-ink1">{fmt.dur(g.dur_ms)}</b>
        <span className="truncate text-ink2">{GAP_LABEL[gapKind(g)]}</span>
      </span>
      <span />
      <span className="flex justify-end">
        <Duration ms={g.dur_ms} />
      </span>
      <Track>{axis ? <Bar box={barBox(axis, g.start_ms, g.end_ms)} height={BAR_H} style={stateFill(gapBarState(g))} title={GAP_LABEL[gapKind(g)]} /> : null}</Track>
      <span />
    </div>
  )
}
