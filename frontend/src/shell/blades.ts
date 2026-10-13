import { openBlades, type BladeLevel, type UrlState } from '../state/url'

export type BladeKey = BladeLevel

/** The open blades, from the URL (F1a's `openBlades`): the explorer always; the session if `s`; the turn if `t`; the detail if `detail=1`. */
export function bladeKeys(url: UrlState): BladeKey[] {
  return openBlades(url)
}

/** SES-5: when the set of open blades changes (open or close), the newest takes focus. Otherwise focus stays. */
export function focusAfterChange(prev: readonly string[], keys: readonly string[], current: string): string {
  const same = prev.length === keys.length && prev.every((k, i) => k === keys[i])
  return same && keys.includes(current) ? current : keys[keys.length - 1]
}
