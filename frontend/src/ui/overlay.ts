import { useEffect, useRef } from 'react'

// SES-6 step 1: the palette, a popover or an overlay panel closes before anything else on Esc.
// Overlays register while open; the newest closes first.
// A toast is an overlay that does not block keys (D45): Esc closes it only when no blocking overlay is open.
const stack: { close: () => void; blocking: boolean }[] = []

export function registerOverlay(close: () => void, blocking = true): () => void {
  const entry = { close, blocking }
  stack.push(entry)
  return () => {
    const i = stack.indexOf(entry)
    if (i >= 0) stack.splice(i, 1)
  }
}

/** Close the topmost blocking overlay, or the topmost non-blocking one when no blocking overlay is open. Returns false when none is open. */
export function closeTopOverlay(): boolean {
  const top = [...stack].reverse().find((e) => e.blocking) ?? stack[stack.length - 1]
  if (!top) return false
  top.close()
  return true
}

/** True while a blocking overlay is open: the shell then ignores every key but Esc and Cmd+K. */
export function overlayOpen(): boolean {
  return stack.some((e) => e.blocking)
}

/** Register `close` as an overlay while `open` is true. `blocking: false` for a toast. */
export function useOverlay(open: boolean, close: () => void, blocking = true): void {
  const ref = useRef(close)
  ref.current = close
  useEffect(() => {
    if (!open) return
    return registerOverlay(() => ref.current(), blocking)
  }, [open, blocking])
}
