// SYS-10, SYS-11: a project's colour slot (1 to 6, from the backend) becomes a token; no slot means "other".
export type Slot = 1 | 2 | 3 | 4 | 5 | 6 | null

export function seriesVar(slot: Slot): string {
  return slot === null ? 'var(--color-series-other)' : `var(--color-series-${slot})`
}
