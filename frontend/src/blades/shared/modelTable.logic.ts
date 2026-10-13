import type { ModelLine, Provider } from '../../api/types'
import { PROVIDER_NAME } from '../../ui/provider'

/** First-token time: milliseconds below a second, seconds with one decimal above. */
export function ttftText(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`
}

/** Speed in tok/s, blank when there is no model time (DM-47) or no figure. */
export function speedText(m: Pick<ModelLine, 'speed_tok_s' | 'model_ms'>): string {
  return m.speed_tok_s === null || m.model_ms === 0 ? '' : `${Math.round(m.speed_tok_s)} tok/s`
}

/** SES-28: models by cost, highest first. */
export function modelsByCost(models: readonly ModelLine[]): ModelLine[] {
  // FALLBACK-OK: SES-28 (a model with no cost sorts after every priced one)
  return [...models].sort((a, b) => (b.cost_usd ?? -1) - (a.cost_usd ?? -1))
}

/** SES-28 note: shown when any listed model's model time comes from log record times. */
export function derivedNote(models: readonly ModelLine[], provider: Provider): string | null {
  return models.some((m) => m.speed_basis === 'derived')
    ? `Model time for ${PROVIDER_NAME[provider]} is taken from log record times, so speed is approximate.`
    : null
}
