import type { Day } from '../api/types'

/** Calendar arithmetic on `Day` strings ('YYYY-MM-DD'). Zone-free: a Day is a label, not an instant. */

export const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'] as const
export const MONTH_NAMES = [
  'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november',
  'december',
] as const
/** Index 0 = Sunday, as Date.getUTCDay. */
export const WEEKDAY_NAMES = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'] as const

const MS_PER_DAY = 86_400_000
const DAY_RE = /^(\d{4})-(\d{2})-(\d{2})$/

const pad2 = (n: number) => String(n).padStart(2, '0')

export function makeDay(year: number, month: number, day: number): Day {
  return `${String(year).padStart(4, '0')}-${pad2(month)}-${pad2(day)}`
}

/** Day → integer days since 1970-01-01. Throws on a malformed or impossible date. */
export function dayNum(day: Day): number {
  const m = DAY_RE.exec(day)
  if (!m) throw new Error(`not a Day: ${day}`)
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const ms = Date.UTC(y, mo - 1, d)
  const back = new Date(ms)
  if (back.getUTCFullYear() !== y || back.getUTCMonth() !== mo - 1 || back.getUTCDate() !== d) {
    throw new Error(`not a calendar date: ${day}`)
  }
  return ms / MS_PER_DAY
}

export function isDay(text: string): boolean {
  try {
    dayNum(text)
    return true
  } catch {
    // FALLBACK-OK: explorer.md EXP-75 (text that is not a Day is an unknown value; the caller drops it)
    return false
  }
}

export function dayFromNum(n: number): Day {
  const d = new Date(n * MS_PER_DAY)
  return makeDay(d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate())
}

export function addDays(day: Day, n: number): Day {
  return dayFromNum(dayNum(day) + n)
}

/** b - a in days. */
export function diffDays(a: Day, b: Day): number {
  return dayNum(b) - dayNum(a)
}

/** 0 = Sunday … 6 = Saturday. */
export function weekdayOf(day: Day): number {
  return new Date(dayNum(day) * MS_PER_DAY).getUTCDay()
}

/** The Monday on or before `day`. */
export function mondayOf(day: Day): Day {
  return addDays(day, -((weekdayOf(day) + 6) % 7))
}
