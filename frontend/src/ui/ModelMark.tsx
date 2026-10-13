import { modelShort } from '../lib/model'
import { markShape } from './model-mark.logic'
import { Tooltip } from './Tooltip'

/** SYS-41 (P4): a 10 px shape, a 4 px gap, then the short name. Compact is the shape alone, with the full id in the tooltip.
 *  `chart` colours the shape with the family's chart colour (SES-22) for legends; rows use one ink. */
export function ModelMark({ model, compact = false, chart = false }: { model: string; compact?: boolean; chart?: boolean }) {
  const s = markShape(model)
  const shape = (
    <svg
      viewBox="0 0 10 10"
      width={10}
      height={10}
      aria-hidden="true"
      className={chart ? undefined : 'text-ink2'}
      style={{ fill: chart ? `var(--chart-model-${s.family})` : 'currentColor' }}
    >
      <path d={s.d} />
    </svg>
  )
  const body = (
    <span className="inline-flex items-center gap-1 align-middle font-mono text-body font-normal text-ink2" aria-label={compact ? model : undefined}>
      {shape}
      {compact ? null : <span>{modelShort(model)}</span>}
    </span>
  )
  return compact ? <Tooltip content={model}>{body}</Tooltip> : body
}
