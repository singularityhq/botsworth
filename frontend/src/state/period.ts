import type { Day } from '../api/types'
import {
  MONTH_NAMES, WEEKDAY_NAMES, addDays, dayNum, diffDays, isDay, makeDay, mondayOf, weekdayOf,
} from '../lib/day'

/**
 * Period resolution in the browser time zone (QRY-4, FE-9), the chart window
 * (EXP-32) and the date parser shared by the period fields and the palette
 * (EXP-15, EXP-69 item 2, FE-10).
 */

export type Preset = 'today' | 'yesterday' | 'last7' | 'thisweek' | 'lastweek' | 'all'
export type Period = { kind: 'preset'; preset: Preset } | { kind: 'custom'; from: Day; to: Day }

export const PRESETS: readonly { id: Preset; label: string }[] = [
  { id: 'today', label: 'Today' },
  { id: 'yesterday', label: 'Yesterday' },
  { id: 'last7', label: 'Last 7 days' },
  { id: 'thisweek', label: 'This week' },
  { id: 'lastweek', label: 'Last week' },
  { id: 'all', label: 'All time' },
]

export const DEFAULT_PERIOD: Period = { kind: 'preset', preset: 'last7' }

/** `from` and `to` are both null for all time (QRY-3). */
export interface DayRange {
  from: Day | null
  to: Day | null
}

/** Today's local date in `tz` (an IANA zone; the browser's, `state/zone.ts`). */
export function todayIn(tz: string, nowMs: number): Day {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date(nowMs))
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value)
  const [y, m, d] = [get('year'), get('month'), get('day')]
  if (!(y > 0 && m > 0 && d > 0)) throw new Error(`todayIn: could not read a date for zone ${tz}`)
  return makeDay(y, m, d)
}

/** Preset or custom period → days (EXP-13, EXP-14). Weeks run Monday to Sunday. */
export function resolvePeriod(p: Period, today: Day): DayRange {
  if (p.kind === 'custom') return { from: p.from, to: p.to }
  switch (p.preset) {
    case 'today':
      return { from: today, to: today }
    case 'yesterday': {
      const y = addDays(today, -1)
      return { from: y, to: y }
    }
    case 'last7':
      return { from: addDays(today, -6), to: today }
    case 'thisweek':
      return { from: mondayOf(today), to: today }
    case 'lastweek': {
      const mon = addDays(mondayOf(today), -7)
      return { from: mon, to: addDays(mon, 6) }
    }
    case 'all':
      return { from: null, to: null }
  }
}

// ---- chart window (EXP-32, D41 M10) -------------------------------------

export interface ChartWindow {
  from: Day
  to: Day
  bucket: 'day' | 'week'
}

const WINDOW_DAYS = 21
const RECENT_DAYS = 14
const MAX_DAY_COLUMNS = 92

/**
 * The days the explorer chart shows.
 * - Period of 21 days or fewer: 21 columns. They end at the period end, or at
 *   today when the period ended within the last 14 days (today and the 13
 *   days before). The window always contains the whole period.
 * - Period of 22 to 92 days: the period itself, one column per day.
 * - Longer: the period itself, one column per week.
 * `extent` is the data extent (`PeriodSummary.first_day`/`last_day`); all
 * time needs it because it has no days of its own.
 */
export function chartWindow(
  period: Period, today: Day, extent?: { first_day: Day | null; last_day: Day | null },
): ChartWindow {
  const r = resolvePeriod(period, today)
  let from = r.from
  let to = r.to
  if (from === null || to === null) {
    if (!extent || extent.first_day === null || extent.last_day === null) {
      throw new Error('chartWindow: an all-time period needs the data extent (first_day, last_day)')
    }
    from = extent.first_day
    to = extent.last_day
  }
  const len = diffDays(from, to) + 1
  if (len > MAX_DAY_COLUMNS) return { from, to, bucket: 'week' }
  if (len > WINDOW_DAYS) return { from, to, bucket: 'day' }
  const ago = diffDays(to, today)
  const end = ago >= 0 && ago < RECENT_DAYS ? today : to
  const last = addDays(from, WINDOW_DAYS - 1) // the latest end that still shows `from`
  const windowEnd = dayNum(end) < dayNum(last) ? end : last
  return { from: addDays(windowEnd, -(WINDOW_DAYS - 1)), to: windowEnd, bucket: 'day' }
}

// ---- date parsing (EXP-15, EXP-69 item 2) -------------------------------

const ISO_RE = /^\d{4}-\d{2}-\d{2}$/
const NAME_DAY_RE = /^([a-z]{3,9})\.?\s*(\d{1,2})$/ // oct 6
const DAY_NAME_RE = /^(\d{1,2})\s*([a-z]{3,9})$/ // 6 oct
const NUM_DAY_RE = /^(\d{1,2})[-/](\d{1,2})$/ // 10-06, 10/06 (month-day)

/** Month index 0–11 when `word` (3+ letters) is a prefix of a month name. */
function monthOf(word: string): number {
  if (word.length < 3) return -1
  return MONTH_NAMES.findIndex((m) => m.startsWith(word))
}

/** Weekday index 0–6 when `word` (3+ letters) is a prefix of a weekday name. */
function weekdayIndex(word: string): number {
  if (word.length < 3) return -1
  return WEEKDAY_NAMES.findIndex((w) => w.startsWith(word))
}

/** The latest valid month/day on or before `today`, looking back at most 8 years. */
function latestMonthDay(month: number, day: number, today: Day): Day | null {
  const year = Number(today.slice(0, 4))
  for (let y = year; y >= year - 8; y--) {
    const d = makeDay(y, month, day)
    if (isDay(d) && dayNum(d) <= dayNum(today)) return d
  }
  return null
}

/** The latest date with weekday `wd` (0 = Sunday) on or before `today`; today counts. */
function latestWeekday(wd: number, today: Day): Day {
  return addDays(today, -((weekdayOf(today) - wd + 7) % 7))
}

function normalise(text: string): string {
  return text.trim().toLowerCase().replace(/\s+/g, ' ')
}

/**
 * One date typed into a period field (EXP-15): `oct 6`, `6 oct`, `10-06`,
 * `10/06`, `2026-10-06`, `today`, `yesterday`, or a weekday name. A date
 * without a year, and a weekday, mean the latest such day up to today. Null
 * when the text is not one of these.
 */
export function parseDay(text: string, today: Day): Day | null {
  const q = normalise(text)
  if (q === '') return null
  if (q === 'today') return today
  if (q === 'yesterday') return addDays(today, -1)
  if (ISO_RE.test(q)) return isDay(q) ? q : null
  let m = NAME_DAY_RE.exec(q)
  if (m) {
    const mi = monthOf(m[1])
    return mi >= 0 ? latestMonthDay(mi + 1, Number(m[2]), today) : null
  }
  m = DAY_NAME_RE.exec(q)
  if (m) {
    const mi = monthOf(m[2])
    return mi >= 0 ? latestMonthDay(mi + 1, Number(m[1]), today) : null
  }
  m = NUM_DAY_RE.exec(q)
  if (m) return latestMonthDay(Number(m[1]), Number(m[2]), today)
  const wd = weekdayIndex(q)
  if (wd >= 0) return latestWeekday(wd, today)
  return null
}

/**
 * Apply an edit of one field of the custom range (EXP-15): when From ends up
 * later than To, or To earlier than From, the other field takes the same day.
 */
export function editRange(range: { from: Day; to: Day }, field: 'from' | 'to', day: Day): { from: Day; to: Day } {
  if (field === 'from') return { from: day, to: dayNum(range.to) < dayNum(day) ? day : range.to }
  return { from: dayNum(range.from) > dayNum(day) ? day : range.from, to: day }
}

export interface DateRow {
  kind: 'day' | 'range'
  from: Day | null
  to: Day | null
  /** The preset a range row stands for. */
  preset: Preset | null
}

const RANGE_ALIASES: readonly { preset: Preset; exact: readonly string[]; words: string }[] = [
  { preset: 'thisweek', exact: [], words: 'this week' },
  { preset: 'lastweek', exact: [], words: 'last week' },
  { preset: 'last7', exact: ['7d', 'last 7', 'last7', '7 days'], words: 'last 7 days' },
  { preset: 'all', exact: [], words: 'all' },
]

/**
 * The rows of the palette's Dates section for typed input (EXP-69 item 2,
 * FE-10): one day (any parseDay form; `today` and `yesterday` also by prefix
 * of 2+ letters) and the ranges `this week`, `last week`, `last 7`/`7d`, `all`.
 * The caller labels and orders them.
 */
export function parseDateRows(text: string, today: Day): DateRow[] {
  const q = normalise(text)
  if (q === '') return []
  const rows: DateRow[] = []
  let day = parseDay(q, today)
  if (day === null && q.length >= 2 && 'today'.startsWith(q)) day = today
  if (day === null && q.length >= 2 && 'yesterday'.startsWith(q)) day = addDays(today, -1)
  if (day !== null) rows.push({ kind: 'day', from: day, to: day, preset: null })
  for (const r of RANGE_ALIASES) {
    if (r.exact.includes(q) || (q.length >= 3 && r.words.startsWith(q))) {
      const range = resolvePeriod({ kind: 'preset', preset: r.preset }, today)
      rows.push({ kind: 'range', from: range.from, to: range.to, preset: r.preset })
    }
  }
  return rows
}
