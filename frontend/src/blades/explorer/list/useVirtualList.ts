import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { shouldFade } from './list.logic'
import { visibleRange, type Layout, type Pin } from './virtual.logic'

/** The nearest ancestor that scrolls vertically: the explorer page (EXP-5: "the page scrolls as one") or the rail's own body. */
export function findScrollParent(el: HTMLElement): HTMLElement | null {
  for (let n = el.parentElement; n; n = n.parentElement) {
    const o = getComputedStyle(n).overflowY
    if (o === 'auto' || o === 'scroll' || o === 'overlay') return n
  }
  return null
}

interface View {
  from: number
  to: number
  pin: Pin | null
}

const NONE: View = { from: 0, to: 0, pin: null }

/**
 * Which items to draw. The list sits inside a scroll container it does not own; this reads where that container's viewport falls on the
 * list and returns the item range under it, plus the group header to hold at the top (SYS-37 "sticky"). `stickyTop` is the height of
 * whatever is already stuck above the list's rows (the column band).
 */
export function useVirtualList(layout: Layout, o: { stickyTop: number; overscan: number }) {
  // A callback ref held in state: the list container mounts after the data arrives, and the effects below must follow it.
  const [node, container] = useState<HTMLDivElement | null>(null)
  const nodeRef = useRef(node)
  nodeRef.current = node
  const scroller = useRef<HTMLElement | null>(null)
  /** True once the scroll container is found; effects that scroll wait for it. */
  const [ready, setReady] = useState(false)
  const [view, setView] = useState<View>({ ...NONE, to: Math.min(layout.count, o.overscan + 1) })
  const latest = useRef({ layout, ...o })
  latest.current = { layout, ...o }

  const measure = useCallback(() => {
    const c = nodeRef.current
    const s = scroller.current
    if (!c || !s) return
    const { layout: L, stickyTop, overscan } = latest.current
    const cr = c.getBoundingClientRect()
    const sr = s.getBoundingClientRect()
    const y0 = sr.top - cr.top
    const y1 = sr.bottom - cr.top
    const [from, to] = visibleRange(L, y0, y1, overscan)
    const pin = L.pin(y0 + stickyTop)
    setView((v) => (v.from === from && v.to === to && v.pin?.index === pin?.index && v.pin?.top === pin?.top ? v : { from, to, pin }))
  }, [])

  useLayoutEffect(() => {
    const c = node
    if (!c) return
    const s = findScrollParent(c)
    if (!s) throw new Error('The session list must sit inside a vertical scroll container (EXP-5)')
    scroller.current = s
    setReady(true)
    let frame = 0
    const onScroll = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(measure)
    }
    s.addEventListener('scroll', onScroll, { passive: true })
    const ro = new ResizeObserver(measure)
    ro.observe(s)
    ro.observe(c)
    measure()
    return () => {
      cancelAnimationFrame(frame)
      s.removeEventListener('scroll', onScroll)
      ro.disconnect()
      setReady(false)
    }
  }, [measure, node])

  // The layout changed (rows loaded, a group folded, the sort changed): measure again.
  useLayoutEffect(() => {
    measure()
  }, [layout, o.stickyTop, o.overscan, measure])

  /** Scroll so item `i` is in view below `inset` px of stuck headers. Does nothing if it already is. */
  const scrollToItem = useCallback((i: number, inset: number) => {
    const c = nodeRef.current
    const s = scroller.current
    if (!c || !s) return
    const L = latest.current.layout
    const cr = c.getBoundingClientRect()
    const sr = s.getBoundingClientRect()
    const top = cr.top - sr.top + L.offset(i)
    const bottom = top + L.size(i)
    if (top < inset) s.scrollTop += top - inset
    else if (bottom > sr.height) s.scrollTop += bottom - sr.height
  }, [])

  return { container, view, scrollToItem, ready }
}

/** `prefers-reduced-motion: reduce` (SYS-59). */
export function reducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** EXP-78: when the new data arrives, the list fades from 0.45 to 1 over 120 ms. */
export function useArrivalFade(stale: boolean, ref: React.RefObject<HTMLElement | null>): void {
  const was = useRef(stale)
  useEffect(() => {
    if (was.current && !stale && shouldFade(was.current, stale, reducedMotion())) {
      ref.current?.animate([{ opacity: 0.45 }, { opacity: 1 }], { duration: 120, easing: 'ease-out' })
    }
    was.current = stale
  }, [stale, ref])
}
