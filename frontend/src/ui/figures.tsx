import { fmt } from '../lib/format'
import { Skeleton } from './Skeleton'
import { Tooltip } from './Tooltip'

/** SYS-29, SYS-31, SYS-32. A null value is "zero by construction": the cell is blank. */
export function Money({
  value, lowerBound = false, nUnpriced = 0, weight = 500, loading = false, className = '',
}: { value: number | null; lowerBound?: boolean; nUnpriced?: number; weight?: 400 | 500 | 600; loading?: boolean; className?: string }) {
  if (loading) return <Skeleton width={56} className={className} />
  // FALLBACK-OK: SYS-31 (zero by construction: the cell is blank)
  if (value === null) return null
  const w = weight === 600 ? 'font-semibold' : weight === 500 ? 'font-medium' : 'font-normal'
  const text = fmt.money(value, { lowerBound: false })
  const body = (
    <span className={`fig whitespace-nowrap text-money ${w} ${className}`}>
      {text}
      {lowerBound ? <span className="text-ink3">+</span> : null}
    </span>
  )
  return lowerBound ? <Tooltip content={`Excludes ${nUnpriced} calls to unpriced models`}>{body}</Tooltip> : body
}

type Ink = 'ink1' | 'ink2' | 'ink3'
const INK: Record<Ink, string> = { ink1: 'text-ink1', ink2: 'text-ink2', ink3: 'text-ink3' }

export function Tokens({ value, ink = 'ink2', loading = false, className = '' }: { value: number | null; ink?: Ink; loading?: boolean; className?: string }) {
  if (loading) return <Skeleton width={36} className={className} />
  // FALLBACK-OK: SYS-31 (zero by construction: the cell is blank)
  if (value === null) return null
  return <span className={`fig whitespace-nowrap ${INK[ink]} ${className}`}>{fmt.tokens(value)}</span>
}

export function Duration({ ms, ink = 'ink2', loading = false, className = '' }: { ms: number | null; ink?: Ink; loading?: boolean; className?: string }) {
  if (loading) return <Skeleton width={36} className={className} />
  // FALLBACK-OK: SYS-31 (zero by construction: the cell is blank)
  if (ms === null) return null
  return <span className={`fig whitespace-nowrap ${INK[ink]} ${className}`}>{fmt.dur(ms)}</span>
}

/** SYS-29: ink1 500 as a row's leading time, otherwise ink3. */
export function Clock({ ms, tz, lead = false, className = '' }: { ms: number; tz: string; lead?: boolean; className?: string }) {
  return <span className={`fig whitespace-nowrap ${lead ? 'font-medium text-ink1' : 'text-ink3'} ${className}`}>{fmt.clock(ms, tz)}</span>
}
