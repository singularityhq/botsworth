import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { placeBox, type Placement } from './place'

const REST_MS = 350

/** SYS-43: l4 surface, shadow-pop, 8 x 10 padding, 14 px text. Shows after the pointer rests, and on keyboard focus.
 *  It never holds the only copy of information needed to act. */
export function Tooltip({ content, children, side = 'top' }: { content: React.ReactNode; children: React.ReactNode; side?: 'top' | 'bottom' }) {
  const anchor = useRef<HTMLSpanElement>(null)
  const tip = useRef<HTMLDivElement>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState<Placement | null>(null)
  const id = useRef(`tip-${Math.random().toString(36).slice(2, 9)}`).current

  const show = (delay: number) => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setOpen(true), delay)
  }
  const hide = () => {
    if (timer.current) clearTimeout(timer.current)
    setOpen(false)
    setAt(null)
  }
  useEffect(() => () => void (timer.current && clearTimeout(timer.current)), [])

  useLayoutEffect(() => {
    if (!open || !anchor.current || !tip.current) return
    const r = anchor.current.getBoundingClientRect()
    const t = tip.current.getBoundingClientRect()
    setAt(placeBox(r, { w: t.width, h: t.height }, { w: window.innerWidth, h: window.innerHeight }, { side, align: 'center' }))
  }, [open, side, content])

  return (
    <span ref={anchor} className="inline-flex" aria-describedby={open ? id : undefined} onPointerEnter={() => show(REST_MS)} onPointerLeave={hide} onFocus={() => show(0)} onBlur={hide}>
      {children}
      {open
        ? createPortal(
            <div
              ref={tip}
              id={id}
              role="tooltip"
              className="pointer-events-none fixed z-[80] max-w-[360px] rounded-control bg-l4 px-2.5 py-2 text-body text-ink1 shadow-pop"
              // FALLBACK-OK: SYS-43 (the tooltip is hidden until its first measure places it)
              style={{ left: at?.left ?? 0, top: at?.top ?? 0, visibility: at ? 'visible' : 'hidden', fontVariantNumeric: 'tabular-nums' }}
            >
              {content}
            </div>,
            document.body,
          )
        : null}
    </span>
  )
}
