import { createContext, useContext, useEffect } from 'react'

/** Status bar registration (SES-70); the provider and the bar are in `status.tsx`. */
export const ORDER = ['session', 'turn', 'detail'] as const
export type StatusKey = (typeof ORDER)[number]
export type StatusItems = Partial<Record<StatusKey, React.ReactNode>>
export const SetCtx = createContext<((key: StatusKey, node: React.ReactNode) => void) | null>(null)
export const ItemsCtx = createContext<StatusItems>({})

/**
 * Show `node` in the status bar while this blade is mounted; null shows nothing. Re-register on every render that changes the text.
 * Outside a provider (a blade rendered alone) it does nothing.
 */
export function useStatus(key: StatusKey, node: React.ReactNode): void {
  const set = useContext(SetCtx)
  useEffect(() => {
    set?.(key, node) // FALLBACK-OK: SES-70 (a blade rendered without the shell has no bar to write to)
    return () => set?.(key, null) // FALLBACK-OK: SES-70 (same: no shell, no bar)
  }, [set, key, node])
}
