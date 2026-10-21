import { Duration } from '../../ui/figures'
import { Icon } from '../../ui/Icon'
import { FigureCell, Row } from '../../ui/Row'
import { fmt } from '../../lib/format'
import { gapFill } from '../shared/causes'
import { GAP_GLYPH, GAP_LABEL, gapKind } from '../shared/states'
import { gapBarPct, GRID_GAP, GRID_PAD, type ColumnId, type ListEntry } from './turnList.logic'
import { WhenStack, type RowShared } from './TurnRow'

type GapEntry = Extract<ListEntry, { type: 'gap' }>

interface GapRowProps extends RowShared {
  entry: GapEntry
  prevStartMs: number | null
  maxMs: number
  onOpen: () => void
}

/** SES-36: a wait of 2 min or more between turns. 44 px, l2 fill; glyph and label by kind; a hatched bar against the longest gap; the duration again in the Wall column. */
export function GapRowView({ entry, prevStartMs, maxMs, onOpen, tz, singleDay, columns }: GapRowProps) {
  const g = entry.gap
  const has = (id: ColumnId) => columns.ids.includes(id)
  const text = `${fmt.dur(g.dur_ms)} ${GAP_LABEL[gapKind(g)]}, ${entry.reason}`
  return (
    <Row
      columns={columns.template}
      data-gap-key={entry.key}
      role="button"
      tabIndex={-1}
      title={text}
      className="bg-l2"
      onClick={onOpen}
      style={{ columnGap: GRID_GAP, padding: GRID_PAD }}
    >
      <WhenStack startMs={g.start_ms} prevStartMs={prevStartMs} tz={tz} singleDay={singleDay} />
      {has('kind') ? <Icon name={GAP_GLYPH[gapKind(g)]} tone="quiet" /> : null}
      {/* The flags slot is empty on a gap row, so the name runs across it (more room for the reason). */}
      <span className="flex min-w-0 items-baseline gap-2 whitespace-nowrap" style={has('flags') ? { gridColumn: 'span 2' } : undefined}>
        <b className="fig font-medium text-ink1">{fmt.dur(g.dur_ms)}</b>
        <span className="shrink-0 text-ink2">{GAP_LABEL[gapKind(g)]}</span>
        <span className="truncate text-ink3">{entry.reason}</span>
      </span>
      {has('bar') ? (
        <span aria-hidden="true" className="flex justify-end" style={{ width: 104 }}>
          <i className="block h-3 rounded-[2px]" style={{ width: `${gapBarPct(g.dur_ms, maxMs)}%`, ...gapFill(g) }} />
        </span>
      ) : null}
      {has('cost') ? <span /> : null}
      {has('wall') ? (
        <FigureCell>
          <Duration ms={g.dur_ms} />
        </FigureCell>
      ) : null}
      {has('waiting') ? <span /> : null}
      {has('tokens') ? <span /> : null}
      {has('go') ? <span /> : null}
    </Row>
  )
}
