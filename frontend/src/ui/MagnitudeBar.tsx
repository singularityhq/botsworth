import { barWidth } from './bar.logic'

/** SYS-39: a bar for a single figure, in its own track left of the figure. Linear to the visible list's maximum, right-anchored,
 *  10 px tall, radius 2; a bar under 1 px is not drawn. The track stays so columns keep their alignment. */
export function MagnitudeBar({ value, max, track = 64 }: { value: number | null; max: number; track?: 64 | 96 }) {
  const w = barWidth(value, max, track)
  return (
    <span aria-hidden="true" className="flex shrink-0 justify-end" style={{ width: track }}>
      {w > 0 ? <i className="block h-2.5 rounded-[2px]" style={{ width: w, background: 'var(--list-bar)' }} /> : null}
    </span>
  )
}
