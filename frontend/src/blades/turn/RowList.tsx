import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { scrollTarget } from '../session/turnList.logic'
import type { Item } from './rowList.logic'

/** How many items to draw before the viewport is measured (and in tests, which have no layout). */
const FALLBACK_ITEMS = 80
/** Items drawn beyond the viewport on each side. */
const OVERSCAN = 12

export type ScrollRequest = { n: number; mode: 'nearest' | 'center' }

interface RowListProps {
  items: readonly Item[]
  heightOf: (item: Item) => number
  render: (item: Item, index: number) => React.ReactNode
  /** Band nesting of an item: 0 on the main thread (TRN-35). */
  levelOf: (item: Item) => number
  /** Index of the item that holds the cursor, or -1. */
  cursorIndex: number
  /** Bump `n` to scroll the cursor into view: `nearest` puts it about 30 % from the top, `center` centres it (TRN-54). */
  scroll: ScrollRequest
  label: string
  /** Extra room under the last row so the last rows can reach the 30 % line. */
  tail?: number
}

/** TRN-35: a band is a 20 px indent with a 1 px rule on its left; nested bands add 16 px each, to three levels. */
function wrap(level: number, child: React.ReactNode): React.ReactNode {
  let out = child
  for (let l = Math.min(level, 3); l >= 1; l--) {
    out = <div className={`border-l border-rule ${l === 1 ? 'ml-5' : 'ml-4'}`}>{out}</div>
  }
  return out
}

/**
 * The scrolling, windowed row list (TRN-63: a turn of 1,000 rows or more renders without blocking input). Every item has a known height, so
 * the window is plain arithmetic: spacers above and below stand in for the items that are not drawn. Bands are drawn per row (a left rule on
 * each), so they need no wrapper that spans rows.
 */
export function RowList({ items, heightOf, render, levelOf, cursorIndex, scroll, label, tail = 160 }: RowListProps) {
  const box = useRef<HTMLDivElement>(null)
  const [top, setTop] = useState(0)
  const [viewH, setViewH] = useState(0)

  useLayoutEffect(() => {
    const el = box.current!
    const measure = () => setViewH(el.clientHeight)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const offsets = useMemo(() => {
    const o = new Array<number>(items.length + 1)
    o[0] = 0
    for (let i = 0; i < items.length; i++) o[i + 1] = o[i] + heightOf(items[i])
    return o
  }, [items, heightOf])
  const total = offsets[items.length]

  // The scroll request: bring the cursor row into view (TRN-54).
  const done = useRef(0)
  useEffect(() => {
    if (cursorIndex < 0 || done.current === scroll.n) return
    const el = box.current
    if (!el || viewH === 0) return
    done.current = scroll.n
    const rowTop = offsets[cursorIndex]
    const rowH = offsets[cursorIndex + 1] - rowTop
    const to = scroll.mode === 'center' ? Math.max(0, Math.round(rowTop - (viewH - rowH) / 2)) : scrollTarget(rowTop, rowH, el.scrollTop, viewH)
    if (to !== null) el.scrollTop = to
  }, [scroll, cursorIndex, offsets, viewH])

  // The window: binary search for the first item that ends below the top.
  let first = 0
  let last = items.length
  if (viewH > 0) {
    let lo = 0
    let hi = items.length
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (offsets[mid + 1] <= top) lo = mid + 1
      else hi = mid
    }
    first = Math.max(0, lo - OVERSCAN)
    let end = lo
    while (end < items.length && offsets[end] < top + viewH) end++
    last = Math.min(items.length, end + OVERSCAN)
  } else last = Math.min(items.length, FALLBACK_ITEMS)
  const drawn = items.slice(first, last)

  return (
    <div ref={box} aria-label={label} className="min-h-0 flex-1 overflow-y-auto" onScroll={(e) => setTop(e.currentTarget.scrollTop)}>
      <div style={{ height: offsets[first] }} />
      {drawn.map((it, i) => (
        <Fragment key={it.key}>{wrap(levelOf(it), render(it, first + i))}</Fragment>
      ))}
      <div style={{ height: total - offsets[last] + tail }} />
    </div>
  )
}
