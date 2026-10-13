import type { AgentRow as AgentWire } from '../../api/types'
import { ModelFlag, BarCell, CostCell, Glyph, LedgerFrame, TimeCell, TokensCell, When, type RowEnv } from './rowKit'
import { agentName } from './rowName'
import { rowStart, type AgentRange, type RowNode } from './rowList.logic'

interface AgentRowProps {
  node: RowNode
  env: RowEnv
  open: boolean
  /** TRN-14: in the Cost and Duration orders the list holds main-thread rows only, so there is no "N rows" control. */
  expandable: boolean
  /** The in-range figures of this agent in a zoom (TRN-33). */
  range?: AgentRange
  onToggle: () => void
}

/**
 * TRN-33: a spawn or SendMessage that caused an engagement. `bot` in the teammate colour, the agent names, "started"/"messaged" and the
 * description, "N rows" (a button: `l` / `h`), the models, the whole cost of the engagement, its run time, its tokens. In a zoom, Cost and
 * Tokens cover only the calls that start inside the range and the sub-text adds "N of M calls".
 */
export function AgentRowView({ node, env, open, expandable, range, onToggle }: AgentRowProps) {
  const r = node.row as AgentWire
  const name = agentName(r, open, range)
  const cost = range ? range.cost_usd : r.cost_usd
  const tokens = range ? range.tok_total : r.tok_total
  const models = [...new Set(r.models)].slice(0, 3)
  return (
    <LedgerFrame
      env={env}
      node={node}
      title={`${name.text} ${name.sub}`}
      cells={{
        when: <When ms={rowStart(r)} tz={env.tz} />,
        glyph: <Glyph name="bot" tone="inherit" />,
        name: (
          <>
            <span className="min-w-0 shrink-[0.1] truncate text-ink1 group-data-[open=true]/row:text-struct">{name.text}</span>
            <span className="min-w-0 shrink-[4] truncate text-ink3">{name.sub}</span>
            {name.inRange ? <span className="shrink-0 text-ink3">{name.inRange}</span> : null}
            {expandable && (r.n_rows > 0 || open) ? (
              <button
                type="button"
                aria-expanded={open}
                title={open ? 'Fold its rows (h)' : 'Show its rows (l)'}
                onClick={(e) => {
                  e.stopPropagation()
                  onToggle()
                }}
                className="shrink-0 rounded-chip px-1 text-ink3 hover:text-struct"
              >
                {name.rows}
              </button>
            ) : null}
          </>
        ),
        flags: (
          <span className="inline-flex items-center gap-1.5">
            {models.map((m) => (
              <ModelFlag key={m} model={m} />
            ))}
          </span>
        ),
        bar: <BarCell row={r} env={env} value={measureValue(r, range, env.measure)} />,
        cost: <CostCell value={cost} lowerBound={r.cost_lower_bound} nUnpriced={r.n_unpriced} env={env} />,
        time: <TimeCell ms={r.run_ms} env={env} />,
        tokens: <TokensCell value={tokens} env={env} />,
      }}
    />
  )
}

function measureValue(r: AgentWire, range: AgentRange | undefined, measure: 'cost' | 'time' | 'tokens'): number | null {
  return measure === 'cost' ? (range ? range.cost_usd : r.cost_usd) : measure === 'time' ? r.run_ms : range ? range.tok_total : r.tok_total
}
