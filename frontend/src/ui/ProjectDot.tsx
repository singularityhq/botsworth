import { seriesVar, type Slot } from './series'

/** SYS-28: a filled dot, 8 px in rows and 10 px in headers, in the project's series colour. */
export function ProjectDot({ slot, size = 8, className = '' }: { slot: Slot; size?: 8 | 10; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block shrink-0 rounded-full ${className}`}
      style={{ width: size, height: size, background: seriesVar(slot) }}
    />
  )
}
