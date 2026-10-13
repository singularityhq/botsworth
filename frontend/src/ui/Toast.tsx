import { useEffect, useRef } from 'react'
import { useOverlay } from './overlay'
import { PauseTimer } from './toast.logic'

/** One toast on l3 (architecture ambiguity 1): bottom centre of the stage, shadow-pop, 14 px ink1.
 *  Closes on Undo, on Esc, or after `timeoutMs` (8 s with Undo per EXP-73; 2.4 s without per SES-71). The timer pauses while the pointer is over it. */
export function Toast({ message, onClose, onUndo, timeoutMs = 8000 }: { message: React.ReactNode; onClose: () => void; onUndo?: () => void; timeoutMs?: number }) {
  const timer = useRef<PauseTimer | null>(null)
  const close = useRef(onClose)
  close.current = onClose
  useOverlay(true, onClose, false)
  useEffect(() => {
    const t = new PauseTimer(timeoutMs, () => close.current())
    timer.current = t
    return () => t.cancel()
  }, [timeoutMs])

  return (
    <div
      role="status"
      onPointerEnter={() => timer.current?.pause()}
      onPointerLeave={() => timer.current?.resume()}
      className="pop-in fixed bottom-6 left-1/2 z-[90] flex -translate-x-1/2 items-center gap-4 rounded-card bg-l3 px-4 py-2.5 text-body text-ink1 shadow-pop"
    >
      <span>{message}</span>
      {onUndo ? (
        <button
          type="button"
          onClick={() => {
            onUndo()
            onClose()
          }}
          className="font-medium text-struct hover:underline"
        >
          Undo
        </button>
      ) : null}
    </div>
  )
}
