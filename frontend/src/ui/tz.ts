// Local-day maths in the zone it is given (QRY-4 [D50]: one zone, the browser's, from `state/zone.ts`). Used by the SYS-34 weekday rule
// and the SYS-40 midnight ticks. Pure; no Date-local calls, so the browser zone never leaks in.

const dayFmt = new Map<string, Intl.DateTimeFormat>()
function fmtFor(tz: string, key: string, opts: Intl.DateTimeFormatOptions, locale: string): Intl.DateTimeFormat {
  const k = `${tz}|${key}`
  let f = dayFmt.get(k)
  if (!f) {
    f = new Intl.DateTimeFormat(locale, { timeZone: tz, ...opts })
    dayFmt.set(k, f)
  }
  return f
}

/** 'YYYY-MM-DD' of an instant in the zone. */
export function localDay(ms: number, tz: string): string {
  return fmtFor(tz, 'day', { year: 'numeric', month: '2-digit', day: '2-digit' }, 'en-CA').format(ms)
}

/** 'Mon', 'Tue', ... of an instant in the zone. */
export function weekdayShort(ms: number, tz: string): string {
  return fmtFor(tz, 'wd', { weekday: 'short' }, 'en-US').format(ms)
}

/** Offset of the zone from UTC at an instant, in ms (positive east of UTC). */
function offsetMs(tz: string, utcMs: number): number {
  const parts = fmtFor(tz, 'parts', { hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric' }, 'en-US').formatToParts(utcMs)
  const get = (t: string) => Number(parts.find((p) => p.type === t)!.value)
  const asUtc = Date.UTC(get('year'), get('month') - 1, get('day'), get('hour'), get('minute'), get('second'))
  return asUtc - Math.floor(utcMs / 1000) * 1000
}

function nextDay(day: string): string {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10)
}

/** The instant a local day starts. */
export function localMidnight(day: string, tz: string): number {
  const [y, m, d] = day.split('-').map(Number)
  const guess = Date.UTC(y, m - 1, d)
  const t1 = guess - offsetMs(tz, guess)
  return guess - offsetMs(tz, t1)
}

const MAX_DAYS = 800

/** Local midnights strictly after `a` and strictly before `b`, in order. */
export function localMidnightsBetween(a: number, b: number, tz: string): number[] {
  const out: number[] = []
  if (!(b > a)) return out
  let day = nextDay(localDay(a, tz))
  for (let i = 0; i < MAX_DAYS; i++) {
    const m = localMidnight(day, tz)
    if (m >= b) return out
    if (m > a) out.push(m)
    day = nextDay(day)
  }
  throw new Error(`localMidnightsBetween: span of more than ${MAX_DAYS} days`)
}
