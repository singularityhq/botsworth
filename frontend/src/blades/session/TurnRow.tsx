import { barWidth } from '../../ui/bar.logic'
import { Clock, Duration, Money } from '../../ui/figures'
import { Icon } from '../../ui/Icon'
import { ModelMark } from '../../ui/ModelMark'
import { Row } from '../../ui/Row'
import { FigureCell } from '../../ui/Row'
import { Tooltip } from '../../ui/Tooltip'
import { weekdayLabel } from '../../ui/row.logic'
import type { Bars } from '../../state/url'
import { plural, kindOf } from '../shared/states'
import {
  GRID_GAP, GRID_PAD, measureValue, tokensText, turnFlags, waitingShown, type ColumnId, type Flag, type TurnEntry,
} from './turnList.logic'

export interface RowShared {
  tz: string
  singleDay: boolean
  columns: { ids: ColumnId[]; template: string }
}

/** SES-34 item 1 / SYS-34: the start clock; the weekday goes above it when the local day differs from the row above. */
export function WhenStack({ startMs, prevStartMs, tz, singleDay }: { startMs: number | null; prevStartMs: number | null; tz: string; singleDay: boolean }) {
  if (startMs === null) return <span />
  const wd = weekdayLabel(startMs, prevStartMs, tz, singleDay)
  return (
    <span className="flex flex-col leading-[18px]">
      {wd ? <span className="text-ink3">{wd}</span> : null}
      <Clock ms={startMs} tz={tz} />
    </span>
  )
}

/** SES-34 item 4: the flag slot. `compact` is the rail form (SES-4): the same flags, in a tighter run. */
export function Flags({ flags, find }: { flags: Flag[]; find: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-ink3">
      {flags.map((f, i) => {
        switch (f.type) {
          case 'model':
            return <ModelMark key={i} model={f.model} compact />
          case 'more':
            return (
              <span key={i} className="fig" title={`${plural(f.n, 'more model')}`}>
                +{f.n}
              </span>
            )
          case 'agents':
            return <Counted key={i} icon="bot" n={f.n} title={plural(f.n, 'agent')} />
          case 'skills':
            return <Counted key={i} icon="book-open" n={f.n} title={plural(f.n, 'skill load')} />
          case 'failed':
            return <Counted key={i} icon="circle-alert" n={f.n} title={`${f.n} failed`} error />
          case 'search':
            return <Counted key={i} icon="search" n={f.n} title={`rows matching “${find}”`} hit />
        }
      })}
    </span>
  )
}

function Counted({ icon, n, title, error = false, hit = false }: { icon: 'bot' | 'book-open' | 'circle-alert' | 'search'; n: number; title: string; error?: boolean; hit?: boolean }) {
  return (
    <Tooltip content={title}>
      <span className="inline-flex items-center gap-1">
        <Icon name={icon} tone={error ? 'error' : hit ? 'struct' : 'muted'} />
        <span className={`fig ${hit ? 'text-struct' : ''}`}>{n}</span>
      </span>
    </Tooltip>
  )
}

/** SES-34 item 5 (SYS-39): linear against the largest visible turn, right-anchored, 8 px; cost bars `--list-bar`, others ink2. */
export function MeasureBar({ value, max, bars, track = TRACK }: { value: number; max: number; bars: Bars; track?: number }) {
  const w = barWidth(value, max, track)
  return (
    <span aria-hidden="true" className="flex shrink-0 justify-end" style={{ width: track }}>
      {w > 0 ? <i className="block h-2 rounded-[2px]" style={{ width: w, background: bars === 'cost' ? 'var(--list-bar)' : 'var(--color-ink2)' }} /> : null}
    </span>
  )
}
const TRACK = 104
/** SYS-39: the bar's track is 64 px outside the explorer list; the rail's compact row (SYS-36) uses it. */
export const RAIL_TRACK = 64

interface TurnRowProps extends RowShared {
  entry: TurnEntry
  prevStartMs: number | null
  showKind: boolean
  bars: Bars
  max: number
  searchCount: number | null
  find: string
  open: boolean
  cursor: boolean
  onOpen: () => void
}

/** SES-34: one numbered turn. */
export function TurnRowView({ entry, prevStartMs, showKind, bars, max, searchCount, find, open, cursor, onOpen, tz, singleDay, columns }: TurnRowProps) {
  const r = entry.row
  const kind = kindOf(r.kind, r.origin, r.label)
  const has = (id: ColumnId) => columns.ids.includes(id)
  const wait = waitingShown(r.waiting_ms)
  return (
    <Row
      columns={columns.template}
      open={open}
      cursor={cursor}
      data-turn-key={entry.key}
      role="button"
      tabIndex={-1}
      aria-current={open || undefined}
      onClick={onOpen}
      style={{ columnGap: GRID_GAP, padding: GRID_PAD }}
    >
      <WhenStack startMs={r.start_ms} prevStartMs={prevStartMs} tz={tz} singleDay={singleDay} />
      {has('kind') ? <span className="flex">{showKind ? <Icon name={kind.icon} tone="quiet" label={kind.label} /> : null}</span> : null}
      <span className="flex min-w-0 items-baseline gap-2">
        <span className="fig min-w-5 shrink-0 text-right text-ink3">{entry.ix}</span>
        <span
          title={r.label}
          className={`truncate group-data-[open=true]/row:text-struct ${kind.lead ? 'font-medium text-ink1' : 'font-normal text-ink2'}`}
        >
          {r.label}
        </span>
      </span>
      {has('flags') ? <Flags flags={turnFlags(r, searchCount)} find={find} /> : null}
      {has('bar') ? <MeasureBar value={measureValue(r, bars)} max={max} bars={bars} /> : null}
      {has('cost') ? (
        <FigureCell>
          <Money value={r.cost_usd} lowerBound={r.cost_lower_bound} nUnpriced={r.n_unpriced} weight={400} />
        </FigureCell>
      ) : null}
      {has('wall') ? (
        <FigureCell>
          <Duration ms={r.wall_ms} />
        </FigureCell>
      ) : null}
      {has('waiting') ? (
        <FigureCell>
          {wait !== null ? (
            <Tooltip content="Inside the turn, nothing ran on the main thread for this long">
              <span>
                <Duration ms={wait} ink="ink3" />
              </span>
            </Tooltip>
          ) : null}
        </FigureCell>
      ) : null}
      {has('tokens') ? <FigureCell className="fig text-ink3">{tokensText(r.tok_total)}</FigureCell> : null}
      {has('go') ? <Icon name="chevron-right" /> : null}
    </Row>
  )
}

