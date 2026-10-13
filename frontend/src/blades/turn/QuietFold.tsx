import { fmt } from '../../lib/format'
import { Duration, Money } from '../../ui/figures'
import { Icon } from '../../ui/Icon'
import type { Item } from './rowList.logic'
import { ROW_H, type LedgerGrid } from './turn.logic'

type Fold = Extract<Item, { type: 'fold' }>

/**
 * TRN-37: a run of 4 or more quiet rows. `list-collapse`, "Show N quiet rows" in struct with "C calls · T tools · HH:MM–HH:MM" in ink3,
 * the run's cost, and the time from its first start to its last. A click (or Enter on it) unfolds that run.
 */
export function QuietFold({ item, tz, grid, onUnfold }: { item: Fold; tz: string; grid: LedgerGrid; onUnfold: () => void }) {
  const n = item.nodes.length
  const from = item.firstMs === null ? '' : fmt.clock(item.firstMs, tz)
  const to = item.lastMs === null ? '' : fmt.clock(item.lastMs, tz)
  const cell: Record<string, React.ReactNode> = {
    when: <span className="fig text-ink3">{from}</span>,
    glyph: <Icon name="list-collapse" tone="struct" />,
    name: (
      <span className="flex min-w-0 items-baseline gap-2 whitespace-nowrap">
        <span>Show {n} quiet rows</span>
        <span className="min-w-0 truncate text-ink3">
          {item.calls} {item.calls === 1 ? 'call' : 'calls'} · {item.tools} {item.tools === 1 ? 'tool' : 'tools'} · {from}–{to}
        </span>
      </span>
    ),
    cost: <span className="flex justify-end"><Money value={item.cost} weight={400} /></span>,
    time: <span className="flex justify-end">{item.firstMs !== null && item.lastMs !== null ? <Duration ms={item.lastMs - item.firstMs} /> : null}</span>,
  }
  return (
    <button
      type="button"
      data-fold-key={item.key}
      onClick={onUnfold}
      className="grid w-full items-center border-b border-rule-soft text-body text-struct transition-colors duration-150 hover:bg-l3"
      style={{ height: ROW_H, gridTemplateColumns: grid.template, columnGap: 12, padding: '0 20px' }}
    >
      {grid.ids.map((id) => (
        <span key={id} className="min-w-0">{cell[id] ?? /* FALLBACK-OK: TRN-11 (a column the fold does not use is blank) */ null}</span>
      ))}
    </button>
  )
}
