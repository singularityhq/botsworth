import { useState } from 'react'
import type { TurnRow } from '../../api/types'
import { Clock, Money } from '../../ui/figures'
import { Icon } from '../../ui/Icon'
import { foldCost, foldLabels, foldTitle, GRID_GAP, GRID_PAD, type ColumnId } from './turnList.logic'

interface HarnessFoldProps {
  rows: TurnRow[]
  tz: string
  columns: { ids: ColumnId[]; template: string }
}

const base = 'grid h-8 items-center border-b border-rule-soft text-ink3'

/**
 * SES-35 (D34): consecutive harness turns in one 32 px row, in their time position. A click expands it in place into one
 * 32 px row per harness turn; a second click folds it. Harness rows have no number, never open, and are not cursor stops.
 */
export function HarnessFold({ rows, tz, columns }: HarnessFoldProps) {
  const [open, setOpen] = useState(false)
  const cost = foldCost(rows)
  const has = (id: ColumnId) => columns.ids.includes(id)
  const style = { gridTemplateColumns: columns.template, columnGap: GRID_GAP, padding: GRID_PAD }
  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={`${base} w-full cursor-pointer transition-colors duration-150 hover:bg-l3`}
        style={style}
      >
        <span />
        {has('kind') ? <Icon name="list-collapse" /> : null}
        <span className="flex min-w-0 gap-2" style={has('flags') ? { gridColumn: 'span 2' } : undefined}>
          <span className="shrink-0">{foldTitle(rows)}</span>
          <span className="truncate" title={foldLabels(rows)}>
            {foldLabels(rows)}
          </span>
        </span>
        {has('bar') ? <span /> : null}
        {has('cost') ? <span className="flex justify-end">{cost !== null ? <Money value={cost} weight={400} /> : null}</span> : null}
      </button>
      {open
        ? rows.map((r, i) => {
            const rc = foldCost([r])
            return (
              <div key={`${r.turn}-${i}`} className={base} style={style}>
                {r.start_ms !== null ? <Clock ms={r.start_ms} tz={tz} /> : <span />}
                {has('kind') ? <Icon name="list-collapse" /> : null}
                <span className="truncate" title={r.label} style={has('flags') ? { gridColumn: 'span 2' } : undefined}>
                  {r.label}
                </span>
                {has('bar') ? <span /> : null}
                {has('cost') ? <span className="flex justify-end">{rc !== null ? <Money value={rc} weight={400} /> : null}</span> : null}
              </div>
            )
          })
        : null}
    </>
  )
}
