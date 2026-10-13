import { useCallback, useContext, useMemo, useState } from 'react'
import { StatusBar } from './StatusBar'
import { ItemsCtx, ORDER, SetCtx, type StatusItems, type StatusKey } from './statusCtx'

/**
 * SES-70: the status bar is one line along the bottom of the stage, owned by the shell. Each blade says what it shows with `useStatus`;
 * the bar shows the text of the rightmost blade that has one. The session blade gives "Turn N selected"; the turn blade (F4a) gives its
 * row position, the skill counter and the search reminder, without touching this file.
 */

export function StatusProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<StatusItems>({})
  const set = useCallback((key: StatusKey, node: React.ReactNode) => setItems((cur) => (node === null ? (key in cur ? Object.fromEntries(Object.entries(cur).filter(([k]) => k !== key)) : cur) : { ...cur, [key]: node })), [])
  return (
    <SetCtx.Provider value={set}>
      <ItemsCtx.Provider value={items}>{children}</ItemsCtx.Provider>
    </SetCtx.Provider>
  )
}

/** The bar itself, for the shell: shown while a session blade is open and one of the open blades has text. */
export function StatusHost({ sessionOpen }: { sessionOpen: boolean }) {
  const items = useContext(ItemsCtx)
  const shown = useMemo(() => [...ORDER].reverse().map((k) => items[k]).find((n) => n !== undefined && n !== null), [items])
  if (!sessionOpen) return null
  return <StatusBar>{shown ?? null}</StatusBar>
}
