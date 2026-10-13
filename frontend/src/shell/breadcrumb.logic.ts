export const CRUMB_MAX = 44

/** SYS-49: a crumb is cut at 44 characters, ellipsis included; the full text goes in `title`. */
export function truncateCrumb(text: string): string {
  return text.length <= CRUMB_MAX ? text : `${text.slice(0, CRUMB_MAX - 1)}…`
}

export interface CrumbSpec {
  key: string
  label: string
}

/** Crumbs left to right. Index 0 reads "Sessions"; a crumb's index is the blade level it closes to. */
export function crumbList(blades: readonly CrumbSpec[], labels: Readonly<Record<string, string>>): { key: string; text: string; current: boolean }[] {
  return blades.map((b, i) => ({
    key: b.key,
    // FALLBACK-OK: SES-7 (the crumb shows the strip's label until the blade registers its own)
    text: i === 0 ? 'Sessions' : (labels[b.key] ?? b.label),
    current: i === blades.length - 1,
  }))
}
