import { stripGeometry } from './activity.logic'

interface ActivityStripProps {
  /** Tokens per bin along first to last record (DM-44). */
  bins: readonly number[]
  /** The largest bin among all in-scope rows (D38 L7); the scale is passed in so rows share it. */
  maxBin: number
  wallMs: number
  firstMs: number
  lastMs: number
  tz: string
  /** 200 or 248 in the list, 88 in parent blades (SYS-40). */
  width: number
  height?: number
  label?: string
}

/** SYS-40 (P3): length from log10(wall), bars from log(1 + tokens), a baseline, and a tick at each local midnight. */
export function ActivityStrip({ bins, maxBin, wallMs, firstMs, lastMs, tz, width, height = 28, label }: ActivityStripProps) {
  const g = stripGeometry({ bins, maxBin, wallMs, firstMs, lastMs, tz, width, height })
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true} className="shrink-0">
      <rect x={0} y={height - 1} width={g.length} height={1} className="fill-rule" />
      {g.ticks.map((x, i) => (
        <rect key={i} x={x - 0.75} y={0} width={1.5} height={height} className="fill-ink3" />
      ))}
      {g.bars.map((b, i) => (
        <rect key={i} x={b.x} y={height - 1 - b.h} width={b.w} height={b.h} className="fill-ink2 group-hover/row:fill-ink1" />
      ))}
    </svg>
  )
}
