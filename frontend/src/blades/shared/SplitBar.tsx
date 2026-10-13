import { useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { Tooltip } from '../../ui/Tooltip'
import { segmentWidths } from './answers.logic'

export interface Segment {
  key: string
  value: number
  style: CSSProperties
  /** Tooltip text, e.g. "Waiting on you 30s". */
  title: string
}

/** Width of an element in px, kept up to date by a ResizeObserver; 0 until measured. */
function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null)
  const [w, setW] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => setW(el.clientWidth)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

/**
 * An 8 px bar of segments on l2, radius 4, 2 px gaps (SES-26). Each segment is as wide as its value allows, at least 2 px
 * (`segmentWidths`). Used for the money split and the seven-state split. Segments with no value are not drawn.
 * Until the bar is measured the segments share the width by `flex-grow`, so the first paint is already close.
 */
export function SplitBar({ segments, label }: { segments: readonly Segment[]; label: string }) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const shown = segments.filter((s) => s.value > 0)
  const widths = width > 0 ? segmentWidths(shown.map((s) => s.value), width) : null
  return (
    <div ref={ref} role="img" aria-label={label} className="my-2 flex h-2 gap-0.5 overflow-hidden rounded-[4px] bg-l2">
      {shown.map((s, i) => (
        <Tooltip key={s.key} content={s.title}>
          <i
            className="block h-2"
            style={{ ...s.style, ...(widths ? { width: widths[i] } : { flexGrow: s.value, minWidth: 2 }) }}
          />
        </Tooltip>
      ))}
    </div>
  )
}
