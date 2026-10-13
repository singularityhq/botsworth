import { useEffect, useRef } from 'react'
import { closeTopOverlay, overlayOpen } from '../ui/overlay'
import { classifyKey, runEscape } from './keys.logic'

// Per-blade registries. A blade registers while mounted; the global router reads them at key time.
type KeyHandler = (e: KeyboardEvent) => boolean | void
const keyHandlers = new Map<string, KeyHandler>()
const escHandlers = new Map<string, () => boolean>()

/** Register the handler that gets the keys other than Cmd+K, Esc and the arrows while blade `key` is focused (SYS-50).
 *  Return true to say the key was used (the router then prevents the browser default). */
export function useBladeKeys(key: string, handler: KeyHandler): void {
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => {
    keyHandlers.set(key, (e) => ref.current(e))
    return () => void keyHandlers.delete(key)
  }, [key])
}

/** Let blade `key` consume Esc (SES-6): return true when it did something. Called only when the blade is the rightmost. */
export function useEscape(key: string, fn: () => boolean): void {
  const ref = useRef(fn)
  ref.current = fn
  useEffect(() => {
    escHandlers.set(key, () => ref.current())
    return () => void escHandlers.delete(key)
  }, [key])
}

const isTyping = (t: EventTarget | null) => {
  const el = t as HTMLElement | null
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)
}

interface GlobalKeys {
  /** Keys of the open blades, left to right. */
  bladeKeys: readonly string[]
  focusKey: string
  setFocusIndex: (delta: number) => void
  closeRightmost: () => void
  togglePalette: () => void
}

/** Install the global key router once, in the shell. */
export function useGlobalKeys(o: GlobalKeys): void {
  const ref = useRef(o)
  ref.current = o
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const s = ref.current
      const typing = isTyping(e.target)
      const cls = classifyKey(e, typing, overlayOpen())
      switch (cls) {
        case 'palette-toggle':
          e.preventDefault()
          s.togglePalette()
          return
        case 'escape': {
          const rightmost = s.bladeKeys[s.bladeKeys.length - 1]
          const r = runEscape({
            closeOverlay: closeTopOverlay,
            isTyping: () => typing,
            blurField: () => (e.target as HTMLElement).blur(),
            bladeCount: s.bladeKeys.length,
            // FALLBACK-OK: SES-6 (a blade with no Esc handler lets Esc close it)
            rightmostEscape: () => escHandlers.get(rightmost)?.() ?? false,
            closeRightmost: s.closeRightmost,
          })
          if (r !== 'none') e.preventDefault()
          return
        }
        case 'focus-left':
          e.preventDefault()
          s.setFocusIndex(-1)
          return
        case 'focus-right':
          e.preventDefault()
          s.setFocusIndex(1)
          return
        case 'blade':
          if (keyHandlers.get(s.focusKey)?.(e) === true) e.preventDefault()
          return
        case 'ignore':
          return
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
}
