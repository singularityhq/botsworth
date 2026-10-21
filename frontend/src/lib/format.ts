import type { Day } from '../api/types'
import { dayNum, MONTHS, WEEKDAY_NAMES, weekdayOf } from './day'
import { modelFamily, modelShort } from './model'

/**
 * The SYS-29 formats, one function per fact. A formatter prints the value it
 * is given: a zero prints as zero. SYS-31 (a zero by construction is blank)
 * is the caller's rule, because only the caller knows why the value is zero.
 * A null value prints as the empty string ("not in data", SYS-31).
 */

export { modelFamily, modelShort }

function assertFinite(v: number, what: string): void {
  if (!Number.isFinite(v)) throw new Error(`fmt.${what}: not a finite number: ${v}`)
}

const usd = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

/** `$2,123.30`: exactly 2 decimals at every magnitude. `lowerBound` appends `+` (SYS-32). */
function moneyParts(v: number | null | undefined, opts: { lowerBound?: boolean } = {}): { text: string; plus: boolean } {
  if (v === null || v === undefined) return { text: '', plus: false } // FALLBACK-OK: system.md SYS-31 (null cost = no model call: not in data)
  assertFinite(v, 'money')
  const digits = usd.format(Math.abs(v))
  const negative = v < 0 && digits !== '0.00'
  return { text: `${negative ? '-' : ''}$${digits}`, plus: opts.lowerBound === true }
}

function money(v: number | null | undefined, opts: { lowerBound?: boolean } = {}): string {
  const p = moneyParts(v, opts)
  return p.text === '' ? '' : p.text + (p.plus ? '+' : '')
}

const TOKEN_UNITS = [
  [1e3, 'k'],
  [1e6, 'M'],
  [1e9, 'B'],
] as const

/** Scaled value printed with 0 decimals from 100, 1 from 10, else 2; trailing zeros stripped. */
function scaled(x: number): string {
  const s = x >= 100 ? x.toFixed(0) : x >= 10 ? x.toFixed(1) : x.toFixed(2)
  return s.includes('.') ? s.replace(/\.?0+$/, '') : s
}

/** `290`, `97k`, `12.4M`, `8.69B`. A value that rounds up to 1000 of a unit moves to the next unit. */
function tokens(v: number | null | undefined): string {
  if (v === null || v === undefined) return '' // FALLBACK-OK: system.md SYS-31 (null tokens = no model call: not in data)
  assertFinite(v, 'tokens')
  if (v < 0) return `-${tokens(-v)}`
  if (Math.round(v) < 1000) return String(Math.round(v))
  let i = v >= 1e9 ? 2 : v >= 1e6 ? 1 : 0
  let text = scaled(v / TOKEN_UNITS[i][0])
  if (Number(text) >= 1000 && i < TOKEN_UNITS.length - 1) {
    i += 1
    text = scaled(v / TOKEN_UNITS[i][0])
  }
  return text + TOKEN_UNITS[i][1]
}

const pad2 = (n: number) => String(n).padStart(2, '0')

/** `<1s`, `45s`, `13m`, `1h05m`, `2d03h`. */
function dur(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '' // FALLBACK-OK: system.md SYS-31 (null duration = not in data)
  assertFinite(ms, 'dur')
  if (ms < 0) return `-${dur(-ms)}`
  const s = Math.round(ms / 1000)
  if (s < 1) return '<1s'
  if (s < 60) return `${s}s`
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h}h${pad2(m % 60)}m`
  return `${Math.floor(h / 24)}d${pad2(h % 24)}h`
}

interface ZoneParts {
  day: Day
  hour: number
  minute: number
}

const zoneFormats = new Map<string, Intl.DateTimeFormat>()

/** Local calendar day and clock time of `ms` in `tz`. */
function zoneParts(ms: number, tz: string): ZoneParts {
  let f = zoneFormats.get(tz)
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', {
      timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    })
    zoneFormats.set(tz, f)
  }
  const parts = f.formatToParts(new Date(ms))
  const get = (t: string): string => {
    const v = parts.find((p) => p.type === t)?.value
    if (v === undefined) throw new Error(`no ${t} in the formatted date for zone ${tz}`)
    return v
  }
  return {
    day: `${get('year')}-${get('month')}-${get('day')}`,
    hour: Number(get('hour')),
    minute: Number(get('minute')),
  }
}

/** `13:44`, 24 h, in `tz`. */
function clock(ms: number | null | undefined, tz: string): string {
  if (ms === null || ms === undefined) return '' // FALLBACK-OK: system.md SYS-31 (null time = not in data)
  assertFinite(ms, 'clock')
  const p = zoneParts(ms, tz)
  return `${pad2(p.hour)}:${pad2(p.minute)}`
}

/** `21:43–11:41 +2d`; the `+Nd` counts the local midnights between start and end. */
function range(start: number | null | undefined, end: number | null | undefined, tz: string): string {
  if (start === null || start === undefined || end === null || end === undefined) return '' // FALLBACK-OK: system.md SYS-31 (a session or turn with no start or end time: not in data)
  assertFinite(start, 'range')
  assertFinite(end, 'range')
  const a = zoneParts(start, tz)
  const b = zoneParts(end, tz)
  const days = dayNum(b.day) - dayNum(a.day)
  return `${pad2(a.hour)}:${pad2(a.minute)}–${pad2(b.hour)}:${pad2(b.minute)}${days > 0 ? ` +${days}d` : ''}`
}

const cap = (s: string) => s[0].toUpperCase() + s.slice(1)

/** `Oct 6`. */
function monthDay(day: Day): string {
  return `${cap(MONTHS[Number(day.slice(5, 7)) - 1])} ${Number(day.slice(8, 10))}`
}

/** `Mon Oct 12` for a Day, or for an epoch ms read in `tz`. */
function date(when: Day | number, tz?: string): string {
  let day: Day
  if (typeof when === 'number') {
    if (!tz) throw new Error('fmt.date: an epoch ms needs a time zone')
    day = zoneParts(when, tz).day
  } else {
    day = when
  }
  return `${cap(WEEKDAY_NAMES[weekdayOf(day)].slice(0, 3))} ${monthDay(day)}`
}

/** `Oct 6 – Oct 12`; a one-day range prints as `Oct 6`. */
function dateRange(from: Day, to: Day): string {
  return from === to ? monthDay(from) : `${monthDay(from)} – ${monthDay(to)}`
}

/** Share of a whole as `82%`, or `<1%` below 1 %. `share` is a fraction (0.82). */
function pct(share: number | null | undefined): string {
  if (share === null || share === undefined) return '' // FALLBACK-OK: system.md SYS-31 (no whole to take a share of: not in data)
  assertFinite(share, 'pct')
  if (share < 0) return `-${pct(-share)}`
  if (share === 0) return '0%'
  const n = Math.round(share * 100)
  return n < 1 ? '<1%' : `${n}%`
}

/** SYS number format for counts: en-US thousands separators (1,244). */
function count(n: number): string {
  assertFinite(n, 'count')
  return n.toLocaleString('en-US')
}

export const fmt = { count, money, moneyParts, tokens, dur, clock, range, date, dateRange, pct }
