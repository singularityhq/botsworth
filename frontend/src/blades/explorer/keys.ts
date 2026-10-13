import { useEffect, useRef } from 'react'
import { useBladeKeys, useEscape } from '../../shell/keys'

/**
 * The shell keeps one key handler and one Esc handler per blade (shell/keys.ts), so the explorer's page (F2a) and its list and rail
 * (F2b) cannot each call `useBladeKeys('explorer')`: the later registration would replace the earlier. The page registers once and
 * dispatches here. Parts register with `useExplorerKeys` / `useExplorerEscape`; the page's own keys run first, then the parts in
 * registration order. The first handler that returns true wins.
 */
type KeyFn = (e: KeyboardEvent) => boolean | void
type EscFn = () => boolean

const keyParts: KeyFn[] = []
const escParts: EscFn[] = []

function add<T>(list: T[], fn: T): () => void {
  list.push(fn)
  return () => {
    const i = list.indexOf(fn)
    if (i >= 0) list.splice(i, 1)
  }
}

/** For a part of the explorer page (list, rail): receive blade keys while the explorer blade is focused. */
export function useExplorerKeys(handler: KeyFn): void {
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => add(keyParts, (e) => ref.current(e)), [])
}

/** For a part of the explorer page: take Esc when the explorer is the rightmost blade. Return true when it did something. */
export function useExplorerEscape(fn: EscFn): void {
  const ref = useRef(fn)
  ref.current = fn
  useEffect(() => add(escParts, () => ref.current()), [])
}

/** Registers the single `explorer` handlers with the shell. Call once, from the page component. */
export function useExplorerRouter(own: { key: KeyFn; escape: EscFn }): void {
  const ref = useRef(own)
  ref.current = own
  useBladeKeys('explorer', (e) => {
    if (ref.current.key(e) === true) return true
    for (const part of [...keyParts]) if (part(e) === true) return true
    return false
  })
  useEscape('explorer', () => {
    if (ref.current.escape()) return true
    for (const part of [...escParts]) if (part()) return true
    return false
  })
}
