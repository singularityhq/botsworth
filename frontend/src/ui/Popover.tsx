import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { useOverlay } from './overlay'
import { placeBox, type Placement } from './place'

/** SYS-44: l3 surface, shadow-pop, radius-card. Closes on Esc (through the overlay stack), on outside click, and the caller closes it
 *  after a choice that ends the task. On close, focus returns to the control that opened it. */
export function Popover({
  open, onClose, anchorRef, width = 320, align = 'end', label, children,
}: { open: boolean; onClose: () => void; anchorRef: RefObject<HTMLElement | null>; width?: number; align?: 'start' | 'end'; label: string; children: React.ReactNode }) {
  const box = useRef<HTMLDivElement>(null)
  const [at, setAt] = useState<Placement | null>(null)
  const wasOpen = useRef(false)
  useOverlay(open, onClose)

  useLayoutEffect(() => {
    if (!open || !anchorRef.current || !box.current) return
    const r = anchorRef.current.getBoundingClientRect()
    const b = box.current.getBoundingClientRect()
    setAt(placeBox(r, { w: width, h: b.height }, { w: window.innerWidth, h: window.innerHeight }, { side: 'bottom', align, gap: 6 }))
  }, [open, anchorRef, width, align, children])

  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node
      if (box.current?.contains(t) || anchorRef.current?.contains(t)) return
      onClose()
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [open, onClose, anchorRef])

  useEffect(() => {
    if (open) wasOpen.current = true
    else if (wasOpen.current) {
      wasOpen.current = false
      setAt(null)
      anchorRef.current?.focus()
    }
  }, [open, anchorRef])

  if (!open) return null
  return createPortal(
    <div
      ref={box}
      role="dialog"
      aria-label={label}
      className="pop-in fixed z-[70] rounded-card bg-l3 p-2 text-body text-ink1 shadow-pop"
      // FALLBACK-OK: SYS-44 (the popover is hidden until its first measure places it)
      style={{ left: at?.left ?? 0, top: at?.top ?? 0, width, visibility: at ? 'visible' : 'hidden' }}
    >
      {children}
    </div>,
    document.body,
  )
}
