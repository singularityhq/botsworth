// Placement of a floating box (tooltip, popover) against an anchor, clamped to the viewport.
export interface Rect { left: number; top: number; right: number; bottom: number }
export interface Placement { left: number; top: number; side: 'top' | 'bottom' }

export function placeBox(
  anchor: Rect,
  size: { w: number; h: number },
  viewport: { w: number; h: number },
  opts: { side: 'top' | 'bottom'; align: 'start' | 'center' | 'end'; gap?: number; margin?: number },
): Placement {
  const { gap = 8, margin = 8 } = opts
  const roomAbove = anchor.top - gap - margin
  const roomBelow = viewport.h - anchor.bottom - gap - margin
  let side = opts.side
  if (side === 'top' && roomAbove < size.h && roomBelow > roomAbove) side = 'bottom'
  else if (side === 'bottom' && roomBelow < size.h && roomAbove > roomBelow) side = 'top'
  const top = side === 'top' ? anchor.top - gap - size.h : anchor.bottom + gap
  const raw =
    opts.align === 'start' ? anchor.left : opts.align === 'end' ? anchor.right - size.w : (anchor.left + anchor.right) / 2 - size.w / 2
  const left = Math.max(margin, Math.min(raw, viewport.w - size.w - margin))
  return { left, top, side }
}
