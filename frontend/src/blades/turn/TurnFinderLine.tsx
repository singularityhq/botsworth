import type { TurnOrder } from '../../state/url'
import { Segmented } from '../../ui/Segmented'
import { Tooltip } from '../../ui/Tooltip'
import { FinderChips } from '../shared/FinderChips'
import type { FinderKind } from '../shared/finders'
import type { FinderSet } from '../shared/useFinders'

const ORDERS = [
  { value: 'order', label: 'As it ran' },
  { value: 'cost', label: 'Cost' },
  { value: 'duration', label: 'Duration' },
] as const

/** TRN-42: "Fold gaps" is disabled while zoomed (the axis is the zoom range, in real time). */
function AxisSegment({ real, zoomed, onChange }: { real: boolean; zoomed: boolean; onChange: (real: boolean) => void }) {
  if (!zoomed) {
    return (
      <Segmented
        label="Axis"
        value={real ? 'real' : 'fold'}
        onChange={(v) => onChange(v === 'real')}
        options={[{ value: 'fold', label: 'Fold gaps' }, { value: 'real', label: 'Real time' }]}
      />
    )
  }
  return (
    <div role="group" aria-label="Axis" className="inline-flex h-8 overflow-hidden rounded-control border border-edge">
      <Tooltip content="Zoomed: real time">
        <button type="button" disabled aria-pressed={false} className="cursor-default px-3 text-body text-edge">
          Fold gaps
        </button>
      </Tooltip>
      <button type="button" aria-pressed className="border-l border-edge bg-l3 px-3 text-body font-medium text-ink1">
        Real time
      </button>
    </div>
  )
}

export interface TurnFinderLineProps {
  waterfall: boolean
  real: boolean
  zoomed: boolean
  order: TurnOrder | null
  turn: string
  finders: Record<FinderKind, FinderSet>
  onView: (waterfall: boolean) => void
  onOrder: (order: TurnOrder | null) => void
  onReal: (real: boolean) => void
  onFind: (kind: FinderKind) => void
}

/** TRN-10: the view segment, then the order (Ledger) or the axis (Waterfall), a spacer, and this turn's finder chips. 8 px gaps. */
export function TurnFinderLine(p: TurnFinderLineProps) {
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-y border-hair px-6 py-2">
      <span title="Ledger or Waterfall (v)">
        <Segmented
          label="View"
          value={p.waterfall ? 'waterfall' : 'ledger'}
          onChange={(v) => p.onView(v === 'waterfall')}
          options={[{ value: 'ledger', label: 'Ledger' }, { value: 'waterfall', label: 'Waterfall' }]}
        />
      </span>
      {p.waterfall ? (
        <AxisSegment real={p.real} zoomed={p.zoomed} onChange={p.onReal} />
      ) : (
        <>
          <span className="text-ink3">Order</span>
          <Segmented label="Order" value={p.order ?? /* FALLBACK-OK: TRN-10 (no order set is "As it ran") */ 'order'} onChange={(v) => p.onOrder(v === 'order' ? null : v)} options={ORDERS} />
        </>
      )}
      <span className="flex-1" />
      <FinderChips sets={p.finders} turn={p.turn} onFind={p.onFind} />
    </div>
  )
}
