import { useEffect, useMemo, useRef } from 'react'
import type { Bars } from '../../state/url'
import { EmptyState } from '../../ui/EmptyState'
import { Icon } from '../../ui/Icon'
import { SkeletonRows } from '../../ui/Skeleton'
import { GapRowView } from './GapRow'
import { HarnessFold } from './HarnessFold'
import { TurnRowView } from './TurnRow'
import {
  cursorStops, findRow, GRID_GAP, GRID_PAD, kindsVary, listColumns, listHead, maxGap, maxMeasure, scrollTarget, type ColumnId, type ListEntry,
} from './turnList.logic'
import { matchCount } from './sessionSearch'
import type { FindResult } from '../../api/types'

interface TurnListProps {
  entries: ListEntry[]
  loading: boolean
  width: number
  tz: string
  singleDay: boolean
  bars: Bars
  find: string
  search: FindResult | undefined
  /** The session has numbered turns at all (SES-83); false shows the "no turns with a message" state. */
  hasNumbered: boolean
  range: boolean
  cursor: string | null
  openKey: string | null
  onOpenTurn: (key: string) => void
  onOpenGap: (key: string) => void
  onClearRange: () => void
}

const BAR_NAME = { cost: 'cost', time: 'time', tokens: 'tokens' } as const

/** SES-33: the column header, 32 px, ink3, hairline below. */
function ColumnHead({ ids, template, find, bars }: { ids: ColumnId[]; template: string; find: string; bars: Bars }) {
  const has = (id: ColumnId) => ids.includes(id)
  return (
    <div
      className="grid h-8 shrink-0 items-center border-b border-hair text-ink3"
      style={{ gridTemplateColumns: template, columnGap: GRID_GAP, padding: GRID_PAD }}
    >
      <span>When</span>
      {has('kind') ? <span /> : null}
      <span className="truncate">{listHead(find)}</span>
      {has('flags') ? <span /> : null}
      {has('bar') ? <span className="text-right">by {BAR_NAME[bars]}</span> : null}
      {has('cost') ? <span className="text-right">Cost</span> : null}
      {has('wall') ? <span className="text-right">Wall</span> : null}
      {has('waiting') ? <span className="text-right">Waiting</span> : null}
      {has('tokens') ? <span className="text-right">Tokens</span> : null}
      {has('go') ? <span /> : null}
    </div>
  )
}

/** SES-33 to SES-38: the column header and the scrolling list of turn rows, gap rows and harness folds. */
export function TurnList(p: TurnListProps) {
  const cols = useMemo(() => listColumns(p.width), [p.width])
  const scroller = useRef<HTMLDivElement>(null)
  const showKind = kindsVary(p.entries)
  const max = maxMeasure(p.entries, p.bars)
  const maxGapMs = maxGap(p.entries)
  const hasTurns = cursorStops(p.entries).length > 0

  // SES-38: the cursor row scrolls to about 30 % from the top when it leaves the view.
  useEffect(() => {
    const box = scroller.current
    const el = p.cursor === null ? null : findRow(box, p.cursor)
    if (!box || !el) return
    const boxTop = box.getBoundingClientRect().top
    const rowTop = el.getBoundingClientRect().top - boxTop + box.scrollTop
    const to = scrollTarget(rowTop, el.offsetHeight, box.scrollTop, box.clientHeight)
    if (to !== null) box.scrollTop = to
  }, [p.cursor])

  let body: React.ReactNode
  if (p.loading) {
    body = <SkeletonRows count={8} />
  } else if (p.find !== '' && !hasTurns) {
    body = <EmptyState icon="search-x">No turn has a step matching “{p.find}”.</EmptyState>
  } else if (p.range && !hasTurns) {
    body = (
      <EmptyState icon="clock" action={{ label: 'All turns', onClick: p.onClearRange }}>
        No turn lies in this range.
      </EmptyState>
    )
  } else {
    let prevStart: number | null = null
    const rows: React.ReactNode[] = []
    if (!p.hasNumbered) {
      rows.push(
        <div key="no-turns" className="flex items-center justify-center gap-2 px-6 py-8 text-ink2">
          <Icon name="info" size={24} tone="muted" />
          This session has no turns with a message.
        </div>,
      )
    }
    for (const e of p.entries) {
      const shared = { tz: p.tz, singleDay: p.singleDay, columns: cols }
      if (e.type === 'turn') {
        rows.push(
          <TurnRowView
            key={e.key} {...shared} entry={e} prevStartMs={prevStart} showKind={showKind} bars={p.bars} max={max}
            searchCount={p.find === '' ? null : matchCount(p.search, e.row.turn)} find={p.find}
            open={p.openKey === e.key} cursor={p.cursor === e.key} onOpen={() => p.onOpenTurn(e.key)}
          />,
        )
        prevStart = e.row.start_ms ?? prevStart // FALLBACK-OK: SES-85 (a turn with no start leaves the row above as the weekday reference)
      } else if (e.type === 'gap') {
        rows.push(<GapRowView key={e.key} {...shared} entry={e} prevStartMs={prevStart} maxMs={maxGapMs} onOpen={() => p.onOpenGap(e.key)} />)
        prevStart = e.gap.start_ms
      } else {
        rows.push(<HarnessFold key={e.key} rows={e.rows} tz={p.tz} columns={cols} />)
      }
    }
    body = rows
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {p.loading || hasTurns ? <ColumnHead ids={cols.ids} template={cols.template} find={p.find} bars={p.bars} /> : null}
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto" aria-label="Turns">
        {body}
        <div style={{ height: 160 }} />
      </div>
    </div>
  )
}
