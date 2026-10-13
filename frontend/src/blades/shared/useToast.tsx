import { useCallback, useState } from 'react'
import { Toast } from '../../ui/Toast'
import type { Rich } from './toastText'

/** SES-71: one toast at a time, a new one replaces the current, closes after 2.4 s or on Esc, figures in 500, one line up to 760 px. */
const TOAST_MS = 2400

export function useToast(): { show: (message: Rich) => void; node: React.ReactNode } {
  const [toast, setToast] = useState<{ id: number; message: Rich } | null>(null)
  const show = useCallback((message: Rich) => setToast((t) => ({ id: (t?.id ?? 0) + 1, message })), []) // FALLBACK-OK: SES-71 (the first toast has no earlier id)
  const node = toast ? (
    <Toast
      key={toast.id}
      timeoutMs={TOAST_MS}
      onClose={() => setToast((t) => (t?.id === toast.id ? null : t))}
      message={
        <span className="block max-w-[760px] truncate whitespace-nowrap">
          {toast.message.map((part, i) =>
            typeof part === 'string' ? (
              part
            ) : (
              <b key={i} className={part.plain ? 'font-medium' : 'fig font-medium'}>
                {part.b}
              </b>
            ),
          )}
        </span>
      }
    />
  ) : null
  return { show, node }
}
