import { localDay, weekdayShort } from './tz'

/** SYS-34: the weekday shows only when the row's local start day differs from the row above. */
export function weekdayLabel(startMs: number, prevStartMs: number | null, tz: string, singleDay: boolean): string {
  // FALLBACK-OK: SYS-34 (no weekday under a single-day period or on the same local day as the row above)
  if (singleDay) return ''
  if (prevStartMs !== null && localDay(prevStartMs, tz) === localDay(startMs, tz)) return ''
  return weekdayShort(startMs, tz)
}

export const ROW_H = 44
export const ROW_H_DENSE = 36

// SYS-33 slot widths
const WHEN_W = 88
const CONTEXT_W = 136
const FLAGS_W = 128
const PRIMARY_W = 184
const SECONDARY_W = [64, 64, 56] as const // Wall, Active, Tokens, in that order

export interface RowColumnsOpts {
  /** An extra context column after When (the explorer list inserts Project). */
  context?: boolean
  /** Width of the magnitude mark slot: 200 to 248. */
  magnitude?: number
  /** How many secondary figures (Wall, Active, Tokens) the row keeps; the rest drop from the right. */
  secondaries?: 0 | 1 | 2 | 3
}

/** SYS-33: the P1 grid. Slot order is fixed; columns that do not apply drop from the right end. */
export function rowColumns({ context = false, magnitude = 200, secondaries = 3 }: RowColumnsOpts = {}): string {
  if (magnitude < 200 || magnitude > 248) throw new Error(`rowColumns: magnitude ${magnitude} outside 200-248`)
  const cols = [`${WHEN_W}px`]
  if (context) cols.push(`${CONTEXT_W}px`)
  cols.push('minmax(0,1fr)', `${FLAGS_W}px`, `${magnitude}px`, `${PRIMARY_W}px`)
  for (let i = 0; i < secondaries; i++) cols.push(`${SECONDARY_W[i]}px`)
  return cols.join(' ')
}

/** SYS-37: a group header's totals sit in the same columns as its rows, from the magnitude slot on. The left block spans the slots before it. */
export function groupColumns({ magnitude = 200, secondaries = 3 }: Pick<RowColumnsOpts, 'magnitude' | 'secondaries'> = {}): string {
  const full = rowColumns({ magnitude, secondaries }).split(' ')
  const from = full.indexOf(`${magnitude}px`)
  return ['minmax(0,1fr)', ...full.slice(from)].join(' ')
}
