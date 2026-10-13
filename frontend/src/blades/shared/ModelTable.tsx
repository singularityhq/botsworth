import type { ModelLine, Provider } from '../../api/types'
import { Duration, Money } from '../../ui/figures'
import { Icon } from '../../ui/Icon'
import { ModelMark } from '../../ui/ModelMark'
import { derivedNote, modelsByCost, speedText, ttftText } from './modelTable.logic'

/** SES-28 (DM-47): the model table and, below it, the one info line. */
export function ModelTable({ models, provider }: { models: readonly ModelLine[]; provider: Provider }) {
  const rows = modelsByCost(models)
  const withTtft = rows.some((m) => m.ttft_ms_mean !== null)
  const note = derivedNote(rows, provider)
  const th = 'pb-1 pr-6 text-left font-normal text-ink3'
  const td = 'py-0.5 pr-6 text-ink2'
  return (
    <div className="flex flex-col gap-3">
      <table className="w-max border-collapse">
        <thead>
          <tr>
            <th className={th}>Model</th>
            <th className={th}>Calls</th>
            <th className={th}>Cost</th>
            <th className={th}>Model time</th>
            <th className={th}>Speed</th>
            {withTtft ? <th className={th}>First token</th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.model}>
              <td className={td}>
                <ModelMark model={m.model} />
              </td>
              <td className={`${td} fig`}>{m.n_calls}</td>
              <td className={td}>
                <Money value={m.cost_usd} lowerBound={m.cost_lower_bound} nUnpriced={m.n_unpriced} weight={400} />
              </td>
              <td className={td}>
                <Duration ms={m.model_ms} />
              </td>
              <td className={`${td} fig`}>{speedText(m)}</td>
              {withTtft ? <td className={`${td} fig`}>{m.ttft_ms_mean === null ? '' : ttftText(m.ttft_ms_mean)}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
      {note ? (
        <p className="flex items-center gap-1.5 text-ink3">
          <Icon name="info" />
          {note}
        </p>
      ) : null}
    </div>
  )
}
