import { localMidnightsBetween } from './tz'

/** SYS-40: strip length is log-linear in wall time: 1 min and below = 16 px, 48 h and above = the full width. */
const MIN_LEN = 16
const MIN_WALL_MS = 60_000
const MAX_WALL_MS = 48 * 3600_000

export function stripLength(wallMs: number, full: number): number {
  if (wallMs <= MIN_WALL_MS) return Math.min(MIN_LEN, full)
  if (wallMs >= MAX_WALL_MS) return full
  const k = Math.log10(wallMs / MIN_WALL_MS) / Math.log10(MAX_WALL_MS / MIN_WALL_MS)
  return MIN_LEN + k * (full - MIN_LEN)
}

/** Bar height for one bin: log(1 + tokens) over log(1 + the largest bin among all in-scope rows); min 2 px if non-empty. */
export function binHeight(tokens: number, maxBin: number, maxPx: number): number {
  // FALLBACK-OK: SYS-40 (empty bins draw nothing)
  if (!(tokens > 0) || !(maxBin > 0)) return 0
  const h = (Math.log1p(tokens) / Math.log1p(maxBin)) * maxPx
  return Math.max(2, Math.min(h, maxPx))
}

export interface StripGeometry {
  length: number
  bars: { x: number; w: number; h: number }[]
  /** x of each local-midnight tick, proportional on the first-to-last span. */
  ticks: number[]
}

export function stripGeometry(o: {
  bins: readonly number[]
  maxBin: number
  wallMs: number
  firstMs: number
  lastMs: number
  tz: string
  width: number
  height: number
}): StripGeometry {
  const length = stripLength(o.wallMs, o.width)
  const n = o.bins.length
  const bars: StripGeometry['bars'] = []
  if (n > 0) {
    const bw = length / n
    // Room for the 1 px baseline under the bars.
    const maxPx = o.height - 2
    o.bins.forEach((b, i) => {
      const h = binHeight(b, o.maxBin, maxPx)
      if (h > 0) bars.push({ x: i * bw, w: Math.max(1, bw - 0.5), h })
    })
  }
  const span = o.lastMs - o.firstMs
  const ticks = span > 0 ? localMidnightsBetween(o.firstMs, o.lastMs, o.tz).map((t) => ((t - o.firstMs) / span) * length) : []
  return { length, bars, ticks }
}
