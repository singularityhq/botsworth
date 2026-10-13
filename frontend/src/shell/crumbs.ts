import { createContext, useContext, useEffect } from 'react'

// A blade registers the label its crumb shows (SES-7), e.g. the session's display name. The shell falls back
// to the strip's own label until a blade has one.
export interface CrumbCtx {
  labels: Readonly<Record<string, string>>
  setLabel: (key: string, label: string | null) => void
}
export const Ctx = createContext<CrumbCtx | null>(null)

function useCrumbCtx(): CrumbCtx {
  const c = useContext(Ctx)
  if (!c) throw new Error('useCrumb used outside CrumbProvider')
  return c
}

export function useCrumbLabels(): Readonly<Record<string, string>> {
  return useCrumbCtx().labels
}

/** Register the crumb label for blade `key` while the calling blade is mounted. */
export function useCrumb(key: string, label: string | null): void {
  const { setLabel } = useCrumbCtx()
  useEffect(() => {
    setLabel(key, label)
    return () => setLabel(key, null)
  }, [key, label, setLabel])
}
