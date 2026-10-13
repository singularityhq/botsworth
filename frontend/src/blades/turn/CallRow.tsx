import type { CallRow as CallWire } from '../../api/types'
import { Glyph, BarCell, CostCell, LedgerFrame, ModelFlag, TimeCell, TokensCell, When, type RowEnv } from './rowKit'
import { callName } from './rowName'
import type { RowNode } from './rowList.logic'

/** TRN-31: a model call. `arrow-left-right`, its name (with the "final" tag), the compact model mark, bar, Cost, model time, Tokens. */
export function CallRowView({ node, env, toolNames }: { node: RowNode; env: RowEnv; toolNames: readonly string[] }) {
  const r = node.row as CallWire
  const name = callName(r, toolNames)
  return (
    <LedgerFrame
      env={env}
      node={node}
      title={name.text}
      cells={{
        when: <When ms={r.start_ms} tz={env.tz} />,
        glyph: <Glyph name="arrow-left-right" />,
        name: (
          <>
            {name.final ? <FinalTag /> : null}
            <span className={`min-w-0 truncate group-data-[open=true]/row:text-struct ${name.muted ? 'text-ink2' : 'text-ink1'}`}>{name.text}</span>
          </>
        ),
        flags: <ModelFlag model={r.model} />,
        bar: <BarCell row={r} env={env} value={valueFor(r, env.measure)} />,
        cost: <CostCell value={r.cost_usd} lowerBound={r.cost_lower_bound} nUnpriced={r.n_unpriced} env={env} />,
        time: <TimeCell ms={r.dur_ms} env={env} prov={r.time_prov} />,
        tokens: <TokensCell value={r.tok.total} env={env} call />,
      }}
    />
  )
}

function valueFor(r: CallWire, measure: 'cost' | 'time' | 'tokens'): number | null {
  return measure === 'cost' ? r.cost_usd : measure === 'time' ? r.dur_ms : r.tok.total
}

/** TRN-31: the "final" tag, 12 px ink3 in a 1 px rule border, radius 4 (the D31 exception, D38 L3). */
export function FinalTag() {
  return <span className="shrink-0 rounded-chip border border-rule px-1 text-label text-ink3">final</span>
}
