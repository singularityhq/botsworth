import { useEffect } from 'react'
import { useOverlay } from '../../ui/overlay'
import { Icon } from '../../ui/Icon'
import { KeyCap } from '../../ui/KeyCap'
import { KEYS } from './keysList'

/** SES-72: a centred l3 card listing every key and its effect. `?`, Esc or its `x` closes it. */
export function KeysOverlay({ open, onClose }: { open: boolean; onClose: () => void }) {
  useOverlay(open, onClose) // Esc closes it first (SES-6 step 1)
  useEffect(() => {
    if (!open) return
    // An open overlay owns the keys (SYS-50), so the router never passes `?` on: the overlay listens for it itself.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '?') {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center" onPointerDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Keyboard"
        className="pop-in max-h-[80vh] w-[640px] max-w-[calc(100vw-48px)] overflow-y-auto rounded-card bg-l3 p-6 shadow-pop"
        onPointerDown={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center gap-2">
          <Icon name="keyboard" size={20} tone="quiet" />
          <h2 className="flex-1 text-lead font-semibold text-ink1">Keyboard</h2>
          <button type="button" aria-label="Close (?)" onClick={onClose} className="inline-flex h-7 w-7 items-center justify-center rounded-control hover:bg-l4">
            <Icon name="x" tone="muted" />
          </button>
        </div>
        <dl className="grid grid-cols-[auto_1fr] items-center gap-x-6 gap-y-3">
          {KEYS.map((k) => (
            <div key={k.keys.join(' ')} className="contents">
              <dt className="flex flex-wrap gap-1">
                {k.keys.map((c) => (
                  <KeyCap key={c}>{c}</KeyCap>
                ))}
              </dt>
              <dd className="text-ink2">{k.effect}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  )
}
