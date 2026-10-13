// SYS-50, SES-6: key routing decisions, free of the DOM so the order can be tested.

export interface KeyInfo {
  key: string
  metaKey: boolean
  ctrlKey: boolean
  altKey: boolean
}

export type KeyClass = 'palette-toggle' | 'escape' | 'focus-left' | 'focus-right' | 'blade' | 'ignore'

/** Which handler gets a key. Only Cmd/Ctrl+K and Esc work while a text field has focus; an open overlay owns every other key. */
export function classifyKey(e: KeyInfo, typing: boolean, overlayOpen: boolean): KeyClass {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') return 'palette-toggle'
  if (e.key === 'Escape') return 'escape'
  if (typing || overlayOpen) return 'ignore'
  if (e.altKey || e.metaKey || e.ctrlKey) return 'ignore'
  if (e.key === 'ArrowLeft') return 'focus-left'
  if (e.key === 'ArrowRight') return 'focus-right'
  return 'blade'
}

export interface EscapeCtx {
  /** Close the topmost overlay (palette, popover, overlay panel). Returns false when none is open. */
  closeOverlay(): boolean
  isTyping(): boolean
  blurField(): void
  bladeCount: number
  /** The rightmost blade's own Esc handler. Returns true when it consumed the key. */
  rightmostEscape(): boolean
  closeRightmost(): void
}

export type EscapeResult = 'overlay' | 'blur' | 'blade' | 'close' | 'none'

/** SES-6: overlay, then blur the field, then the rightmost blade's handler, then close the rightmost blade. The explorer alone is never closed. */
export function runEscape(c: EscapeCtx): EscapeResult {
  if (c.closeOverlay()) return 'overlay'
  if (c.isTyping()) {
    c.blurField()
    return 'blur'
  }
  if (c.rightmostEscape()) return 'blade'
  if (c.bladeCount > 1) {
    c.closeRightmost()
    return 'close'
  }
  return 'none'
}
